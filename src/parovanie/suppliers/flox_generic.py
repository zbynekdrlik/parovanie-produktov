"""Generic flox.sk search-result parser shared by flox-platform suppliers.

ROY (roy.sk) runs on the flox.sk e-commerce platform (vevo.flox.sk CDN,
``floxConsent`` / ``SSID`` session cookie). Its fulltext search renders results
server-side (static SSR HTML) at
    https://www.roy.sk/e/search?word=<query>
The ``SearchClient`` already warms up the session with a homepage GET before the
first search, so the parser needs nothing special here. This module is the
platform-level parser: a single ``parse_search`` reusable by any flox supplier —
they differ only in base URL, the markup is the same.

Results live inside ``div.productListSearch``; each ``li.productListItemJS`` card
carries a ``data-href="/p-<id>/<slug>"`` attribute (the canonical product URL) and
its name in the title anchor ``h3.s1-listProductTitle a`` (fallback: the card's
``data-img-alt`` / an image ``title``/``alt``).

Scoping rule (auto-ordering safety — a wrong link is worse than no link): we scope
STRICTLY to ``div.productListSearch``. The raw page also carries a cross-sell /
"recommended accessories" carousel of ``/p-`` links OUTSIDE this container (a
whole-page ``a[href^="/p-"]`` scan would grab batteries/mounts), so if the results
container is absent we return ``[]`` rather than scrape every ``/p-`` link on the
page — a "no recognizable results container" page never yields a stray link to
auto-ordering.
"""
from __future__ import annotations

from urllib.parse import urldefrag, urljoin

from bs4 import BeautifulSoup

from parovanie.models import Candidate


def parse_search(html: str, base_url: str) -> list[Candidate]:
    """Parse a flox (roy.sk) ``/e/search`` page → product Candidates.

    Scopes to ``div.productListSearch`` (the real results container); without it,
    returns ``[]`` rather than scraping the page-wide cross-sell ``/p-`` carousel —
    a wrong link feeds auto-ordering, so "no recognizable container" means no match.
    Each ``li.productListItemJS`` card yields one Candidate: URL from the card's
    ``data-href`` (fallback: the title anchor's ``href``), name from
    ``h3.s1-listProductTitle a`` (fallback: the card's ``data-img-alt`` or an
    image ``title``/``alt``). URLs are absolute, ``#`` fragment stripped,
    deduplicated, and the ``base_url + "/"`` host-boundary is enforced.
    """
    soup = BeautifulSoup(html, "lxml")
    # The results container MUST be present; without it, degrade to no results rather
    # than scrape the page-wide cross-sell ``/p-`` carousel — a wrong link feeds
    # auto-ordering, so "no recognizable container" means no match.
    scope = soup.select_one("div.productListSearch")
    if scope is None:
        return []

    out: list[Candidate] = []
    seen: set[str] = set()
    for card in scope.select("li.productListItemJS"):
        # The canonical product URL is the card's ``data-href``; fall back to the
        # title/thumbnail anchor href only if the attribute is absent.
        href = (card.get("data-href") or "").strip()
        if not href:
            a = (card.select_one("h3.s1-listProductTitle a[href]")
                 or card.select_one("a.productListLink[href]")
                 or card.select_one("a[href]"))
            href = (a.get("href") or "").strip() if a else ""
        if not href:
            continue
        url, _ = urldefrag(urljoin(base_url + "/", href))
        # host-boundary check: ``base_url + "/"`` so a look-alike host
        # (e.g. www.roy.sk.evil.com) cannot satisfy a bare prefix match.
        if not url.startswith(base_url + "/") or url in seen:
            continue
        seen.add(url)
        nm = (card.select_one("h3.s1-listProductTitle a")
              or card.select_one("a.s1-gridItem-name")
              or card.select_one("h3.s1-listProductTitle"))
        name = (nm.get_text(strip=True) if nm else "").strip()
        if not name:
            img = card.select_one("img[title], img[alt]")
            if img:
                name = ((img.get("title") or img.get("alt") or "")).strip()
        out.append(Candidate(name=name, url=url))
    return out
