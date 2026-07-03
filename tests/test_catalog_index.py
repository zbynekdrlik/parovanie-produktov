from parovanie.catalog_index import (
    normalize_text,
    build_catalog_index,
    search_catalog,
    supplier_from_url,
    build_promoted_entry,
)


def _row(code, pair, name, supplier="WETLAND", img=""):
    return {"code": code, "pairCode": pair, "name": name, "supplier": supplier, "defaultImage": img}


def _crow(code, pair, name, supplier="WETLAND", vis="visible", ais="", aos="",
          price="", stock=""):
    """Export row WITH the commerce columns (the real Shoptet export carries them)."""
    return {**_row(code, pair, name, supplier),
            "productVisibility": vis, "availabilityInStock": ais,
            "availabilityOutOfStock": aos, "price": price, "stock": stock}


def test_normalize_strips_diacritics_and_lowercases():
    assert normalize_text("Bundá ČIERNA") == "bunda cierna"
    assert normalize_text("") == ""


def test_build_groups_by_paircode_and_collects_codes():
    rows = [_row("4931/S", "512", "Mikina GARDE", img="a.jpg"),
            _row("4931/L", "512", "Mikina GARDE"),
            _row("60611/M", "425", "Bunda Tradition", supplier="GRUBE")]
    cat = build_catalog_index(rows, review_keys={"425"})
    assert set(cat) == {"512", "425"}
    assert cat["512"]["variant_codes"] == ["4931/S", "4931/L"]
    assert cat["512"]["image"] == "a.jpg"
    assert cat["512"]["in_review"] is False
    assert cat["425"]["in_review"] is True
    assert cat["425"]["supplier"] == "GRUBE"


def test_build_skips_rows_without_code_but_keeps_empty_paircode():
    """BUG 1: a row with NO code carries nothing (code is the variant id AND the
    fallback key) → skip. But an empty-pairCode row WITH a code is a valid
    single-variant product (čiapky/nože/svietidlá) → it IS indexed now, keyed by its
    code. (Old code dropped every empty-pairCode row → 2722 products missing.)"""
    # no code → skipped, even with a pairCode present
    assert build_catalog_index([_row("", "512", "x")]) == {}
    # code present, pairCode empty → indexed under the code (was wrongly dropped)
    cat = build_catalog_index([_row("4931/S", "", "x")])
    assert set(cat) == {"4931/S"} and cat["4931/S"]["pairCode"] == ""


def test_empty_paircode_single_variant_product_indexed_and_searchable():
    """BUG 1 (the biggest — 'všetky produkty'): single-variant products have an EMPTY
    pairCode in the Shoptet export and were dropped from the index. Now keyed by their
    own variant code → present AND findable by name."""
    cat = build_catalog_index([_row("CIAP123", "", "Čiapka Merino Zimná")])
    assert "CIAP123" in cat                       # keyed by its code (pairCode is '')
    e = cat["CIAP123"]
    assert e["key"] == "CIAP123" and e["pairCode"] == ""
    assert e["variant_codes"] == ["CIAP123"]
    assert [x["key"] for x in search_catalog(cat, "merino")] == ["CIAP123"]
    # does NOT collide with a normal pairCode product in the same index
    cat2 = build_catalog_index([_row("CIAP123", "", "Čiapka Merino Zimná"),
                                _row("4931/S", "512", "Mikina GARDE")])
    assert set(cat2) == {"CIAP123", "512"}


def test_in_review_keyed_by_bare_paircode_not_composite_key():
    """C1 contract (webreview/app.py line ~88): review_keys passed to build_catalog_index
    are the products' BARE pairCodes, NOT their composite 'SUPPLIER|pairCode' keys. A
    product keyed 'GRUBE|425' is in_review iff its pairCode '425' is in the set, so the
    app must collect {p['pairCode']}. Collecting {p['key']} (the C1 bug) put 'GRUBE|425'
    in the set, which never equals the catalog's bare pairCode '425' → every supplier-keyed
    product was wrongly marked not-in-review."""
    rows = [_row("60611/L", "425", "Bunda Tradition", supplier="GRUBE")]
    # correct contract: review_keys = bare pairCodes → in_review True
    assert build_catalog_index(rows, review_keys={"425"})["425"]["in_review"] is True
    # the C1 bug: review_keys = composite keys → never matches bare pairCode → False
    assert build_catalog_index(rows, review_keys={"GRUBE|425"})["425"]["in_review"] is False


def test_search_by_name_accent_insensitive():
    cat = build_catalog_index([_row("4931/S", "512", "Mikina GARDE HART")])
    # Accent-insensitive positive: query without diacritics matches the name.
    assert [e["pairCode"] for e in search_catalog(cat, "garde")] == ["512"]
    # True non-match: a word not present in the catalog returns nothing.
    assert search_catalog(cat, "nohavice") == []
    # Multi-word substring spanning the whole normalized name still matches.
    assert search_catalog(cat, "garde hart")[0]["pairCode"] == "512"


def test_search_by_code_and_supplier():
    cat = build_catalog_index([_row("60611/M", "425", "Bunda", supplier="GRUBE")])
    assert search_catalog(cat, "60611")[0]["pairCode"] == "425"
    assert search_catalog(cat, "grube")[0]["pairCode"] == "425"


def test_search_empty_or_short_query_returns_empty():
    cat = build_catalog_index([_row("4931/S", "512", "Mikina")])
    assert search_catalog(cat, "") == []
    assert search_catalog(cat, "m") == []


def test_search_limit():
    rows = [_row(f"c{i}", str(i), "Spolocny nazov") for i in range(60)]
    cat = build_catalog_index(rows)
    assert len(search_catalog(cat, "spolocny", limit=50)) == 50


def test_search_accent_insensitive_through_search_path():
    # Accent-less query matches an accented stored name THROUGH search_catalog
    # (not just the normalize_text unit) — closes Task 1's review gap.
    cat = build_catalog_index([_row("x/1", "700", "Bundá ČIERNA")])
    assert [e["pairCode"] for e in search_catalog(cat, "bunda")] == ["700"]


# ---- commerce fields (price / stock / state) per catalog entry ----------- #
# The manager complained search results show "almost no data" — each entry must
# aggregate OUR price, summed variant stock and the 3-state eshop classification
# (via the canonical export_helpers current_of/state_of — never re-derived).

def test_entry_carries_price_stock_state():
    e = build_catalog_index(
        [_crow("4931/S", "512", "Mikina GARDE", ais="Skladom", price="119", stock="3")])["512"]
    assert e["price"] == "119"
    assert e["stock"] == 3
    assert e["state"] == 1


def test_state_is_best_across_variants_mixed_1_and_3():
    """One sellable variant (state 1) + one hidden (state 3) → entry state 1."""
    rows = [_crow("A/S", "9", "X", vis="hidden"),
            _crow("A/M", "9", "X", ais="Skladom")]
    assert build_catalog_index(rows)["9"]["state"] == 1


def test_state_all_variants_3():
    rows = [_crow("A/S", "9", "X", vis="hidden"),
            _crow("A/M", "9", "X", vis="blocked")]
    assert build_catalog_index(rows)["9"]["state"] == 3


def test_state_2_beats_3_when_no_variant_sellable():
    """avail arg mirrors current_of: availabilityInStock or availabilityOutOfStock."""
    rows = [_crow("A/S", "9", "X", aos="Vypredané"),
            _crow("A/M", "9", "X", vis="hidden")]
    assert build_catalog_index(rows)["9"]["state"] == 2


def test_stock_sums_across_variants_nonnumeric_counts_zero():
    rows = [_crow("A/S", "9", "X", stock="3"),
            _crow("A/M", "9", "X", stock="xx"),
            _crow("A/L", "9", "X", stock=""),
            _crow("A/XL", "9", "X", stock="4")]
    assert build_catalog_index(rows)["9"]["stock"] == 7


def test_price_is_first_nonempty_across_variants():
    rows = [_crow("A/S", "9", "X", price=""),
            _crow("A/M", "9", "X", price="49,90"),
            _crow("A/L", "9", "X", price="59,90")]
    assert build_catalog_index(rows)["9"]["price"] == "49,90"


def test_minimal_rows_without_commerce_columns_get_defaults():
    """Existing minimal fixtures (code/pairCode/name/supplier/defaultImage only)
    must not crash — defaults: price '' / stock 0 / state 1."""
    e = build_catalog_index([_row("4931/S", "512", "Mikina")])["512"]
    assert e["price"] == "" and e["stock"] == 0 and e["state"] == 1


# ---- new: multi-word / word-boundary / ranked search --------------------- #

def _search_cat():
    """Catalog exercising the four failure classes: a percussion jacket, a knife,
    socks (the ponožky→ponozky trap that CONTAINS 'noz'), and a coded product."""
    rows = [
        _row("A1", "1", "Pánska bunda Percussion Predator"),
        _row("A2/S", "1", "Pánska bunda Percussion Predator"),
        _row("B1", "2", "Poľovnícky nôž Morakniv"),
        _row("C1", "3", "Poľovnícke ponožky Dr. Hunter"),
        _row("60024/L", "4", "Lovecké nohavice", supplier="GRUBE"),
    ]
    return build_catalog_index(rows)


def test_build_adds_name_words_tokens():
    """build_catalog_index precomputes name_words = alnum word-tokens of name_norm."""
    cat = build_catalog_index([_row("x", "1", "Pánska Bunda Percussion")])
    assert cat["1"]["name_words"] == ["panska", "bunda", "percussion"]
    # existing keys preserved
    assert cat["1"]["name_norm"] == "panska bunda percussion"


def test_search_multiword_order_independent_is_the_core_regression():
    """CORE regression: 'percussion bunda' AND 'bunda percussion' BOTH find the
    jacket. Old contiguous-substring search returned 0 for the natural word order."""
    cat = _search_cat()
    a = search_catalog(cat, "percussion bunda")
    b = search_catalog(cat, "bunda percussion")
    assert a and b                              # both non-empty (old: percussion bunda → [])
    assert a[0]["pairCode"] == "1"
    assert b[0]["pairCode"] == "1"
    assert "1" in [e["pairCode"] for e in a]
    assert "1" in [e["pairCode"] for e in b]


def test_search_noz_ranks_knife_first_socks_after():
    """NEW behaviour (BUG 2 fix): 'noz'/'nôž' now FINDS by substring, but RANKS the
    knife FIRST — 'noz' is a WHOLE WORD of the knife's name (tier 5) yet only a mid-word
    substring inside 'ponozky' for the socks (tier 3). The knife therefore ranks above
    the socks. (Old word-boundary search dropped the socks entirely; the new design
    keeps every substring hit but orders by relevance, so nothing is 'never found'.)"""
    for q in ("noz", "nôž"):
        codes = [e["pairCode"] for e in search_catalog(_search_cat(), q)]
        assert codes[0] == "2", f"{q!r} must rank the knife first, got {codes}"
        if "3" in codes:                          # socks may appear — but only AFTER
            assert codes.index("2") < codes.index("3")


def test_search_substring_midword_finds_deerhunter():
    """BUG 2 ('nikdy nič nevyhľadá'): 'hunter' finds 'Deerhunter …' — a substring in the
    MIDDLE of a word, which the old whole-word/prefix matching missed entirely (returned
    []). RED on the old code, GREEN after."""
    cat = build_catalog_index([_row("DH1", "80", "Bunda Deerhunter Muflon")])
    assert [e["pairCode"] for e in search_catalog(cat, "hunter")] == ["80"]


def test_search_finds_by_externalcode_and_description_not_in_name():
    """BUG 3 ('hľadať všetko nie len názov'): a product whose NAME does not contain the
    query but whose externalCode / description / manufacturer / category does → is FOUND.
    Old search only looked at name/supplier/code → these returned []."""
    rows = [{"code": "X1", "pairCode": "50", "name": "Lampáš Petromax",
             "supplier": "WETLAND", "externalCode": "AH5",
             "shortDescription": "kempingové svietidlo",
             "description": "Odolný benzínový lampáš pre outdoor.",
             "manufacturer": "Petromax GmbH", "categoryText": "Svietidlá / Lampáše"}]
    cat = build_catalog_index(rows)
    assert [e["pairCode"] for e in search_catalog(cat, "ah5")] == ["50"]          # externalCode
    assert [e["pairCode"] for e in search_catalog(cat, "benzinovy")] == ["50"]    # description word
    assert [e["pairCode"] for e in search_catalog(cat, "kempingove")] == ["50"]   # shortDescription
    assert [e["pairCode"] for e in search_catalog(cat, "svietidla")] == ["50"]    # category
    # a word present in NONE of the searchable fields still returns nothing
    assert search_catalog(cat, "nonexistentxyz") == []


def test_search_ranking_wholeword_then_name_substring_then_description_only():
    """RANKING (not tautological): the query is a WHOLE WORD of one name, a mid-word
    SUBSTRING of a second name, and ONLY in a third's description → all three are FOUND
    (substring gate) and ordered name-whole-word > name-substring > description-only."""
    rows = [
        {"code": "K1", "pairCode": "1", "name": "Poľovnícky nôž", "supplier": "S"},
        {"code": "K2", "pairCode": "2", "name": "Pánske ponožky", "supplier": "S"},
        {"code": "K3", "pairCode": "3", "name": "Puzdro na opasok", "supplier": "S",
         "description": "Vhodné na prenášanie noža a doplnkov."},
    ]
    cat = build_catalog_index(rows)
    codes = [e["pairCode"] for e in search_catalog(cat, "noz")]
    assert set(codes) == {"1", "2", "3"}          # all three FOUND
    assert codes[0] == "1"                        # whole-word name ranks first
    assert codes.index("2") < codes.index("3")    # name-substring before description-only


def test_search_by_code_substring_still_works():
    """Short/numeric variant codes still match by substring."""
    res = search_catalog(_search_cat(), "60024")
    assert res and res[0]["pairCode"] == "4"


def test_search_ranking_whole_word_and_exact_before_prefix():
    """RANKING: for two products both matching a term, the WHOLE-WORD / exact-name
    match ranks before a mere prefix/partial match."""
    rows = [
        _row("p", "10", "Zimná bundaska pánska"),   # 'bunda' only a PREFIX of 'bundaska'
        _row("w", "20", "Bunda"),                    # 'bunda' whole word AND exact name
    ]
    cat = build_catalog_index(rows)
    ordered = [e["pairCode"] for e in search_catalog(cat, "bunda")]
    assert ordered[0] == "20"                        # exact/whole-word first
    assert set(ordered) == {"10", "20"}              # both are candidates


def test_search_all_terms_must_match_and():
    """A product is a candidate only if EVERY term matches (order-independent AND)."""
    cat = _search_cat()
    # 'percussion' matches the jacket, 'morakniv' matches only the knife → no product
    # has BOTH → empty.
    assert search_catalog(cat, "percussion morakniv") == []


class _Cfg:
    def __init__(self, base_url):
        self.base_url = base_url


_SUP = {
    "WETLAND": _Cfg("https://www.wetland.sk"),
    "BETALOV": _Cfg("https://www.huntingshop.eu"),
    "GRUBE": _Cfg("https://www.grube.sk"),
    "ODIMON": _Cfg("https://www.odimon.sk"),
}


def test_supplier_from_url_matches_host():
    assert supplier_from_url("https://www.wetland.sk/p/x", _SUP) == "WETLAND"
    assert supplier_from_url("https://wetland.sk/p/x", _SUP) == "WETLAND"
    assert supplier_from_url("https://www.huntingshop.eu/h?search=x", _SUP) == "BETALOV"


def test_supplier_from_url_grube_de_maps_to_grube():
    assert supplier_from_url("https://www.grube.de/p/x/154773/", _SUP) == "GRUBE"


def test_supplier_from_url_unknown_returns_empty():
    assert supplier_from_url("https://example.com/x", _SUP) == ""
    assert supplier_from_url("", _SUP) == ""


def test_build_promoted_entry_shape():
    ce = {"pairCode": "425", "name": "Bunda Tradition", "supplier": "GRUBE",
          "variant_codes": ["60611/L", "60611/M"], "image": "img.jpg"}
    cur = {"state": 1, "off": False, "vis": "visible", "avail": "", "price": "99,00", "std": "", "stock": "3"}
    e = build_promoted_entry(ce, cur, "https://www.forestshop.sk/x/", "GRUBE", 2600)
    assert e["key"] == "425" and e["pairCode"] == "425"
    assert e["variant_codes"] == ["60611/L", "60611/M"]
    assert e["our_images"] == ["img.jpg"]
    assert e["candidates"] == [] and e["ai_status"] == "unmatched"
    assert e["supplier"] == "GRUBE"
    assert e["our_url"] == "https://www.forestshop.sk/x/"
    assert e["current"] == cur and e["idx"] == 2600
    # supplier falls back to catalog supplier when inferred is empty
    e2 = build_promoted_entry(ce, cur, None, "", 1)
    assert e2["supplier"] == "GRUBE" and e2["our_url"] is None and e2["our_images"] == ["img.jpg"]
