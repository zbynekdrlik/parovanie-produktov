"""One-time: add stable `key` (supplier|pairCode) to review_data.json and migrate
existing decisions.json from idx-keys to stable keys, so review-work survives any
data rebuild/redeploy. Backs up the old decisions file. Idempotent."""
import json
import os

OUT = "data/out"
cands = json.load(open(f"{OUT}/candidates.json", encoding="utf-8"))
idx2key = {i: r["pair_key"] for i, r in enumerate(cands)}

# add key to review_data (preserves our_url already resolved)
rd = json.load(open(f"{OUT}/review_data.json", encoding="utf-8"))
for p in rd:
    p["key"] = idx2key.get(p["idx"], p.get("key"))
json.dump(rd, open(f"{OUT}/review_data.json", "w", encoding="utf-8"), ensure_ascii=False)

# migrate decisions: idx-key -> stable key (only if old idx-style keys present)
dpath = f"{OUT}/decisions.json"
if os.path.exists(dpath):
    dec = json.load(open(dpath, encoding="utf-8"))
    migrated, kept = {}, 0
    for k, v in dec.items():
        if k.isdigit() and int(k) in idx2key:        # old idx key
            migrated[idx2key[int(k)]] = v
        else:                                          # already a stable key
            migrated[k] = v
            kept += 1
    if migrated != dec:
        json.dump(dec, open(dpath + ".bak", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        json.dump(migrated, open(dpath, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"decisions: {len(dec)} -> {len(migrated)} (kept {kept} already-stable), backup .bak written")
else:
    print("no decisions.json yet")
print(f"review_data: {len(rd)} products keyed; direct URLs {sum(1 for p in rd if p.get('our_url'))}")
