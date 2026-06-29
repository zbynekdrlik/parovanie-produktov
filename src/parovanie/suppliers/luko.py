"""LUKO (luko.cz) search-result parser + deterministic code matcher.

LUKO is the shirt manufacturer's own **Shoptet** eshop (same platform as
forestshop.sk). Forestshop carries LUKO's own 6-digit article code INSIDE every
product name (e.g. "Košeľa LUKO ALPINA Krátky rukáv 034230" → code 034230), so
LUKO does not need the fuzzy top-K + AI-verify pipeline the other suppliers use:
the code is an exact join key.

Shoptet fulltext search lives at
    https://www.luko.cz/vyhledavani/?string=<code>
and renders results server-side inside ``div.products.products-block`` as
``div.product`` cards. The page also has a ``div.recommended-products`` cross-sell
block (class ``recommended-product``) which we must NOT pick up — scoping to
``.products.products-block .product`` excludes it.

Matching rule (auto-ordering safety: a wrong link is worse than no link):
accept a result ONLY when EXACTLY ONE result card's NAME contains the exact
6-digit code. The Shoptet *slug* can be stale (a renamed product keeps its old
slug, e.g. code 024245 lives at ``…-model-022263/``), but the product NAME
(``[data-micro="name"]``) always carries the current code — so we match on the
name, never the slug. Zero matches, or two products sharing one code (LUKO lists
some codes as both a regular and a slim-fit cut), resolve to -1 (no match).
"""
from __future__ import annotations

import re
from urllib.parse import urldefrag, urljoin

from bs4 import BeautifulSoup

from parovanie.models import Candidate

_SIX = re.compile(r"\b\d{6}\b")


def parse_search(html: str, base_url: str) -> list[Candidate]:
    """Parse a luko.cz Shoptet search page → product Candidates.

    Scopes to ``.products.products-block`` (the real results grid) so the
    ``recommended-products`` cross-sell carousel, header, and cart are excluded.
    Each ``.product`` card yields one Candidate (name from ``[data-micro="name"]``,
    url = absolute product URL with any ``#`` fragment stripped). Deduplicated by URL.
    """
    soup = BeautifulSoup(html, "lxml")
    scope = soup.select_one(".products.products-block") or soup

    out: list[Candidate] = []
    seen: set[str] = set()
    for card in scope.select(".product"):
        a = (card.select_one("a.name[href]")
             or card.select_one("a.image[href]")
             or card.select_one("a[href]"))
        if not a:
            continue
        href = (a.get("href") or "").strip()
        if not href:
            continue
        url, _ = urldefrag(urljoin(base_url + "/", href))
        if not url.startswith(base_url) or url in seen:
            continue
        seen.add(url)
        nm = card.select_one('[data-micro="name"]') or card.select_one("a.name")
        name = (nm.get_text(strip=True) if nm else "").strip()
        out.append(Candidate(name=name, url=url))
    return out


def extract_code(name: str) -> str | None:
    """Return the single 6-digit LUKO article code embedded in a forestshop product
    name, or ``None`` when the name does not contain exactly one such code.

    Every LUKO product name carries exactly one 6-digit code; requiring uniqueness
    keeps a stray future name (zero or two codes) from producing an ambiguous query.
    """
    codes = _SIX.findall(name or "")
    return codes[0] if len(codes) == 1 else None


def choose_exact(code: str | None, candidates: list[Candidate]) -> int:
    """Index of the unique candidate whose NAME contains the exact ``code``, else -1.

    -1 (no match) is returned for: no code, zero candidates carrying the code, or
    more than one candidate carrying it (a code LUKO lists under two cuts). A wrong
    link feeds auto-ordering, so ambiguity must never resolve to a guess.
    """
    if not code:
        return -1
    hits = [i for i, c in enumerate(candidates) if code in (c.name or "")]
    return hits[0] if len(hits) == 1 else -1
