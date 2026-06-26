from parovanie.models import Product
from parovanie.verify import extract_page, code_verdict, merge_verdict

WET = open("tests/fixtures/wetland_product_page.html", encoding="utf-8",
           errors="replace").read()

HUNT = open("tests/fixtures/huntingshop_product_page.html", encoding="utf-8",
            errors="replace").read()


def test_extract_page_has_title():
    """Wetland: extracted title must contain 'deerhunter' or 'colton'."""
    page = extract_page(WET)
    assert page["title"]
    assert "deerhunter" in page["title"].lower() or "colton" in page["title"].lower()


def test_extract_page_huntingshop_has_title():
    """Huntingshop: extracted title must contain 'clausen'."""
    page = extract_page(HUNT)
    assert page["title"]
    assert "clausen" in page["title"].lower()


def test_extract_page_generic_fallbacks_no_h1():
    """No <h1> and only generic selectors: title falls back to <title> (site name
    stripped), code to itemprop=sku, price to itemprop=price (cross-layout paths)."""
    html = ('<html><head><title>Produkt XY | Forestshop</title></head><body>'
            '<span itemprop="sku">ABC123</span>'
            '<span itemprop="price">12,50</span></body></html>')
    page = extract_page(html)
    assert page["title"] == "Produkt XY"
    assert page["code"] == "ABC123"
    assert page["price"] == "12,50"


def test_code_verdict_unsure_when_no_external_code():
    p = Product("BETALOV", "k", None, "X", ["c"])
    verdict, reason = code_verdict(p, {"title": "čokoľvek", "code": None})
    assert verdict == "UNSURE"


def test_code_verdict_ok_when_code_present():
    page = {"title": "HART RANDO XHP OB570", "code": "OB570", "price": "99 €"}
    p = Product("BETALOV", "k", "OB570", "HART RANDO XHP", ["c"])
    verdict, _ = code_verdict(p, page)
    assert verdict == "OK"


def test_code_verdict_unsure_when_absent():
    page = {"title": "Iný produkt", "code": "ZZ9", "price": None}
    p = Product("BETALOV", "k", "OB570", "HART RANDO XHP", ["c"])
    verdict, _ = code_verdict(p, page)
    assert verdict == "UNSURE"


def test_code_verdict_unsure_on_substring_of_longer_number():
    # Regression: code '376' must NOT verify-OK against a page titled '...3760'
    # (a raw `code in hay` substring test wrongly rubber-stamped it).
    page = {"title": "Lampáš model 3760", "code": None, "price": None}
    p = Product("BETALOV", "k", "376", "Lampáš", ["c"])
    verdict, _ = code_verdict(p, page)
    assert verdict == "UNSURE", "code '376' wrongly matched '3760'"


def test_code_verdict_ok_on_delimited_short_code():
    page = {"title": "Lampáš model 376 LED", "code": "376", "price": None}
    p = Product("BETALOV", "k", "376", "Lampáš", ["c"])
    verdict, _ = code_verdict(p, page)
    assert verdict == "OK"


def test_merge_verdict_fills_columns():
    rows = [{"supplier": "BETALOV", "name": "X", "verdict": "", "verdict_reason": "",
             "attempts": ""}]
    merged = merge_verdict(rows, {0: {"verdict": "OK", "verdict_reason": "code", "attempts": 1}})
    assert merged[0]["verdict"] == "OK" and merged[0]["attempts"] == 1
