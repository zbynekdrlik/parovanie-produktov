"""Generic WordPress+Jigoshop search-result parser shared by Jigoshop suppliers.

KOZAP (kozap.cz) runs on WordPress with the **Jigoshop** plugin (NOT WooCommerce —
``body.jigoshop``; product URLs are ``/produkt/<slug>/``). Its fulltext search
renders results server-side (static SSR HTML) at
    https://www.kozap.cz/?s=<query>
using the **bare** WordPress ``?s=`` param. IMPORTANT: adding ``&post_type=product``
is a DECOY that silently returns the whole catalogue — the bare ``?s=`` is the real,
filtering search. This module is the platform-level parser: a single ``parse_search``
reusable by any Jigoshop supplier — they differ only in base URL, the markup is the
same. kozap.cz is cookie-gated (PHPSESSID); the ``SearchClient`` already warms up the
session with a homepage GET before the first search, so the parser needs nothing
special here.

Results live inside ``div#content ul.products``; each ``li.product`` card wraps a
single ``a[href*="/produkt/"]`` (the whole card is the product link), with the name
in ``.product_info h2`` and the thumbnail in ``.product_img img``.

Scoping rule (auto-ordering safety — a wrong link is worse than no link): we scope
STRICTLY to ``div#content ul.products``. If that grid is absent we return ``[]``
rather than scrape every ``/produkt/`` link on the page (header, footer, cross-sell),
so a "no recognizable results grid" page never yields a stray link to auto-ordering.

NOTE: kozap.cz is a CZECH shop — queries must use Czech words (``ruksak``/``loden``,
not the Slovak ``batoh``). On 0 hits WordPress renders a "Nebyly nalezeny" page with
no ``ul.products`` grid → the parser correctly returns ``[]``.
"""
from __future__ import annotations

from urllib.parse import urldefrag, urljoin

from bs4 import BeautifulSoup

from parovanie.models import Candidate


def parse_search(html: str, base_url: str) -> list[Candidate]:
    """Parse a WordPress+Jigoshop (kozap.cz) ``?s=`` search page → Candidates.

    Scopes to ``div#content ul.products`` (the real results grid); without it,
    returns ``[]`` rather than scraping stray ``/produkt/`` links across the page —
    a wrong link feeds auto-ordering, so "no recognizable grid" means no match. Each
    ``li.product`` card yields one Candidate: link from its ``a[href*="/produkt/"]``,
    name from ``.product_info h2`` (fallbacks: any heading in ``.product_info``, then
    the anchor's ``img[alt]``). URLs are absolute, ``#`` fragment stripped,
    deduplicated, and the ``base_url + "/"`` host-boundary is enforced.
    """
    soup = BeautifulSoup(html, "lxml")
    # The results grid MUST be present; degrade to no results rather than scrape a
    # header/footer/cross-sell ``/produkt/`` link — a wrong link feeds auto-ordering,
    # so "no recognizable grid" means no match. On a 0-hit ("Nebyly nalezeny") page
    # WordPress emits no ``ul.products``, so this correctly yields [].
    scope = soup.select_one("div#content ul.products") or soup.select_one("#content ul.products")
    if scope is None:
        return []

    out: list[Candidate] = []
    seen: set[str] = set()
    for card in scope.select("li.product"):
        a = card.select_one('a[href*="/produkt/"]') or card.select_one("a[href]")
        if not a:
            continue
        href = (a.get("href") or "").strip()
        if not href:
            continue
        url, _ = urldefrag(urljoin(base_url + "/", href))
        # host-boundary check: ``base_url + "/"`` so a look-alike host
        # (e.g. www.kozap.cz.evil.com) cannot satisfy a bare prefix match.
        if not url.startswith(base_url + "/") or url in seen:
            continue
        seen.add(url)
        nm = (card.select_one(".product_info h2")
              or card.select_one(".product_info h3")
              or card.select_one(".product_info h1"))
        name = (nm.get_text(strip=True) if nm else "").strip()
        if not name:
            img = card.select_one("img[alt]")
            name = ((img.get("alt") if img else "") or "").strip()
        out.append(Candidate(name=name, url=url))
    return out
