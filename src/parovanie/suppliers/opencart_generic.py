"""Generic OpenCart search-result parser shared by OpenCart suppliers.

MÁZI HUNT (wildzone.eu) runs on OpenCart. Its fulltext search renders results
server-side (static HTML 200) at
    https://wildzone.eu/index.php?route=product/search&search=<query>
inside the ``#content`` results column, one ``div.product-layout`` card per hit.
This module is the platform-level parser: a single ``parse_search`` reusable by
any OpenCart supplier — they differ only in base URL, the markup is the same.

wildzone.eu is cookie-gated (a PHPSESSID / language / currency cookie is set on the
homepage); the ``SearchClient`` already warms up the session with a homepage GET
before the first search, so the parser needs nothing special here.

Each ``div.product-layout`` card holds:
  * ``div.name a`` — the product-detail link + name.
  * ``.image img`` — the product thumbnail (not returned; kept for reference).

The product URL carries a ``?search=<query>`` tracking param (e.g.
``/m-269-1917-prime-polo-szarvas.html?search=szarvas``); we strip the query (and any
``#`` fragment) so the same product resolves to one stable URL regardless of the
search term. This is a Hungarian shop (HUF) so name matching is weak — the code
``M-xxx-xxxx`` appears in both name and URL and is the real join key, handled by AI
verify downstream.

Scoping rule (auto-ordering safety — a wrong link is worse than no link): we scope
strictly to ``#content`` (the search result column). If it is absent we return
``[]`` rather than scrape every ``div.product-layout`` on the page (a "featured
products" carousel in the header/footer would otherwise leak), so a "no recognizable
results column" page never yields a stray link to auto-ordering.
"""
from __future__ import annotations

from urllib.parse import urldefrag, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from parovanie.models import Candidate


def _strip_query_and_fragment(url: str) -> str:
    """Drop the ``?query`` (OpenCart's ``?search=`` tracking param) and ``#fragment``
    so the same product maps to one stable URL regardless of the search term."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def parse_search(html: str, base_url: str) -> list[Candidate]:
    """Parse an OpenCart (wildzone.eu) search page → product Candidates.

    Scopes to ``#content`` (the results column); without it, returns ``[]`` rather
    than scraping stray links across the page — a wrong link feeds auto-ordering, so
    "no recognizable column" means no match. Each ``div.product-layout`` card yields
    one Candidate: link + name from ``div.name a``, with the ``?search=`` tracking
    query and any ``#`` fragment stripped. URLs are absolute, deduplicated, and the
    ``base_url + "/"`` host-boundary is enforced, so a look-alike host or off-site
    card is dropped.
    """
    soup = BeautifulSoup(html, "lxml")
    # The results column MUST be present; degrade to no results rather than scrape a
    # header/footer "featured products" carousel — a wrong link feeds auto-ordering,
    # so "no recognizable column" means no match.
    scope = soup.select_one("#content")
    if scope is None:
        return []

    out: list[Candidate] = []
    seen: set[str] = set()
    for card in scope.select("div.product-layout"):
        a = card.select_one("div.name a[href]") or card.select_one("div.name a")
        if not a:
            continue
        href = (a.get("href") or "").strip()
        if not href:
            continue
        # resolve relative → absolute, drop the ?search= tracking query AND # fragment
        abs_url = urljoin(base_url + "/", href)
        url, _ = urldefrag(abs_url)
        url = _strip_query_and_fragment(url)
        # host-boundary check: ``base_url + "/"`` so a look-alike host
        # (e.g. wildzone.eu.evil.com) cannot satisfy a bare prefix match.
        if not url.startswith(base_url + "/") or url in seen:
            continue
        seen.add(url)
        name = a.get_text(strip=True)
        out.append(Candidate(name=name, url=url))
    return out
