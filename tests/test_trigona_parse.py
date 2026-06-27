"""Tests for the TRIGONA (trigona.sk, Unisite) search-result parser.

Fixture: trigona_search_mt1c.html — live path-search `searchstring/Nitecore MT1C`
(returns the single NITECORE MT1C PRO product). Confirms div.Product results-card
parsing and that the SEO path search filters correctly.
"""
import os

from parovanie.suppliers.trigona import parse_search

_F = os.path.join(os.path.dirname(__file__), "fixtures", "trigona_search_mt1c.html")
HTML = open(_F, encoding="utf-8", errors="replace").read()
BASE = "https://www.trigona.sk"


def test_returns_filtered_product_with_name_and_url():
    cands = parse_search(HTML, BASE)
    assert len(cands) >= 1
    assert all(c.url.startswith(BASE + "/eshop/") and "/p-" in c.url for c in cands), [c.url for c in cands]
    assert all(c.name for c in cands)


def test_mt1c_search_returns_the_mt1c_product():
    cands = parse_search(HTML, BASE)
    assert any("mt1c" in c.name.lower() for c in cands), [c.name for c in cands]


def test_no_duplicate_urls():
    cands = parse_search(HTML, BASE)
    urls = [c.url for c in cands]
    assert len(urls) == len(set(urls))
