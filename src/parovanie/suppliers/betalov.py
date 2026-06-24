"""BETALOV (huntingshop.eu) search-result parser.

huntingshop.eu is a Nette custom app.  The search page at
    https://www.huntingshop.eu/hladanie?search=<q>
delivers an **empty** `#snippet--productList` div in the initial HTML and
populates it via a Nette AJAX call (POST, X-Requested-With: XMLHttpRequest,
body `page=1`).  After the AJAX response is injected the DOM looks like:

    <div id="snippet--productList" data-ajax-append>
        <div class="tab-pane … active" id="home">
            <div class="row grid-view">
                <div class="product-col …">
                    …
                    <a href="/product-slug" class="mh-100">   ← thumbnail link
                    …
                    <h3 class="product-title …">
                        <a href="product-slug">Name</a>       ← title link (no leading /)
                    </h3>
                    …
                </div>
                …
            </div>
        </div>
        <!-- also a list-view tab with the same products; we dedup by URL -->
    </div>

The fixture HTML files have this injected content present because the server
serves it server-side when a valid session cookie is present (a Nette quirk:
the first anonymous request renders an empty snippet, while a request that
carries a fresh session gets SSR'd results).

parse_search() accepts the full page HTML (as produced by a session-carrying
curl / requests call) OR a partial snippet HTML and returns Candidates for the
actual search results — never the navigation bar, the featured-product carousel
(which lives outside #snippet--productList), or utility routes.
"""
from __future__ import annotations

from urllib.parse import urljoin, urldefrag

from bs4 import BeautifulSoup

from parovanie.models import Candidate

# Non-product routes to exclude (Nette app navigation / account / utility paths).
_EXCLUDE_PREFIXES = (
    "/kosik",
    "/prihlasenie",
    "/registracia",
    "/kontakt",
    "/hladanie",
    "/obchodne",
    "/ochrana",
    "/blog",
    "/clanok",
    "/kategoria",
    "/znacka",
    "/akcia",
    "/akcie",
    "/novinky",
    "/assets",
    "/assets2",
    "/upload",
    "/vyrobca",
)


def parse_search(html: str, base_url: str) -> list[Candidate]:
    """Parse a huntingshop.eu search result page and return product Candidates.

    Scopes to ``#snippet--productList`` (the Nette AJAX snippet container that
    holds ONLY search results) to exclude the homepage featured-product carousel
    and the navigation menu that also appear on the page.

    Within the snippet, extracts the thumbnail link ``a.mh-100`` from each
    ``.product-col`` card and its associated title text.  Products appear twice
    in the snippet (grid view + list view); duplicates are suppressed by URL.

    Args:
        html: Full page HTML or snippet HTML from a huntingshop.eu search page.
        base_url: Site root, e.g. ``"https://www.huntingshop.eu"``.

    Returns:
        Deduplicated list of Candidate objects with absolute URLs.
    """
    soup = BeautifulSoup(html, "lxml")

    # Scope to the Nette search-results snippet; fall back to whole document
    # if somehow the snippet ID is absent (e.g. pure snippet HTML).
    scope = soup.select_one("#snippet--productList") or soup

    out: list[Candidate] = []
    seen: set[str] = set()

    for card in scope.select(".product-col"):
        # Prefer the thumbnail link (always has a leading slash → well-formed).
        thumb = card.select_one("a.mh-100")
        href = (thumb.get("href") or "") if thumb else ""

        # Fall back to the title link (no leading slash on this site).
        if not href:
            title_a = card.select_one(".product-title a")
            href = (title_a.get("href") or "") if title_a else ""

        if not href:
            continue

        # Normalise to an absolute URL, strip fragments.
        url, _ = urldefrag(urljoin(base_url + "/", href.lstrip("/")))
        if not url.startswith(base_url):
            continue

        # Extract the path for exclusion checks.
        path = url[len(base_url):]
        if any(path.startswith(p) for p in _EXCLUDE_PREFIXES):
            continue

        # Dedup by canonical URL.
        if url in seen:
            continue
        seen.add(url)

        # Extract the name from the product-title anchor text.
        title_a = card.select_one(".product-title a")
        name = (title_a.get_text(strip=True) if title_a else "") or ""

        out.append(Candidate(name=name, url=url))

    return out
