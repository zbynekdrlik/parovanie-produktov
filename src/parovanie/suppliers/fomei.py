"""FOMEI (fomei.com) search-result parser.

FOMEI runs a custom ASP.NET / EasyWeb storefront. Its fulltext search lives at
    https://www.fomei.com/sk/produkty?ProductsSearch=<query>
(the ``?ProductsSearch=`` param is the real search — ``?search=`` is a decoy that
returns the whole catalogue).

Results render server-side inside ``div.boxPl`` (the product grid; it carries the
``data-shop-product-stats-list`` marker). The grid holds a single ``div.boxPlItem``
wrapper, and the actual product cards are the ``div.plWrap[data-shop-product]``
elements inside it — one per product. Each card has:
  * ``a[href*="-detail-"]`` — the product-detail link
    (pattern ``/sk/produkty-<slug>-detail-<numericId>``)
  * ``h2.plWrapTitle`` — the product name.

Category tiles / nav use ``.item-title`` / ``.item-link`` (NOT ``.plWrap``), so
scoping to ``div.boxPl … div.plWrap[data-shop-product]`` excludes them.

QUIRK (live HTML vs. blueprint): the blueprint described the cards as
``div.boxPlItem`` each holding an inner ``div.plWrap``. In the captured live pages
there is exactly ONE ``div.boxPlItem`` that wraps ALL the ``div.plWrap`` cards, so
the per-product card IS the ``div.plWrap[data-shop-product]`` element. We scope to
``div.boxPl`` and iterate ``div.plWrap[data-shop-product]`` accordingly.
"""
from __future__ import annotations

from urllib.parse import urldefrag, urljoin

from bs4 import BeautifulSoup

from parovanie.models import Candidate


def parse_search(html: str, base_url: str) -> list[Candidate]:
    """Parse a fomei.com search page → product Candidates.

    Scopes to the ``div.boxPl`` product grid; without it, returns ``[]`` rather
    than scraping stray links across the page (header / nav / category tiles) — a
    wrong link feeds auto-ordering, so "no recognizable grid" means no match.
    Each ``div.plWrap[data-shop-product]`` card yields one Candidate (name from
    ``h2.plWrapTitle``, url = absolute product-detail URL with any ``#`` fragment
    stripped). Deduplicated by URL; a card missing its link or title is skipped.
    """
    soup = BeautifulSoup(html, "lxml")
    # The results grid MUST be present; without it, degrade to no results rather than
    # scrape every link on the page (header / nav / category tiles / cross-sell) — a
    # wrong link feeds auto-ordering, so "no recognizable grid" means no match.
    scope = soup.select_one("div.boxPl")
    if scope is None:
        return []

    out: list[Candidate] = []
    seen: set[str] = set()
    for card in scope.select("div.plWrap[data-shop-product]"):
        a = card.select_one('a[href*="-detail-"]')
        if not a:
            continue
        href = (a.get("href") or "").strip()
        if not href:
            continue
        url, _ = urldefrag(urljoin(base_url + "/", href))
        # host-boundary check: ``base_url + "/"`` so a look-alike host
        # (e.g. www.fomei.com.evil.com) cannot satisfy a bare prefix match.
        if not url.startswith(base_url + "/") or url in seen:
            continue
        seen.add(url)
        nm = card.select_one("h2.plWrapTitle")
        name = (nm.get_text(strip=True) if nm else "").strip()
        out.append(Candidate(name=name, url=url))
    return out
