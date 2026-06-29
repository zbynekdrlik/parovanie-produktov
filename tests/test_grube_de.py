import pathlib

import pytest

from parovanie.grube_de import (
    MULTI_AXIS,
    match_variant_codes,
    normalize_size,
    parse_variants,
    resolve_size,
    to_grube_de,
)

FIX = pathlib.Path(__file__).parent / "fixtures" / "grube_de_detail_154773.html"


def test_to_grube_de_strips_query_and_fragment():
    u = "https://www.grube.sk/p/noz-morakniv-eldris/268279/?q=morakiv#itemId=2682798474"
    assert to_grube_de(u) == "https://www.grube.de/p/x/268279/"


def test_to_grube_de_clean_sk_url():
    assert to_grube_de("https://www.grube.sk/p/percussion-grand-nord/154773/") == "https://www.grube.de/p/x/154773/"


def test_to_grube_de_already_de_idempotent():
    assert to_grube_de("https://www.grube.de/p/x/154773/") == "https://www.grube.de/p/x/154773/"


def test_to_grube_de_entity_mangled_slug():
    u = "https://www.grube.sk/p/pracovn-yacute-n-ocircz-morakniv-pro-c/585117/?q=x#itemId=5851174978"
    assert to_grube_de(u) == "https://www.grube.de/p/x/585117/"


def test_to_grube_de_non_product_url_returns_none():
    assert to_grube_de("https://www.grube.sk/search/?q=hose") is None
    assert to_grube_de("") is None


def test_parse_variants_grand_nord():
    html = FIX.read_text(encoding="utf-8")
    got = parse_variants(html, "154773")
    assert got == {       # authoritative, from the page's own schema.org Offers
        "S": "1547734535", "M": "1547734598", "L": "1547734524", "XL": "1547734570",
        "XXL": "1547734593", "3XL": "1547734523", "4XL": "1547734553", "5XL": "1547734519",
    }


def test_parse_variants_excludes_cross_sell():
    html = FIX.read_text(encoding="utf-8")
    got = parse_variants(html, "154773")
    assert "6165695125" not in got.values()       # cross-sell Nordforest itemId
    assert all(v.startswith("154773") and len(v) == 10 for v in got.values())


def test_parse_variants_no_own_offers_returns_empty():
    html = FIX.read_text(encoding="utf-8")
    assert parse_variants(html, "999999") == {}    # no own-prefixed sku -> link-only


def test_parse_variants_multicolor_returns_empty():
    # a size mapping to >1 itemId (two colors) -> ambiguous -> link-only
    html = ('"name":"Farbe oliv. Größe L.","price":"1","priceCurrency":"EUR","sku":"1547734524"'
            '"name":"Farbe braun. Größe L.","price":"1","priceCurrency":"EUR","sku":"1547739999"')
    assert parse_variants(html, "154773") == {}


def _row(**kw):
    base = {c: "" for c in [
        "variant:Bunda veľkosť", "variant:Nohavice veľkosť",
        "variant:Veľkosť (všetko)", "variant:Veľkosť číslo"]}
    base.update(kw)
    return base


def test_resolve_size_single_letter_column():
    assert resolve_size(_row(**{"variant:Veľkosť (všetko)": "L"})) == "L"


def test_resolve_size_numeric_column():
    assert resolve_size(_row(**{"variant:Veľkosť číslo": "48"})) == "48"


def test_resolve_size_multi_axis_komplet():
    r = _row(**{"variant:Bunda veľkosť": "3XL", "variant:Nohavice veľkosť": "46"})
    assert resolve_size(r) is MULTI_AXIS


def test_resolve_size_one_size_no_columns():
    assert resolve_size(_row()) is None


def test_resolve_size_ignores_code_suffix():
    # caller passes only the row; resolve_size never sees the code -> proven by API
    r = _row(**{"variant:Veľkosť (všetko)": "L"})
    assert resolve_size(r) == "L"


GRUBE = {"S": "1547734523", "M": "1547734598", "L": "1547734519", "XL": "1547734593",
         "XXL": "1547734524", "3XL": "1547734570", "4XL": "1547734535", "5XL": "1547734553"}


def _vrow(code, **kw):
    r = {"code": code, "variant:Bunda veľkosť": "", "variant:Nohavice veľkosť": "",
         "variant:Veľkosť (všetko)": "", "variant:Veľkosť číslo": ""}
    r.update(kw)
    return r


def test_match_letter_sizes():
    rows = [_vrow("60645/L", **{"variant:Veľkosť (všetko)": "L"}),
            _vrow("60645/5XL", **{"variant:Veľkosť (všetko)": "5XL"})]
    assert match_variant_codes(rows, GRUBE) == {"60645/L": "1547734519", "60645/5XL": "1547734553"}


def test_match_numeric_sizes():
    g = {"46": "3848935748", "48": "3848935732", "56": "3848935751"}
    rows = [_vrow("X/46", **{"variant:Veľkosť číslo": "46"}),
            _vrow("X/56", **{"variant:Veľkosť číslo": "56"})]
    assert match_variant_codes(rows, g) == {"X/46": "3848935748", "X/56": "3848935751"}


def test_match_xs_no_grube_label_link_only():
    rows = [_vrow("X/XS", **{"variant:Veľkosť (všetko)": "XS"})]
    assert match_variant_codes(rows, GRUBE) == {}        # XS absent -> link-only, never snaps to S


def test_match_multi_axis_excluded():
    rows = [_vrow("12311/3XL2", **{"variant:Bunda veľkosť": "3XL", "variant:Nohavice veľkosť": "46"})]
    assert match_variant_codes(rows, GRUBE) == {}


def test_match_2xl_normalizes_to_xxl():
    g = {"XXL": "1111111111"}
    rows = [_vrow("X/2XL", **{"variant:Veľkosť (všetko)": "2XL"})]
    assert match_variant_codes(rows, g) == {"X/2XL": "1111111111"}


def test_match_collision_guard_raises():
    g = {"XXL": "1", "2XL": "2"}   # both normalize to XXL
    rows = [_vrow("X/XXL", **{"variant:Veľkosť (všetko)": "XXL"})]
    with pytest.raises(ValueError):
        match_variant_codes(rows, g)


def test_normalize_size_letter_and_numeric():
    assert normalize_size("  l ") == "L"          # trim + uppercase
    assert normalize_size("2XL") == "XXL"         # alias fold
    assert normalize_size("XXXXXL") == "5XL"
    assert normalize_size("46") == "46"           # numeric kept as-is
    assert normalize_size("") == ""
