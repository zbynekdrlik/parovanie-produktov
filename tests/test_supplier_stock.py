"""Pure-logic tests for the „Dodávateľský sklad" scraper core (#106).

Hermetic: inline HTML fixtures for the three static tiers (JSON-LD / meta / text),
mocked LLM JSON, no network. Proves the static tier resolves what it can and the
LLM is invoked ONLY when it can't (need_llm), plus the stale-skip + link source.
"""
from datetime import datetime, timedelta, timezone

import pytest

from parovanie import config, supplier_stock as ss

# ── availability classifiers ──────────────────────────────────────────────────
@pytest.mark.parametrize("text,expected", [
    ("Dostupnosť: Skladom", True),
    ("na sklade — expedujeme ihneď", True),
    ("Skladem u dodavatele", True),
    ("Momentálne vypredané", False),
    ("Produkt je nedostupný", False),
    ("Není skladem", False),
    ("out of stock", False),
    ("nejasný popis produktu", None),
    ("", None),
    # out-of-stock wins when both signals appear (decisive negative)
    ("Skladom? Nie — vypredané", False),
])
def test_classify_availability(text, expected):
    assert ss.classify_availability(text) is expected


@pytest.mark.parametrize("token,expected", [
    ("https://schema.org/InStock", True),
    ("http://schema.org/OutOfStock", False),
    ("InStock", True),
    ("LimitedAvailability", True),
    ("PreOrder", True),
    ("SoldOut", False),
    ("Discontinued", False),
    ("in stock", True),
    ("out of stock", False),
    ("", None),
    ("SomethingElse", None),
])
def test_availability_from_schema(token, expected):
    assert ss.availability_from_schema(token) is expected


@pytest.mark.parametrize("raw,expected", [
    ("59,90", 59.90), ("59.90", 59.90), ("1 299,00 €", 1299.0),
    ("1.299,00", 1299.0), ("1,299.00", 1299.0), (129.9, 129.9), (100, 100.0),
    ("", None), (None, None), ("zadarmo", None),
])
def test_to_price(raw, expected):
    assert ss._to_price(raw) == expected


# ── JSON-LD tier ──────────────────────────────────────────────────────────────
JSONLD_HTML = """<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"Poľovnícka bunda",
 "offers":{"@type":"Offer","price":"129.90","priceCurrency":"EUR",
 "availability":"https://schema.org/InStock"}}
</script></head><body>obsah</body></html>"""

JSONLD_GRAPH_OOS = """<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@graph":[
 {"@type":"WebSite","name":"Shop"},
 {"@type":"Product","name":"Nôž","offers":{"@type":"Offer","price":49,
  "priceCurrency":"CZK","availability":"https://schema.org/OutOfStock"}}]}
</script></head><body></body></html>"""


def test_extract_jsonld_instock():
    r = ss.extract_jsonld(JSONLD_HTML)
    assert r == {"available": True, "price": 129.90, "currency": "EUR"}


def test_extract_jsonld_graph_outofstock():
    r = ss.extract_jsonld(JSONLD_GRAPH_OOS)
    assert r == {"available": False, "price": 49.0, "currency": "CZK"}


def test_extract_jsonld_absent():
    assert ss.extract_jsonld("<html><body>nič</body></html>") == {
        "available": None, "price": None, "currency": ""}


def test_extract_jsonld_ignores_unparseable_block():
    html = '<script type="application/ld+json">{ broken json,,, </script>'
    assert ss.extract_jsonld(html)["available"] is None


# ── meta tier ─────────────────────────────────────────────────────────────────
META_HTML = """<html><head>
<meta property="product:availability" content="in stock">
<meta property="product:price:amount" content="59,90">
<meta property="product:price:currency" content="EUR">
</head><body></body></html>"""

META_HTML_CONTENT_FIRST = (
    '<meta content="out of stock" property="og:availability">'
    '<meta content="19.99" property="og:price:amount">')


def test_extract_meta_instock():
    assert ss.extract_meta(META_HTML) == {
        "available": True, "price": 59.90, "currency": "EUR"}


def test_extract_meta_content_before_property_and_oos():
    r = ss.extract_meta(META_HTML_CONTENT_FIRST)
    assert r["available"] is False and r["price"] == 19.99


# ── extract_static: tier priority + verified-domain text gate ─────────────────
TEXT_ONLY_HTML = """<html><head><title>Produkt</title></head>
<body><div class="stock">Dostupnosť: Skladom</div><p>cena na dopyt</p></body></html>"""

TEXT_ONLY_OOS_HTML = """<html><body><span>Momentálne vypredané</span></body></html>"""


def test_extract_static_prefers_jsonld():
    r = ss.extract_static(JSONLD_HTML, "https://www.example-supplier.com/p/1")
    assert r["available"] is True and r["price"] == 129.90
    assert r["extractedBy"] == "jsonld"
    assert ss.need_llm(r) is False


def test_extract_static_meta_when_no_jsonld():
    r = ss.extract_static(META_HTML, "https://www.example-supplier.com/p/1")
    assert r["available"] is True and r["price"] == 59.90 and r["extractedBy"] == "meta"


def test_extract_static_text_only_on_verified_domain():
    # zubicek.cz is a verified static-text domain → keyword classifier trusted
    r = ss.extract_static(TEXT_ONLY_HTML, "https://www.zubicek.cz/vyhledavani/produkt")
    assert r["available"] is True and r["extractedBy"] == "text"
    # price still unknown here → need_llm stays True (availability OR price missing)
    assert r["price"] is None and ss.need_llm(r) is True


def test_extract_static_text_oos_on_verified_domain():
    r = ss.extract_static(TEXT_ONLY_OOS_HTML, "https://virginiashop.sk/vyhladavanie/x")
    assert r["available"] is False and r["extractedBy"] == "text"


def test_extract_static_text_NOT_used_on_unverified_domain():
    # same page text, but an unverified domain → keyword classifier must NOT fire,
    # so availability stays unknown and the LLM fallback is needed.
    r = ss.extract_static(TEXT_ONLY_HTML, "https://www.example-supplier.com/p/1")
    assert r["available"] is None and r["extractedBy"] is None
    assert ss.need_llm(r) is True


# ── page_text ─────────────────────────────────────────────────────────────────
def test_page_text_strips_scripts_styles_tags():
    html = ("<html><head><style>.x{color:red}</style>"
            "<script>var secret=1;</script></head>"
            "<body><h1>Bunda</h1><p>Skladom &amp; pripravené</p></body></html>")
    txt = ss.page_text(html)
    assert "secret" not in txt and "color:red" not in txt
    assert "Bunda" in txt and "Skladom & pripravené" in txt


def test_page_text_truncates():
    assert len(ss.page_text("<p>" + "a" * 9000 + "</p>", limit=7000)) == 7000


# ── LLM prompt + parse ────────────────────────────────────────────────────────
def test_build_llm_messages_shape():
    msgs = ss.build_llm_messages("text stránky", "https://x/p")
    assert msgs[0]["role"] == "system" and "JSON" in msgs[0]["content"]
    assert msgs[1]["role"] == "user" and "https://x/p" in msgs[1]["content"]


def test_parse_llm_json_valid():
    out = ss.parse_llm_json(
        '{"available": true, "price": "59,90", "currency": "eur",'
        ' "availabilityText": "Skladom", "variants": [{"size":"M","available":true}]}')
    assert out["available"] is True and out["price"] == 59.90
    assert out["currency"] == "EUR" and out["variants"] == [{"size": "M", "available": True}]


def test_parse_llm_json_code_fence_and_null():
    out = ss.parse_llm_json('```json\n{"available": null, "price": null, "currency": ""}\n```')
    assert out["available"] is None and out["price"] is None and out["variants"] == []


def test_parse_llm_json_invalid_raises():
    with pytest.raises(ValueError):
        ss.parse_llm_json("toto nie je json")
    with pytest.raises(ValueError):
        ss.parse_llm_json("[1,2,3]")            # not an object


# ── stale-skip ────────────────────────────────────────────────────────────────
def test_is_recently_checked():
    now = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
    fresh = {"ok": True, "checkedAt": (now - timedelta(hours=2)).isoformat()}
    old = {"ok": True, "checkedAt": (now - timedelta(hours=30)).isoformat()}
    errored = {"ok": False, "checkedAt": (now - timedelta(hours=1)).isoformat()}
    assert ss.is_recently_checked(fresh, now, 20) is True
    assert ss.is_recently_checked(old, now, 20) is False
    assert ss.is_recently_checked(errored, now, 20) is False   # errors always retried
    assert ss.is_recently_checked(None, now, 20) is False
    assert ss.is_recently_checked({"ok": True, "checkedAt": ""}, now, 20) is False


def test_is_recently_checked_naive_timestamp():
    now = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
    naive = {"ok": True, "checkedAt": (now - timedelta(hours=1)).replace(tzinfo=None).isoformat()}
    assert ss.is_recently_checked(naive, now, 20) is True


# ── links from export ─────────────────────────────────────────────────────────
EXPORT_CSV = (
    "code;pairCode;name;supplier;productVisibility;internalNote\r\n"
    "1/M;P1;Bunda;BETALOV;visible;https://www.huntingshop.eu/p/bunda\r\n"
    "1/L;P1;Bunda;BETALOV;visible;https://www.huntingshop.eu/p/bunda\r\n"   # same link → deduped
    "2/S;P2;Nôž;;visible;https://www.zubicek.cz/p/noz\r\n"                  # supplier via host
    "3/X;P3;Skryté;BETALOV;hidden;https://www.huntingshop.eu/p/skryte\r\n"  # not visible → dropped
    "4/Y;P4;Text;BETALOV;visible;betalov.sk\r\n"                           # not http → dropped
)


def test_links_from_export_filters_dedupes_aggregates():
    links = ss.links_from_export(EXPORT_CSV, config.SUPPLIERS)
    by = {lk["link"]: lk for lk in links}
    assert set(by) == {"https://www.huntingshop.eu/p/bunda", "https://www.zubicek.cz/p/noz"}
    bunda = by["https://www.huntingshop.eu/p/bunda"]
    assert bunda["supplier"] == "BETALOV" and bunda["codes"] == ["1/M", "1/L"]
    assert bunda["count"] == 2 and bunda["name"] == "Bunda"
    # supplier inferred from the URL host when the export column is blank
    assert by["https://www.zubicek.cz/p/noz"]["supplier"] == "ZUBÍČEK"


def test_links_from_export_visible_only_toggle():
    links = ss.links_from_export(EXPORT_CSV, config.SUPPLIERS, visible_only=False)
    assert any(lk["link"].endswith("/skryte") for lk in links)   # hidden now included


def test_links_from_export_empty():
    assert ss.links_from_export("", config.SUPPLIERS) == []
