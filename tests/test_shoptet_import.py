# tests/test_shoptet_import.py
import csv

import pytest

from parovanie.shoptet_import import (
    EXPECTED_HEADER,
    ShoptetError,
    classify_row,
    load_credentials,
    parse_import_log,
    pick_result_row,
    preflight_csv,
    result_exit_code,
)


def _write(tmp_path, text):
    p = tmp_path / ".shoptet_admin"
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_result_exit_code_unreadable_is_nonzero():
    # processed=None (Log unreadable) → never report success
    assert result_exit_code({"processed": None, "updated": None, "failed": None}) == 2
    assert result_exit_code(None) == 2


def test_result_exit_code_failures_is_nonzero():
    assert result_exit_code({"processed": 100, "updated": 50, "failed": 3}) == 2


def test_result_exit_code_clean_is_zero():
    assert result_exit_code({"processed": 100, "updated": 50, "failed": None}) == 0
    assert result_exit_code({"processed": 100, "updated": 50, "failed": 0}) == 0


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
    assert classify_row({"internalNote": "https://h/x", "productVisibility": ""}) == "link"
    assert classify_row({"internalNote": "", "productVisibility": "detailOnly"}) == "discontinued"
    assert classify_row({"internalNote": "", "productVisibility": "visible",
                         "availabilityInStock": "Vypredané"}) == "unavailable"
    assert classify_row({"internalNote": "", "productVisibility": ""}) == "other"


def test_preflight_counts_breakdown(tmp_path):
    path = _csv(tmp_path, [
        ["A/1", "100", "https://h/x", "", "", "", ""],                  # link (internalNote)
        ["B", "200", "", "visible", "0", "Vypredané", "Vypredané"],     # unavailable
        ["C", "300", "", "detailOnly", "0", "Predaj výrobku skončil",
         "Predaj výrobku skončil"],                                     # discontinued
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
    # unrecognised text → all None. The browser shell (scripts/shoptet_import.py)
    # treats processed=None as an UNREADABLE result and exits 2 (never silent success).
    # That caller branch is browser-only and is verified live, not in CI.
    r = parse_import_log("import prebehol")
    assert r["processed"] is None and r["updated"] is None and r["failed"] is None
    assert r["raw"] == "import prebehol"


def test_parse_import_log_real_failed_line_not_fooled_by_chybou():
    # real Shoptet phrasing: 'chybou' is prose (no count) — 'failed' must read 'Zlyhanie … N'
    txt = "Import skončil s chybou. Spracované: 50. Zlyhanie variantov: 3."
    r = parse_import_log(txt)
    assert r["processed"] == 50
    assert r["failed"] == 3        # NOT 50 (must not grab the processed number after 'chybou')
    assert r["updated"] is None


def test_parse_import_log_czech_success_line():
    r = parse_import_log("Import doběhl úspěšně. Zpracováno: 9. Upraveno: 4.")
    assert r["processed"] == 9 and r["updated"] == 4 and r["failed"] is None


def test_classify_externalcode_row():
    # an externalCode write-back row: externalCode set, no internalNote / visibility
    assert classify_row({"code": "60645/L", "pairCode": "395",
                         "externalCode": "1547734519"}) == "externalcode"


def test_classify_row_externalcode_does_not_shadow_link():
    # a link row (internalNote set) stays "link" even if externalCode is also present
    assert classify_row({"internalNote": "https://h/x", "productVisibility": "",
                         "externalCode": "1547734519"}) == "link"


def test_preflight_counts_externalcode(tmp_path):
    # UTF-8-BOM CSV with the externalCode header + one row -> plan counts it
    p = tmp_path / "ext.csv"
    with open(p, "w", encoding="utf-8-sig", newline="") as f:
        f.write("code;pairCode;externalCode\r\n60645/L;395;1547734519\r\n")
    plan = preflight_csv(str(p))
    assert plan["total"] == 1
    assert plan["externalcode"] == 1
    assert plan["other"] == 0


# --------------------------------------------------------------------------- #
# #23 — result read-back must never grab a STALE row / mask a hard error
# --------------------------------------------------------------------------- #
def test_parse_import_log_hard_error_row_no_summary():
    # Shoptet ABORTED the whole import (duplicate 'code' column) — the log page
    # carries ONLY this hard error line, no Spracované/Zlyhanie summary at all.
    # processed must stay None (=> never reported as success) and the raw error
    # text must be surfaced explicitly (not just silently swallowed).
    txt = "Chyba | Číslo riadku: 42 - Data in column code are not unique"
    r = parse_import_log(txt)
    assert r["processed"] is None
    assert r["failed"] is None
    assert r["error_detail"] == txt
    assert result_exit_code(r) == 2   # never a silent success


def test_parse_import_log_clean_result_has_no_error_detail():
    txt = "Spracované: 100. Upravené: 50. Zlyhanie variantov: 0."
    r = parse_import_log(txt)
    assert r["error_detail"] is None
    assert result_exit_code(r) == 0


def test_parse_import_log_chybou_prose_not_mistaken_for_hard_error():
    # 'chybou' (declined prose, "skončil s chybou") must NOT trip error_detail —
    # only relevant when there's genuinely no processed count at all.
    txt = "Import skončil s chybou. Spracované: 50. Zlyhanie variantov: 3."
    r = parse_import_log(txt)
    assert r["error_detail"] is None
    assert r["processed"] == 50 and r["failed"] == 3


def test_pick_result_row_picks_hard_error_over_older_stale_success():
    # THE issue #23 regression: a failed 690-row import wrote ONLY a hard
    # 'Chyba | Číslo riadku' entry (no Spracované/Zlyhanie line) at the TOP of
    # the log table (Shoptet renders newest-first). The OLD keyword-only
    # picker (spracov|zpracov|upraven|zlyhan) didn't match that row, fell
    # through to an OLDER completed run further down, and reported ITS
    # 'spracované=19' as if it were this run's result. The picker must return
    # the TOP error row, never skip past it.
    rows = [
        "Chyba | Číslo riadku: 42 - Data in column code are not unique",
        "12.7.2026 10:00  Spracované: 19. Upravené: 19. Zlyhanie variantov: 0.",
    ]
    row = pick_result_row(rows)
    assert row == rows[0]
    assert "chyba" in row.lower()
    # and parsing that row never looks like success
    assert result_exit_code(parse_import_log(row)) == 2


def test_pick_result_row_skips_header_row():
    rows = ["Dátum Výsledok", "Spracované: 5. Upravené: 5."]
    assert pick_result_row(rows) == rows[1]


def test_pick_result_row_returns_none_when_table_empty_or_only_header():
    assert pick_result_row([]) is None
    assert pick_result_row(["Dátum Výsledok"]) is None


def test_pick_result_row_returns_none_when_unchanged_from_baseline():
    # a large/async import: right after submitting, the log page may not have
    # written THIS run's row yet — the topmost entry is still the run BEFORE
    # this one (the pre-submit baseline). Must return None ("not ready yet"),
    # never report the baseline row as today's result.
    baseline = "12.7.2026 10:00  Spracované: 19. Upravené: 19."
    rows = [baseline]
    assert pick_result_row(rows, baseline=baseline) is None


def test_pick_result_row_returns_new_row_once_it_differs_from_baseline():
    baseline = "12.7.2026 10:00  Spracované: 19. Upravené: 19."
    rows = ["12.7.2026 10:05  Spracované: 3776. Upravené: 784.", baseline]
    row = pick_result_row(rows, baseline=baseline)
    assert row == rows[0]
