"""Backfill OUR product's price/std/stock into review items that have none.

Newer suppliers were appended via add_supplier_review_data, which used to build the
`current` snapshot without price/std/stock — so the review card showed no price for
OUR product (only the supplier candidate's price showed). This fills them in from the
current forestshop export, keyed by each item's variant_codes, without touching
codes / images / decisions / eshop-state. Idempotent.

Run after refreshing data/products.csv:
  PYTHONPATH=src .venv/bin/python scripts/backfill_current_price.py
"""
import csv
import json
import os

from parovanie.export_helpers import fill_missing_prices

csv.field_size_limit(10**9)
SRC = "data/products.csv"
REVIEW = "data/out/review_data.json"

# code -> (price, standardPrice, stock) from the live export (cp1250)
code2price: dict[str, tuple[str, str, str]] = {}
with open(SRC, encoding="cp1250", errors="replace") as f:
    for row in csv.DictReader(f, delimiter=";"):
        c = (row.get("code") or "").strip()
        if c:
            code2price[c] = ((row.get("price") or "").strip(),
                             (row.get("standardPrice") or "").strip(),
                             (row.get("stock") or "").strip())

items = json.load(open(REVIEW, encoding="utf-8"))
missing_before = sum(1 for p in items if not str((p.get("current") or {}).get("price") or "").strip())
filled = fill_missing_prices(items, code2price)
still_missing = sum(1 for p in items if not str((p.get("current") or {}).get("price") or "").strip())

tmp = REVIEW + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(items, f, ensure_ascii=False)
os.replace(tmp, REVIEW)
print(f"backfilled price for {filled}/{missing_before} price-less items; "
      f"{still_missing} still without price (no priced variant code in export)")
