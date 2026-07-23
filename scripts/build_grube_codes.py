"""Join grube.de per-size itemIds with forestshop variant sizes ->
data/out/grube_codes.json  {code: {itemId, size, deUrl, productId}}."""
import csv
import json
import os
import re
import sys

sys.path.insert(0, "src")
from parovanie.grube_de import to_grube_de, match_variant_codes, resolve_size, MULTI_AXIS, ONE_SIZE

_PID = re.compile(r"/p/x/(\d+)/")


def build_grube_codes(decisions, itemids, export_rows, review_by_key=None):
    """{forestshop code: {itemId, size, deUrl, productId}} joining grube.de per-size
    itemIds with forestshop variants. Two join paths:
      * MULTI-size: group GRUBE export rows by pairCode (populated for sized products).
      * SINGLE-size (#60 class 1): single-variant knives have an EMPTY pairCode, so they
        are absent from the pairCode grouping — they join instead via the review item's
        `variant_codes` (from review_by_key), which carry the forestshop code(s)."""
    review_by_key = review_by_key or {}
    # decision -> (productId, .de url), keyed both by pairCode-part and by full review key
    pair_pid, pair_de, key_pid, key_de = {}, {}, {}, {}
    for key, dec in decisions.items():
        if not key.startswith("GRUBE|"):
            continue
        de = to_grube_de(dec.get("url", ""))
        if not de:
            continue
        pid = _PID.search(de).group(1)
        pair = key.split("|", 1)[1]
        pair_pid[pair] = pid
        pair_de[pair] = de
        key_pid[key] = pid
        key_de[key] = de

    # group GRUBE export rows by pairCode + index by code (single-size join)
    by_pair: dict[str, list[dict]] = {}
    by_code: dict[str, dict] = {}
    for row in export_rows:
        if row.get("supplier") != "GRUBE":
            continue
        by_pair.setdefault(row.get("pairCode", ""), []).append(row)
        by_code[row.get("code", "")] = row

    out: dict[str, dict] = {}

    # --- MULTI-size path: join by export pairCode ---
    for pair, pid in pair_pid.items():
        gsizes = itemids.get(pid)
        rows = by_pair.get(pair)
        if not gsizes or not rows:
            continue
        matched = match_variant_codes(rows, gsizes)          # {code: itemId}
        size_by_code = {r["code"]: resolve_size(r) for r in rows}
        for code, iid in matched.items():
            size = size_by_code.get(code)
            out[code] = {"itemId": iid, "size": "" if size in (None, MULTI_AXIS) else size,
                         "deUrl": pair_de[pair], "productId": pid}

    # --- SINGLE-size path (#60 class 1): join by review_data variant_codes ---
    for key, pid in key_pid.items():
        gsizes = itemids.get(pid)
        if not gsizes or list(gsizes) != [ONE_SIZE]:         # single-size grube products only
            continue
        vcs = (review_by_key.get(key) or {}).get("variant_codes") or []
        rows = [by_code[c] for c in vcs if c in by_code]
        if not rows:
            continue
        matched = match_variant_codes(rows, gsizes)          # one-size row(s) -> itemId
        for code, iid in matched.items():
            if code in out:                                  # multi-size path already wrote it
                continue
            out[code] = {"itemId": iid, "size": "",
                         "deUrl": key_de[key], "productId": pid}

    # Shoptet matches imports by `code` alone: a code shared with a non-GRUBE
    # product would overwrite THAT product's externalCode. Fail loud, never write.
    nongrube = {r["code"] for r in export_rows if r.get("supplier") != "GRUBE"}
    collisions = sorted(c for c in out if c in nongrube)
    if collisions:
        raise ValueError(
            "GRUBE externalCode codes also exist on non-GRUBE products "
            "(Shoptet matches by code → would overwrite them): " + ", ".join(collisions[:10]))
    return out


def _load_export(path="data/products.csv"):
    csv.field_size_limit(10**7)
    with open(path, encoding="cp1250", newline="") as f:
        return list(csv.DictReader(f, delimiter=";"))


def _load_review_by_key(path="data/out/review_data.json"):
    """{review key: item} — carries variant_codes for the single-size join
    (single-variant knives have an empty pairCode, absent from the export grouping)."""
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    items = data if isinstance(data, list) else data.get("products", [])
    return {it["key"]: it for it in items if isinstance(it, dict) and it.get("key")}


def main():
    with open("data/out/decisions.json", encoding="utf-8") as f:
        decisions = json.load(f)
    with open("data/out_grube/itemids.json", encoding="utf-8") as f:
        itemids = json.load(f)
    result = build_grube_codes(decisions, itemids, _load_export(), _load_review_by_key())
    tmp = "data/out/grube_codes.json.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=0)
    os.replace(tmp, "data/out/grube_codes.json")
    print(f"{len(result)} variant codes -> data/out/grube_codes.json")


if __name__ == "__main__":
    main()
