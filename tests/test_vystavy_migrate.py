"""Migration + store tests for „Poľovnícke výstavy" (#111, Task 1).

Pure: exercises scripts/migrate_vystavy.py's column mapping + status normalization +
Message-ID extraction against a fixture CSV (a trimmed copy of the real Sheet export),
and the idempotent file guard. No network, no live Sheet.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import migrate_vystavy as mv  # noqa: E402

# A representative slice of the real vystavy_import.csv (UTF-8): the odslany typo,
# the ososlaný typo, an akcia-bude row whose email_otazka is a NOTE (not a msgid),
# an empty-status row, a pdf-ziadost row, and the fully-threaded marekova row.
CSV = (
    "Dátum,kedy riesit,ziadost,Názov poľovníckych dní,Miesto,Kontaktná osoba,"
    "Tel. číslo,email,velkost_stanku,email_datum,email_status,uzavierka prihlašiek,"
    "Prihlaška poslaná,suhlasne_stanovisko,email_otazka,email_ziadost,discord\n"
    "27.03.,Január,email,Hunting Expo Pieštany,Pieštany,p. Černek,,cernok@agrokomplex.sk,"
    "8x3,15-04-2026,odslany,,,,,,\n"
    "01.05.,,email,Dni Stromu Topolčianky,Topolčianky,Lesy SR,421 905 444 068,"
    "tlacove@lesy.sk,,15-04-2026,odslany,,,,,,\n"
    "15.8.2026,jún,email,Poľovnícky deň Malacky,Malacky,,,malacky@opk.sk,9x3,"
    "2026-06-02T09:00:15.084+02:00,akcia bude,,,ano,poslať dokumenty na mesto,,\n"
    "13.9.2026,,,Den sv. Huberta Snina,Snina,,,,,,,,,,,,\n"
    "5.9.2026,jún,email,Den Sv. Huberta Bijacovce,Bijacovce,Dilong Michal,0907 960 621,"
    "dilong.michal@slspo.sk,9x3,18-06-2026,ososlaný,,ano,ano,,,\n"
    "12-13.9.2026,apríl,pdf,Dni Sv. Huberta Anton,Svätý Anton,,,korenova@msa.sk,,"
    "16-04-2026,odoslany,15.maj,ano,ano,,,\n"
    "19.6,apríl,email,marekova testovacia vystava,spiska osada,marek drlik,"
    "421 095 889 182,drlik.marek@gmail.com,9x3,2026-04-30T09:00:06.928+02:00,"
    "odpovedane od organizatora,,ano,,"
    "<31a0971e-4fdf-944f-8b6a-51802fdcf28b@forestshop.sk>,"
    "<7dcaa97d-6522-d03f-24e7-9cd96865cba9@forestshop.sk>,1499304360780496966\n"
    # a fully blank spreadsheet row (no name) → skipped
    ",,,,,,,,,,,,,,,,\n"
)


@pytest.fixture
def rows():
    return mv.migrate(CSV)


def _by_name(rows, name):
    return next(r for r in rows if r["nazov"] == name)


def test_blank_rows_skipped_and_count(rows):
    # 7 data rows carry a name; the all-empty row is dropped
    assert len(rows) == 7
    assert all(r["nazov"] for r in rows)


def test_column_mapping(rows):
    r = _by_name(rows, "Hunting Expo Pieštany")
    assert r["datum"] == "27.03." and r["miesto"] == "Pieštany"
    assert r["kontakt_osoba"] == "p. Černek"
    assert r["email"] == "cernok@agrokomplex.sk"
    assert r["velkost_stanku"] == "8x3"
    assert r["kedy_riesit"] == "január"       # lowercased
    assert r["sposob"] == "email"
    assert r["feed"] == []
    assert len(r["id"]) == 32                  # uuid4 hex


def test_status_typos_normalize_to_otazka(rows):
    assert _by_name(rows, "Hunting Expo Pieštany")["status"] == "otazka"   # odslany
    assert _by_name(rows, "Den Sv. Huberta Bijacovce")["status"] == "otazka"  # ososlaný
    assert _by_name(rows, "Dni Sv. Huberta Anton")["status"] == "otazka"   # odoslany


def test_status_passthrough_values(rows):
    assert _by_name(rows, "Poľovnícky deň Malacky")["status"] == "akcia bude"
    assert _by_name(rows, "marekova testovacia vystava")["status"] == "odpovedane od organizatora"


def test_empty_status_stays_new(rows):
    assert _by_name(rows, "Den sv. Huberta Snina")["status"] == ""


def test_sposob_pdf_flag(rows):
    assert _by_name(rows, "Dni Sv. Huberta Anton")["sposob"] == "pdf"
    assert _by_name(rows, "Den sv. Huberta Snina")["sposob"] == "email"   # empty ziadost → email


def test_msgid_note_is_not_extracted(rows):
    # Malacky's email_otazka is a free-text note ("poslať dokumenty na mesto"), NOT a msgid
    r = _by_name(rows, "Poľovnícky deň Malacky")
    assert r["email_otazka_msgid"] == ""
    assert r["email_ziadost_msgid"] == ""


def test_real_msgids_extracted(rows):
    r = _by_name(rows, "marekova testovacia vystava")
    assert r["email_otazka_msgid"] == "<31a0971e-4fdf-944f-8b6a-51802fdcf28b@forestshop.sk>"
    assert r["email_ziadost_msgid"] == "<7dcaa97d-6522-d03f-24e7-9cd96865cba9@forestshop.sk>"


def test_normalize_status_unknown_becomes_new():
    assert mv.normalize_status("čosi divné") == ""
    assert mv.normalize_status("") == ""
    assert mv.normalize_status("  odslany ") == "otazka"


def test_looks_like_msgid():
    assert mv.looks_like_msgid("<a@b.sk>") is True
    assert mv.looks_like_msgid("poslať dokumenty") is False
    assert mv.looks_like_msgid("") is False


def test_main_is_idempotent_without_force(tmp_path):
    src = tmp_path / "in.csv"
    src.write_text(CSV, encoding="utf-8")
    dst = tmp_path / "vystavy.json"
    assert mv.main(["--src", str(src), "--dst", str(dst)]) == 0
    first = json.loads(dst.read_text(encoding="utf-8"))
    assert len(first) == 7
    # second run WITHOUT --force must refuse (rc 1) and leave the file untouched
    assert mv.main(["--src", str(src), "--dst", str(dst)]) == 1
    assert json.loads(dst.read_text(encoding="utf-8")) == first
    # --force re-migrates (fresh uuids, same count)
    assert mv.main(["--src", str(src), "--dst", str(dst), "--force"]) == 0
    second = json.loads(dst.read_text(encoding="utf-8"))
    assert len(second) == 7
    assert {r["id"] for r in second} != {r["id"] for r in first}   # regenerated ids
