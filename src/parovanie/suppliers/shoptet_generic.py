"""Generic Shoptet search-result parser shared by several Shoptet eshops.

Shoptet (the same platform as luko.cz — see ``luko.py``) renders fulltext search
results server-side at ``/vyhledavani/?string=<query>`` (``/vyhladavanie/`` on the
Slovak locale) inside a ``div.products.products-block`` grid, one ``div.product``
card per hit. This module is the platform-level parser: a single ``parse_search``
reused by every plain-Shoptet supplier (Zubíček, Virginiashop, Thermvisia/Tenolix),
which differ only in base URL — the markup is identical.

Scoping rule (auto-ordering safety — a wrong link is worse than no link): we scope
strictly to ``.products.products-block``. If that grid is absent we return ``[]``
rather than scrape every ``.product`` on the page (header, cart, cross-sell), so a
"no recognizable results grid" page never yields a stray link to auto-ordering.

NOTE: on 0 exact matches Shoptet renders "did you mean" suggestion cards with the
SAME markup — the parser simply extracts whatever cards are present; result
strictness (is this card actually the right product?) is enforced downstream.
"""
from __future__ import annotations

from urllib.parse import urldefrag, urljoin

from bs4 import BeautifulSoup

from parovanie.models import Candidate


def parse_search(html: str, base_url: str) -> list[Candidate]:
    """Parse a Shoptet ``/vyhledavani/`` search page → product Candidates.

    Scopes to ``.products.products-block`` (the real results grid); each
    ``.product`` card yields one Candidate. Link from ``a.name`` (fallback
    ``a.image``); name from ``[data-micro="name"]`` (fallbacks
    ``[data-testid="productCardName"]``, then ``a.name`` text). Relative hrefs are
    resolved against ``base_url``, ``#`` fragments stripped, results deduped by URL,
    and any URL outside ``base_url + "/"`` (host boundary) dropped.
    """
    soup = BeautifulSoup(html, "lxml")
    # The results grid MUST be present; without it, degrade to no results rather than
    # scrape every ``.product`` on the page — a wrong link feeds auto-ordering, so
    # "no recognizable grid" means no match, never scrape-all.
    scope = soup.select_one(".products.products-block")
    if scope is None:
        return []

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
        # hrefs may be relative → resolve against ``base_url + "/"`` and drop fragment.
        url, _ = urldefrag(urljoin(base_url + "/", href))
        # host-boundary check: ``base_url + "/"`` so a look-alike host
        # (e.g. www.zubicek.cz.evil.com) cannot satisfy a bare prefix match.
        if not url.startswith(base_url + "/") or url in seen:
            continue
        seen.add(url)
        nm = (card.select_one('[data-micro="name"]')
              or card.select_one('[data-testid="productCardName"]')
              or card.select_one("a.name"))
        name = (nm.get_text(strip=True) if nm else "").strip()
        out.append(Candidate(name=name, url=url))
    return out
