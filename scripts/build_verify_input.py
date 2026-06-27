"""Build verify_input.json (the AI-verification Workflow's input) from a gather
candidates.json. Each record: {idx, supplier, name, code, cands:[{i,name,url}]}.

Usage: PYTHONPATH=src .venv/bin/python scripts/build_verify_input.py \
           data/out_odimon/candidates.json data/out_odimon/verify_input.json
"""
import json
import sys

src, dst = sys.argv[1], sys.argv[2]
recs = json.load(open(src, encoding="utf-8"))
out = []
for idx, r in enumerate(recs):
    out.append({
        "idx": idx,
        "supplier": r["supplier"],
        "name": r["name"],
        "code": r.get("external_code") or "",
        "cands": [{"i": i, "name": c["name"], "url": c["url"]}
                  for i, c in enumerate(r["candidates"])],
    })
json.dump(out, open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"verify_input: {len(out)} products -> {dst}")
