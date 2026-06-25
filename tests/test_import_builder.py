from parovanie.import_builder import import_rows, HEADER


def test_header_has_code_paircode_and_visibility():
    assert HEADER[0] == "code"
    assert HEADER[1] == "pairCode"
    assert "productVisibility" in HEADER


def test_link_rows_carry_url_marker_and_no_state():
    products = [{"key": "k1", "variant_codes": ["A/1", "A/2"]}]
    dec = {"k1": {"status": "good", "url": "https://h/x"}}
    rows = import_rows(products, dec, {"A/1": "100", "A/2": "100"})
    assert ["A/1", "100", "https://h/x", "human matched", "", "", "", ""] in rows
    # link rows leave visibility/stock/availability empty (automation turns on)
    assert all(r[4] == "" and r[5] == "" and r[6] == "" and r[7] == "" for r in rows)


def test_unavailable_is_visible_vypredane_state2():
    products = [{"key": "k2", "variant_codes": ["B"]}]
    dec = {"k2": {"status": "unavailable", "url": ""}}
    rows = import_rows(products, dec, {"B": "200"})
    assert rows == [["B", "200", "", "", "visible", "0", "Vypredané", "Vypredané"]]


def test_discontinued_is_detailonly_skoncil_state3():
    products = [{"key": "k3", "variant_codes": ["C"]}]
    dec = {"k3": {"status": "discontinued", "url": ""}}
    rows = import_rows(products, dec, {"C": "300"})
    assert rows == [["C", "300", "", "", "detailOnly", "0",
                     "Predaj výrobku skončil", "Predaj výrobku skončil"]]


def test_undecided_products_excluded():
    rows = import_rows([{"key": "k4", "variant_codes": ["D"]}], {}, {"D": "400"})
    assert rows == []
