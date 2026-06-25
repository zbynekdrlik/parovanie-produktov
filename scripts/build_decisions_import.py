"""Build Shoptet import files from the user's review decisions (decisions.json).

Two outputs (cp1250, ;, CRLF), one row per variant:
  data/out/import_links.csv        — code;textProperty10  (good = AI url, manual = user url)
  data/out/import_unavailable.csv  — code;stock;availabilityInStock;availabilityOutOfStock
                                     (unavailable → sold-out convention: visible stays,
                                      stock 0, both availability = "Vypredané")
"""
import csv
import json

OUT = "data/out"
rd = {p["key"]: p for p in json.load(open(f"{OUT}/review_data.json", encoding="utf-8"))}
dec = json.load(open(f"{OUT}/decisions.json", encoding="utf-8"))


def _w(path, header):
    f = open(path, "w", encoding="cp1250", errors="replace", newline="")
    w = csv.writer(f, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    w.writerow(header)
    return f, w


# textProperty11 = "human matched" marks the product as human-verified, so the
# reorder automation works with it and future pairing skips it.
lf, lw = _w(f"{OUT}/import_links.csv", ["code", "textProperty10", "textProperty11"])
uf, uw = _w(f"{OUT}/import_unavailable.csv",
            ["code", "stock", "availabilityInStock", "availabilityOutOfStock"])
n_links = n_unavail = 0
for key, d in dec.items():
    p = rd.get(key)
    if not p:
        continue
    status, url = d.get("status"), d.get("url", "")
    if status in ("good", "manual") and url:
        for c in p["variant_codes"]:
            lw.writerow([c, url, "human matched"])
        n_links += 1
    elif status == "unavailable":
        for c in p["variant_codes"]:
            uw.writerow([c, "0", "Vypredané", "Vypredané"])
        n_unavail += 1
lf.close()
uf.close()
print(f"import_links.csv: {n_links} products (good+manual urls)")
print(f"import_unavailable.csv: {n_unavail} products -> sold-out (visible stays, stock 0, Vypredané)")
