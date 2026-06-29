"""LUKO gather — DETERMINISTIC, no AI verify step.

LUKO's 6-digit article code sits inside every forestshop LUKO product name, and
luko.cz (Shoptet) search by that exact code returns the product. So instead of the
top-K + AI-verify pipeline the other suppliers use, this script searches LUKO by the
exact code and picks the result whose NAME carries that code (luko.choose_exact) —
an exact join, the safest possible for auto-ordering (a wrong link is worse than none).

Writes the SAME two files the live-app merge step consumes, so LUKO plugs straight
into scripts/add_supplier_review_data.py:
  <out>/candidates.json   — candidates_io.record shape (one rec per product)
  <out>/ai_verdicts.json  — [{idx, chosen_i, reason}] (chosen_i = exact match or -1)

Usage:
  PYTHONPATH=src .venv/bin/python scripts/gather_luko.py data/out_luko
"""
import json
import os
import sys

from parovanie import candidates_io
from parovanie.client import SearchClient
from parovanie.csv_loader import load_rows
from parovanie.grouping import group_products
from parovanie.suppliers.luko import choose_exact, extract_code

SRC = "data/products.csv"
out_dir = sys.argv[1] if len(sys.argv) > 1 else "data/out_luko"
os.makedirs(out_dir, exist_ok=True)

client = SearchClient(throttle=0.6)
products = group_products(load_rows(SRC, {"LUKO"}))
print(f"LUKO products: {len(products)}")

records: list[dict] = []
verdicts: list[dict] = []
for i, p in enumerate(products):
    code = extract_code(p.name)
    cands = []
    if code:
        try:
            cands = client.search("LUKO", code)
        except Exception as e:  # noqa: BLE001 — degrade per-item, never abort the batch
            print(f"  [{i}] {p.name[:50]!r} code={code} SEARCH-ERR {e}")
    ci = choose_exact(code, cands)
    if not code:
        reason = "V názve nie je jednoznačný 6-miestny kód LUKO."
    elif ci != -1:
        reason = f"Presná zhoda kódu {code} v názve produktu LUKO."
    elif not cands:
        reason = f"Kód {code} sa na LUKO nenašiel (vypredané/stiahnuté)."
    else:
        reason = f"Kód {code} má na LUKO {len(cands)} produktov — nejednoznačné, bez odkazu."
    records.append(candidates_io.record(p, [code] if code else [], cands))
    verdicts.append({"idx": i, "chosen_i": ci, "reason": reason})
    flag = "OK " if ci != -1 else ">> "
    print(f"{flag}[{i}] code={code} -> chosen_i={ci} ({len(cands)} cand) | {p.name[:55]}")

candidates_io.write_candidates(records, os.path.join(out_dir, "candidates.json"))
with open(os.path.join(out_dir, "ai_verdicts.json"), "w", encoding="utf-8") as f:
    json.dump(verdicts, f, ensure_ascii=False, indent=2)

matched = sum(1 for v in verdicts if v["chosen_i"] != -1)
print(f"\nLUKO gather done: {matched}/{len(products)} matched -> {out_dir}/")
