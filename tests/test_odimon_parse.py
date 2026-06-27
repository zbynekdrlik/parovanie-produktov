"""Tests for the ODIMON (odimon.sk) search-result parser.

Fixture:
  odimon_search_ah11.html — live "code" query `term=AH11` (an Alpenheat insole
      supplier code). 20 product cards. Exercises the static-HTML BUXUS card
      parser and confirms code-search returns the right product near the top.
"""
import os

from parovanie.suppliers.odimon import parse_search

_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "odimon_search_ah11.html")
HTML = open(_FIXTURE, encoding="utf-8", errors="replace").read()
BASE = "https://www.odimon.sk"


def test_code_search_returns_products():
    cands = parse_search(HTML, BASE)
    assert len(cands) >= 5, f"expected several products, got {len(cands)}"
    # every candidate is an absolute odimon product URL with a name
    assert all(c.url.startswith(BASE + "/") for c in cands), [c.url for c in cands]
    assert all(c.name for c in cands), "a candidate is missing its name"


def test_no_duplicate_urls():
    cands = parse_search(HTML, BASE)
    urls = [c.url for c in cands]
    assert len(urls) == len(set(urls)), "duplicate product URLs leaked through"


def test_code_ah11_returns_the_alpenheat_insole_on_top():
    # AH11 is the Alpenheat HotSole insole code → the matching product must be the
    # first result (code-search precision the pairing relies on).
    cands = parse_search(HTML, BASE)
    assert "alpenheat" in cands[0].name.lower(), cands[0].name


def test_excludes_filter_facets_and_cart():
    # producer-filter facets carry `?...producer=` and the mini-cart links to
    # /obsah-kosika — neither is an a.product-card, so none must appear.
    cands = parse_search(HTML, BASE)
    assert not any("producer=" in c.url for c in cands)
    assert not any("obsah-kosika" in c.url for c in cands)
