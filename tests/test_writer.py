import csv
import io
from parovanie.models import Product, Candidate, Match
from parovanie.writer import (
    shoptet_dict_writer, shoptet_writer, write_import, write_report, write_unmatched,
)


def _matches():
    p1 = Product("BETALOV", "k1", "OB570", "HART RANDO", ["60177/46", "60177/48"])
    p2 = Product("WETLAND", "k2", None, "Neznámy", ["99/1"])
    m1 = Match(p1, "OB570", Candidate("HART", "https://h/hart-ob570"), "high", 2)
    m2 = Match(p2, "Neznámy", None, "none", 0)
    return [m1, m2]


def test_import_has_one_row_per_variant(tmp_path):
    path = tmp_path / "imp.csv"
    write_import(_matches(), str(path), {"60177/46": "K1", "60177/48": "K1"})
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f, delimiter=";"))
    assert rows[0] == ["code", "pairCode", "internalNote"]
    body = rows[1:]
    assert ["60177/46", "K1", "https://h/hart-ob570"] in body
    assert ["60177/48", "K1", "https://h/hart-ob570"] in body
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


def test_shoptet_dict_writer_matches_shoptet_writer_dialect():
    """shoptet_dict_writer (DictWriter) must emit the SAME canonical dialect as
    shoptet_writer (plain writer) — ';' delimiter, CRLF line ending, minimal
    quoting — so producers can pick either without the on-disk format drifting
    (#12: report_io used to re-declare these params by hand)."""
    plain_buf = io.StringIO()
    shoptet_writer(plain_buf).writerow(["a", "b;c", "d"])

    dict_buf = io.StringIO()
    w = shoptet_dict_writer(dict_buf, fieldnames=["x", "y", "z"])
    w.writerow({"x": "a", "y": "b;c", "z": "d"})

    assert plain_buf.getvalue() == dict_buf.getvalue() == 'a;"b;c";d\r\n'


def test_shoptet_dict_writer_ignores_extra_fields():
    """report_io passes extrasaction='ignore' through **kw — verify that still
    works (a plain row dict with keys beyond fieldnames must not raise)."""
    buf = io.StringIO()
    w = shoptet_dict_writer(buf, fieldnames=["x"], extrasaction="ignore")
    w.writerow({"x": "1", "extra": "dropped"})
    assert buf.getvalue() == "1\r\n"
