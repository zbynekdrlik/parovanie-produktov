"""Resolve forestshop product URLs for products NOT in the sitemap (detailOnly):
try slug-candidates, keep the one that returns HTTP 200 on its own slug.
Incremental save to data/out/review_data.json. Run in background."""
import json
import logging
import time

import requests

from parovanie.export_helpers import slug

log = logging.getLogger("resolve_urls")

BASE = "https://www.forestshop.sk/"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/120 Safari/537.36"}
GEN = {"nohavice", "bunda", "mikina", "vesta", "komplet", "set", "ponozky", "ciapka",
       "rukavice", "kratasy", "sortky", "obuv", "damske", "panske", "detske", "letne",
       "zimne", "polovnicke", "strelecke", "ochranne", "lovecke", "klobuk", "siltovka",
       "vreckovy", "flisova", "funkcne", "zatvaraci", "suprava"}


def candidates(name):
    base = slug(name, strip_leading_number=True)
    out = [base]
    toks = base.split("-")
    i = 0
    while i < len(toks) - 1 and toks[i] in GEN:
        i += 1
    if i > 0:
        out.append("-".join(toks[i:]))
    for p in ("polovnicke-", "polovnicka-", "polovnicky-"):
        out.append(p + base)
    seen, o = set(), []
    for c in out:
        if c and c not in seen:
            seen.add(c)
            o.append(c)
    return o


def main():
    rd = json.load(open("data/out/review_data.json", encoding="utf-8"))
    todo = [p for p in rd if not p.get("our_url")]
    print(f"to resolve: {len(todo)}", flush=True)
    sess = requests.Session()
    sess.headers.update(UA)
    hit = 0
    for n, p in enumerate(todo):
        for c in candidates(p["name"]):
            url = BASE + c + "/"
            try:
                r = sess.get(url, timeout=12, allow_redirects=True)
                if (r.status_code == 200 and "/vyhladavanie" not in r.url
                        and r.url.rstrip("/").endswith(c)):
                    p["our_url"] = r.url
                    hit += 1
                    break
            except requests.RequestException as e:
                log.debug("probe failed url=%s: %r", url, e)
            time.sleep(0.12)
        if n % 25 == 0:
            json.dump(rd, open("data/out/review_data.json", "w", encoding="utf-8"),
                      ensure_ascii=False)
            print(f"  {n}/{len(todo)} resolved {hit}", flush=True)
    json.dump(rd, open("data/out/review_data.json", "w", encoding="utf-8"), ensure_ascii=False)
    total = sum(1 for p in rd if p.get("our_url"))
    print(f"RESOLVE DONE: newly {hit}/{len(todo)}; total direct {total}/{len(rd)}", flush=True)


if __name__ == "__main__":
    main()
