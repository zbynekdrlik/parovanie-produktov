from __future__ import annotations
import csv
from parovanie.models import Match


def shoptet_writer(f):
    """The canonical Shoptet CSV dialect (';' delimiter, minimal quoting, CRLF).
    One home so the invariant can't drift across the import producers."""
    return csv.writer(f, delimiter=";", quoting=csv.QUOTE_MINIMAL,
                      lineterminator="\r\n")


def write_import(matches: list[Match], path: str,
                 code2pair: dict[str, str] | None = None) -> None:
    """Shoptet link import: code;pairCode;internalNote, UTF-8 with BOM (the
    documented contract — Shoptet needs both code AND pairCode, and cp1250 causes
    'č'→'è' mojibake). One row per matched variant; reorder URL in the private
    internalNote field."""
    code2pair = code2pair or {}
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = shoptet_writer(f)
        w.writerow(["code", "pairCode", "internalNote"])
        for m in matches:
            if m.chosen is None:
                continue
            for code in m.product.variant_codes:
                w.writerow([code, code2pair.get(code, ""), m.chosen.url])


REPORT_COLS = ["supplier", "external_code", "name", "query", "chosen_url",
               "confidence", "candidate_count", "variant_count",
               "verdict", "verdict_reason", "attempts"]


def write_report(matches: list[Match], path: str) -> None:
    with open(path, "w", encoding="cp1250", errors="replace", newline="") as f:
        w = shoptet_writer(f)
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
        w = shoptet_writer(f)
        w.writerow(["supplier", "external_code", "name", "query", "candidate_count"])
        for m in matches:
            if m.chosen is None:
                w.writerow([m.product.supplier, m.product.external_code or "",
                            m.product.name, m.query, m.candidate_count])
