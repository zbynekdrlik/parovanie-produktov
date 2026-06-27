"""ODIMON (odimon.sk) search-result parser.

odimon.sk is a BUXUS CMS shop. The search page at
    https://www.odimon.sk/vysledky-vyhladavania?term=<q>
renders results server-side as static HTML (cookie-gated like huntingshop — the
shared session fetcher's homepage warm-up establishes the BUXUS session cookie).

Each result is a single anchor that IS the card:

    <div class="product-list__results">
        <a class="product-card" href="https://www.odimon.sk/<category-path>/<slug>">
            <img alt="Protišmyky Alpenheat" title="..." src="..."/>
        </a>
        ...
    </div>

The product name lives in the card image's ``alt``/``title``. We scope to
``.product-list__results`` so the producer-filter facets, the mini-cart, and the
"recommended" carousel (all elsewhere on the page) are excluded.
"""
from __future__ import annotations

from urllib.parse import urldefrag, urljoin

from bs4 import BeautifulSoup

from parovanie.models import Candidate


def parse_search(html: str, base_url: str) -> list[Candidate]:
    """Parse an odimon.sk search result page → product Candidates.

    Scopes to ``.product-list__results``; each ``a.product-card`` is one product
    (href = absolute product URL, name from the card image's alt/title).
    Deduplicated by canonical URL. Cart/account links never carry the
    ``product-card`` class, so no path-exclusion list is needed.
    """
    soup = BeautifulSoup(html, "lxml")
    scope = soup.select_one(".product-list__results") or soup

    out: list[Candidate] = []
    seen: set[str] = set()
    for a in scope.select("a.product-card"):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        url, _ = urldefrag(urljoin(base_url + "/", href))
        if not url.startswith(base_url) or url in seen:
            continue
        seen.add(url)
        img = a.find("img")
        name = ((img.get("alt") if img else "") or (img.get("title") if img else "") or "").strip()
        out.append(Candidate(name=name, url=url))
    return out
