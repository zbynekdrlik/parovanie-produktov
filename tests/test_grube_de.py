import pathlib

import pytest

from parovanie.grube_de import (
    MULTI_AXIS,
    ONE_SIZE,
    match_variant_codes,
    normalize_size,
    parse_variants,
    resolve_size,
    to_grube_de,
)

FIX = pathlib.Path(__file__).parent / "fixtures" / "grube_de_detail_154773.html"
# Single-size knife (#60 class 1): NO 'Größe' Offer list; one own itemId in the
# `itemId=` anchor (2540316117 = 254031+4). The page ALSO carries 8 cross-sell
# anchors with OTHER productIds — real proof the prefix+len filter excludes them.
FIX_SINGLE = pathlib.Path(__file__).parent / "fixtures" / "grube_de_detail_254031_ocielka.html"
# Range-size shirt (#60 class 3): the grube.de Offer 'name' carries 'Größe <RANGE>.'
# where <RANGE> is the SAME slash format as forestshop ("39/40"). Verified LIVE on
# grube.de 2026-07-23 (pid 113567). Own itemId = 113567 + 4 digits; the page also
# carries a foreign cross-sell offer (sku 6165695125) proving the prefix filter.
FIX_RANGE = pathlib.Path(__file__).parent / "fixtures" / "grube_de_detail_113567_kosela.html"


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


def test_parse_variants_foreign_offer_excluded_by_prefix():
    own = '"name":"Größe L.","price":"1","priceCurrency":"EUR","sku":"1547734524"'
    foreign = '"name":"Größe L.","price":"1","priceCurrency":"EUR","sku":"6165695125"'
    # foreign sku does NOT start with 154773 -> excluded by prefix; only own L remains
    assert parse_variants(own + foreign, "154773") == {"L": "1547734524"}


def test_parse_variants_no_own_offers_returns_empty():
    html = FIX.read_text(encoding="utf-8")
    assert parse_variants(html, "999999") == {}    # no own-prefixed sku -> link-only


def test_parse_variants_multicolor_returns_empty():
    # a size mapping to >1 itemId (two colors) -> ambiguous -> link-only
    html = ('"name":"Farbe oliv. Größe L.","price":"1","priceCurrency":"EUR","sku":"1547734524"'
            '"name":"Farbe braun. Größe L.","price":"1","priceCurrency":"EUR","sku":"1547739999"')
    assert parse_variants(html, "154773") == {}


# --- single-size (#60 class 1) -------------------------------------------------

def test_parse_variants_single_size_knife_real_page():
    # real single-size ocieľka page: 0 sized Offers -> the ONE own itemId from the
    # `itemId=` anchor, under the ONE_SIZE sentinel key.
    html = FIX_SINGLE.read_text(encoding="utf-8")
    assert parse_variants(html, "254031") == {ONE_SIZE: "2540316117"}


def test_parse_variants_single_size_excludes_cross_sell():
    # the real page carries 8 foreign cross-sell itemId anchors (other productIds);
    # only the own 254031+4 id survives the prefix+len filter.
    html = FIX_SINGLE.read_text(encoding="utf-8")
    got = parse_variants(html, "254031")
    assert list(got.values()) == ["2540316117"]
    assert all(v.startswith("254031") and len(v) == 10 for v in got.values())


def test_parse_variants_single_size_multicolor_link_only():
    # no sized Offer list but >1 OWN itemId anchor (multi-color, no size axis) -> link-only
    html = ('<a href="/ajax/reminder/add?itemId=2540316117">a</a>'
            '<a href="/ajax/reminder/add?itemId=2540319999">b</a>')
    assert parse_variants(html, "254031") == {}


def test_parse_variants_single_size_no_own_itemid_link_only():
    # no sized Offer AND no own itemId anchor (only foreign cross-sell) -> link-only
    html = '<a href="/ajax/reminder/add?itemId=6165154086">foreign</a>'
    assert parse_variants(html, "254031") == {}


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


# --- single-size matching (#60 class 1) ---------------------------------------

def test_match_one_size_matches_single_grube():
    # one-size forestshop knife (no size columns) + single-size grube -> the itemId
    rows = [_vrow("MO14238")]                      # all size columns empty -> resolve_size None
    assert match_variant_codes(rows, {ONE_SIZE: "2540316117"}) == {"MO14238": "2540316117"}


def test_match_one_size_multiple_rows_link_only():
    # 2 one-size codes but only 1 grube itemId -> never spread 1 itemId over N codes
    rows = [_vrow("A"), _vrow("B")]
    assert match_variant_codes(rows, {ONE_SIZE: "2540316117"}) == {}


def test_match_one_size_row_against_multisize_grube_link_only():
    # a one-size forestshop row must NOT grab a random size of a multi-size grube product
    rows = [_vrow("MO14238")]
    assert match_variant_codes(rows, GRUBE) == {}


def test_match_sized_row_against_single_size_grube_link_only():
    # a sized forestshop row must NOT match a single-size grube product
    rows = [_vrow("X/L", **{"variant:Veľkosť (všetko)": "L"})]
    assert match_variant_codes(rows, {ONE_SIZE: "2540316117"}) == {}


# --- range / single-number / cm sizes (#60 class 3) ---------------------------
# LIVE verification (2026-07-23): grube.de emits the SAME size string as forestshop
# for every "range/special" format — slash ranges ("39/40"), single numbers ("39",
# shoe sizes) and cm kids sizes ("140"). The spec's assumption that grube would use a
# DIFFERENT range notation (e.g. "Gr. 39-40") requiring normalization is false, so the
# existing EXACT-string match already covers class 3. These tests LOCK that, and prove
# the fail-closed guarantee: no fuzzy range-membership, no cross-format match.

def test_parse_variants_range_sizes_real_page():
    # real slim-fit shirt page (pid 113567): grube Offer 'Größe 39/40.' etc. -> the
    # range label is the same slash format as forestshop.
    html = FIX_RANGE.read_text(encoding="utf-8")
    assert parse_variants(html, "113567") == {
        "39/40": "1135678118", "41/42": "1135678134", "43/44": "1135678153",
        "45/46": "1135678180", "47/48": "1135678192", "49/50": "1135678114",
    }


def test_parse_variants_range_excludes_cross_sell():
    # the page carries a foreign cross-sell offer (sku 6165695125, other productId);
    # only own 113567+4 ids survive the prefix+len filter.
    html = FIX_RANGE.read_text(encoding="utf-8")
    got = parse_variants(html, "113567")
    assert "6165695125" not in got.values()
    assert all(v.startswith("113567") and len(v) == 10 for v in got.values())


def test_match_range_sizes_exact():
    # forestshop range "39/40" ↔ grube range "39/40" — exact match (no normalization
    # needed: grube uses the identical slash string). Verified LIVE on pid 113567.
    grube = parse_variants(FIX_RANGE.read_text(encoding="utf-8"), "113567")
    rows = [_vrow("62093/39/40", **{"variant:Veľkosť (všetko)": "39/40"}),
            _vrow("62093/47/48", **{"variant:Veľkosť (všetko)": "47/48"})]
    assert match_variant_codes(rows, grube) == {
        "62093/39/40": "1135678118", "62093/47/48": "1135678192"}


def test_match_single_numeric_shoe_sizes():
    # boots (pid 134030) — grube 'Größe 39.'..'48.' single numbers == forestshop
    # single numbers "37","39",... in the numeric column. Verified LIVE.
    grube = {"37": "1340302316", "38": "1340302323", "39": "1340302332", "40": "1340302336"}
    rows = [_vrow("B/37", **{"variant:Veľkosť číslo": "37"}),
            _vrow("B/39", **{"variant:Veľkosť číslo": "39"})]
    assert match_variant_codes(rows, grube) == {"B/37": "1340302316", "B/39": "1340302332"}


def test_match_cm_kids_sizes():
    # kids sweater (pid 115162) — grube 'Größe 140.' cm sizes == forestshop cm sizes.
    grube = {"140": "1151629218", "152": "1151629296"}
    rows = [_vrow("K/140", **{"variant:Veľkosť (všetko)": "140"}),
            _vrow("K/152", **{"variant:Veľkosť (všetko)": "152"})]
    assert match_variant_codes(rows, grube) == {"K/140": "1151629218", "K/152": "1151629296"}


def test_match_single_number_never_snaps_to_range():
    # FAIL-CLOSED: a forestshop single "39" must NEVER match a grube range "39/40"
    # (range-membership is fuzzy and banned — "39" could be a shoe size, not a collar
    # range). No exact string equality -> link-only.
    rows = [_vrow("X/39", **{"variant:Veľkosť číslo": "39"})]
    assert match_variant_codes(rows, {"39/40": "1135678118"}) == {}


def test_match_range_never_snaps_to_different_grube_format():
    # FAIL-CLOSED: if grube ever formatted the range differently (hyphen "39-40" vs
    # forestshop slash "39/40"), exact match fails -> link-only, never a wrong code.
    rows = [_vrow("X/39/40", **{"variant:Veľkosť (všetko)": "39/40"})]
    assert match_variant_codes(rows, {"39-40": "1135678118"}) == {}


def test_normalize_size_letter_and_numeric():
    assert normalize_size("  l ") == "L"          # trim + uppercase
    assert normalize_size("2XL") == "XXL"         # alias fold
    assert normalize_size("XXXXXL") == "5XL"
    assert normalize_size("46") == "46"           # numeric kept as-is
    assert normalize_size("") == ""


def test_parse_variants_color_only_no_size_is_link_only():
    """#60 review Finding 1: a color-only product (own Offers EXIST but carry only
    Farbe, no 'Größe') must be link-only — NOT the default colour's itemId from the
    anchor. The single-size anchor fallback may run ONLY when the page has zero own
    Offers (a true single-variant knife); own offers with no size axis = fail-closed."""
    html = (
        '{"name":"Morakniv Companion Farbe oliv","price":"12","priceCurrency":"EUR","sku":"2540316117"}'
        '{"name":"Morakniv Companion Farbe braun","price":"12","priceCurrency":"EUR","sku":"2540319999"}'
        '<a href="/p/morakniv/254031/#itemId=2540316117">Erinnerung</a>'
    )
    # own offers 2540316117 + 2540319999 (prefix 254031, len 10) but NO Größe → color-only.
    assert parse_variants(html, "254031") == {}
