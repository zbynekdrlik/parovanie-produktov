"""Build the ONE Shoptet import file from review decisions → data/out/import_forestshop.csv

Columns: code;pairCode;textProperty10;textProperty11;stock;availabilityInStock;availabilityOutOfStock
Encoding: UTF-8 (with BOM). Import into Shoptet as UTF-8, with "replace empty
values" OFF. See .claude/skills/shoptet for the full rules.
"""
import csv
import json

from parovanie.import_builder import import_rows, HEADER

csv.field_size_limit(10**9)
OUT = "data/out"
SRC = "data/products.csv"

products = json.load(open(f"{OUT}/review_data.json", encoding="utf-8"))
dec = json.load(open(f"{OUT}/decisions.json", encoding="utf-8"))
code2pair = {}
with open(SRC, encoding="cp1250", errors="replace") as f:
    for row in csv.DictReader(f, delimiter=";"):
        c = (row.get("code") or "").strip()
        if c:
            code2pair[c] = (row.get("pairCode") or "").strip()

rows = import_rows(products, dec, code2pair)
with open(f"{OUT}/import_forestshop.csv", "w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    w.writerow(HEADER)
    w.writerows(rows)
n_link = sum(1 for r in rows if r[2])
print(f"import_forestshop.csv: {len(rows)} variant rows ({n_link} link, {len(rows)-n_link} unavailable)")
