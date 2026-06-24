"""Live smoke: match a small slice of BETALOV/WETLAND products against the real
supplier sites and print results. NOT a test — a manual live verification.

Usage: .venv/bin/python scripts/smoke.py [N_per_supplier]
"""
import sys
import logging
from parovanie.csv_loader import load_rows
from parovanie.grouping import group_products
from parovanie.matcher import match_one
from parovanie.client import SearchClient

logging.basicConfig(level=logging.WARNING)

N = int(sys.argv[1]) if len(sys.argv) > 1 else 10
rows = load_rows("data/products.csv", {"BETALOV", "WETLAND"})
products = group_products(rows)

# take first N of each supplier
picked = {"BETALOV": [], "WETLAND": []}
for p in products:
    if len(picked[p.supplier]) < N:
        picked[p.supplier].append(p)
    if all(len(v) >= N for v in picked.values()):
        break

client = SearchClient(throttle=0.4)
stats = {"BETALOV": {"hit": 0, "high": 0, "tot": 0}, "WETLAND": {"hit": 0, "high": 0, "tot": 0}}

for sup in ("BETALOV", "WETLAND"):
    print(f"\n{'='*100}\n{sup}\n{'='*100}")
    for p in picked[sup]:
        m = match_one(p, client)
        s = stats[sup]
        s["tot"] += 1
        if m.chosen:
            s["hit"] += 1
        if m.confidence == "high":
            s["high"] += 1
        url = m.chosen.url if m.chosen else "—— NO MATCH ——"
        print(f"  code={p.external_code or '(none)':10} conf={m.confidence:6} cands={m.candidate_count:2} "
              f"q={m.query[:32]:32} -> {url}")
        print(f"      name: {p.name[:80]}")

print(f"\n{'='*100}\nSUMMARY")
for sup, s in stats.items():
    print(f"  {sup}: matched {s['hit']}/{s['tot']}  (high-confidence {s['high']}/{s['tot']})")
