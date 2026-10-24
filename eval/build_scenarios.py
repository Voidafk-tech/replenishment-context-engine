"""Build 20 replenishment scenarios: real M5 demand spikes + synthetic warehouse,
order, truck and policy tables. Each scenario gets a known correct answer and two
prompts that differ only in the data section: raw tables vs. context packet.

Output: eval/out/scenarios/sNN.json, eval/out/prompts/sNN_{raw,context}.txt
"""
from __future__ import annotations

import json
import math
import random
from pathlib import Path

from engine import APPROVAL_LIMIT, DEADLINE_HOURS, build_packet

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "out"
SEED = 20261022

PRODUCTS = ["Spring water 24x500ml", "Bagged ice 5kg", "Sports drink 12x591ml", "Charcoal briquettes 7kg",
            "Sparkling water 12x355ml", "Burger buns 8pk", "Lemonade 2L", "Popsicles 12pk",
            "Iced tea 12x340ml", "Hot dog buns 8pk", "Watermelon whole", "Paper plates 50pk",
            "Ground beef 1kg", "Potato chips 255g", "Sunscreen SPF50", "Coconut water 6x330ml",
            "Frozen pizza", "Cola 24x355ml", "Ice cream 1.5L", "Corn on the cob 6pk"]
DECOY_SUFFIX = " (single)"
NOISE_ITEMS = 6  # other products in the same category, as a category-level SQL pull would return
DC_NAMES = {"CA": ("Stockton DC", "Fontana DC"), "TX": ("Waco DC", "Houston DC"), "WI": ("Madison DC", "Green Bay DC")}
SYSTEM = ("You are a supply-chain copilot for a grocery retailer. Answer the planner's question using only the "
          "data provided. Reply with a single JSON object and nothing else.")
POLICY = f"""Business rules:
1. Need per store until the next scheduled delivery = yesterday's sales (in cases) x 1.5, rounded up.
   Gap per store = need - store on-hand (never below 0). Only the listed stores count.
2. Use the most recent inventory record for each warehouse. Inventory may be recorded in cases or eaches (EA);
   convert eaches to cases with the product's case pack (round down).
3. Stock committed to open customer orders cannot be moved. Shipped orders have already left the building and
   cancelled orders hold no stock.
4. A truck can be used only if its status is 'available' and it arrives by 07:00 tomorrow
   (arrival = departure time today + drive hours). A truck carries at most its capacity.
5. Ship from the primary DC first; use the secondary DC only for what the primary cannot cover.
   Deliver as many cases as possible, up to the total gap.
6. Any truck run costing more than ${APPROVAL_LIMIT} needs regional manager approval."""
ANSWER_FORMAT = """Reply with JSON only, in this exact shape:
{"total_cases": <int, cases delivered to the listed stores by 07:00>,
 "shipments": [{"from_dc": "<DC name>", "cases": <int>}],
 "fully_covered": <true if total_cases covers the whole gap>,
 "needs_approval": <true if the plan needs regional approval>,
 "explanation": "<one sentence>"}"""


def make_tables(spike: dict, i: int, rng: random.Random) -> tuple[dict, dict, dict]:
    name = PRODUCTS[i]
    upc, decoy_upc = f"0 41{i:02d}0 {rng.randint(10000, 99999)} 4", f"0 41{i:02d}0 {rng.randint(10000, 99999)} 7"
    mat, decoy_mat = f"MAT-{1000 + i * 7}", f"MAT-{1000 + i * 7 + 1}"
    pack = rng.choice([6, 12, 24])
    product_master = [{"item_desc": name, "upc": upc, "material": mat, "case_pack": pack},
                      {"item_desc": name + DECOY_SUFFIX, "upc": decoy_upc, "material": decoy_mat, "case_pack": 1}]
    stores = sorted(spike["history"])
    spike_date = spike["dates"][-1]

    pos, store_inv = [], []
    gap_by_store = {}
    for s in stores:
        hist = spike["history"][s]
        base = max(1.0, sum(hist[:-1]) / (len(hist) - 1))
        for d, q in zip(spike["dates"], hist):
            pos.append({"date": d, "store": s, "upc": upc, "qty_cases": q})
            pos.append({"date": d, "store": s, "upc": decoy_upc, "qty_cases": max(0, round(q * 0.4 + rng.randint(-2, 3)))})
        on_hand = round(base * rng.uniform(0.2, 0.8))
        store_inv.append({"store": s, "material": mat, "on_hand_cases": on_hand, "as_of": "17:45"})
        store_inv.append({"store": s, "material": decoy_mat, "on_hand_cases": rng.randint(5, 60), "as_of": "17:45"})
        if s in spike["spiked_stores"]:
            gap_by_store[s] = max(0, math.ceil(hist[-1] * 1.5) - on_hand)
    rng.shuffle(pos)
    G = sum(gap_by_store.values())

    # synthetic supply side, with traps chosen deterministically by index
    p_dc, s_dc = DC_NAMES[spike["state"]]
    traps = {"secondary_in_eaches": i % 3 == 1, "primary_trucks_down": i in (5, 13),
             "secondary_truck_late": i % 4 == 2, "primary_capacity_binding": i % 3 == 0}
    free = {p_dc: round(G * rng.uniform(0.35, 1.2)), s_dc: round(G * rng.uniform(0.25, 0.9))}
    dc_inv, orders, trucks = [], [], []
    oid = 4500 + i * 10
    for dc, role in ((p_dc, "primary"), (s_dc, "secondary")):
        committed = []
        for _ in range(rng.randint(2, 3)):
            committed.append(max(1, round(G * rng.uniform(0.15, 0.5))))
        for q in committed:
            oid += 1
            orders.append({"order_id": f"SO-{oid}", "dc": dc, "material": mat, "qty_cases": q,
                           "customer": rng.choice(["Store replenishment", "Wholesale club", "Online fulfilment"]),
                           "status": "open"})
        for status in ("shipped", "cancelled"):
            oid += 1
            orders.append({"order_id": f"SO-{oid}", "dc": dc, "material": mat,
                           "qty_cases": max(1, round(G * rng.uniform(0.2, 0.6))), "customer": "Store replenishment",
                           "status": status})
        oid += 1
        orders.append({"order_id": f"SO-{oid}", "dc": dc, "material": decoy_mat,
                       "qty_cases": rng.randint(20, 120), "customer": "Online fulfilment", "status": "open"})
        live = free[dc] + sum(committed)
        stale = live + max(2, round(G * rng.uniform(0.2, 0.6)))
        in_ea = role == "secondary" and traps["secondary_in_eaches"]
        k, extra = (pack, rng.randint(0, pack - 1)) if in_ea else (1, 0)
        snaps = [{"dc": dc, "material": mat, "on_hand": stale * k + extra, "uom": "EA" if in_ea else "CS",
                  "snapshot_time": "06:00"},
                 {"dc": dc, "material": mat, "on_hand": live * k + extra, "uom": "EA" if in_ea else "CS",
                  "snapshot_time": f"17:{rng.randint(40, 58)}"},
                 {"dc": dc, "material": decoy_mat, "on_hand": rng.randint(200, 900), "uom": "CS",
                  "snapshot_time": "17:30"}]
        rng.shuffle(snaps)
        dc_inv += snaps

    cap_p = (round(free[p_dc] * rng.uniform(0.55, 0.85)) if traps["primary_capacity_binding"]
             else round(max(free[p_dc], G) * rng.uniform(1.05, 1.3)))
    cap_s = round(G * rng.uniform(0.6, 1.2)) + 1
    cost_p, cost_s = rng.randint(450, 950), rng.choice([rng.randint(1100, 1450), rng.randint(1600, 2600)])
    late = traps["secondary_truck_late"]
    trucks = [
        {"truck_id": f"T{i:02d}-P1", "dc": p_dc, "status": "maintenance" if traps["primary_trucks_down"] else "available",
         "departs": rng.choice(["20:00", "20:30", "21:00", "21:30"]), "drive_hours": round(rng.uniform(1.5, 3.0), 1),
         "capacity_cases": cap_p, "run_cost_usd": cost_p},
        {"truck_id": f"T{i:02d}-P2", "dc": p_dc, "status": rng.choice(["maintenance", "assigned"]),
         "departs": "19:30", "drive_hours": 2.0, "capacity_cases": round(G * 1.5) + 10, "run_cost_usd": cost_p},
        {"truck_id": f"T{i:02d}-S1", "dc": s_dc, "status": "available", "departs": "22:30" if late else "21:00",
         "drive_hours": round(rng.uniform(9.0, 11.0), 1) if late else round(rng.uniform(4.0, 7.0), 1),
         "capacity_cases": cap_s, "run_cost_usd": cost_s},
        {"truck_id": f"T{i:02d}-S2", "dc": s_dc, "status": "assigned", "departs": "20:00", "drive_hours": 5.0,
         "capacity_cases": round(G * 1.5) + 10, "run_cost_usd": cost_s - 200},
    ]
    add_noise(i, rng, product_master, pos, store_inv, dc_inv, orders, stores, spike["dates"], (p_dc, s_dc), oid)
    rng.shuffle(pos)
    rng.shuffle(dc_inv)
    rng.shuffle(orders)
    tables = {"product_master": product_master, "dcs": [{"dc": p_dc, "role": "primary"}, {"dc": s_dc, "role": "secondary"}],
              "pos_daily_sales": pos, "store_inventory": store_inv, "dc_inventory": dc_inv,
              "order_book": orders, "trucks": trucks}
    question = {"product_name": name, "stores": spike["spiked_stores"], "spike_date": spike_date}

    def usable_capacity(dc: str) -> int:
        return sum(t["capacity_cases"] for t in trucks if t["dc"] == dc and t["status"] == "available"
                   and int(t["departs"][:2]) + int(t["departs"][3:]) / 60 + t["drive_hours"] <= DEADLINE_HOURS)

    ship_p = min(G, free[p_dc], usable_capacity(p_dc))
    ship_s = min(G - ship_p, free[s_dc], usable_capacity(s_dc))
    truth = {"total_gap": G, "total_cases": ship_p + ship_s, "shipments": {p_dc: ship_p, s_dc: ship_s},
             "fully_covered": ship_p + ship_s >= G, "needs_approval": (ship_p > 0 and cost_p > APPROVAL_LIMIT)
             or (ship_s > 0 and cost_s > APPROVAL_LIMIT),
             "limits": {dc: min(free[dc], usable_capacity(dc)) for dc in (p_dc, s_dc)},
             "traps": [k for k, v in traps.items() if v]}
    return tables, question, truth


def add_noise(i: int, rng: random.Random, master: list, pos: list, store_inv: list, dc_inv: list,
              orders: list, stores: list, dates: list, dcs: tuple, oid: int) -> None:
    """Rows for unrelated products, so the raw pull looks like a real category-level query."""
    others = [n for j, n in enumerate(PRODUCTS) if j != i][:NOISE_ITEMS]
    for k, name in enumerate(others):
        upc, mat = f"0 52{k:02d}0 {rng.randint(10000, 99999)} 1", f"MAT-{5000 + i * 20 + k}"
        master.append({"item_desc": name, "upc": upc, "material": mat, "case_pack": rng.choice([6, 12, 24])})
        level = rng.randint(3, 40)
        for s in stores:
            for d in dates:
                pos.append({"date": d, "store": s, "upc": upc, "qty_cases": max(0, level + rng.randint(-4, 6))})
            store_inv.append({"store": s, "material": mat, "on_hand_cases": rng.randint(10, 90), "as_of": "17:45"})
        for dc in dcs:
            dc_inv.append({"dc": dc, "material": mat, "on_hand": rng.randint(100, 1500), "uom": "CS",
                           "snapshot_time": f"17:{rng.randint(30, 58)}"})
            oid += 1
            orders.append({"order_id": f"SO-{oid}", "dc": dc, "material": mat, "qty_cases": rng.randint(20, 300),
                           "customer": "Store replenishment", "status": rng.choice(["open", "open", "shipped"])})


def csv_block(name: str, rows: list[dict]) -> str:
    cols = list(rows[0])
    body = "\n".join(",".join(str(r[c]) for c in cols) for r in rows)
    return f"## {name}\n{','.join(cols)}\n{body}"


def question_text(q: dict) -> str:
    return (f"Planner question (today is the day after {q['spike_date']}, time 18:00): Sales of "
            f"'{q['product_name']}' spiked yesterday at stores {', '.join(q['stores'])}. How many cases can we get "
            f"to those stores by 07:00 tomorrow, from which DCs, is the gap fully covered, and does the plan need "
            f"approval?")


def main() -> None:
    spikes = json.loads((OUT / "spikes.json").read_text())
    (OUT / "scenarios").mkdir(parents=True, exist_ok=True)
    (OUT / "prompts").mkdir(parents=True, exist_ok=True)
    for i, spike in enumerate(spikes):
        rng = random.Random(SEED + i)
        tables, question, truth = make_tables(spike, i, rng)
        packet, summary = build_packet(tables, question)
        # the engine must reproduce the generator's ground truth from raw tables alone
        assert summary["total_gap"] == truth["total_gap"], (i, summary, truth)
        for dc, lim in truth["limits"].items():
            assert min(summary["free"][dc], summary["capacity"][dc]) == lim, (i, dc, summary, truth)
        raw = "\n\n".join(csv_block(k, v) for k, v in tables.items())
        head = f"{SYSTEM}\n\n{POLICY}\n\n{question_text(question)}\n\n"
        sid = f"s{i + 1:02d}"
        (OUT / "prompts" / f"{sid}_raw.txt").write_text(f"{head}# Data: raw tables\n{raw}\n\n{ANSWER_FORMAT}\n")
        (OUT / "prompts" / f"{sid}_context.txt").write_text(f"{head}# Data: context packet\n{packet}\n\n{ANSWER_FORMAT}\n")
        (OUT / "scenarios" / f"{sid}.json").write_text(json.dumps(
            {"id": sid, "m5": {k: spike[k] for k in ("item", "state", "date", "state_ratio", "spiked_stores")},
             "question": question, "truth": truth}, indent=1))
        print(sid, spike["state"], f"gap={truth['total_gap']:4d} ship={truth['total_cases']:4d}",
              f"covered={truth['fully_covered']!s:5} approval={truth['needs_approval']!s:5}", truth["traps"])


if __name__ == "__main__":
    main()
