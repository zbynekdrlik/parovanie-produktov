from __future__ import annotations
from urllib.parse import urljoin, urldefrag
from bs4 import BeautifulSoup
from parovanie.models import Candidate


def parse_search(html: str, base_url: str) -> list[Candidate]:
    """Parse wetland.sk (PrestaShop) search result page.

    Selector: `div.product-miniature__title a.link` — each result card has a
    <div class="product-miniature__title"> containing a <a class="link"> with
    the product name as text and the canonical product URL as href.
    The thumbnail link (a.product-miniature__link) points to the same URL with
    a #/variant fragment; we strip the fragment and dedup so each product
    appears once regardless of which default variant the page chose.
    """
    soup = BeautifulSoup(html, "lxml")
    out: list[Candidate] = []
    seen: set[str] = set()

    # Primary selector: title anchors inside product cards (contain name text)
    anchors = soup.select("div.product-miniature__title a.link")
    if not anchors:
        # Fallback: thumbnail links on the product card image
        anchors = soup.select("a.product-miniature__link")

    for a in anchors:
        href = a.get("href")
        if not href:
            continue
        url, _ = urldefrag(urljoin(base_url, href))
        if not url.startswith(base_url):
            continue
        if url in seen:
            continue
        seen.add(url)
        name = a.get_text(strip=True) or a.get("title") or ""
        out.append(Candidate(name=name.strip(), url=url))

    return out
