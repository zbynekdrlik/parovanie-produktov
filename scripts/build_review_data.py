"""Build webreview/review_data.json from the export (images) + candidates + AI verdicts.

Reads: data/products.csv (cp1250, for our images + pairCode),
       data/out/candidates.json, data/out/ai_verdicts.json
Writes: data/out/review_data.json  (consumed by webreview/app.py)
"""
import csv
import json
import re

import requests

from parovanie import candidates_io
from parovanie.export_helpers import current_of, row_images
from parovanie.url_resolver import assign_urls

csv.field_size_limit(10**9)
SRC = "data/products.csv"
OUT = "data/out"
BASE = "https://www.forestshop.sk/"


# Forestshop product URLs come from the public sitemap (the export has none).
# Matching is name + image-filename based — see parovanie.url_resolver. Products
# with no confident match fall back (in the UI) to the working ?string= search.
_smr = requests.get(BASE + "sitemap.xml", timeout=30,
                    headers={"User-Agent": "Mozilla/5.0"})
_smr.raise_for_status()   # fail loud on 4xx/5xx instead of writing all-null output
_locs = re.findall(r"<loc>https://www\.forestshop\.sk/([^<]+?)/?</loc>", _smr.text)
if not _locs:
    raise SystemExit("sitemap prázdny/nečitateľný — odmietam zapísať all-null review_data.json")

code2img, code2pair, code2cur = {}, {}, {}
with open(SRC, encoding="cp1250", errors="replace") as f:
    for row in csv.DictReader(f, delimiter=";"):
        c = (row.get("code") or "").strip()
        if not c:
            continue
        code2pair[c] = (row.get("pairCode") or "").strip()
        code2cur[c] = ((row.get("productVisibility") or "").strip(),
                       (row.get("availabilityInStock") or "").strip(),
                       (row.get("availabilityOutOfStock") or "").strip(),
                       (row.get("price") or "").strip(),
                       (row.get("standardPrice") or "").strip(),
                       (row.get("stock") or "").strip())
        code2img[c] = row_images(row)

recs = json.load(open(f"{OUT}/candidates.json", encoding="utf-8"))
verdicts = json.load(open(f"{OUT}/ai_verdicts.json", encoding="utf-8"))
# keyed by pair_key, never by array position — see candidates_io.join_verdicts (#43)
verds_by_rec = candidates_io.join_verdicts(recs, verdicts)

out = []
for i, r in enumerate(recs):
    vcodes = r["variant_codes"]
    our_imgs = []
    for c in vcodes:
        for u in code2img.get(c, []):
            if u not in our_imgs:
                our_imgs.append(u)
        if our_imgs:
            break
    v = verds_by_rec[i]
    ci = v["chosen_i"] if v else -1
    cands = r["candidates"]
    matched = isinstance(ci, int) and 0 <= ci < len(cands)
    _vis, _ais, _aos, _price, _std, _stock = (
        code2cur.get(vcodes[0], ("?", "", "", "", "", "")) if vcodes
        else ("?", "", "", "", "", ""))
    # 3 states (1 Skladom / 2 Nie je skladom / 3 Už sa nebude predávať); detailOnly
    # (drop-ship, sellable via link) is NOT off. resync_export.py later refreshes
    # this from the live catalog. current_of carries OUR price/std/stock for the card.
    out.append({
        "idx": i, "key": r["pair_key"], "supplier": r["supplier"], "name": r["name"],
        "pairCode": code2pair.get(vcodes[0], "") if vcodes else "",
        "variant_codes": vcodes,
        "our_images": our_imgs[:6],
        "our_url": None,  # filled below by the image-aware, dedup-safe resolver
        "current": current_of(_vis, _ais, _aos, _price, _std, _stock),
        "ai_status": "matched" if matched else "unmatched",
        "ai_chosen_url": cands[ci]["url"] if matched else "",
        "ai_reason": v["reason"] if v else "",
        "candidates": cands,
    })

urls = assign_urls(out, _locs)
for i, rec in enumerate(out):
    rec["our_url"] = urls[i]
linked = sum(1 for u in urls.values() if u)
json.dump(out, open(f"{OUT}/review_data.json", "w", encoding="utf-8"), ensure_ascii=False)
print(f"review_data.json: {len(out)} products, {linked} with a forestshop URL")
