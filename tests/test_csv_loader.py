from parovanie.csv_loader import load_rows

FIX = "tests/fixtures/sample_products.csv"


def test_filters_by_supplier():
    rows = load_rows(FIX, {"BETALOV", "WETLAND"})
    sups = {r["supplier"].strip().upper() for r in rows}
    assert sups == {"BETALOV", "WETLAND"}
    assert len(rows) == 4  # GRUBE excluded


def test_keeps_all_columns():
    rows = load_rows(FIX, {"WETLAND"})
    assert rows[0]["externalCode"] == ""
    assert rows[0]["name"].startswith("Strike Nohavice DEERHUNTER")
