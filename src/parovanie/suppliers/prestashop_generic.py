"""Generic PrestaShop search-result parser shared by several suppliers (1.7 + 1.6).

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

PrestaShop **1.6** (HUNTINGLAND, huntingland.sk) uses DIFFERENT markup: the
results grid is ``#product_list`` / ``.product_list``, cards are
``div.product-container``, and the link+name is a single ``a.product-name`` (with
a ``title`` attribute; the href carries a ``?search_query=…&results=N`` tracking
query that we strip). ``parse_search`` tries 1.7 first and falls back to the 1.6
branch only when no 1.7 grid is present, so 1.7 behaviour is untouched.
"""
from __future__ import annotations

from urllib.parse import urldefrag, urljoin, urlsplit, urlunsplit

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


def _clean_url(href: str, base_url: str) -> str | None:
    """Resolve ``href`` against ``base_url``, drop the ``#`` fragment, and enforce
    the ``base_url + "/"`` host boundary. Returns ``None`` for empty / off-host."""
    href = (href or "").strip()
    if not href:
        return None
    url, _ = urldefrag(urljoin(base_url + "/", href))
    if not url.startswith(base_url + "/"):
        return None
    return url


def _strip_query(url: str) -> str:
    """Drop a ``?query`` (PS-1.6 appends ``?search_query=…&results=N`` tracking
    params to result hrefs) so the same product maps to one stable URL."""
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, p.path, "", ""))


def _parse_16(soup: BeautifulSoup, base_url: str) -> list[Candidate]:
    """PrestaShop **1.6** fallback: ``#product_list``/``.product_list`` grid,
    ``div.product-container`` cards, ``a.product-name`` link+name. The 1.6 result
    hrefs carry a ``?search_query=…`` tracking query → stripped for a stable URL.
    Returns ``[]`` if the 1.6 grid is absent (never scrape the whole page)."""
    scope = soup.select_one("#product_list") or soup.select_one(".product_list")
    if scope is None:
        return []
    out: list[Candidate] = []
    seen: set[str] = set()
    for card in scope.select("div.product-container"):
        a = card.select_one("a.product-name[href]") or card.select_one("a.product-name")
        if not a:
            continue
        url = _clean_url(a.get("href") or "", base_url)
        if url is None:
            continue
        url = _strip_query(url)
        if url in seen:
            continue
        seen.add(url)
        name = a.get_text(strip=True) or (a.get("title") or "").strip()
        if not name:
            img = card.select_one("img[alt]")
            name = ((img.get("alt") if img else "") or "").strip()
        out.append(Candidate(name=name, url=url))
    return out


def parse_search(html: str, base_url: str) -> list[Candidate]:
    """Parse a PrestaShop search page (1.7, else 1.6) → product Candidates.

    Tries the **1.7** markup first: scopes to ``#js-product-list`` (falling back
    to ``.products``); each ``article.product-miniature`` yields one Candidate —
    the product detail URL (any ``#`` variant fragment stripped) and the name from
    the first matching title selector (falling back to ``img[alt]``). If no 1.7
    grid is present, falls back to the **1.6** branch (``#product_list`` /
    ``div.product-container`` / ``a.product-name``, with the 1.6 tracking query
    stripped). If neither markup is recognized, returns ``[]`` rather than scrape
    the whole page — a wrong link feeds auto-ordering. Deduplicated by URL.
    """
    soup = BeautifulSoup(html, "lxml")
    # 1.7 grid MUST be present for the 1.7 path; degrade to the 1.6 fallback rather
    # than scrape nav / autocomplete / cross-sell links scattered across the page.
    scope = soup.select_one("#js-product-list") or soup.select_one(".products")
    if scope is None:
        return _parse_16(soup, base_url)

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
        url = _clean_url(a.get("href") or "", base_url)
        if url is None or url in seen:
            continue
        seen.add(url)
        name = a.get_text(strip=True)
        if not name:
            img = a.select_one("img[alt]")
            name = (img.get("alt") if img else "") or ""
        out.append(Candidate(name=name.strip(), url=url))
    return out
