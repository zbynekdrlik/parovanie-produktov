"""Re-point every product's `our_url` in data/out/review_data.json using the
image-aware, dedup-safe resolver (parovanie.url_resolver), in place — preserving
candidates / AI verdicts / current state / images / keys.

Run after a sitemap change, or to repair URLs assigned by an older resolver
(the wrong-our_url bug: two products that differ only by a number used to share
one forestshop URL). Reports the collision count before and after so the repair
is visible. Idempotent.
"""
import collections
import json
import re

import requests

from parovanie.export_helpers import slug
from parovanie.url_resolver import assign_urls

OUT = "data/out"
DATA = f"{OUT}/review_data.json"
BASE = "https://www.forestshop.sk/"


def _collisions(rd):
    """Groups where one URL is shared by DIFFERENT products (= a wrong link on at
    least one). Genuine catalog duplicates — two entries whose name reduces to the
    same slug — are the same product on one page and are excluded (matches the
    dedup definition in url_resolver.assign_urls)."""
    by_url = collections.defaultdict(list)
    for p in rd:
        if p.get("our_url"):
            by_url[p["our_url"]].append(p)
    bad = 0
    for ps in by_url.values():
        if len(ps) > 1 and len({slug(p.get("name") or "") for p in ps}) > 1:
            bad += 1
    return bad


def main():
    smr = requests.get(BASE + "sitemap.xml", timeout=30,
                       headers={"User-Agent": "Mozilla/5.0"})
    smr.raise_for_status()
    locs = re.findall(r"<loc>https://www\.forestshop\.sk/([^<]+?)/?</loc>", smr.text)
    if not locs:
        raise SystemExit("sitemap prázdny/nečitateľný — odmietam prepísať URL")

    rd = json.load(open(DATA, encoding="utf-8"))
    before_bad = _collisions(rd)
    before_linked = sum(1 for p in rd if p.get("our_url"))

    urls = assign_urls(rd, locs)
    changed = 0
    for i, p in enumerate(rd):
        if p.get("our_url") != urls[i]:
            changed += 1
        p["our_url"] = urls[i]

    after_bad = _collisions(rd)
    after_linked = sum(1 for p in rd if p.get("our_url"))
    json.dump(rd, open(DATA, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"reresolve: {len(rd)} products | linked {before_linked}→{after_linked} | "
          f"changed {changed} | wrong-shared-URL groups {before_bad}→{after_bad}")
    if after_bad:
        raise SystemExit(f"STILL {after_bad} different-product URL collisions — investigate")


if __name__ == "__main__":
    main()
