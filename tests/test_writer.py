import csv
from parovanie.models import Product, Candidate, Match
from parovanie.writer import write_import, write_report, write_unmatched


def _matches():
    p1 = Product("BETALOV", "k1", "OB570", "HART RANDO", ["60177/46", "60177/48"])
    p2 = Product("WETLAND", "k2", None, "Neznámy", ["99/1"])
    m1 = Match(p1, "OB570", Candidate("HART", "https://h/hart-ob570"), "high", 2)
    m2 = Match(p2, "Neznámy", None, "none", 0)
    return [m1, m2]


def test_import_has_one_row_per_variant(tmp_path):
    path = tmp_path / "imp.csv"
    write_import(_matches(), str(path))
    with open(path, encoding="cp1250", newline="") as f:
        rows = list(csv.reader(f, delimiter=";"))
    assert rows[0] == ["code", "textProperty10"]
    body = rows[1:]
    assert ["60177/46", "https://h/hart-ob570"] in body
    assert ["60177/48", "https://h/hart-ob570"] in body
    assert len(body) == 2  # unmatched p2 produced no rows


def test_report_one_row_per_product(tmp_path):
    path = tmp_path / "rep.csv"
    write_report(_matches(), str(path))
    with open(path, encoding="cp1250", newline="") as f:
        rows = list(csv.reader(f, delimiter=";"))
    assert "verdict" in rows[0]
    assert len(rows) == 1 + 2  # header + 2 products


def test_unmatched_lists_only_none(tmp_path):
    path = tmp_path / "un.csv"
    write_unmatched(_matches(), str(path))
    with open(path, encoding="cp1250", newline="") as f:
        rows = list(csv.reader(f, delimiter=";"))
    assert len(rows) == 1 + 1  # header + p2
    assert rows[1][0] == "WETLAND"
