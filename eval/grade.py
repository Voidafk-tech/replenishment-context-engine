"""Grade model answers against the known correct answers.

Reads eval/out/answers/sNN_{raw,context}.txt (the model's reply) and optional
sNN_{cond}.usage.json ({"input_tokens": n, "output_tokens": n, "latency_s": x}).
Writes eval/out/results.json and prints a summary.

Correct  = total cases within 1, coverage verdict right, approval verdict right.
Unsafe   = the plan would break a real constraint: ships more from a DC than is free
           and transportable there (committed, stale, unit or truck errors), or
           skips an approval the plan needs.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

OUT = Path(__file__).resolve().parent / "out"
CONDITIONS = ("raw", "context")
TOLERANCE = 1
CHARS_PER_TOKEN = 4  # rough estimate used only when no measured usage exists


def parse(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def grade_one(ans: dict | None, truth: dict) -> dict:
    if ans is None:
        return {"valid": False, "correct": False, "unsafe": False, "issues": ["no valid JSON answer"]}
    issues, unsafe = [], []
    try:
        total = int(ans.get("total_cases", -1))
    except (TypeError, ValueError):
        total = -1
    ok_total = abs(total - truth["total_cases"]) <= TOLERANCE
    ok_cov = ans.get("fully_covered") is truth["fully_covered"]
    ok_appr = ans.get("needs_approval") is truth["needs_approval"]
    if not ok_total:
        issues.append(f"total {total} vs {truth['total_cases']}")
    if not ok_cov:
        issues.append("coverage verdict wrong")
    if not ok_appr:
        issues.append("approval verdict wrong")
    for s in ans.get("shipments") or []:
        dc = next((d for d in truth["limits"] if d.split()[0].lower() in str(s.get("from_dc", "")).lower()), None)
        try:
            cases = int(s.get("cases", 0))
        except (TypeError, ValueError):
            cases = 0
        if dc is None:
            if cases > 0:
                unsafe.append(f"ships from unknown DC {s.get('from_dc')!r}")
        elif cases > truth["limits"][dc] + TOLERANCE:
            unsafe.append(f"{dc}: ships {cases}, only {truth['limits'][dc]} free and transportable")
    if truth["needs_approval"] and ans.get("needs_approval") is False:
        unsafe.append("skips required regional approval")
    return {"valid": True, "correct": ok_total and ok_cov and ok_appr, "unsafe": bool(unsafe),
            "issues": issues + unsafe, "total": total}


def main() -> None:
    rows = []
    for sc in sorted((OUT / "scenarios").glob("s*.json")):
        s = json.loads(sc.read_text())
        for cond in CONDITIONS:
            ans_path = OUT / "answers" / f"{s['id']}_{cond}.txt"
            if not ans_path.exists():
                continue
            prompt = (OUT / "prompts" / f"{s['id']}_{cond}.txt").read_text()
            usage_path = ans_path.with_suffix(".usage.json")
            usage = json.loads(usage_path.read_text()) if usage_path.exists() else {}
            g = grade_one(parse(ans_path.read_text()), s["truth"])
            rows.append({"id": s["id"], "cond": cond, "traps": s["truth"]["traps"], **g,
                         "input_tokens": usage.get("input_tokens", round(len(prompt) / CHARS_PER_TOKEN)),
                         "tokens_measured": "input_tokens" in usage, "latency_s": usage.get("latency_s")})
    summary = {}
    for cond in CONDITIONS:
        r = [x for x in rows if x["cond"] == cond]
        if not r:
            continue
        summary[cond] = {"n": len(r), "correct": sum(x["correct"] for x in r), "unsafe": sum(x["unsafe"] for x in r),
                         "invalid": sum(not x["valid"] for x in r),
                         "mean_input_tokens": round(sum(x["input_tokens"] for x in r) / len(r)),
                         "tokens_measured": all(x["tokens_measured"] for x in r)}
    (OUT / "results.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=1))
    for cond, m in summary.items():
        print(f"{cond:8s} correct {m['correct']}/{m['n']}  unsafe {m['unsafe']}/{m['n']}  invalid {m['invalid']}  "
              f"mean input tokens {m['mean_input_tokens']} ({'measured' if m['tokens_measured'] else 'estimated'})")
    for x in rows:
        if x["issues"]:
            print(f"  {x['id']} {x['cond']:7s} {'UNSAFE ' if x['unsafe'] else ''}{'; '.join(x['issues'])}")


if __name__ == "__main__":
    main()
