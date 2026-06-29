"""Generic PrestaShop 1.7 search-result parser shared by several suppliers.

PrestaShop 1.7 (the same family as ``wetland.py``) renders fulltext search
results server-side inside ``#js-product-list`` as ``article.product-miniature``
cards. The product detail link + name live in a heading anchor whose tag VARIES
by theme (``h1``/``h2``/``h3``) — TTHUNT uses ``h2.product-title a``, LESONA
``h1.product-title a``, LASTING ``h3.product-title a`` — so the title anchor is
tried in a fixed preference order per card.

The result URLs carry a ``#/<id>-<attr>`` default-variant fragment (e.g.
``…-bunda-1605-673.html#/55-konfekcna_velkost-52``); we ``urldefrag`` it so each
product appears once regardless of which variant the page pre-selected, and we
scope STRICTLY to ``#js-product-list`` so the header search autocomplete, nav,
and cross-sell carousels are never scraped (a wrong link feeds auto-ordering).
"""
from __future__ import annotations

from urllib.parse import urldefrag, urljoin

from bs4 import BeautifulSoup

from parovanie.models import Candidate

# Title-anchor selectors, tried in order per card: the heading level differs by
# PrestaShop theme, so the FIRST that matches wins. The thumbnail link is the
# last-resort fallback (it carries the same href but no name text).
_TITLE_SELECTORS = (
    "h1.product-title a",
    "h2.product-title a",
    "h3.product-title a",
    ".product-miniature__title a.link",
    "a.product-thumbnail[href]",
)


def parse_search(html: str, base_url: str) -> list[Candidate]:
    """Parse a PrestaShop 1.7 search page → product Candidates.

    Scopes to ``#js-product-list`` (falling back to ``.products``, the results
    grid) — without a recognizable grid we return ``[]`` rather than scrape the
    whole page, because a wrong link feeds auto-ordering. Each
    ``article.product-miniature`` yields one Candidate: the product detail URL
    (any ``#`` variant fragment stripped) and the name from the first matching
    title selector (falling back to the anchor's ``img[alt]``). Deduplicated by URL.
    """
    soup = BeautifulSoup(html, "lxml")
    # The results grid MUST be present; degrade to no results rather than scrape
    # nav / autocomplete / cross-sell links scattered across the page.
    scope = soup.select_one("#js-product-list") or soup.select_one(".products")
    if scope is None:
        return []

    out: list[Candidate] = []
    seen: set[str] = set()
    for card in scope.select("article.product-miniature"):
        a = None
        for sel in _TITLE_SELECTORS:
            a = card.select_one(sel)
            if a:
                break
        if a is None:
            continue
        href = (a.get("href") or "").strip()
        if not href:
            continue
        url, _ = urldefrag(urljoin(base_url + "/", href))
        # host-boundary: ``base_url + "/"`` so a look-alike host cannot satisfy a
        # bare prefix match, and an off-site sponsored card is skipped.
        if not url.startswith(base_url + "/") or url in seen:
            continue
        seen.add(url)
        name = a.get_text(strip=True)
        if not name:
            img = a.select_one("img[alt]")
            name = (img.get("alt") if img else "") or ""
        out.append(Candidate(name=name.strip(), url=url))
    return out
