from parovanie.csv_loader import load_rows
from parovanie.grouping import group_products

FIX = "tests/fixtures/sample_products.csv"


def test_groups_variants_by_paircode():
    products = group_products(load_rows(FIX, {"BETALOV", "WETLAND"}))
    assert len(products) == 2  # one WETLAND product, one BETALOV product
    by_sup = {p.supplier: p for p in products}
    assert by_sup["BETALOV"].external_code == "OB570"
    assert by_sup["BETALOV"].variant_codes == ["60177/46", "60177/48"]
    assert by_sup["WETLAND"].external_code is None
    assert by_sup["WETLAND"].variant_codes == ["61246/46", "61246/48"]


def test_name_fallback_when_no_paircode(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text(
        "code;pairCode;name;externalCode;supplier\r\n"
        '"A";"";"Ciapka FOO";"";"WETLAND"\r\n'
        '"B";"";"Ciapka FOO";"";"WETLAND"\r\n',
        encoding="cp1250", newline="",
    )
    products = group_products(load_rows(str(p), {"WETLAND"}))
    assert len(products) == 1
    assert products[0].variant_codes == ["A", "B"]


def test_pair_key_is_supplier_scoped(tmp_path):
    # same pairCode at two different suppliers must NOT collide (checkpoint key)
    p = tmp_path / "x.csv"
    p.write_text(
        "code;pairCode;name;externalCode;supplier\r\n"
        '"A";"123";"X";"";"BETALOV"\r\n'
        '"B";"123";"Y";"";"WETLAND"\r\n',
        encoding="cp1250", newline="",
    )
    products = group_products(load_rows(str(p), {"BETALOV", "WETLAND"}))
    keys = {pr.pair_key for pr in products}
    assert len(keys) == 2  # distinct keys despite identical pairCode
    assert all("|" in k for k in keys)
