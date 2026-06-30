from parovanie.catalog_index import (
    normalize_text,
    build_catalog_index,
    search_catalog,
    supplier_from_url,
)


def _row(code, pair, name, supplier="WETLAND", img=""):
    return {"code": code, "pairCode": pair, "name": name, "supplier": supplier, "defaultImage": img}


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


def test_build_skips_rows_without_code_or_paircode():
    rows = [_row("", "512", "x"), _row("4931/S", "", "x")]
    assert build_catalog_index(rows) == {}


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
