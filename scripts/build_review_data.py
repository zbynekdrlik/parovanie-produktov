"""Build webreview/review_data.json from the export (images) + candidates + AI verdicts.

Reads: data/products.csv (cp1250, for our images + pairCode),
       data/out/candidates.json, data/out/ai_verdicts.json
Writes: data/out/review_data.json  (consumed by webreview/app.py)
"""
import csv
import json
import re
import unicodedata

import requests

csv.field_size_limit(10**9)
SRC = "data/products.csv"
OUT = "data/out"
BASE = "https://www.forestshop.sk/"


def _slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


# Build a resolver of forestshop product URLs from the public sitemap.
# Real product URLs sometimes carry a category prefix (e.g. "polovnicke-..."),
# so we match name-slug exactly, as a suffix, or by full token-subset; the rest
# fall back (in the UI) to the working ?string= search.
_smr = requests.get(BASE + "sitemap.xml", timeout=30,
                    headers={"User-Agent": "Mozilla/5.0"})
_smr.raise_for_status()   # fail loud on 4xx/5xx instead of writing all-null output
_sm = _smr.text
_locs = re.findall(r"<loc>https://www\.forestshop\.sk/([^<]+?)/?</loc>", _sm)
if not _locs:
    raise SystemExit("sitemap prázdny/nečitateľný — odmietam zapísať all-null review_data.json")
_slugset = set(_locs)
_slug_tokens = {s: set(s.split("-")) for s in _locs}


def resolve_url(name: str):
    sn = _slug(name)
    if not sn:
        return None
    if sn in _slugset:
        return BASE + sn + "/"
    suf = [s for s in _slugset if s.endswith("-" + sn)]
    if suf:
        return BASE + min(suf, key=len) + "/"
    nt = {t for t in sn.split("-") if t and not t.isdigit()}
    cands = [s for s, toks in _slug_tokens.items() if nt and nt <= toks]
    if cands:
        return BASE + min(cands, key=lambda s: len(_slug_tokens[s])) + "/"
    return None

imgcols = ["defaultImage"] + [f"image{i}" for i in range(2, 16)] + ["image"]
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
        imgs = []
        for col in imgcols:
            v = (row.get(col) or "").strip()
            if v.startswith("http") and v not in imgs:
                imgs.append(v)
        code2img[c] = imgs

recs = json.load(open(f"{OUT}/candidates.json", encoding="utf-8"))
verds = {v["idx"]: v for v in json.load(open(f"{OUT}/ai_verdicts.json", encoding="utf-8"))}

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
    v = verds.get(i)
    ci = v["chosen_i"] if v else -1
    cands = r["candidates"]
    matched = isinstance(ci, int) and 0 <= ci < len(cands)
    _vis, _ais, _aos = code2cur.get(vcodes[0], ("?", "", "")) if vcodes else ("?", "", "")
    _a = _ais or _aos
    # off = NOT sellable: hidden/blocked OR sold-out/discontinued availability.
    # detailOnly (drop-ship, sellable via link) is NOT off. Run resync_export.py
    # afterwards to refresh codes/images/status against the live catalog.
    _vl, _al = _vis.lower(), _a.lower()
    # 3 states: 1 Skladom, 2 Nie je skladom (vypredané), 3 Už sa nebude predávať
    # (Predaj skončil/hidden/blocked). resync_export.py refreshes this from the live catalog.
    if "skon" in _al or _vl in ("hidden", "blocked", "cashdeskonly", "blockunregistered"):
        _state = 3
    elif any(x in _al for x in ("vypredan", "nedostupn", "není skladem")):
        _state = 2
    else:
        _state = 1
    _off = _state != 1
    out.append({
        "idx": i, "key": r["pair_key"], "supplier": r["supplier"], "name": r["name"],
        "pairCode": code2pair.get(vcodes[0], "") if vcodes else "",
        "variant_codes": vcodes,
        "our_images": our_imgs[:6],
        "our_url": resolve_url(r["name"]),
        "current": {"state": _state, "off": _off, "vis": _vis, "avail": _a},
        "ai_status": "matched" if matched else "unmatched",
        "ai_chosen_url": cands[ci]["url"] if matched else "",
        "ai_reason": v["reason"] if v else "",
        "candidates": cands,
    })
json.dump(out, open(f"{OUT}/review_data.json", "w", encoding="utf-8"), ensure_ascii=False)
print(f"review_data.json: {len(out)} products")
