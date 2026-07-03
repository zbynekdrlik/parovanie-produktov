"""Generic Magento 1.x search-result parser shared by Magento suppliers.

RAPPA (rappa.cz) runs on Magento 1.x. Its fulltext search renders results
server-side (static HTML) at
    https://www.rappa.cz/sk/catalogsearch/result/?q=<query>
(the search param is ``?q=`` — NOT ``?string=``) inside a ``ul.products-grid``
grid, one ``li.item`` card per hit. This module is the platform-level parser: a
single ``parse_search`` reusable by any Magento-1.x supplier — they differ only in
base URL, the markup is the same.

Each ``li.item`` card holds:
  * ``h2.product-name > a`` — the product-detail link + name (the anchor carries
    both ``href`` and a ``title`` attribute, and its text is the product name);
    ``a.product-image`` is the thumbnail-link fallback (same href, name in ``title``).

Scoping rule (auto-ordering safety — a wrong link is worse than no link): we scope
strictly to ``ul.products-grid``. If that grid is absent we return ``[]`` rather
than scrape every ``li.item`` / stray link on the page (header, nav, "you may also
like" carousel), so a "no recognizable results grid" page never yields a stray link
to auto-ordering.
"""
from __future__ import annotations

from urllib.parse import urldefrag, urljoin

from bs4 import BeautifulSoup

from parovanie.models import Candidate


def parse_search(html: str, base_url: str) -> list[Candidate]:
    """Parse a Magento-1.x (rappa.cz) search page → product Candidates.

    Scopes to ``ul.products-grid`` (the results grid); without it, returns ``[]``
    rather than scraping stray links across the page — a wrong link feeds
    auto-ordering, so "no recognizable grid" means no match. Each ``li.item`` card
    yields one Candidate: link + name from ``h2.product-name > a`` (fallback
    ``a.product-image`` — href + ``title``/``img[alt]`` name). URLs are absolute,
    ``#`` fragment stripped, deduplicated, and the ``base_url + "/"`` host-boundary
    is enforced, so a look-alike host or off-site card is dropped.
    """
    soup = BeautifulSoup(html, "lxml")
    # The results grid MUST be present; degrade to no results rather than scrape
    # every card / link on the page (header, nav, cross-sell carousel) — a wrong
    # link feeds auto-ordering, so "no recognizable grid" means no match.
    scope = soup.select_one("ul.products-grid")
    if scope is None:
        return []

    out: list[Candidate] = []
    seen: set[str] = set()
    for card in scope.select("li.item"):
        # h2.product-name > a carries both href and the name text; a.product-image
        # is the thumbnail-link fallback (same href, name in its title/img alt).
        a = card.select_one("h2.product-name > a[href]") or card.select_one("a.product-image[href]")
        if not a:
            continue
        href = (a.get("href") or "").strip()
        if not href:
            continue
        url, _ = urldefrag(urljoin(base_url + "/", href))
        # host-boundary check: ``base_url + "/"`` so a look-alike host
        # (e.g. www.rappa.cz.evil.com) cannot satisfy a bare prefix match.
        if not url.startswith(base_url + "/") or url in seen:
            continue
        seen.add(url)
        name = a.get_text(strip=True)
        if not name:
            name = (a.get("title") or "").strip()
        if not name:
            img = a.select_one("img[alt]")
            name = ((img.get("alt") if img else "") or "").strip()
        out.append(Candidate(name=name, url=url))
    return out
