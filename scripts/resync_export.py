"""Re-sync each review product's export-side fields (variant codes, images,
current on/off status) against the CURRENT forestshop export, joined by
(supplier, name). The catalog drifts (products get re-coded / re-stocked) so the
gather-time codes go stale; this keeps the review app + the eventual import in
sync with the live eshop. Preserves candidates / AI verdict / our_url / key.

Run after refreshing data/products.csv. Idempotent.
"""
import csv
import json
from collections import defaultdict

from parovanie.export_helpers import row_images, state_of

csv.field_size_limit(10**9)
SRC = "data/products.csv"
OUT = "data/out"


# index current export by (supplier, name)
idx = defaultdict(lambda: {"codes": [], "images": [], "vis": "", "ais": "", "aos": "",
                           "price": "", "std": "", "stock": ""})
with open(SRC, encoding="cp1250", errors="replace") as f:
    for row in csv.DictReader(f, delimiter=";"):
        sup = (row.get("supplier") or "").strip().upper()
        name = (row.get("name") or "").strip()
        code = (row.get("code") or "").strip()
        if not name or sup not in ("BETALOV", "WETLAND"):
            continue
        g = idx[(sup, name)]
        if code:
            g["codes"].append(code)
        if not g["images"]:
            g["images"] = row_images(row)
        if not g["vis"]:
            g["vis"] = (row.get("productVisibility") or "").strip()
            g["ais"] = (row.get("availabilityInStock") or "").strip()
            g["aos"] = (row.get("availabilityOutOfStock") or "").strip()
        if not g["price"]:
            g["price"] = (row.get("price") or "").strip()
            g["std"] = (row.get("standardPrice") or "").strip()
            g["stock"] = (row.get("stock") or "").strip()

rd = json.load(open(f"{OUT}/review_data.json", encoding="utf-8"))
synced = stale = 0
for p in rd:
    g = idx.get((p["supplier"], p["name"]))
    if g and g["codes"]:
        p["variant_codes"] = g["codes"]
        p["our_images"] = g["images"][:6]
        a = g["ais"] or g["aos"]
        st = state_of(g["vis"], a)
        p["current"] = {"state": st, "off": st != 1, "vis": g["vis"], "avail": a,
                        "price": g["price"], "std": g["std"], "stock": g["stock"]}
        synced += 1
    else:
        # not found by name in current export (renamed/removed) — flag, keep old
        p.setdefault("current", {})["vis"] = p.get("current", {}).get("vis", "?")
        p["current"]["stale"] = True
        stale += 1
json.dump(rd, open(f"{OUT}/review_data.json", "w", encoding="utf-8"), ensure_ascii=False)
off = sum(1 for p in rd if p.get("current", {}).get("off"))
print(f"synced {synced}, not-found-by-name {stale}; currently OFF (sold-out/hidden) {off}/{len(rd)}")
