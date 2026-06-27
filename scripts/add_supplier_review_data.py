"""Append a NEW supplier's products to the live data/out/review_data.json, without
disturbing the existing products or the workers' decisions (decisions.json is keyed
by stable supplier|pairCode). Mirrors build_review_data's per-product record, but
scoped to one supplier's gather + verify outputs and appended in place.

Usage:
  PYTHONPATH=src .venv/bin/python scripts/add_supplier_review_data.py data/out_odimon

Reads <dir>/candidates.json + <dir>/ai_verdicts.json + data/products.csv (images,
pairCode, state). Idempotent: re-running replaces that supplier's review rows
(matched by key) rather than duplicating them.
"""
import csv
import json
import re
import sys

import requests

from parovanie.export_helpers import row_images, state_of
from parovanie.url_resolver import assign_urls

csv.field_size_limit(10**9)
SRC = "data/products.csv"
OUT = "data/out"
REVIEW = f"{OUT}/review_data.json"
BASE = "https://www.forestshop.sk"

sup_dir = sys.argv[1]
recs = json.load(open(f"{sup_dir}/candidates.json", encoding="utf-8"))
verds = {v["idx"]: v for v in json.load(open(f"{sup_dir}/ai_verdicts.json", encoding="utf-8"))}
supplier = recs[0]["supplier"] if recs else "?"

# product-side fields from the current export
code2img, code2pair, code2cur = {}, {}, {}
with open(SRC, encoding="cp1250", errors="replace") as f:
    for row in csv.DictReader(f, delimiter=";"):
        c = (row.get("code") or "").strip()
        if not c:
            continue
        code2pair[c] = (row.get("pairCode") or "").strip()
        code2cur[c] = ((row.get("productVisibility") or "").strip(),
                       (row.get("availabilityInStock") or "").strip(),
                       (row.get("availabilityOutOfStock") or "").strip())
        code2img[c] = row_images(row)

new = []
for i, r in enumerate(recs):
    vcodes = r["variant_codes"]
    our_imgs = []
    for c in vcodes:
        for u in code2img.get(c, []):
            if u not in our_imgs:
                our_imgs.append(u)
        if our_imgs:
            break
    v = verds.get(i)
    ci = v["chosen_i"] if v else -1
    cands = r["candidates"]
    matched = isinstance(ci, int) and 0 <= ci < len(cands)
    _vis, _ais, _aos = code2cur.get(vcodes[0], ("?", "", "")) if vcodes else ("?", "", "")
    _a = _ais or _aos
    _state = state_of(_vis, _a)
    new.append({
        "key": r["pair_key"], "supplier": r["supplier"], "name": r["name"],
        "pairCode": code2pair.get(vcodes[0], "") if vcodes else "",
        "variant_codes": vcodes,
        "our_images": our_imgs[:6],
        "our_url": None,
        "current": {"state": _state, "off": _state != 1, "vis": _vis, "avail": _a},
        "ai_status": "matched" if matched else "unmatched",
        "ai_chosen_url": cands[ci]["url"] if matched else "",
        "ai_reason": v["reason"] if v else "",
        "candidates": cands,
    })

# resolve the forestshop product URL for the new products (sitemap-based)
smr = requests.get(BASE + "/sitemap.xml", timeout=30, headers={"User-Agent": "Mozilla/5.0"})
smr.raise_for_status()
locs = re.findall(r"<loc>https://www\.forestshop\.sk/([^<]+?)/?</loc>", smr.text)
if not locs:
    raise SystemExit("sitemap prázdny — odmietam pokračovať")
urls = assign_urls(new, locs)
for i, rec in enumerate(new):
    rec["our_url"] = urls[i]

# append into the live review_data, replacing any prior rows for this supplier's keys
live = json.load(open(REVIEW, encoding="utf-8"))
new_keys = {rec["key"] for rec in new}
kept = [p for p in live if p.get("key") not in new_keys]
merged = kept + new
for i, rec in enumerate(merged):       # reindex idx (decisions key by `key`, not idx)
    rec["idx"] = i
json.dump(merged, open(REVIEW, "w", encoding="utf-8"), ensure_ascii=False)
linked = sum(1 for u in urls.values() if u)
print(f"{supplier}: +{len(new)} products (kept {len(kept)} existing) -> review_data.json now {len(merged)}; "
      f"{sum(1 for r in new if r['ai_status']=='matched')} AI-matched, {linked} with forestshop URL")
