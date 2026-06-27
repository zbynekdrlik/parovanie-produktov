"""GRUBE (grube.sk) search-result parser.

grube.sk is a Shopware shop. The search page /search/?q=<q> renders its results
ONLY in a real browser (a plain requests fetch returns 0 product boxes — bot/JS
gated), so the HTML must be produced by a headless-Playwright fetcher
(scripts/gather_grube.py); this parser then reads that rendered HTML.

Each result is a ``.product-box`` whose product link is ``a[href*="/p/"]`` of the
form ``/p/<slug>/<id>/?q=...#itemId=...``. The clean product name is the
de-slugified ``<slug>`` (the box text mixes in code/price/"Porovnaj"/sizes).
"""
from __future__ import annotations

import re
from urllib.parse import urldefrag, urljoin

from bs4 import BeautifulSoup

from parovanie.models import Candidate

_SLUG = re.compile(r"/p/([^/]+)/\d+")


def _name_from_url(url: str) -> str:
    m = _SLUG.search(url)
    return m.group(1).replace("-", " ").strip() if m else ""


def parse_search(html: str, base_url: str) -> list[Candidate]:
    soup = BeautifulSoup(html, "lxml")
    out: list[Candidate] = []
    seen: set[str] = set()
    for box in soup.select(".product-box"):
        a = box.select_one('a[href*="/p/"]')
        if not a:
            continue
        url, _ = urldefrag(urljoin(base_url, (a.get("href") or "")))
        url = url.split("?", 1)[0]               # drop the ?q= search context
        if not url.startswith(base_url) or url in seen:
            continue
        seen.add(url)
        out.append(Candidate(name=_name_from_url(url), url=url))
    return out
