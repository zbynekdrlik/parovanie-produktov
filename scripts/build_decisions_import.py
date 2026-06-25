"""Build the Shoptet import files from review decisions → data/out/.

TWO files (Shoptet wipes a present-but-empty cell, so columns are split — see
import_builder docstring):
  - import_links.csv   : code;pairCode;internalNote  (reorder URL in the private
                         "Interná poznámka" field; public textProperty* is NOT importable)
  - import_states.csv  : code;pairCode;productVisibility;stock;availabilityInStock;availabilityOutOfStock
                         (nie je skladom / už sa nebude predávať)

Encoding: UTF-8 (with BOM). Import each with scripts/shoptet_import.py.
See .claude/skills/shoptet for the full rules.
"""
import csv
import json

from parovanie.import_builder import (
    LINK_HEADER,
    STATE_HEADER,
    link_rows,
    state_rows,
)

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


def _write(name, header, rows):
    with open(f"{OUT}/{name}", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
        w.writerow(header)
        w.writerows(rows)
    print(f"{name}: {len(rows)} variant rows")


_write("import_links.csv", LINK_HEADER, link_rows(products, dec, code2pair))
_write("import_states.csv", STATE_HEADER, state_rows(products, dec, code2pair))
