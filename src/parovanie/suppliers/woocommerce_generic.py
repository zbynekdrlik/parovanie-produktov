"""Generic WooCommerce (WordPress) search-result parser shared by several suppliers.

WooCommerce fulltext search (``?s=<query>&post_type=product``) renders results
server-side, but the page comes in TWO shapes depending on how many products
matched — so this parser handles a DUAL MODE:

(A) RESULTS LIST — several matches. The product loop lives in the WooCommerce
    results wrapper — ``ul.products`` (canonical) OR ``div.products`` (Flatsome's
    LOVTEK adds ``row …``; pyra's adds ``products-loop products-grid``; Divi/Extra's
    tatragoat uses the canonical ``ul.products columns-4``). Each card is
    ``li.product``/``div.product`` with a detail
    link (``a.woocommerce-LoopProduct-link`` / ``a.woocommerce-loop-product__link``
    / ``.product-title a``) and a title (``.woocommerce-loop-product__title`` /
    ``h2.product-title`` / ``p.product-title``).

(B) SINGLE-PRODUCT REDIRECT — an EXACT single match. WooCommerce/XStore 301/302s
    straight to the product detail page, which has NO ``div.products`` loop (the
    related-products block is a swiper carousel, NOT ``div.products``). We detect
    it via ``body.single-product`` / ``meta[og:type]=product`` and return EXACTLY
    ONE Candidate from ``link[rel=canonical]`` / ``og:url`` + ``h1.product_title``.
    We must NOT parse the related-products carousel on that page — a wrong link
    feeds auto-ordering.

Single-product mode is checked FIRST: on a detail page a stray related-products
``div.products`` (some themes render one) would otherwise be scraped as results.
Always ``urldefrag(urljoin(base_url + "/", href))``; dedup by URL; enforce the
``base_url + "/"`` host-boundary so a look-alike host or off-site card is dropped.
"""
from __future__ import annotations

from urllib.parse import urldefrag, urljoin

from bs4 import BeautifulSoup

from parovanie.models import Candidate

# Per-card detail-link selectors, tried in order: the loop anchor class differs by
# WooCommerce theme; the ``.product-title a`` heading link is the last resort.
_LINK_SELECTORS = (
    "a.woocommerce-LoopProduct-link[href]",
    "a.woocommerce-loop-product__link[href]",
    ".product-title a[href]",
)

# Per-card title selectors, tried in order (theme-dependent tag: <p> on Flatsome,
# <h2> on pyra's theme).
_TITLE_SELECTORS = (
    ".woocommerce-loop-product__title",
    "h2.product-title",
    "p.product-title",
)


def _clean(base_url: str, href: str | None, seen: set[str]) -> str | None:
    """Normalize a raw href → in-host, fragment-free, not-yet-seen absolute URL, else None."""
    href = (href or "").strip()
    if not href:
        return None
    url, _ = urldefrag(urljoin(base_url + "/", href))
    # host-boundary: ``base_url + "/"`` so a look-alike host (e.g. lovtek.sk.evil.com)
    # cannot satisfy a bare prefix match, and an off-site card is skipped.
    if not url.startswith(base_url + "/") or url in seen:
        return None
    return url


def _parse_single(soup: BeautifulSoup, base_url: str) -> list[Candidate]:
    """Single-product detail page → EXACTLY ONE Candidate (url from canonical/og:url,
    name from ``h1.product_title.entry-title`` / ``og:title``), or ``[]`` if the URL
    is missing or off-host. The related-products carousel is deliberately ignored.
    """
    href = None
    can = soup.select_one('link[rel="canonical"][href]')
    if can:
        href = can.get("href")
    if not href:
        ogu = soup.select_one('meta[property="og:url"]')
        href = ogu.get("content") if ogu else None
    url = _clean(base_url, href, set())
    if not url:
        return []
    h1 = soup.select_one("h1.product_title.entry-title")
    name = h1.get_text(strip=True) if h1 else ""
    if not name:
        ogt = soup.select_one('meta[property="og:title"]')
        name = (ogt.get("content") if ogt else "") or ""
    return [Candidate(name=name.strip(), url=url)]


def parse_search(html: str, base_url: str) -> list[Candidate]:
    """Parse a WooCommerce search page → product Candidates (dual mode).

    Single-product redirect (``body.single-product`` / ``og:type=product``) →
    exactly one Candidate. Results list → one Candidate per ``li.product`` in
    ``div.products``. No recognizable grid and not a product page → ``[]`` (never
    scrape the whole page, because a wrong link feeds auto-ordering). URLs are
    absolute, fragment-stripped, in-host, and deduplicated.
    """
    soup = BeautifulSoup(html, "lxml")

    # Mode B FIRST: a product detail page must yield only its own product, never the
    # related-products carousel that some themes render as a stray ``div.products``.
    body = soup.select_one("body")
    is_single = bool(body and "single-product" in (body.get("class") or []))
    if not is_single:
        ogt = soup.select_one('meta[property="og:type"]')
        is_single = bool(ogt and (ogt.get("content") or "").strip() == "product")
    if is_single:
        return _parse_single(soup, base_url)

    # Mode A: the results grid MUST be present; degrade to no results rather than
    # scrape nav / autocomplete / cross-sell links scattered across the page.
    # WooCommerce's canonical results wrapper is ``ul.products``; some themes
    # (Flatsome/LOVTEK, pyra) render a ``div.products`` instead — accept both.
    scope = soup.select_one("ul.products") or soup.select_one("div.products")
    if scope is None:
        return []

    out: list[Candidate] = []
    seen: set[str] = set()
    for card in scope.select("li.product, div.product"):
        a = None
        for sel in _LINK_SELECTORS:
            a = card.select_one(sel)
            if a:
                break
        if a is None:
            continue
        url = _clean(base_url, a.get("href"), seen)
        if not url:
            continue
        seen.add(url)
        name = ""
        for sel in _TITLE_SELECTORS:
            t = card.select_one(sel)
            if t:
                name = t.get_text(strip=True)
                break
        out.append(Candidate(name=name.strip(), url=url))
    return out
