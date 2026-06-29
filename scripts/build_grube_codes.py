"""Join grube.de per-size itemIds with forestshop variant sizes ->
data/out/grube_codes.json  {code: {itemId, size, deUrl, productId}}."""
import csv
import json
import os
import re
import sys

sys.path.insert(0, "src")
from parovanie.grube_de import to_grube_de, match_variant_codes, resolve_size, MULTI_AXIS

_PID = re.compile(r"/p/x/(\d+)/")


def build_grube_codes(decisions, itemids, export_rows):
    # pairCode -> productId (from GRUBE decisions)
    pair_pid, pair_de = {}, {}
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

    # group GRUBE export rows by pairCode
    by_pair: dict[str, list[dict]] = {}
    for row in export_rows:
        if row.get("supplier") != "GRUBE":
            continue
        by_pair.setdefault(row.get("pairCode", ""), []).append(row)

    out: dict[str, dict] = {}
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
    return out


def _load_export(path="data/products.csv"):
    csv.field_size_limit(10**7)
    with open(path, encoding="cp1250", newline="") as f:
        return list(csv.DictReader(f, delimiter=";"))


def main():
    with open("data/out/decisions.json", encoding="utf-8") as f:
        decisions = json.load(f)
    with open("data/out_grube/itemids.json", encoding="utf-8") as f:
        itemids = json.load(f)
    result = build_grube_codes(decisions, itemids, _load_export())
    tmp = "data/out/grube_codes.json.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=0)
    os.replace(tmp, "data/out/grube_codes.json")
    print(f"{len(result)} variant codes -> data/out/grube_codes.json")


if __name__ == "__main__":
    main()
