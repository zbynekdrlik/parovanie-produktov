from parovanie.import_builder import import_rows, HEADER


def test_header_has_code_and_paircode():
    assert HEADER[0] == "code"
    assert HEADER[1] == "pairCode"


def test_link_rows_carry_url_marker_and_no_availability():
    products = [{"key": "k1", "variant_codes": ["A/1", "A/2"]}]
    dec = {"k1": {"status": "good", "url": "https://h/x"}}
    rows = import_rows(products, dec, {"A/1": "100", "A/2": "100"})
    assert ["A/1", "100", "https://h/x", "human matched", "", "", ""] in rows
    assert ["A/2", "100", "https://h/x", "human matched", "", "", ""] in rows
    # link rows leave stock/availability empty (no preserved 'Predaj skončil')
    assert all(r[4] == "" and r[5] == "" and r[6] == "" for r in rows)


def test_unavailable_rows_sold_out_empty_link():
    products = [{"key": "k2", "variant_codes": ["B"]}]
    dec = {"k2": {"status": "unavailable", "url": ""}}
    rows = import_rows(products, dec, {"B": "200"})
    assert rows == [["B", "200", "", "", "0", "Vypredané", "Vypredané"]]


def test_undecided_products_excluded():
    products = [{"key": "k3", "variant_codes": ["C"]}]
    rows = import_rows(products, {}, {"C": "300"})
    assert rows == []
