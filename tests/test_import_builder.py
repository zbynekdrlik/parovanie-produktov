from parovanie.import_builder import (
    LINK_HEADER,
    STATE_HEADER,
    link_rows,
    state_rows,
)


def test_headers_disjoint_no_empty_wipe():
    # link file carries ONLY internalNote; state file ONLY the state columns —
    # so neither writes an empty cell that would wipe the other's fields.
    assert LINK_HEADER == ["code", "pairCode", "internalNote"]
    assert "internalNote" not in STATE_HEADER
    assert STATE_HEADER[:2] == ["code", "pairCode"]
    assert "productVisibility" in STATE_HEADER and "availabilityInStock" in STATE_HEADER


def test_link_rows_put_url_in_internalnote():
    products = [{"key": "k1", "variant_codes": ["A/1", "A/2"]}]
    dec = {"k1": {"status": "good", "url": "https://h/x"}}
    rows = link_rows(products, dec, {"A/1": "100", "A/2": "100"})
    assert rows == [["A/1", "100", "https://h/x"], ["A/2", "100", "https://h/x"]]


def test_manual_status_is_also_a_link():
    products = [{"key": "k", "variant_codes": ["M"]}]
    rows = link_rows(products, {"k": {"status": "manual", "url": "https://h/m"}}, {"M": "1"})
    assert rows == [["M", "1", "https://h/m"]]


def test_link_rows_skip_link_without_url_and_non_links():
    products = [{"key": "k1", "variant_codes": ["A"]}, {"key": "k2", "variant_codes": ["B"]}]
    dec = {"k1": {"status": "good", "url": ""}, "k2": {"status": "unavailable"}}
    assert link_rows(products, dec, {"A": "1", "B": "2"}) == []


def test_state_rows_unavailable_is_visible_vypredane():
    products = [{"key": "k2", "variant_codes": ["B"]}]
    rows = state_rows(products, {"k2": {"status": "unavailable"}}, {"B": "200"})
    assert rows == [["B", "200", "visible", "0", "Vypredané", "Vypredané"]]


def test_state_rows_discontinued_is_detailonly_skoncil():
    products = [{"key": "k3", "variant_codes": ["C"]}]
    rows = state_rows(products, {"k3": {"status": "discontinued"}}, {"C": "300"})
    assert rows == [["C", "300", "detailOnly", "0",
                     "Predaj výrobku skončil", "Predaj výrobku skončil"]]


def test_state_rows_skip_links():
    # a link decision produces NO state row (it goes to link_rows instead)
    products = [{"key": "k1", "variant_codes": ["A"]}]
    assert state_rows(products, {"k1": {"status": "good", "url": "https://h"}}, {"A": "1"}) == []


def test_undecided_products_excluded_from_both():
    products = [{"key": "k4", "variant_codes": ["D"]}]
    assert link_rows(products, {}, {"D": "400"}) == []
    assert state_rows(products, {}, {"D": "400"}) == []
