# tests/test_shoptet_import.py
import csv

import pytest

from parovanie.shoptet_import import (
    EXPECTED_HEADER,
    ShoptetError,
    chunk_outcome,
    classify_row,
    hard_error_detail,
    load_credentials,
    log_entry_id,
    parse_import_log,
    pick_result_row,
    preflight_csv,
    result_exit_code,
    result_stdout_slice,
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


# --------------------------------------------------------------------------- #
# #257 / #196 — the read-back must identify THIS run's row, not "the newest one"
#
# Real rows, copied verbatim from data/out/shoptet_import_*.log. Shoptet renders
# its Log NEWEST FIRST and prefixes every entry with its own increasing id (#N).
# --------------------------------------------------------------------------- #
ROW_BASELINE = ("#12688 26.07.2026 20:12 Info Import dobehol úspešne. "
                "Spracované: 4. Upravené: 1.")
ROW_OURS_35 = ("#12689 26.07.2026 21:00 Upozornenie Import skončil s chybou. "
               "Spracované: 35. Upravené: 31. Zlyhanie variantov: 2.")
ROW_FOREIGN_1 = ("#12704 26.07.2026 21:01 Upozornenie Import skončil s chybou. "
                 "Spracované: 1. Zlyhanie variantov: 1.")


def test_pick_result_row_ignores_a_foreign_newer_row_and_picks_our_own(): #257
    # #257: two imports write to the same Shoptet Log within ~90 s (two automations
    # in one app instance, or two instances). Taking the NEWEST row read the FOREIGN
    # 1-row supplier import as the result of OUR 35-row pairings import → the run was
    # booked as a failure and uploaded_pairings.json froze. With the row count of the
    # file we actually submitted, our own row is identifiable.
    rows = [ROW_FOREIGN_1, ROW_OURS_35, ROW_BASELINE]
    assert pick_result_row(rows, baseline=ROW_BASELINE, expected_rows=35) == ROW_OURS_35


def test_pick_result_row_never_reports_a_stale_row_as_our_result(): #196
    # #196 verbatim: the read-back reported processed=1/failed=1 while the import had
    # really processed 260. Without a baseline (the Log page rendered no entry at
    # capture time) EVERY visible entry — including days-old ones — would be a
    # candidate, so nothing may be attributed at all: fail closed, poll, exit 2.
    stale = ("#12542 23.07.2026 21:00 Upozornenie Import skončil s chybou. "
             "Spracované: 1. Zlyhanie variantov: 1.")
    ours = ("#12584 23.07.2026 22:13 Info Import dobehol úspešne. "
            "Spracované: 260. Upravené: 74.")
    assert pick_result_row([stale], baseline=None, expected_rows=260) is None
    # even a row with the RIGHT count is unattributable without a baseline — it could
    # be any older run of the same size
    assert pick_result_row([ours, stale], baseline=None, expected_rows=260) is None
    # with the baseline known, our own row is picked
    assert pick_result_row([ours, stale], baseline=stale, expected_rows=260) == ours


def test_pick_result_row_fails_closed_when_two_new_rows_match_the_count():
    # Two concurrent imports of the SAME size are genuinely indistinguishable —
    # report "unattributable" (None → exit 2), never guess one of them.
    a = "#12691 26.07.2026 21:00 Info Import dobehol úspešne. Spracované: 35. Upravené: 5."
    b = "#12690 26.07.2026 21:00 Info Import dobehol úspešne. Spracované: 35. Upravené: 31."
    assert pick_result_row([a, b, ROW_BASELINE], baseline=ROW_BASELINE,
                           expected_rows=35) is None


def test_pick_result_row_older_id_is_never_a_candidate():
    # A row whose entry id is OLDER than the baseline predates this run, whatever
    # the table order claims.
    old = "#12500 20.07.2026 21:00 Info Import dobehol úspešne. Spracované: 35. Upravené: 35."
    assert pick_result_row([old, ROW_BASELINE], baseline=ROW_BASELINE,
                           expected_rows=35) is None


def test_pick_result_row_accepts_our_hard_error_row_when_it_is_the_only_new_one():
    # A hard abort writes NO Spracované summary at all (#23) — so it can never match
    # the expected count. It is still ours when it is the single new entry.
    hard = "#12692 Chyba | Číslo riadku: 42 - Data in column code are not unique"
    assert pick_result_row([hard, ROW_BASELINE], baseline=ROW_BASELINE,
                           expected_rows=35) == hard


def test_pick_result_row_without_expected_rows_keeps_the_legacy_top_row():
    # The baseline capture itself reads the Log with no expectation — unchanged.
    rows = [ROW_FOREIGN_1, ROW_OURS_35, ROW_BASELINE]
    assert pick_result_row(rows) == ROW_FOREIGN_1


def test_log_entry_id_reads_the_shoptet_entry_number():
    assert log_entry_id(ROW_OURS_35) == 12689
    assert log_entry_id("Spracované: 5. Upravené: 5.") is None
    assert log_entry_id(None) is None
    # anchored at the START of the row: a '#42' further inside the text (an order
    # number, a row reference) is NOT the entry id — mistaking it for one would cut
    # the candidate list short and lose our own entry
    assert log_entry_id("#12689 26.07.2026 Objednávka #42 Spracované: 1.") == 12689
    assert log_entry_id("Chyba | Číslo riadku: 42 - kód #999 nie je unikátny") is None


# --------------------------------------------------------------------------- #
# #257 cause 2 — a partially-failed chunk is NOT a batch that imported nothing
# --------------------------------------------------------------------------- #
# The REAL stdout of scripts/shoptet_import.py, not just its last line: the plan, the
# backup, the login and — the trap — the echo of the baseline Log entry, which carries
# its OWN 'Spracované: N'. parse_import_log takes the FIRST match in the text, so
# parsing the whole stdout reads the PREVIOUS entry's counts as this run's result.
STDOUT_PARTIAL = (
    "Súbor:   data/out/import_links_iu65p2zl.csv\n"
    "Riadkov: 35\n"
    "  • napárované (link):        35\n"
    "[záloha] 57354090 B → data/backups/export_20260726-210026.csv\n"
    "[login] OK → https://forestshop.myshoptet.com/admin/\n"
    "[import] baseline (posledný riadok Logu pred behom): #12688 26.07.2026 20:12 "
    "Info Import dobehol úspešne. Spracované: 4. Upravené: 1.\n"
    "[import] spúšťam import …\n"
    "\nVÝSLEDOK: spracované=35 upravené=31 zlyhania=2\n")


def test_chunk_outcome_partial_when_shoptet_took_every_row_but_rejected_some():
    # The 2026-07-26 21:00 run: Shoptet really updated 31 of the 35 rows we sent,
    # yet the whole chunk was booked as 0 imported rows and the queue froze.
    parsed = parse_import_log(result_stdout_slice(STDOUT_PARTIAL))
    assert parsed["processed"] == 35 and parsed["updated"] == 31 and parsed["failed"] == 2
    assert chunk_outcome(2, parsed, rows_sent=35) == "partial"


def test_result_slice_ignores_the_baseline_echo_in_the_scripts_stdout():
    # THE trap (#196's reported symptom, verbatim): parsing the WHOLE stdout returns
    # the BASELINE row's numbers — 'processed=4/updated=1' here, 'processed=1,failed=1'
    # on 2026-07-23 while the import had really processed 260 rows. Only the text from
    # the script's own VÝSLEDOK marker on describes THIS run.
    whole = parse_import_log(STDOUT_PARTIAL)
    assert whole["processed"] == 4                      # the baseline entry, not ours
    ours = parse_import_log(result_stdout_slice(STDOUT_PARTIAL))
    assert ours["processed"] == 35
    # …and the misread turns a partially accepted chunk into a hard failure
    assert chunk_outcome(2, whole, rows_sent=35) == "failed"
    assert chunk_outcome(2, ours, rows_sent=35) == "partial"


def test_hard_error_detail_is_the_shoptet_line_alone():
    sl = ("VÝSLEDOK: spracované=None upravené=None zlyhania=None\n"
          "CHYBA LOGU: Chyba | Číslo riadku: 42 - Data in column code are not unique\n")
    # what reaches n8n / the automation card is the reason, not the block around it
    assert hard_error_detail(sl) == "Chyba | Číslo riadku: 42 - Data in column code are not unique"
    # no marker → fall back to whatever parse_import_log could tell
    plain = "Chyba | Číslo riadku: 7 - duplicitný kód"
    assert hard_error_detail(plain, parse_import_log(plain)) == plain
    assert hard_error_detail("", {}) is None


def test_result_slice_keeps_a_hard_shoptet_error_and_survives_junk():
    hard = ("[import] baseline (posledný riadok Logu pred behom): #12688 Spracované: 4.\n"
            "\nVÝSLEDOK: spracované=None upravené=None zlyhania=None\n"
            "CHYBA LOGU: Chyba | Číslo riadku: 42 - Data in column code are not unique\n")
    parsed = parse_import_log(result_stdout_slice(hard))
    assert parsed["processed"] is None
    assert "not unique" in (parsed["error_detail"] or "")
    assert chunk_outcome(2, parsed, rows_sent=35) == "failed"
    # no marker at all (script died before printing a result) → nothing to attribute
    assert result_stdout_slice("STOP: záloha zlyhala") == ""
    assert parse_import_log(result_stdout_slice(""))["processed"] is None


def test_chunk_outcome_ok_and_hard_failures():
    assert chunk_outcome(0, parse_import_log("VÝSLEDOK: spracované=35 upravené=31"),
                         rows_sent=35) == "ok"
    # unreadable result → hard failure (never partial)
    assert chunk_outcome(2, parse_import_log(""), rows_sent=35) == "failed"
    # a hard Shoptet abort (no summary at all) → hard failure
    assert chunk_outcome(2, parse_import_log(
        "Chyba | Číslo riadku: 42 - Data in column code are not unique"),
        rows_sent=35) == "failed"
    # Shoptet saw fewer rows than we sent → we cannot claim the rest landed
    assert chunk_outcome(2, parse_import_log("spracované=3 zlyhania=1"),
                         rows_sent=35) == "failed"
    # every row failed → nothing landed
    assert chunk_outcome(2, parse_import_log("spracované=35 zlyhania=35"),
                         rows_sent=35) == "failed"
    # a timeout (rc=1, no output at all)
    assert chunk_outcome(1, parse_import_log(""), rows_sent=35) == "failed"
