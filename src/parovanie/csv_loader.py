from __future__ import annotations
import csv

csv.field_size_limit(10**9)


def load_rows(path: str, suppliers: set[str]) -> list[dict]:
    want = {s.strip().upper() for s in suppliers}
    out: list[dict] = []
    with open(path, encoding="cp1250", errors="replace", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            sup = (row.get("supplier") or "").strip().upper()
            if sup in want:
                out.append(row)
    return out


def load_code2pair(path: str) -> dict[str, str]:
    """Map variant `code` -> `pairCode` from a Shoptet export (cp1250, ';').
    Shoptet imports need BOTH columns; this is the one shared reader for it."""
    out: dict[str, str] = {}
    with open(path, encoding="cp1250", errors="replace", newline="") as f:
        for row in csv.DictReader(f, delimiter=";"):
            code = (row.get("code") or "").strip()
            if code:
                out[code] = (row.get("pairCode") or "").strip()
    return out
