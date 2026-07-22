"""Resolve forestshop product URLs for products NOT in the sitemap (detailOnly).

Two passes, both reusing `parovanie.url_resolver` — never a separate
re-implementation (issue #15: this used to be a standalone HTTP-probe
resolver with no image disambiguation and no cross-product dedup, the same
defect class the sitemap resolver was hardened against):

  1. The canonical sitemap resolver (`url_resolver.assign_urls`) runs FIRST —
     free, no network probing. Only the genuine remainder (no sitemap entry —
     detailOnly / drop-ship pages) falls through to HTTP probing.
  2. For the remainder, guess candidate slugs and HTTP-probe EVERY one of
     them (never stop at the first 200) — when a product has more than one
     confirmed candidate, disambiguate with its own image filename
     (`url_resolver.disambiguate`, the exact same rule the sitemap path
     uses). The whole probed batch, plus every URL some OTHER product already
     carries, is deduped with `url_resolver.dedup` so two different products
     never end up sharing one URL and an already-assigned link is never
     displaced.

Incremental save to data/out/review_data.json. Run in background.
"""
import json
import logging
import re
import time

import requests

from parovanie.export_helpers import slug
from parovanie.url_resolver import assign_urls, dedup, disambiguate

log = logging.getLogger("resolve_urls")

BASE = "https://www.forestshop.sk/"
REVIEW = "data/out/review_data.json"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/120 Safari/537.36"}
GEN = {"nohavice", "bunda", "mikina", "vesta", "komplet", "set", "ponozky", "ciapka",
       "rukavice", "kratasy", "sortky", "obuv", "damske", "panske", "detske", "letne",
       "zimne", "polovnicke", "strelecke", "ochranne", "lovecke", "klobuk", "siltovka",
       "vreckovy", "flisova", "funkcne", "zatvaraci", "suprava"}

# Local resolution-strength scale for the probe path (used only by dedup(),
# which just compares relative order — never mixed numerically with
# url_resolver's own EXACT/IMAGE/SINGLE grades, since the sitemap pass and the
# probe pass always resolve DISJOINT products). EXISTING is a sentinel high
# enough that a URL some other product already carries is never displaced.
_NONE = 0
_SINGLE = 1
_IMAGE = 2
_EXISTING = 99


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


def resolve_probe(name, images, fetch):
    """Resolve ONE detailOnly product's forestshop URL by probing every
    candidate() slug via `fetch(candidate_slug) -> confirmed_url | None`
    (real HTTP in production, a canned callable in tests). Probes ALL
    candidates — never stops at the first 200 — so that when more than one
    guess turns out to be a real page, the product's own image disambiguates
    between them instead of blindly keeping whichever was tried first.

    Returns (url_or_None, strength, name_slug) for `url_resolver.dedup`."""
    ns = slug(name)
    hits = [(c, u) for c in candidates(name) for u in (fetch(c),) if u]
    if not hits:
        return None, _NONE, ns
    if len(hits) == 1:
        return hits[0][1], _SINGLE, ns

    winner = disambiguate([c for c, _u in hits], ns, images)
    if winner is None:
        return None, _NONE, ns
    return dict(hits)[winner], _IMAGE, ns


def resolve_batch(rd, sitemap_slugs, fetch, checkpoint=None):
    """Fill `our_url` for every product in `rd` lacking one, mutating in place.

    `checkpoint(n, total)`, if given, is invoked periodically during the HTTP
    probe pass (every 25 products, and on the last one) — purely for progress
    logging / incremental persistence, never required for correctness (the
    dedup pass is recomputed from scratch on every call, so it's always
    consistent regardless of when a caller chooses to look).

    Returns (from_sitemap, from_probe, remaining) — how many were resolved by
    each pass, and how many entered the probe pass."""
    todo = [p for p in rd if not p.get("our_url")]
    sitemap_urls = assign_urls(todo, sitemap_slugs) if sitemap_slugs else {}
    remaining, from_sitemap = [], 0
    for i, p in enumerate(todo):
        u = sitemap_urls.get(i)
        if u:
            p["our_url"] = u
            from_sitemap += 1
        else:
            remaining.append(p)

    resolved = []
    for n, p in enumerate(remaining):
        resolved.append(resolve_probe(p["name"], p.get("our_images"), fetch))
        existing = [(q["our_url"], _EXISTING, slug(q.get("name") or ""))
                    for q in rd if q.get("our_url")]
        urls = dedup(resolved + existing)
        for i, q in enumerate(remaining[:len(resolved)]):
            q["our_url"] = urls.get(i)
        if checkpoint and (n % 25 == 0 or n == len(remaining) - 1):
            checkpoint(n + 1, len(remaining))

    from_probe = sum(1 for p in remaining if p.get("our_url"))
    return from_sitemap, from_probe, len(remaining)


def make_fetch(sess):
    """Real HTTP probe: GET the candidate's own page and confirm it round-trips
    to itself (not a redirect to the ?string= search fallback)."""
    def fetch(c):
        url = BASE + c + "/"
        try:
            r = sess.get(url, timeout=12, allow_redirects=True)
        except requests.RequestException as e:
            log.debug("probe failed url=%s: %r", url, e)
            return None
        finally:
            time.sleep(0.12)
        if r.status_code == 200 and "/vyhladavanie" not in r.url and r.url.rstrip("/").endswith(c):
            return r.url
        return None
    return fetch


def main():
    rd = json.load(open(REVIEW, encoding="utf-8"))
    todo_n = sum(1 for p in rd if not p.get("our_url"))
    print(f"to resolve: {todo_n}", flush=True)

    sess = requests.Session()
    sess.headers.update(UA)
    smr = sess.get(BASE + "sitemap.xml", timeout=30)
    smr.raise_for_status()
    locs = re.findall(r"<loc>https://www\.forestshop\.sk/([^<]+?)/?</loc>", smr.text)
    if not locs:
        raise SystemExit("sitemap prázdny/nečitateľný — odmietam pokračovať")

    def save():
        json.dump(rd, open(REVIEW, "w", encoding="utf-8"), ensure_ascii=False)

    def checkpoint(n, total):
        save()
        print(f"  {n}/{total} probed", flush=True)

    from_sitemap, hit, remaining_n = resolve_batch(rd, locs, make_fetch(sess), checkpoint)
    save()
    total = sum(1 for p in rd if p.get("our_url"))
    print(f"RESOLVE DONE: sitemap {from_sitemap}, probed {hit}/{remaining_n}; "
          f"total direct {total}/{len(rd)}", flush=True)


if __name__ == "__main__":
    main()
