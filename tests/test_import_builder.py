import csv

import pytest

from parovanie.import_builder import (
    LINK_HEADER,
    RESTOCK_COLS,
    STATE_HEADER,
    link_rows,
    sanitize_csv,
    state_rows,
)


def _write(path, header, rows, delim=";", bom=False):
    enc = "utf-8-sig" if bom else "utf-8"
    with open(path, "w", encoding=enc, newline="") as f:
        w = csv.writer(f, delimiter=delim)
        w.writerow(header)
        w.writerows(rows)


def test_sanitize_drops_unsafe_columns_keeps_restock(tmp_path):
    # An n8n feed with price/name columns must NEVER reach Shoptet — only the
    # restock columns survive, so the live eshop's prices/names are not overwritten.
    src = tmp_path / "feed.csv"
    out = tmp_path / "import.csv"
    header = ["code", "pairCode", "name", "purchasePrice", "ourPrice",
              "productVisibility", "availabilityInStock", "stock", "competition_price"]
    _write(src, header, [["15233/M", "1564", "Vesta", "999", "67.80",
                          "visible", "Skladom", "5", "111"]])
    n = sanitize_csv(str(src), str(out))
    assert n == 1
    with open(out, encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f, delimiter=";")
        assert rd.fieldnames == RESTOCK_COLS
        row = next(rd)
    assert row == {"code": "15233/M", "pairCode": "1564",
                   "productVisibility": "visible", "availabilityInStock": "Skladom",
                   "stock": "5"}
    # the dangerous columns are gone
    text = out.read_text(encoding="utf-8-sig")
    for bad in ("999", "67.80", "Vesta", "purchasePrice", "ourPrice", "competition_price"):
        assert bad not in text


def test_sanitize_writes_bom_utf8(tmp_path):
    src = tmp_path / "feed.csv"
    out = tmp_path / "import.csv"
    _write(src, ["code", "pairCode"], [["A/1", "10"]])
    sanitize_csv(str(src), str(out))
    assert out.read_bytes().startswith(b"\xef\xbb\xbf")  # UTF-8 BOM (Shoptet import)


def test_sanitize_skips_empty_code_rows(tmp_path):
    src = tmp_path / "feed.csv"
    out = tmp_path / "import.csv"
    _write(src, ["code", "pairCode", "stock"], [["A/1", "10", "5"], ["", "10", "5"]])
    assert sanitize_csv(str(src), str(out)) == 1


def test_sanitize_reads_bom_input(tmp_path):
    src = tmp_path / "feed.csv"
    out = tmp_path / "import.csv"
    _write(src, ["code", "pairCode", "stock"], [["A/1", "10", "5"]], bom=True)
    assert sanitize_csv(str(src), str(out)) == 1


def test_sanitize_rejects_csv_without_code_paircode(tmp_path):
    src = tmp_path / "feed.csv"
    out = tmp_path / "import.csv"
    _write(src, ["name", "stock"], [["Vesta", "5"]])
    with pytest.raises(ValueError):
        sanitize_csv(str(src), str(out))


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
