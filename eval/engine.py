"""Context engine: turns raw operational tables into a short, cited context packet.

Steps match the prototype: resolve, gather, verify, constrain, package.
It works only from the raw tables, never from the scenario generator's parameters,
so build_scenarios.py can check its output against the ground truth.
"""
from __future__ import annotations

import math

DEADLINE_HOURS = 31.0  # 07:00 tomorrow, in hours from 00:00 today
APPROVAL_LIMIT = 1500


def hhmm_to_hours(s: str) -> float:
    h, m = s.split(":")
    return int(h) + int(m) / 60


def hours_to_label(h: float) -> str:
    day = "tomorrow " if h >= 24 else ""
    h = h % 24
    return f"{day}{int(h):02d}:{int(round((h % 1) * 60)):02d}"


def resolve(tables: dict, product_name: str) -> dict:
    matches = [p for p in tables["product_master"] if p["item_desc"] == product_name]
    if len(matches) != 1:
        raise ValueError(f"expected one product named {product_name!r}, got {len(matches)}")
    return matches[0]


def latest(rows: list[dict], time_key: str) -> dict:
    return max(rows, key=lambda r: hhmm_to_hours(r[time_key]))


def store_gaps(tables: dict, product: dict, stores: list[str], spike_date: str) -> list[dict]:
    out = []
    for store in stores:
        sold = next(r for r in tables["pos_daily_sales"]
                    if r["upc"] == product["upc"] and r["store"] == store and r["date"] == spike_date)
        inv = latest([r for r in tables["store_inventory"]
                      if r["store"] == store and r["material"] == product["material"]], "as_of")
        need = math.ceil(sold["qty_cases"] * 1.5)
        out.append({"store": store, "sold": sold["qty_cases"], "need": need, "on_hand": inv["on_hand_cases"],
                    "gap": max(0, need - inv["on_hand_cases"]), "as_of": inv["as_of"]})
    return out


def dc_supply(tables: dict, product: dict) -> list[dict]:
    out = []
    for dc in tables["dcs"]:
        rows = [r for r in tables["dc_inventory"] if r["dc"] == dc["dc"] and r["material"] == product["material"]]
        live = latest(rows, "snapshot_time")
        stale = [r for r in rows if r is not live]
        on_hand = live["on_hand"] // product["case_pack"] if live["uom"] == "EA" else live["on_hand"]
        orders = [o for o in tables["order_book"] if o["dc"] == dc["dc"] and o["material"] == product["material"]]
        open_orders = [o for o in orders if o["status"] == "open"]
        committed = sum(o["qty_cases"] for o in open_orders)
        trucks = []
        for t in (t for t in tables["trucks"] if t["dc"] == dc["dc"]):
            eta = hhmm_to_hours(t["departs"]) + t["drive_hours"]
            ok = t["status"] == "available" and eta <= DEADLINE_HOURS
            reason = ("ok" if ok else t["status"] if t["status"] != "available" else "arrives after 07:00")
            trucks.append({**t, "eta": eta, "usable": ok, "reason": reason})
        out.append({"dc": dc["dc"], "role": dc["role"], "on_hand": on_hand, "uom_converted": live["uom"] == "EA",
                    "snapshot": live["snapshot_time"], "ignored_stale": [r["snapshot_time"] for r in stale],
                    "open_orders": open_orders, "ignored_orders": [o for o in orders if o["status"] != "open"],
                    "committed": committed, "free": max(0, on_hand - committed), "trucks": trucks})
    return out


def build_packet(tables: dict, question: dict) -> tuple[str, dict]:
    product = resolve(tables, question["product_name"])
    gaps = store_gaps(tables, product, question["stores"], question["spike_date"])
    supply = dc_supply(tables, product)
    total_gap = sum(g["gap"] for g in gaps)

    lines = [f"question: replenish {product['item_desc']} at {', '.join(question['stores'])} by tomorrow 07:00",
             f"product: material {product['material']} | upc {product['upc']} | case pack {product['case_pack']}"
             f"   [product master]", "demand (need = yesterday's sales x1.5, rounded up):"]
    for g in gaps:
        lines.append(f"  {g['store']}: sold {g['sold']} yesterday, need {g['need']}, on hand {g['on_hand']}, "
                     f"gap {g['gap']}   [POS {question['spike_date']}; store stock @{g['as_of']}]")
    lines.append(f"  total gap: {total_gap} cases")
    lines.append("supply (free = latest on-hand minus open orders; shipped/cancelled orders ignored):")
    for d in supply:
        note = " (converted from eaches)" if d["uom_converted"] else ""
        stale = f"; ignored stale snapshot @{', '.join(d['ignored_stale'])}" if d["ignored_stale"] else ""
        lines.append(f"  {d['dc']} ({d['role']}): on hand {d['on_hand']}{note}, open orders {d['committed']}, "
                     f"FREE {d['free']}   [WMS @{d['snapshot']}{stale}]")
    lines.append("transport (must arrive by tomorrow 07:00):")
    for d in supply:
        for t in d["trucks"]:
            flag = "USABLE" if t["usable"] else f"NOT USABLE ({t['reason']})"
            lines.append(f"  {t['truck_id']} at {d['dc']}: {flag}, capacity {t['capacity_cases']}, "
                         f"cost ${t['run_cost_usd']}, eta {hours_to_label(t['eta'])}   [TMS]")
    lines.append(f"policy: use primary DC first; truck runs over ${APPROVAL_LIMIT} need regional approval   [policy v3]")
    summary = {"total_gap": total_gap,
               "free": {d["dc"]: d["free"] for d in supply},
               "capacity": {d["dc"]: sum(t["capacity_cases"] for t in d["trucks"] if t["usable"]) for d in supply},
               "cost": {d["dc"]: max([t["run_cost_usd"] for t in d["trucks"] if t["usable"]], default=0)
                        for d in supply}}
    return "\n".join(lines), summary
