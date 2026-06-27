"""Tests for the GRUBE (grube.sk, Shopware) search-result parser. Synthetic HTML
(the live results are Playwright-rendered, ~500 KB; the parser logic is selector
+ slug based, exercised here without a giant fixture)."""
from parovanie.suppliers.grube import parse_search

BASE = "https://www.grube.sk"
HTML = """
<div class="product-box product-box--hero">
  <a href="/p/polovnicke-nohavice-percussion-rambouillet-original-warm/434690/?q=Percussion#itemId=4346901812"><img alt="x"></a>
</div>
<div class="product-box">
  <a href="/p/percussion-smock-bristol/200820/?q=Percussion#itemId=2008202826">434690 ...</a>
</div>
<div class="product-box"><a href="/p/percussion-smock-bristol/200820/">dup</a></div>
<a href="/account/login">nav</a>
"""


def test_parses_boxes_deslugs_name_strips_query():
    c = parse_search(HTML, BASE)
    assert len(c) == 2                                   # dup URL collapsed
    assert c[0].url == BASE + "/p/polovnicke-nohavice-percussion-rambouillet-original-warm/434690/"
    assert "rambouillet" in c[0].name.lower() and "?" not in c[0].url
    assert all(x.url.startswith(BASE + "/p/") for x in c)


def test_ignores_nav_links():
    assert not any("/account" in x.url for x in parse_search(HTML, BASE))
