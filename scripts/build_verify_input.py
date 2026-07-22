"""Build verify_input.json (the AI-verification Workflow's input) from a gather
candidates.json. Each record: {idx, pair_key, supplier, name, code, cands:[{i,name,url}]}.

`pair_key` MUST be echoed back into ai_verdicts.json ({pair_key, idx, chosen_i,
reason}) by whatever produces the verdicts — the merge (candidates_io.join_verdicts)
joins on pair_key, never on idx/position (#43). idx stays only as a display/order
aid for the verify step itself. Keep pair_key when stripping to a "slim" (no-url)
input for the AI — it's an opaque key, harmless to show the model.

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
        "pair_key": r["pair_key"],
        "supplier": r["supplier"],
        "name": r["name"],
        "code": r.get("external_code") or "",
        "cands": [{"i": i, "name": c["name"], "url": c["url"]}
                  for i, c in enumerate(r["candidates"])],
    })
json.dump(out, open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"verify_input: {len(out)} products -> {dst}")
