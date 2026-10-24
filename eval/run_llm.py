"""Send every prompt to the same model and save replies plus token usage.

Uses only the logged-in `claude` CLI, which runs on your existing Claude plan.
There is deliberately no paid-API option. Usage:  python3 run_llm.py [model]   (default sonnet)
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

OUT = Path(__file__).resolve().parent / "out"


def call_cli(prompt: str, model: str) -> tuple[str, dict]:
    proc = subprocess.run(["claude", "-p", "--model", model, "--tools", "", "--no-session-persistence",
                           "--system-prompt", "Follow the user's instructions exactly.", "--output-format", "json"],
                          input=prompt, capture_output=True, text=True, timeout=300)
    data = json.loads(proc.stdout)
    if data.get("is_error"):
        raise RuntimeError(data.get("result"))
    u = data["usage"]
    tokens_in = u["input_tokens"] + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
    return data["result"], {"input_tokens": tokens_in, "output_tokens": u["output_tokens"]}


def main() -> None:
    model = sys.argv[1] if len(sys.argv) > 1 else "sonnet"
    (OUT / "answers").mkdir(exist_ok=True)
    for p in sorted((OUT / "prompts").glob("s*_*.txt")):
        t0 = time.time()
        text, usage = call_cli(p.read_text(), model)
        usage["latency_s"] = round(time.time() - t0, 1)
        usage["model"] = model
        (OUT / "answers" / p.name).write_text(text)
        (OUT / "answers" / p.name.replace(".txt", ".usage.json")).write_text(json.dumps(usage))
        print(p.stem, usage)


if __name__ == "__main__":
    main()
