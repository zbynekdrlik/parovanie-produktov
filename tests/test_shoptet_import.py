# tests/test_shoptet_import.py
import csv

import pytest

from parovanie.shoptet_import import (
    EXPECTED_HEADER,
    ShoptetError,
    classify_row,
    load_credentials,
    parse_import_log,
    preflight_csv,
)


def _write(tmp_path, text):
    p = tmp_path / ".shoptet_admin"
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_load_credentials_ok(tmp_path):
    path = _write(tmp_path,
                  "SHOPTET_ADMIN_URL=https://www.forestshop.sk/admin/\n"
                  "# comment line\n"
                  'SHOPTET_USER="bob@x.sk"\n'
                  "SHOPTET_PASS=secret pass\n")
    c = load_credentials(path)
    assert c["SHOPTET_ADMIN_URL"] == "https://www.forestshop.sk/admin/"
    assert c["SHOPTET_USER"] == "bob@x.sk"          # quotes stripped
    assert c["SHOPTET_PASS"] == "secret pass"       # spaces kept


def test_load_credentials_missing_file(tmp_path):
    with pytest.raises(ShoptetError, match="chýba"):
        load_credentials(str(tmp_path / "nope"))


def test_load_credentials_missing_key(tmp_path):
    path = _write(tmp_path, "SHOPTET_ADMIN_URL=https://x/\nSHOPTET_USER=a\n")
    with pytest.raises(ShoptetError, match="SHOPTET_PASS"):
        load_credentials(path)


def _csv(tmp_path, rows):
    p = tmp_path / "import.csv"
    with open(p, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";", lineterminator="\r\n")
        w.writerow(EXPECTED_HEADER)
        w.writerows(rows)
    return str(p)


def test_classify_row_types():
    assert classify_row({"textProperty10": "https://h/x", "productVisibility": ""}) == "link"
    assert classify_row({"textProperty10": "", "productVisibility": "detailOnly"}) == "discontinued"
    assert classify_row({"textProperty10": "", "productVisibility": "visible",
                         "availabilityInStock": "Vypredané"}) == "unavailable"
    assert classify_row({"textProperty10": "", "productVisibility": ""}) == "other"


def test_preflight_counts_breakdown(tmp_path):
    path = _csv(tmp_path, [
        ["A/1", "100", "https://h/x", "human matched", "", "", "", ""],     # link
        ["B", "200", "", "", "visible", "0", "Vypredané", "Vypredané"],     # unavailable
        ["C", "300", "", "", "detailOnly", "0", "Predaj výrobku skončil",
         "Predaj výrobku skončil"],                                         # discontinued
    ])
    plan = preflight_csv(path)
    assert plan["total"] == 3
    assert plan["link"] == 1 and plan["unavailable"] == 1 and plan["discontinued"] == 1
    assert plan["other"] == 0


def test_preflight_rejects_missing_paircode(tmp_path):
    p = tmp_path / "bad.csv"
    with open(p, "w", encoding="utf-8-sig", newline="") as f:
        f.write("code;textProperty10\r\nX;https://h\r\n")
    with pytest.raises(ShoptetError, match="pairCode"):
        preflight_csv(str(p))


def test_preflight_rejects_empty(tmp_path):
    p = tmp_path / "empty.csv"
    with open(p, "w", encoding="utf-8-sig", newline="") as f:
        f.write(";".join(EXPECTED_HEADER) + "\r\n")
    with pytest.raises(ShoptetError, match="žiadne"):
        preflight_csv(str(p))


def test_preflight_missing_file(tmp_path):
    with pytest.raises(ShoptetError, match="chýba"):
        preflight_csv(str(tmp_path / "nope.csv"))


def test_preflight_rejects_non_utf8(tmp_path):
    # cp1250 'č' (0xE8) as a lone byte is invalid UTF-8 -> must fail loud, not import
    p = tmp_path / "cp1250.csv"
    p.write_bytes(b"code;pairCode\r\nX;\xe8\r\n")
    with pytest.raises(ShoptetError, match="UTF-8"):
        preflight_csv(str(p))


def test_parse_import_log_known_phrasing():
    txt = "Spracované 3776, Upravené 784, Zlyhanie variantov 1"
    r = parse_import_log(txt)
    assert r["processed"] == 3776 and r["updated"] == 784 and r["failed"] == 1


def test_parse_import_log_colon_and_newlines():
    txt = "Spracované záznamy: 12\nUpravené produkty: 5\nChyby: 0\n"
    r = parse_import_log(txt)
    assert r["processed"] == 12 and r["updated"] == 5 and r["failed"] == 0


def test_parse_import_log_missing_numbers():
    r = parse_import_log("import prebehol")
    assert r["processed"] is None and r["updated"] is None and r["failed"] is None
    assert r["raw"] == "import prebehol"
