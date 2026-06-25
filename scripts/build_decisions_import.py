"""Build ONE Shoptet import file from the user's review decisions (decisions.json).

data/out/import_forestshop.csv (cp1250, ;, CRLF), one row per variant:
  code;textProperty10;textProperty11;stock;availabilityInStock;availabilityOutOfStock
  - link (good/manual): textProperty10=url, textProperty11='human matched',
    stock/availability preserved (current values → no change; their automation
    turns the product visible from the link).
  - unavailable: textProperty10='' (empty link until one is found),
    textProperty11='', stock=0, availability='Vypredané' (sold-out). These stay
    in the app pool to re-check later — NOT marked human matched.
"""
import csv
import json

csv.field_size_limit(10**9)
OUT = "data/out"
SRC = "data/products.csv"

rd = {p["key"]: p for p in json.load(open(f"{OUT}/review_data.json", encoding="utf-8"))}
dec = json.load(open(f"{OUT}/decisions.json", encoding="utf-8"))

# current stock + availability per variant (to preserve them for link products)
code2stock = {}
with open(SRC, encoding="cp1250", errors="replace") as f:
    for row in csv.DictReader(f, delimiter=";"):
        c = (row.get("code") or "").strip()
        if c:
            code2stock[c] = ((row.get("stock") or "").strip(),
                             (row.get("availabilityInStock") or "").strip(),
                             (row.get("availabilityOutOfStock") or "").strip())

header = ["code", "textProperty10", "textProperty11",
          "stock", "availabilityInStock", "availabilityOutOfStock"]
n_links = n_unavail = 0
with open(f"{OUT}/import_forestshop.csv", "w", encoding="cp1250", errors="replace", newline="") as f:
    w = csv.writer(f, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    w.writerow(header)
    for key, d in dec.items():
        p = rd.get(key)
        if not p:
            continue
        status, url = d.get("status"), d.get("url", "")
        for c in p["variant_codes"]:
            if status in ("good", "manual") and url:
                st, ais, aos = code2stock.get(c, ("", "", ""))
                w.writerow([c, url, "human matched", st, ais, aos])
            elif status == "unavailable":
                w.writerow([c, "", "", "0", "Vypredané", "Vypredané"])
        if status in ("good", "manual") and url:
            n_links += 1
        elif status == "unavailable":
            n_unavail += 1
print(f"import_forestshop.csv: {n_links} link products + {n_unavail} unavailable (sold-out)")
