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


def test_merge_verdict_fills_columns():
    rows = [{"supplier": "BETALOV", "name": "X", "verdict": "", "verdict_reason": "",
             "attempts": ""}]
    merged = merge_verdict(rows, {0: {"verdict": "OK", "verdict_reason": "code", "attempts": 1}})
    assert merged[0]["verdict"] == "OK" and merged[0]["attempts"] == 1
