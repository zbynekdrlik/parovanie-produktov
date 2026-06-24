from parovanie.report_io import read_report, write_report_rows
from parovanie.writer import REPORT_COLS


def test_roundtrip(tmp_path):
    rows = [dict(zip(REPORT_COLS,
                     ["BETALOV", "OB570", "HART", "OB570", "https://h/x",
                      "high", "1", "2", "", "", ""]))]
    path = tmp_path / "r.csv"
    write_report_rows(rows, str(path))
    back = read_report(str(path))
    assert back[0]["chosen_url"] == "https://h/x"
    assert back[0]["verdict"] == ""
