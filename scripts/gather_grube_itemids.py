"""Live Playwright gather of grube.de per-size itemIds for paired GRUBE products.
Resumable (checkpoint keyed by productId). 404/empty tolerated per product."""
import json
import os
import re
import sys

sys.path.insert(0, "src")
from parovanie.grube_de import parse_variants, to_grube_de

_PID = re.compile(r"/p/x/(\d+)/")
_DETAIL_WAIT = ".product-detail-buy-container, .product-detail-configurator, .buy-widget"


def _atomic_write(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=0)
    os.replace(tmp, path)


def gather_itemids(paired, fetch, checkpoint):
    """paired = list of (key, decision_url) for GRUBE. Normalizes each url to .de,
    fetches the rendered detail page, extracts own per-size itemIds. Resumable: an
    already-gathered productId in the checkpoint is skipped (no re-fetch); each new
    productId is written to the checkpoint atomically as it lands. A failed fetch
    (404 / delisted / changed) or an empty parse is skipped — the batch continues."""
    done = {}
    if os.path.exists(checkpoint):
        with open(checkpoint, encoding="utf-8") as f:
            done = json.load(f)
    for key, url in paired:
        de = to_grube_de(url)
        if not de:
            continue
        pid = _PID.search(de).group(1)
        if pid in done:
            continue
        try:
            html = fetch(de, wait_selector=_DETAIL_WAIT)
            sizes = parse_variants(html, pid)
        except Exception as e:  # noqa: BLE001 — 404 / delisted / changed -> skip, continue batch
            print(f"WARN {key} pid={pid}: {e}", file=sys.stderr)
            continue
        if sizes:
            done[pid] = sizes
            _atomic_write(checkpoint, done)
        else:
            print(f"WARN {key} pid={pid}: 0 own itemIds (link-only)", file=sys.stderr)
    return done


def main():
    out_dir = "data/out_grube"
    os.makedirs(out_dir, exist_ok=True)
    # paired GRUBE decisions: key -> url
    with open("data/out/decisions.json", encoding="utf-8") as f:
        dec = json.load(f)
    paired = [(k, v.get("url", "")) for k, v in dec.items()
              if k.startswith("GRUBE|") and v.get("url")]
    from gather_grube import PlaywrightFetcher  # reuse (warm up on grube.de)
    fetcher = PlaywrightFetcher(base="https://www.grube.de/")
    try:
        result = gather_itemids(paired, fetcher, os.path.join(out_dir, "itemids_checkpoint.json"))
    finally:
        fetcher.close()
    _atomic_write(os.path.join(out_dir, "itemids.json"), result)
    print(f"itemids for {len(result)} products -> {out_dir}/itemids.json")


if __name__ == "__main__":
    main()
