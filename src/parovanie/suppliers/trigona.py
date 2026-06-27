"""TRIGONA (trigona.sk) search-result parser.

trigona.sk runs Unisite. The plain ``index.php?page=18228`` search form silently
redirects to a generic listing; the REAL filtering search is the SEO path URL the
JS autosuggest points at:
    /eshop/searchstring/<q>/searchtype/all/searchsubmit/1/action/search/cid/0.xhtml
which renders static HTML. Each result is a card:

    <div class="Product">
        ... <a href="https://www.trigona.sk/eshop/<slug>/p-<id>.xhtml"> ...
        <... class="ProductName">NITECORE MT1C PRO</...>
    </div>

(The homepage's ``h3.product-name`` block is a featured/default carousel — NOT
the search results — so we parse the ``div.Product`` results cards here.)
"""
from __future__ import annotations

from urllib.parse import urldefrag, urljoin

from bs4 import BeautifulSoup

from parovanie.models import Candidate


def parse_search(html: str, base_url: str) -> list[Candidate]:
    soup = BeautifulSoup(html, "lxml")
    out: list[Candidate] = []
    seen: set[str] = set()
    for card in soup.select("div.Product"):
        a = card.find("a", href=lambda h: h and "/p-" in h and h.endswith(".xhtml"))
        if not a:
            continue
        url, _ = urldefrag(urljoin(base_url + "/", (a.get("href") or "").strip()))
        if not url.startswith(base_url) or url in seen:
            continue
        seen.add(url)
        nm = card.select_one(".ProductName, .product-name, h2, h3")
        name = ((nm.get_text(strip=True) if nm else "") or a.get("title") or "").strip()
        out.append(Candidate(name=name, url=url))
    return out
