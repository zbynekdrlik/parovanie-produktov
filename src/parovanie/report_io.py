from __future__ import annotations
import csv
from parovanie.writer import REPORT_COLS

csv.field_size_limit(10**9)


def read_report(path: str) -> list[dict]:
    with open(path, encoding="cp1250", errors="replace", newline="") as f:
        return list(csv.DictReader(f, delimiter=";"))


def write_report_rows(rows: list[dict], path: str) -> None:
    with open(path, "w", encoding="cp1250", errors="replace", newline="") as f:
        w = csv.DictWriter(f, fieldnames=REPORT_COLS, delimiter=";",
                           quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n",
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
