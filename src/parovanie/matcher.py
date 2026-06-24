from __future__ import annotations
import logging
from parovanie.models import Product, Match
from parovanie.normalize import clean_name
from parovanie.ranking import pick_best

log = logging.getLogger("parovanie.matcher")


def query_ladder(p: Product) -> list[str]:
    """Ordered queries to try until one returns candidates: external code,
    full cleaned name, then progressively shorter name prefixes — long exact
    queries frequently miss on the supplier search engines."""
    qs: list[str] = []
    if p.external_code:
        qs.append(p.external_code)
    name = clean_name(p.name)
    if name:
        qs.append(name)
        toks = name.split()
        if len(toks) > 3:
            qs.append(" ".join(toks[:3]))
        if len(toks) > 2:
            qs.append(" ".join(toks[:2]))
    out: list[str] = []
    seen: set[str] = set()
    for q in qs:
        if q and q not in seen:
            seen.add(q)
            out.append(q)
    return out


def match_one(product: Product, client) -> Match:
    ladder = query_ladder(product)
    candidates: list = []
    used_query = ladder[0] if ladder else ""
    for q in ladder:
        used_query = q
        candidates = client.search(product.supplier, q)
        if candidates:
            break
    best, conf = pick_best(product, candidates)
    return Match(product=product, query=used_query, chosen=best,
                 confidence=conf, candidate_count=len(candidates))


def match_products(products: list[Product], client) -> list[Match]:
    matches: list[Match] = []
    for i, p in enumerate(products, 1):
        m = match_one(p, client)
        log.info("[%d/%d] %s %r -> %s (%s)", i, len(products), p.supplier,
                 m.query, m.chosen.url if m.chosen else "NO MATCH", m.confidence)
        matches.append(m)
    return matches
