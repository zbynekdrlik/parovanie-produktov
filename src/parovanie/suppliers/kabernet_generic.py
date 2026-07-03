"""Generic Kabernet-CMS (ASP.NET) search-result parser.

ROSLER (rosler.sk) runs on the Kabernet CMS — an ASP.NET storefront that renders
fulltext search results server-side (static HTML, no cookies needed) at
    https://www.rosler.sk/produkty?q=<query>
inside a ``#category-detail`` results container, one ``div.product-thumb`` card per
hit. This module is the platform-level parser: a single ``parse_search`` reusable
by any Kabernet-CMS supplier, which differ only in base URL — the markup is the same.

Each ``div.product-thumb`` card holds:
  * the first ``a[href]`` under the card — the product-detail link
    (pattern ``/produkty/<category>/.../<slug>``)
  * an ``<h2><a>`` heading — the product name.

ROSLER sells Victorinox knives; the forestshop product NAME carries the Victorinox
CODE (e.g. "Vreckový nôž ... Victorinox 0.8341"), while ROSLER's own product name
differs ("Hunter XT GRIP"), so pairing is by CODE downstream (AI verify) — this
parser only returns the candidates the search page produced.

Scoping rule (auto-ordering safety — a wrong link is worse than no link): we scope
strictly to ``#category-detail``. If that container is absent we return ``[]``
rather than scrape every ``div.product-thumb`` / stray ``a[href]`` on the page
(header, nav, cross-sell), so a "no recognizable results container" page never
yields a stray link to auto-ordering.
"""
from __future__ import annotations

from urllib.parse import urldefrag, urljoin

from bs4 import BeautifulSoup

from parovanie.models import Candidate


def parse_search(html: str, base_url: str) -> list[Candidate]:
    """Parse a Kabernet-CMS (rosler.sk) search page → product Candidates.

    Scopes to ``#category-detail`` (the results container); without it, returns
    ``[]`` rather than scraping stray links across the page — a wrong link feeds
    auto-ordering, so "no recognizable container" means no match. Each
    ``div.product-thumb`` card yields one Candidate: url from the first ``a[href]``
    under the card (absolute, ``#`` fragment stripped), name from ``h2 a`` (fallback
    ``h2``). Deduplicated by URL; the ``base_url + "/"`` host-boundary is enforced,
    so a look-alike host or off-site card is dropped.
    """
    soup = BeautifulSoup(html, "lxml")
    # The results container MUST be present; degrade to no results rather than scrape
    # every card / link on the page (header, nav, cross-sell) — a wrong link feeds
    # auto-ordering, so "no recognizable container" means no match.
    scope = soup.select_one("#category-detail")
    if scope is None:
        return []

    out: list[Candidate] = []
    seen: set[str] = set()
    for card in scope.select("div.product-thumb"):
        a = card.select_one("a[href]")
        if not a:
            continue
        href = (a.get("href") or "").strip()
        if not href:
            continue
        url, _ = urldefrag(urljoin(base_url + "/", href))
        # host-boundary check: ``base_url + "/"`` so a look-alike host
        # (e.g. www.rosler.sk.evil.com) cannot satisfy a bare prefix match.
        if not url.startswith(base_url + "/") or url in seen:
            continue
        seen.add(url)
        nm = card.select_one("h2 a") or card.select_one("h2")
        name = (nm.get_text(strip=True) if nm else "").strip()
        out.append(Candidate(name=name, url=url))
    return out
