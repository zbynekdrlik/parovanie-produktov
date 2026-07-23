#!/usr/bin/env python3
"""One-shot migration of the n8n „Polovnicke vystavy" Google Sheet export into the
app's own store `data/out/vystavy.json` (#111).

Reads `data/out/vystavy_import.csv` (UTF-8, the Sheet export), maps each row to a
výstava object with a stable uuid4, and normalizes the scattered `email_status`
spellings (odslany/odoslany/ososlaný → the canonical `otazka`) into the app's
state machine values. Message-ID columns (`email_otazka`/`email_ziadost`) carry an
actual `<...@forestshop.sk>` msgid only sometimes — a free-text note there (e.g.
„poslať dokumenty na mesto") is NOT a msgid and is ignored.

Idempotent: refuses to overwrite an existing `vystavy.json` unless `--force`, so a
redeploy never clobbers the manager's live edits.

Run:  PYTHONPATH=src python scripts/migrate_vystavy.py [--force]
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SRC = os.path.join(ROOT, "data", "out", "vystavy_import.csv")
DEFAULT_DST = os.path.join(ROOT, "data", "out", "vystavy.json")

# Canonical state-machine values (kept 1:1 with the n8n `email_status`).
STATUS_NEW = ""                                  # Nová
STATUS_OTAZKA = "otazka"                          # Otázka poslaná
STATUS_AKCIA = "akcia bude"                        # Odpovedali — čaká na rozhodnutie
STATUS_POZIADANE = "poziadane"                      # Prihláška poslaná
STATUS_HOTOVO = "odpovedane od organizatora"        # Potvrdené (konečný)

VALID_STATUSES = {STATUS_NEW, STATUS_OTAZKA, STATUS_AKCIA, STATUS_POZIADANE, STATUS_HOTOVO}

# The Sheet's „email_status" is spelled several ways for the same state — the three
# typo variants of „odoslaný" all mean the intro question went out = `otazka`.
_OTAZKA_SPELLINGS = {"odslany", "odoslany", "odoslaný", "ososlany", "ososlaný", "odslaný"}


def normalize_status(raw: str) -> str:
    """Map a raw Sheet `email_status` to a canonical state. Unknown / blank → New."""
    s = (raw or "").strip()
    if not s:
        return STATUS_NEW
    low = s.casefold()
    if low in _OTAZKA_SPELLINGS:
        return STATUS_OTAZKA
    if low == "akcia bude":
        return STATUS_AKCIA
    if low == "poziadane":
        return STATUS_POZIADANE
    if low == "odpovedane od organizatora":
        return STATUS_HOTOVO
    # An unrecognised value is treated as a fresh výstava (New) rather than
    # carried through as an invalid state — logged so the migration is auditable.
    print(f"migrate_vystavy: neznámy email_status {s!r} → beriem ako Nová", file=sys.stderr)
    return STATUS_NEW


def looks_like_msgid(value: str) -> bool:
    """A real RFC Message-ID: <local@domain>. Free-text notes in that Sheet column
    (e.g. „poslať dokumenty na mesto") are NOT msgids and must be dropped."""
    s = (value or "").strip()
    return s.startswith("<") and s.endswith(">") and "@" in s


def _msgid(value: str) -> str:
    s = (value or "").strip()
    return s if looks_like_msgid(s) else ""


def map_row(row: dict) -> dict:
    """One Sheet row → one výstava object (with a fresh uuid4 hex id)."""
    ziadost = (row.get("ziadost") or "").strip().casefold()
    return {
        "id": uuid.uuid4().hex,
        "nazov": (row.get("Názov poľovníckych dní") or "").strip(),
        "datum": (row.get("Dátum") or "").strip(),
        "miesto": (row.get("Miesto") or "").strip(),
        "kontakt_osoba": (row.get("Kontaktná osoba") or "").strip(),
        "tel": (row.get("Tel. číslo") or "").strip(),
        "email": (row.get("email") or "").strip(),
        "velkost_stanku": (row.get("velkost_stanku") or "").strip(),
        # month name is a filter key for chain A — normalize to lowercase/trim
        "kedy_riesit": (row.get("kedy riesit") or "").strip().casefold(),
        # ziadost == 'pdf' → manual (automation sends no mail); anything else → email
        "sposob": "pdf" if ziadost == "pdf" else "email",
        "status": normalize_status(row.get("email_status")),
        "email_datum": (row.get("email_datum") or "").strip(),
        "email_otazka_msgid": _msgid(row.get("email_otazka")),
        "email_ziadost_msgid": _msgid(row.get("email_ziadost")),
        "feed": [],
    }


def migrate(csv_text: str) -> list[dict]:
    """Parse the Sheet CSV text → list of výstava objects. Rows with an empty
    `nazov` are skipped (a blank spreadsheet row is not a výstava)."""
    reader = csv.DictReader(io.StringIO(csv_text))
    out = []
    for row in reader:
        obj = map_row(row)
        if not obj["nazov"]:
            continue
        out.append(obj)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Migrate vystavy_import.csv → vystavy.json")
    ap.add_argument("--src", default=DEFAULT_SRC, help="source CSV (Sheet export)")
    ap.add_argument("--dst", default=DEFAULT_DST, help="destination vystavy.json")
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing vystavy.json (re-migration)")
    args = ap.parse_args(argv)

    if os.path.exists(args.dst) and not args.force:
        print(f"migrate_vystavy: {args.dst} už existuje — použi --force na re-migráciu",
              file=sys.stderr)
        return 1
    with open(args.src, encoding="utf-8") as f:
        vystavy = migrate(f.read())
    tmp = args.dst + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(vystavy, f, ensure_ascii=False, indent=2)
    os.replace(tmp, args.dst)
    print(f"migrate_vystavy: {len(vystavy)} výstav → {args.dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
