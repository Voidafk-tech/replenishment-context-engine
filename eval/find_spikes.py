"""Find real demand spikes in M5 (Walmart) FOODS_3 store sales.

A spike event = one item, one state, one day where the state's total sales
are far above the trailing 28-day mean and at least two stores spiked.
Output: eval/out/spikes.json (20 events, distinct items, spread over states).
"""
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
M5 = ROOT.parent / "data" / "m5_raw"
OUT = ROOT / "out"

LAST_DAYS = 400          # only scan the most recent ~13 months
TRAIL = 28               # trailing window for the baseline
MIN_STATE_BASE = 12.0    # units/day, ignore slow movers
MIN_STATE_RATIO = 2.2    # state total vs baseline
MIN_STORE_RATIO = 1.6
MIN_STORE_UNITS = 6
EVENTS_WANTED = 20
PER_STATE = {"CA": 8, "TX": 6, "WI": 6}


def load_calendar():
    with open(M5 / "calendar.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    return {f"d_{i + 1}": r for i, r in enumerate(rows)}


def load_series():
    """Return {(item, state): {store: [daily units for last LAST_DAYS days]}} and day labels."""
    csv.field_size_limit(sys.maxsize)
    series = {}
    with open(M5 / "sales_train_evaluation.csv", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        day_cols = header[5:][-LAST_DAYS:]
        start = len(header) - LAST_DAYS
        for row in reader:
            if row[1] != "FOODS_3":
                continue
            item, store, state = row[0], row[3], row[4]
            series.setdefault((item, state), {})[store] = [int(x) for x in row[start:]]
    return series, day_cols


def scan(series):
    events = []
    for (item, state), stores in series.items():
        total = [sum(v) for v in zip(*stores.values())]
        best = None
        for t in range(TRAIL, len(total)):
            base = sum(total[t - TRAIL:t]) / TRAIL
            if base < MIN_STATE_BASE:
                continue
            ratio = total[t] / base
            if ratio < MIN_STATE_RATIO or (best and ratio <= best["ratio"]):
                continue
            spiked = []
            for store, s in stores.items():
                sb = sum(s[t - TRAIL:t]) / TRAIL
                if s[t] >= MIN_STORE_UNITS and sb > 0 and s[t] / sb >= MIN_STORE_RATIO:
                    spiked.append(store)
            if len(spiked) >= 2:
                best = {"item": item, "state": state, "t": t, "ratio": round(ratio, 2), "stores": sorted(spiked)}
        if best:
            events.append(best)
    return sorted(events, key=lambda e: -e["ratio"])


def main():
    OUT.mkdir(exist_ok=True)
    cal = load_calendar()
    series, day_cols = load_series()
    candidates = scan(series)
    picked, counts = [], {s: 0 for s in PER_STATE}
    for e in candidates:
        if counts[e["state"]] < PER_STATE[e["state"]]:
            picked.append(e)
            counts[e["state"]] += 1
        if len(picked) == EVENTS_WANTED:
            break
    out = []
    for e in picked:
        t = e["t"]
        stores = series[(e["item"], e["state"])]
        d = day_cols[t]
        out.append({
            "item": e["item"], "state": e["state"], "day": d, "date": cal[d]["date"],
            "event": cal[d]["event_name_1"] if cal[d]["event_name_1"] != "NA" else None,
            "state_ratio": e["ratio"], "spiked_stores": e["stores"],
            # last 14 days per store, spike day last
            "history": {s: v[t - 13:t + 1] for s, v in stores.items()},
            "dates": [cal[c]["date"] for c in day_cols[t - 13:t + 1]],
        })
    (OUT / "spikes.json").write_text(json.dumps(out, indent=1))
    print(f"{len(candidates)} candidate events, picked {len(out)}: {counts}")
    for e in out:
        print(e["state"], e["item"], e["date"], e["event"], e["state_ratio"], e["spiked_stores"])


if __name__ == "__main__":
    main()
