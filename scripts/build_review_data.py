"""Build webreview/review_data.json from the export (images) + candidates + AI verdicts.

Reads: data/products.csv (cp1250, for our images + pairCode),
       data/out/candidates.json, data/out/ai_verdicts.json
Writes: data/out/review_data.json  (consumed by webreview/app.py)
"""
import csv
import json

csv.field_size_limit(10**9)
SRC = "data/products.csv"
OUT = "data/out"

imgcols = ["defaultImage"] + [f"image{i}" for i in range(2, 16)] + ["image"]
code2img, code2pair = {}, {}
with open(SRC, encoding="cp1250", errors="replace") as f:
    for row in csv.DictReader(f, delimiter=";"):
        c = (row.get("code") or "").strip()
        if not c:
            continue
        code2pair[c] = (row.get("pairCode") or "").strip()
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
    out.append({
        "idx": i, "supplier": r["supplier"], "name": r["name"],
        "pairCode": code2pair.get(vcodes[0], "") if vcodes else "",
        "variant_codes": vcodes,
        "our_images": our_imgs[:6],
        "ai_status": "matched" if matched else "unmatched",
        "ai_chosen_url": cands[ci]["url"] if matched else "",
        "ai_reason": v["reason"] if v else "",
        "candidates": cands,
    })
json.dump(out, open(f"{OUT}/review_data.json", "w", encoding="utf-8"), ensure_ascii=False)
print(f"review_data.json: {len(out)} products")
