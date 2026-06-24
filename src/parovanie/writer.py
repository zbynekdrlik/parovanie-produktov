from __future__ import annotations
import csv
from parovanie.models import Match


def _writer(f):
    return csv.writer(f, delimiter=";", quoting=csv.QUOTE_MINIMAL,
                      lineterminator="\r\n")


def write_import(matches: list[Match], path: str) -> None:
    with open(path, "w", encoding="cp1250", errors="replace", newline="") as f:
        w = _writer(f)
        w.writerow(["code", "textProperty10"])
        for m in matches:
            if m.chosen is None:
                continue
            for code in m.product.variant_codes:
                w.writerow([code, m.chosen.url])


REPORT_COLS = ["supplier", "external_code", "name", "query", "chosen_url",
               "confidence", "candidate_count", "variant_count",
               "verdict", "verdict_reason", "attempts"]


def write_report(matches: list[Match], path: str) -> None:
    with open(path, "w", encoding="cp1250", errors="replace", newline="") as f:
        w = _writer(f)
        w.writerow(REPORT_COLS)
        for m in matches:
            w.writerow([
                m.product.supplier, m.product.external_code or "", m.product.name,
                m.query, m.chosen.url if m.chosen else "", m.confidence,
                m.candidate_count, len(m.product.variant_codes),
                "", "", "",
            ])


def write_unmatched(matches: list[Match], path: str) -> None:
    with open(path, "w", encoding="cp1250", errors="replace", newline="") as f:
        w = _writer(f)
        w.writerow(["supplier", "external_code", "name", "query", "candidate_count"])
        for m in matches:
            if m.chosen is None:
                w.writerow([m.product.supplier, m.product.external_code or "",
                            m.product.name, m.query, m.candidate_count])
