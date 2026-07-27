"""In-app „Párovania → eshop" automation (#109) — the nightly push of the
workers' NEW pairings (reorder links → internalNote) + newly assigned suppliers
(→ supplier field) to the Shoptet eshop, migrated from the n8n workflow
`YuDugCCOnwejRfva` onto the generic automation runner (#93).

Hermetic: run_import (the careful Shoptet import subprocess) is monkeypatched —
NO real eshop write ever happens in a test. Every store path is redirected to
tmp. Mirrors test_webreview_shoptet_sync.py's isolation pattern; the automation
reuses the SAME upload cores (_do_upload_pairings/_do_upload_suppliers) as the
two n8n endpoints, so the existing endpoint tests in test_webreview.py stay
green (one place for the logic, NEkopíruj logiku).
"""
import csv as _csv
import inspect
import json
import os
import subprocess
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webreview"))
import app as webapp  # noqa: E402

from tests.conftest import authed_client  # noqa: E402


def _product(variant_codes=("1/M",)):
    return {"key": "BETALOV|P1", "idx": 0, "supplier": "BETALOV", "name": "Bunda Test",
            "pairCode": "P1", "variant_codes": list(variant_codes),
            "our_url": "https://forestshop/x", "ai_status": "matched",
            "ai_chosen_url": "", "ai_reason": "", "candidates": [], "current": {}}


def _export_lines(text):
    """monkeypatch value for `webapp._iter_export_lines` — the nightly push STREAMS
    the catalog export line by line (#272), so THIS is the seam a test feeds a fake
    export through (patching the whole-text `_read_export_for_links` no longer
    reaches the push: it is defined in terms of this one). Returns a fresh iterator
    on every call, exactly like the real generator."""
    return lambda: iter(text.splitlines(keepends=True))


@pytest.fixture
def iso(tmp_path, monkeypatch):
    """Isolate every store the automation reads/writes + the import subprocess."""
    monkeypatch.setattr(webapp.RUNNER, "state_path", str(tmp_path / "automations.json"))
    monkeypatch.setattr(webapp, "OUT", str(tmp_path))
    monkeypatch.setattr(webapp, "DECISIONS", str(tmp_path / "decisions.json"))
    monkeypatch.setattr(webapp, "PAIRINGS_STATE", str(tmp_path / "uploaded_pairings.json"))
    # #38: the manager's inline 'Na objednanie' pairings — own tmp path (never the
    # real live order_pairings.json this box also serves).
    monkeypatch.setattr(webapp, "ORDER_PAIRINGS", str(tmp_path / "order_pairings.json"))
    monkeypatch.setattr(webapp, "SUPPLIER_ASSIGN", str(tmp_path / "supplier_assignments.json"))
    monkeypatch.setattr(webapp, "SUPPLIERS_STATE", str(tmp_path / "uploaded_suppliers.json"))
    products = [_product()]
    monkeypatch.setattr(webapp, "PRODUCTS", products)
    monkeypatch.setattr(webapp, "CODE2PAIR", {"1/M": "P1", "9/Z": "777"})
    # BUG 1 fail-closed: the supplier write-back refuses to run without a catalog export.
    # Default to a minimal non-empty export so the normal push path proceeds (no code is
    # excluded); tests that assert the exclusion / blocked-on-empty behaviour override
    # the export seam themselves. Without this the CI box (no data/products.csv)
    # reads an empty export and the supplier upload is blocked.
    monkeypatch.setattr(webapp, "_iter_export_lines",
                        _export_lines("code;pairCode;supplier\r\n"))
    return {"tmp": tmp_path, "products": products}


def _seed_pairing():
    webapp._save_decisions({"BETALOV|P1": {"status": "good", "url": "https://supplier/x"}})


def _seed_supplier():
    webapp._save_supplier_assign({"9/Z": "BETALOV"})


def _seed_order_pairing():
    webapp._save_order_pairings({"7/Y": "https://supplier/inline"})


def _ok_import():
    """A run_import stub that records every CSV it was handed (header + rows) and
    reports a clean success. Handles both the links CSV and the suppliers CSV."""
    calls = []

    def fake_run(csv_path, dry_run=False, timeout=300):
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            rd = list(_csv.reader(f, delimiter=";"))
        calls.append({"header": rd[0], "rows": rd[1:], "dry_run": dry_run})
        # report back exactly as many rows as the CSV carried — the real script always
        # does (Shoptet's 'Spracované: N' == the rows we submitted), and an rc-0 result
        # whose count disagrees is now correctly refused as a chunk we may not credit
        n = len(rd) - 1
        return 0, f"VÝSLEDOK: spracované={n} upravené={n} zlyhania=0", ""
    return fake_run, calls


# ── registration + status ──────────────────────────────────────────────────────
def test_parovania_eshop_registered_disabled_daily_2100(iso):
    c = authed_client()
    j = c.get("/api/automations").get_json()
    (a,) = [x for x in j["automations"] if x["key"] == "parovania_eshop"]
    assert a["name"] == "Párovania → eshop"
    # SAFETY: this automation WRITES to the live eshop → deploy starts stopped (#93 contract)
    assert a["enabled"] is False
    assert a["schedule"] == "denne o 21:00"
    assert a["running"] is False


# ── successful nightly push ─────────────────────────────────────────────────────
def test_run_pushes_pairings_and_suppliers_and_records_counts(iso, monkeypatch):
    _seed_pairing()
    _seed_supplier()
    fake_run, calls = _ok_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)

    result = webapp.run_parovania_eshop()

    assert result["status"] == "ok"
    assert result["pairings"]["count"] == 1
    assert result["pairings"]["total_uploaded"] == 1
    assert result["pairings"]["total_products"] == 1
    assert result["suppliers"]["count"] == 1
    assert result["suppliers"]["total_uploaded"] == 1
    assert result["suppliers"]["total_assigned"] == 1
    assert result["review_url"].startswith("https://")

    # BOTH cores actually ran the careful import — one links CSV, one suppliers CSV
    headers = sorted(c["header"] for c in calls)
    assert headers == [["code", "pairCode", "internalNote"], ["code", "pairCode", "supplier"]]
    # the reorder link went into internalNote, the supplier name into the supplier column
    links = next(c for c in calls if c["header"][2] == "internalNote")
    assert ["1/M", "P1", "https://supplier/x"] in links["rows"]
    sup = next(c for c in calls if c["header"][2] == "supplier")
    assert ["9/Z", "777", "BETALOV"] in sup["rows"]
    # a nightly write is NEVER a dry run
    assert all(c["dry_run"] is False for c in calls)

    # its OWN incremental state written (idempotency) — NOT the manager stores
    assert json.loads((iso["tmp"] / "uploaded_pairings.json").read_text())["BETALOV|P1"] \
        == "https://supplier/x"
    assert json.loads((iso["tmp"] / "uploaded_suppliers.json").read_text())["9/Z"] == "BETALOV"


# ── #38: the nightly push ALSO covers inline order_pairings (via the SAME shared
#    _do_upload_pairings core, no new HTTP round-trip / no duplicated logic) ────
def test_run_also_pushes_inline_order_pairings(iso, monkeypatch):
    _seed_pairing()
    _seed_order_pairing()
    fake_run, calls = _ok_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)

    result = webapp.run_parovania_eshop()

    assert result["status"] == "ok"
    assert result["pairings"]["order_count"] == 1
    assert result["pairings"]["order_blocked"] == 0
    links = next(c for c in calls if c["header"][2] == "internalNote")
    assert ["7/Y", "", "https://supplier/inline"] in links["rows"]
    assert json.loads((iso["tmp"] / "uploaded_pairings.json").read_text())["order:7/Y"] \
        == "https://supplier/inline"

    # idempotent: a second run pushes neither the decision nor the order pairing again
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not re-import")))
    result2 = webapp.run_parovania_eshop()
    assert result2["pairings"]["count"] == 0 and result2["pairings"]["order_count"] == 0


# ── BUG 1: the nightly supplier write-back must NOT overwrite a REAL eshop
#    supplier with a (possibly stale) manual assignment. A per-product assignment
#    is meant to FILL IN a supplier for an order line that arrived WITHOUT one —
#    a code whose product ALREADY carries its own `supplier` in the current export
#    is excluded, so the automation never clobbers live catalog data. ──────────────
def test_do_upload_suppliers_skips_codes_with_own_supplier_in_export(iso, monkeypatch):
    # two assignments: 9/Z (no own supplier in the export → should be written) and
    # 5/A (product ALREADY has its own supplier in the export → must be excluded).
    webapp._save_supplier_assign({"9/Z": "BETALOV", "5/A": "STALE_ASSIGN"})
    monkeypatch.setattr(webapp, "CODE2PAIR", {"9/Z": "777", "5/A": "555"})
    export = ("code;pairCode;supplier\r\n"
              "9/Z;777;\r\n"
              "5/A;555;REAL_SUPPLIER\r\n")
    monkeypatch.setattr(webapp, "_iter_export_lines", _export_lines(export))
    fake_run, calls = _ok_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)

    result, status = webapp._do_upload_suppliers(dry=False)
    assert status == 200
    sup = next(c for c in calls if c["header"][2] == "supplier")
    written = {r[0] for r in sup["rows"]}
    assert written == {"9/Z"}          # 5/A excluded (own supplier already in export)
    assert "5/A" not in written
    # only the code we actually wrote is recorded as uploaded
    up = json.loads((iso["tmp"] / "uploaded_suppliers.json").read_text())
    assert up == {"9/Z": "BETALOV"}


# ── BUG 1 safety: FAIL-CLOSED when the export is missing/empty/unreadable. The
#    exclusion guard (_codes_with_own_supplier) needs the catalog export to know which
#    codes ALREADY carry their own eshop supplier. With NO export it cannot tell — it
#    must NOT fall open and write, or a stale per-product assignment could clobber a
#    real supplier in the live eshop (exactly the bug this PR fixes). The whole supplier
#    upload is skipped: 0 written, everything blocked, the idempotency store untouched. ──
def test_do_upload_suppliers_blocks_when_export_empty(iso, monkeypatch):
    webapp._save_supplier_assign({"9/Z": "BETALOV", "5/A": "ORBIS"})
    monkeypatch.setattr(webapp, "CODE2PAIR", {"9/Z": "777", "5/A": "555"})
    monkeypatch.setattr(webapp, "_iter_export_lines", _export_lines(""))  # missing/unreadable
    fake_run, calls = _ok_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)

    result, status = webapp._do_upload_suppliers(dry=False)
    assert status == 200
    assert result["count"] == 0           # nothing written to the live eshop
    assert result["blocked"] == 2         # both assignments held back
    assert calls == []                    # the careful import never ran
    # nothing recorded as uploaded → the idempotency store is never even written
    assert not (iso["tmp"] / "uploaded_suppliers.json").exists()


# ── BUG 1 (case c): a code that IS in supplier_assignments but is NOT present in the
#    current export has no "own supplier" there → it is NOT excluded, and the nightly
#    write-back STILL emits it (unchanged pre-PR behaviour). A present-but-partial
#    export must not silently drop a legitimate fill-in assignment — only a genuinely
#    empty export blocks (test above), a non-empty one that merely lacks the code writes. ─
def test_do_upload_suppliers_emits_code_absent_from_export(iso, monkeypatch):
    webapp._save_supplier_assign({"9/Z": "BETALOV"})
    monkeypatch.setattr(webapp, "CODE2PAIR", {"9/Z": "777"})
    # a NON-empty export listing OTHER products (5/A carries its own supplier) but NOT 9/Z
    export = ("code;pairCode;supplier\r\n"
              "5/A;555;REAL_SUPPLIER\r\n")
    monkeypatch.setattr(webapp, "_iter_export_lines", _export_lines(export))
    # the exclusion helper does NOT flag 9/Z (it is absent from the export)
    assert "9/Z" not in webapp._codes_with_own_supplier()
    fake_run, calls = _ok_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)

    result, status = webapp._do_upload_suppliers(dry=False)
    assert status == 200
    sup = next(c for c in calls if c["header"][2] == "supplier")
    assert {r[0] for r in sup["rows"]} == {"9/Z"}      # still written (not excluded)
    assert json.loads((iso["tmp"] / "uploaded_suppliers.json").read_text()) == {"9/Z": "BETALOV"}


def test_run_is_idempotent_second_run_pushes_nothing(iso, monkeypatch):
    _seed_pairing()
    _seed_supplier()
    fake_run, calls = _ok_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)
    webapp.run_parovania_eshop()
    assert len(calls) == 2                       # first run: links + suppliers imports

    # nothing new → the careful import must NOT run again (safe re-run, no double upload)
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not re-import")))
    result = webapp.run_parovania_eshop()
    assert result["status"] == "ok"
    assert result["pairings"]["count"] == 0 and result["suppliers"]["count"] == 0


def test_run_zero_new_reports_ok_without_importing(iso, monkeypatch):
    # no decisions, no assignments → clean no-op run (like the n8n `return []`)
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
    result = webapp.run_parovania_eshop()
    assert result["status"] == "ok"
    assert result["pairings"]["count"] == 0 and result["suppliers"]["count"] == 0


# ── graceful degradation ────────────────────────────────────────────────────────
def test_import_failure_surfaces_failed_status_and_does_not_mark_uploaded(iso, monkeypatch):
    _seed_pairing()
    monkeypatch.setattr(webapp, "run_import",
                        lambda p, dry_run=False, timeout=300: (1, "chyba", "boom"))
    result = webapp.run_parovania_eshop()
    assert result["status"] == "failed"
    assert result["pairings"]["ok"] is False
    # a failed import never records the pairing as uploaded → retried next run
    assert (not (iso["tmp"] / "uploaded_pairings.json").exists()
            or json.loads((iso["tmp"] / "uploaded_pairings.json").read_text()) == {})


def test_blocked_when_variant_codes_missing(iso, monkeypatch):
    # a paired product with NO variant codes yields 0 import rows → blocked (surfaced,
    # not silent — the n8n Sprava node did the same with a ⚠️ warning)
    monkeypatch.setattr(webapp, "PRODUCTS", [_product(variant_codes=[])])
    webapp._save_decisions({"BETALOV|P1": {"status": "good", "url": "https://supplier/x"}})
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
    result = webapp.run_parovania_eshop()
    assert result["status"] == "blocked"
    assert result["pairings"]["blocked"] == 1


# ── #156: a large batch is split into chunked imports (no single import overruns
#    the 120s browser redirect timeout — the nightly 415-product / 1195-row push
#    failed on Timeout 120000ms). Hermetic: run_import (the Playwright subprocess)
#    is mocked; the assertion is on how the rows are SPLIT across import calls. ──
def _recording_import(fail_on_call=None):
    """run_import stub recording each chunk CSV's rows; optionally FAIL the Nth call
    (1-based) to simulate a mid-batch chunk failure."""
    calls = []

    def fake_run(csv_path, dry_run=False, timeout=300):
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            rd = list(_csv.reader(f, delimiter=";"))
        rows = rd[1:]
        calls.append({"header": rd[0], "rows": rows})
        if fail_on_call is not None and len(calls) == fail_on_call:
            return 2, "POZOR: Shoptet hlási zlyhania", "boom"
        return 0, f"VÝSLEDOK: spracované={len(rows)} upravené={len(rows)} zlyhania=0", ""
    return fake_run, calls


def test_large_pairing_batch_split_into_chunks(iso, monkeypatch):
    # 650 variant codes → one link row each → must be imported in >=2 chunks, each
    # <= IMPORT_CHUNK_ROWS. RED before the fix: a single 650-row import call.
    n = 650
    codes = [f"{i}/M" for i in range(n)]
    monkeypatch.setattr(webapp, "PRODUCTS", [_product(variant_codes=codes)])
    monkeypatch.setattr(webapp, "CODE2PAIR", {c: "P1" for c in codes})
    _seed_pairing()
    fake_run, calls = _recording_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)

    result = webapp.run_parovania_eshop()

    assert len(calls) >= 2                                   # split, not one giant import
    assert max(len(c["rows"]) for c in calls) <= webapp.IMPORT_CHUNK_ROWS
    # every code imported exactly once across the chunks — no loss, no duplicate
    imported = [r[0] for c in calls for r in c["rows"]]
    assert sorted(imported) == sorted(codes)
    # whole push succeeded → the single key is recorded uploaded (idempotent state)
    assert result["status"] == "ok"
    assert result["pairings"]["ok"] is True and result["pairings"]["count"] == 1
    assert json.loads((iso["tmp"] / "uploaded_pairings.json").read_text()) \
        == {"BETALOV|P1": "https://supplier/x"}


def test_large_supplier_batch_split_into_chunks(iso, monkeypatch):
    # the supplier write-back path is chunked too (#156 names pairings + suppliers).
    n = 400
    assigns = {f"{i}/S": f"SUP{i}" for i in range(n)}
    monkeypatch.setattr(webapp, "CODE2PAIR", {f"{i}/S": "P" for i in range(n)})
    webapp._save_supplier_assign(assigns)
    fake_run, calls = _recording_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)

    result = webapp.run_parovania_eshop()

    sup_calls = [c for c in calls if c["header"][2] == "supplier"]
    assert len(sup_calls) >= 2
    assert max(len(c["rows"]) for c in sup_calls) <= webapp.IMPORT_CHUNK_ROWS
    assert result["suppliers"]["ok"] is True and result["suppliers"]["count"] == n


def test_mid_batch_chunk_failure_records_partial_and_releases_lock(iso, monkeypatch):
    # #156: a chunk failing mid-batch must → failed status, record ONLY the codes
    # from the SUCCESSFUL chunk(s) (resumable — never all-or-nothing silent success),
    # and release the import lock (no stuck lock → no cascade failure like 21:03).
    n = 650
    products = [{"key": f"K{i}", "idx": i, "supplier": "BETALOV", "name": f"P{i}",
                 "pairCode": "P", "variant_codes": [f"{i}/M"], "our_url": "u",
                 "ai_status": "matched", "ai_chosen_url": "", "ai_reason": "",
                 "candidates": [], "current": {}} for i in range(n)]
    monkeypatch.setattr(webapp, "PRODUCTS", products)
    monkeypatch.setattr(webapp, "CODE2PAIR", {f"{i}/M": "P" for i in range(n)})
    webapp._save_decisions({f"K{i}": {"status": "good", "url": f"https://s/{i}"} for i in range(n)})
    fake_run, calls = _recording_import(fail_on_call=2)     # 1st chunk ok, 2nd fails
    monkeypatch.setattr(webapp, "run_import", fake_run)

    result = webapp.run_parovania_eshop()

    assert result["status"] == "failed"
    assert result["pairings"]["ok"] is False
    # a clear, tab-surfaced message: WHICH chunk failed + how many rows made it
    assert "časti 2/" in result["pairings"]["error"]
    assert "z 650 riadkov" in result["pairings"]["error"]
    assert len(calls) == 2                                  # batch STOPS after the failing chunk
    uploaded = json.loads((iso["tmp"] / "uploaded_pairings.json").read_text())
    # exactly the successful (first) chunk's keys are recorded; the failing chunk's
    # keys stay "new" so the next run retries them (partial progress, not lost work)
    chunk1_keys = {"K" + r[0].split("/")[0] for r in calls[0]["rows"]}
    chunk2_keys = {"K" + r[0].split("/")[0] for r in calls[1]["rows"]}
    assert set(uploaded) == chunk1_keys
    assert not (set(uploaded) & chunk2_keys)
    assert 0 < len(uploaded) < n
    # the import lock was released despite the failure (else the next import 409s)
    assert webapp._import_lock.acquire(blocking=False)
    webapp._import_lock.release()


def test_small_batch_still_single_import(iso, monkeypatch):
    # a small batch must NOT be needlessly chunked — one import call, as before.
    _seed_pairing()
    fake_run, calls = _recording_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)
    webapp.run_parovania_eshop()
    link_calls = [c for c in calls if c["header"][2] == "internalNote"]
    assert len(link_calls) == 1 and len(link_calls[0]["rows"]) == 1


def test_run_via_runner_records_error_when_import_raises(iso, monkeypatch):
    _seed_pairing()

    def boom(*a, **k):
        raise RuntimeError("shoptet_import.py spadol")
    monkeypatch.setattr(webapp, "run_import", boom)

    assert webapp.RUNNER._execute("parovania_eshop") is True    # runner survives
    (st,) = [x for x in webapp.RUNNER.status() if x["key"] == "parovania_eshop"]
    assert st["last_status"] == "error"
    assert "shoptet_import.py spadol" in st["last_error"]
    assert st["running"] is False


# ── disabled automation never runs on a scheduler tick ──────────────────────────
def test_disabled_automation_is_not_ticked(iso, monkeypatch):
    _seed_pairing()
    _seed_supplier()
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("disabled must not run")))
    webapp.RUNNER.tick_once()                    # default state = disabled
    (st,) = [x for x in webapp.RUNNER.status() if x["key"] == "parovania_eshop"]
    assert st["enabled"] is False
    assert st["last_run"] == ""                  # never ran


# ── http run endpoint + runner integration ──────────────────────────────────────
def test_run_now_via_http_endpoint_and_runner(iso, monkeypatch):
    _seed_pairing()
    _seed_supplier()
    fake_run, _calls = _ok_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)
    c = authed_client()
    r = c.post("/api/automations/parovania_eshop/run")
    assert r.status_code == 200 and r.get_json()["started"] is True
    webapp.RUNNER._threads["parovania_eshop"].join(timeout=15)
    (st,) = [x for x in webapp.RUNNER.status() if x["key"] == "parovania_eshop"]
    assert st["last_status"] == "ok"
    assert st["last_result"]["status"] == "ok"
    assert st["last_result"]["pairings"]["count"] == 1
    assert st["enabled"] is False                # run-now must not enable the schedule


# ── never modifies the manager's decision stores (reads only) ───────────────────
def test_run_reads_but_never_writes_manager_decision_stores(iso, monkeypatch):
    webapp._save_decisions({"BETALOV|P1": {"status": "good", "url": "https://supplier/x"}})
    webapp._save_supplier_assign({"9/Z": "BETALOV"})
    dec_before = (iso["tmp"] / "decisions.json").read_text()
    sa_before = (iso["tmp"] / "supplier_assignments.json").read_text()
    fake_run, _calls = _ok_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)

    webapp.run_parovania_eshop()

    # the manager's live stores are untouched (the automation only READS them)
    assert (iso["tmp"] / "decisions.json").read_text() == dec_before
    assert (iso["tmp"] / "supplier_assignments.json").read_text() == sa_before


# ── #257 cause 2: a partially-failed chunk is not a batch that imported nothing ──
#
# The real 2026-07-26 21:00 run: 35 rows sent, Shoptet answered
#   '#12689 … Spracované: 35. Upravené: 31. Zlyhanie variantov: 2.'
# i.e. it took every row we sent and rejected 2 variants. The whole chunk was booked
# as 0 imported rows, uploaded_pairings.json froze on 2026-07-22 and the same rows
# were rebuilt + re-sent every night.
PARTIAL_STDOUT = "VÝSLEDOK: spracované={n} upravené={u} zlyhania=2"


def _partial_import(partial_on_call=1):
    """run_import stub whose Nth chunk answers like the real partial night: Shoptet
    processed EVERY row we sent, but rejected 2 variants (script exit code 2)."""
    calls = []

    def fake_run(csv_path, dry_run=False, timeout=300):
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            rd = list(_csv.reader(f, delimiter=";"))
        rows = rd[1:]
        calls.append({"header": rd[0], "rows": rows})
        if len(calls) == partial_on_call:
            return 2, PARTIAL_STDOUT.format(n=len(rows), u=len(rows) - 4), ""
        return 0, f"VÝSLEDOK: spracované={len(rows)} upravené={len(rows)} zlyhania=0", ""
    return fake_run, calls


def _export(pairs):
    """A minimal catalog export (the eshop's own truth) carrying code→internalNote."""
    head = "code;pairCode;internalNote;supplier\r\n"
    return head + "".join(f"{c};P1;{note};\r\n" for c, note in pairs.items())


def test_partial_stdout_is_read_from_the_scripts_own_result_line(iso, monkeypatch):
    """The import script's stdout STARTS with an echo of the baseline Log entry, which
    carries its own 'Spracované: N'. Parsing the whole stdout read THAT as the result
    (#196's processed=1/failed=1 while 260 rows really went through) — and it silently
    disabled the whole partial-chunk fix, because the baseline counts never match the
    number of rows we sent, so every partial chunk was classified as a hard failure."""
    codes = ("1/M", "1/L", "1/XL")
    monkeypatch.setattr(webapp, "PRODUCTS", [_product(variant_codes=codes)])
    monkeypatch.setattr(webapp, "CODE2PAIR", {c: "P1" for c in codes})
    _seed_pairing()
    real_stdout = (
        "Súbor:   data/out/import_links_x.csv\nRiadkov: 3\n"
        "[import] baseline (posledný riadok Logu pred behom): #12688 26.07.2026 20:12 "
        "Info Import dobehol úspešne. Spracované: 4. Upravené: 1.\n"
        "[import] spúšťam import …\n"
        "\nVÝSLEDOK: spracované=3 upravené=2 zlyhania=1\n")
    monkeypatch.setattr(webapp, "run_import",
                        lambda p, dry_run=False, timeout=300: (2, real_stdout, ""))

    p, _status = webapp._do_upload_pairings(dry=False)

    assert p["partial"] is True           # 3 rows sent, 3 processed, 1 rejected
    assert p["rejected"] == 1
    assert p["processed"] == 3 and p["updated"] == 2   # ours, not the baseline's 4/1
    # nothing credited: the log cannot say WHICH row failed and here it was the only one
    assert (not (iso["tmp"] / "uploaded_pairings.json").exists()
            or json.loads((iso["tmp"] / "uploaded_pairings.json").read_text()) == {})


def test_export_confirmation_ignores_a_code_the_export_lists_twice(iso, monkeypatch):
    # the catalog holds duplicate products sharing variant codes (see link_rows) — if
    # two export rows disagree about a code's internalNote, neither proves anything,
    # so the code must stay unconfirmed and be sent.
    _seed_pairing()
    monkeypatch.setattr(webapp, "_iter_export_lines",
                        _export_lines("code;pairCode;internalNote;supplier\r\n"
                                      "1/M;P1;https://supplier/OTHER;\r\n"
                                      "1/M;P1;https://supplier/x;\r\n"))
    fake_run, calls = _recording_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)

    result = webapp.run_parovania_eshop()

    assert result["pairings"]["confirmed_in_export"] == 0
    links = [c for c in calls if c["header"][2] == "internalNote"]
    assert links and ["1/M", "P1", "https://supplier/x"] in links[0]["rows"]


def test_partial_message_promises_export_confirmation_only_where_it_happens(iso, monkeypatch):
    # only the pairings push reconciles against the export; the supplier write-back
    # writes a different column and never confirms anything, so its message must not
    # tell the manager the rows will be confirmed from the export.
    codes = ("1/M", "1/L", "1/XL")
    monkeypatch.setattr(webapp, "PRODUCTS", [_product(variant_codes=codes)])
    monkeypatch.setattr(webapp, "CODE2PAIR", {**{c: "P1" for c in codes},
                                              "9/Z": "777", "8/Z": "778"})
    _seed_pairing()
    webapp._save_supplier_assign({"9/Z": "BETALOV", "8/Z": "CITRADE"})

    def partial_run(csv_path, dry_run=False, timeout=300):
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            n = len(list(_csv.reader(f, delimiter=";"))) - 1
        return 2, f"VÝSLEDOK: spracované={n} upravené={n - 1} zlyhania=1", ""

    monkeypatch.setattr(webapp, "run_import", partial_run)

    result = webapp.run_parovania_eshop()

    assert "odmietol 1 z 3 riadkov" in result["pairings"]["error"]
    assert "z exportu" in result["pairings"]["error"]
    assert "odmietol 1 z 2 riadkov" in result["suppliers"]["error"]
    assert "z exportu" not in result["suppliers"]["error"]


def test_partial_chunk_keeps_importing_the_rest_of_the_batch(iso, monkeypatch):
    n = 650                                     # 3 chunks of <=300
    products = [{"key": f"K{i}", "idx": i, "supplier": "BETALOV", "name": f"P{i}",
                 "pairCode": "P", "variant_codes": [f"{i}/M"], "our_url": "u",
                 "ai_status": "matched", "ai_chosen_url": "", "ai_reason": "",
                 "candidates": [], "current": {}} for i in range(n)]
    monkeypatch.setattr(webapp, "PRODUCTS", products)
    monkeypatch.setattr(webapp, "CODE2PAIR", {f"{i}/M": "P" for i in range(n)})
    webapp._save_decisions({f"K{i}": {"status": "good", "url": f"https://s/{i}"}
                            for i in range(n)})
    fake_run, calls = _partial_import(partial_on_call=1)
    monkeypatch.setattr(webapp, "run_import", fake_run)

    result = webapp.run_parovania_eshop()

    # the batch is NOT aborted by a partial chunk — chunks 2 and 3 still go up
    assert len(calls) == 3
    p = result["pairings"]
    # the later, fully clean chunks ARE credited (they used to be lost entirely)
    uploaded = json.loads((iso["tmp"] / "uploaded_pairings.json").read_text())
    chunk1_keys = {"K" + r[0].split("/")[0] for r in calls[0]["rows"]}
    assert len(uploaded) == n - len(chunk1_keys) > 0
    assert not (set(uploaded) & chunk1_keys)    # the partial chunk stays unconfirmed
    # …and the rejection is SURFACED, not hidden behind a bare 'ok'
    assert p["ok"] is False
    assert p["partial"] is True
    assert p["rejected"] == 2
    assert "odmietol 2 z 650 riadkov" in p["error"]


def test_rows_already_correct_in_the_eshop_are_credited_from_the_export(iso, monkeypatch):
    """THE unfreeze (#257): the import log reports aggregate counts only, so a
    partially-failed chunk cannot say WHICH rows landed. The eshop's own export can —
    a code whose internalNote already equals the URL we would send is proven to be on
    the eshop, so it is recorded uploaded and never re-sent."""
    _seed_pairing()                                   # BETALOV|P1 → https://supplier/x
    monkeypatch.setattr(webapp, "_iter_export_lines",
                        _export_lines(_export({"1/M": "https://supplier/x"})))
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: pytest.fail("nothing left to import"))

    result = webapp.run_parovania_eshop()

    assert result["pairings"]["ok"] is True
    assert result["pairings"]["count"] == 1
    assert result["pairings"]["confirmed_in_export"] == 1
    assert json.loads((iso["tmp"] / "uploaded_pairings.json").read_text()) \
        == {"BETALOV|P1": "https://supplier/x"}


def test_unreadable_result_is_not_reported_as_zero_imported_rows(iso, monkeypatch):
    # An unattributable read-back (our Log entry never appeared / two same-sized
    # imports) says NOTHING about what landed — the rows very likely DID reach the
    # eshop. Claiming "naimportované 0" as a fact misleads the manager into thinking
    # nothing was written; say the result could not be read and point at the Log.
    _seed_pairing()
    monkeypatch.setattr(webapp, "run_import",
                        lambda p, dry_run=False, timeout=300:
                        (2, "\nVÝSLEDOK: spracované=None upravené=None zlyhania=None\n", ""))

    p, _st = webapp._do_upload_pairings(dry=False)

    assert p["ok"] is False
    assert "nepodarilo" in p["error"] and "Log" in p["error"]
    assert "naimportované 0" not in p["error"]


def test_hard_shoptet_error_reaches_the_automation_card(iso, monkeypatch):
    # the reason Shoptet aborted must be visible where the manager looks (the card's
    # error line), not only in the JSON the n8n call gets back
    _seed_pairing()
    err = "Chyba | Číslo riadku: 7 - Data in column code are not unique"
    out = ("\nVÝSLEDOK: spracované=None upravené=None zlyhania=None\n"
           f"CHYBA LOGU: {err}\n")
    monkeypatch.setattr(webapp, "run_import",
                        lambda p, dry_run=False, timeout=300: (2, out, "boom"))

    result = webapp.run_parovania_eshop()

    assert err in result["pairings"]["error"]


def test_partial_chunk_then_hard_failure_reports_what_really_landed(iso, monkeypatch):
    # chunk 1 partially accepted (Shoptet took its 300 rows, rejected 2), chunk 2
    # hard-fails → the message must not understate the push as "naimportované 0",
    # and the partially accepted rows must be counted WITHOUT the rejected ones.
    n = 650
    products = [{"key": f"K{i}", "idx": i, "supplier": "BETALOV", "name": f"P{i}",
                 "pairCode": "P", "variant_codes": [f"{i}/M"], "our_url": "u",
                 "ai_status": "matched", "ai_chosen_url": "", "ai_reason": "",
                 "candidates": [], "current": {}} for i in range(n)]
    monkeypatch.setattr(webapp, "PRODUCTS", products)
    monkeypatch.setattr(webapp, "CODE2PAIR", {f"{i}/M": "P" for i in range(n)})
    webapp._save_decisions({f"K{i}": {"status": "good", "url": f"https://s/{i}"}
                            for i in range(n)})
    calls = []

    def fake_run(csv_path, dry_run=False, timeout=300):
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            rows = list(_csv.reader(f, delimiter=";"))[1:]
        calls.append(rows)
        if len(calls) == 1:
            return 2, f"VÝSLEDOK: spracované={len(rows)} upravené=10 zlyhania=2", ""
        return 2, ("\nVÝSLEDOK: spracované=None upravené=None zlyhania=None\n"
                   "CHYBA LOGU: Chyba | Číslo riadku: 3 - Data in column code are not unique"), ""

    monkeypatch.setattr(webapp, "run_import", fake_run)

    p, _st = webapp._do_upload_pairings(dry=False)

    assert len(calls) == 2                      # the partial chunk did not stop the batch
    assert "časti 2/3" in p["error"]
    assert "298 čiastočne prijatých" in p["error"]   # 300 sent, 2 rejected by Shoptet


def test_export_confirmation_needs_an_exact_url_match(iso, monkeypatch):
    # a stale / different note on the eshop proves nothing — the row is still sent
    _seed_pairing()
    monkeypatch.setattr(webapp, "_iter_export_lines",
                        _export_lines(_export({"1/M": "https://supplier/OLD"})))
    fake_run, calls = _recording_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)

    result = webapp.run_parovania_eshop()

    assert result["pairings"]["confirmed_in_export"] == 0
    links = [c for c in calls if c["header"][2] == "internalNote"]
    assert links and ["1/M", "P1", "https://supplier/x"] in links[0]["rows"]


# ── PR #271 review — the four ways this push could still lie about what landed ──
def test_run_import_pins_the_child_stdout_encoding(monkeypatch):
    """IMPORTANT 2 — `encoding='utf-8'` on Popen only decodes what the CHILD produced;
    the child encodes with ITS OWN locale. On a box whose locale is not UTF-8 the
    result marker comes back mojibake'd ('V?SLEDOK:'), the slice is empty, processed is
    None and EVERY chunk is booked failed/unreadable — the whole nightly push dies on
    one character. Pin the child's stdout encoding explicitly."""
    seen = {}

    class FakePopen:
        returncode = 0

        def __init__(self, cmd, **kw):
            seen["cmd"], seen["kw"] = cmd, kw

        def communicate(self, timeout=None):
            return "VÝSLEDOK: spracované=1 upravené=1 zlyhania=0", ""

    monkeypatch.setattr(webapp.subprocess, "Popen", FakePopen)
    rc, out, _err = webapp.run_import("/tmp/does-not-matter.csv")

    assert rc == 0 and "VÝSLEDOK" in out
    assert seen["kw"]["env"]["PYTHONIOENCODING"] == "utf-8"
    assert seen["kw"]["env"]["PYTHONPATH"].endswith(os.path.join("", "src"))


def _write_export(path, pairs, age_s=0):
    """A real on-disk catalog export (cp1250, as „Sync zo Shoptetu" writes it), aged
    `age_s` seconds — the freshness of THIS file is what may credit rows as uploaded."""
    path.write_bytes(_export(pairs).encode("cp1250"))
    if age_s:
        old = time.time() - age_s
        os.utime(path, (old, old))
    return str(path)


def test_export_confirmation_refuses_a_stale_export(tmp_path, monkeypatch):
    """IMPORTANT 3 — confirmed rows are NOT sent at all and ARE recorded uploaded, on
    the strength of data/products.csv, which a SEPARATE hourly automation refreshes. If
    that sync is off/broken, a code cleared or changed in the eshop after the last sync
    is silently never re-written. The sibling supplier write-back already fails closed
    on an unusable export; this path trusted it at any age."""
    p = tmp_path / "products.csv"
    _write_export(p, {"1/M": "https://supplier/x"}, age_s=webapp.EXPORT_MAX_AGE_S + 60)
    monkeypatch.setattr(webapp, "SRC", str(p))

    assert webapp._codes_confirmed_in_export([["1/M", "P1", "https://supplier/x"]]) == set()


def test_export_confirmation_still_credits_a_fresh_export(tmp_path, monkeypatch):
    # the guard must not block the normal case — an export the hourly sync just wrote
    p = tmp_path / "products.csv"
    _write_export(p, {"1/M": "https://supplier/x"})
    monkeypatch.setattr(webapp, "SRC", str(p))

    assert (webapp._codes_confirmed_in_export([["1/M", "P1", "https://supplier/x"]])
            == {"1/M"})


def test_export_confirmation_cannot_be_handed_notes_that_skip_the_freshness_check():
    """MINOR (revízia PR #271) — the credit may only ever be computed from an export
    whose AGE was checked. `_codes_confirmed_in_export` also accepted a pre-read
    `notes=` mapping; no caller ever passed one, but that parameter was the single
    entry point through which the natural future optimisation („read the export once,
    hand it down") would have silently deleted the guard the two tests above add.
    There must be no such entry point: the notes are read INSIDE, after the age check,
    or not at all."""
    params = inspect.signature(webapp._codes_confirmed_in_export).parameters
    assert "notes" not in params


def test_timed_out_chunk_is_reported_as_uncertain_not_as_zero_imported(iso, monkeypatch):
    """MINOR — a chunk that TIMED OUT had its rows submitted and very probably landed,
    yet it was reported with the flat 'import zlyhal … naimportované 0 z N riadkov' —
    the exact misleading wording this PR fixes for the unreadable case."""
    _seed_pairing()

    def timeout_run(csv_path, dry_run=False, timeout=300):
        raise subprocess.TimeoutExpired(cmd="shoptet_import.py", timeout=timeout)
    monkeypatch.setattr(webapp, "run_import", timeout_run)

    p, _st = webapp._do_upload_pairings(dry=False)

    assert p["ok"] is False
    assert "nepodarilo prečítať" in p["error"] and "mohli prejsť" in p["error"]
    assert "naimportované 0" not in p["error"]
    assert "timeout" in p["error"]           # the reason still reaches the manager


def test_partially_accepted_row_count_never_goes_negative():
    """MINOR — 'Zlyhanie variantov: N' is an aggregate Shoptet count; if it ever counts
    VARIANTS rather than rows it can exceed the rows we sent, and the message told the
    manager a negative number of rows was accepted."""
    res = {"chunks_ok": 0, "chunks_partial": 1, "chunks_total": 3, "rows_ok": 0,
           "rows_partial": 300, "partial_failed": 400, "unreadable": False,
           "error_detail": None}
    msg = webapp._chunk_error_msg(res, 900)
    assert "-100" not in msg and "+0 čiastočne prijatých" in msg


# --------------------------------------------------------------------------- #
# #272 — the nightly push read the WHOLE 57 MB catalog export into memory (and
# copied it again through io.StringIO, ~115 MB transient) on EVERY run, dry runs
# included, just to learn which codes the eshop already carries. The index is
# built by STREAMING the file line by line; only the {code: internalNote} pairs
# survive the pass.
# --------------------------------------------------------------------------- #
def _big_export(path, rows=6000, pad=2000):
    with open(path, "wb") as f:
        f.write(b"code;pairCode;internalNote;supplier;description\r\n")
        filler = "x" * pad
        for i in range(rows):
            f.write(f"{i}/M;P{i};https://s/{i};;{filler}\r\n".encode("cp1250"))
    return path.stat().st_size


def test_the_export_index_streams_instead_of_materialising_the_whole_file(tmp_path, monkeypatch):
    import tracemalloc
    p = tmp_path / "products.csv"
    size = _big_export(p)
    monkeypatch.setattr(webapp, "SRC", str(p))

    tracemalloc.start()
    try:
        notes, codes = webapp._export_note_index()
        _cur, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert len(codes) == 6000 and notes["0/M"] == "https://s/0"
    # the old path allocated >= 2x the file size (bytes -> str -> io.StringIO copy)
    assert peak < size / 2, (
        f"export index peaked at {peak / 1e6:.1f} MB for a {size / 1e6:.1f} MB export "
        "— it is still materialising the file instead of streaming it")


def test_the_streamed_index_parses_exactly_like_a_whole_text_parse(tmp_path, monkeypatch):
    """Streaming must not change WHAT is parsed: cp1250 decoding, quoted fields with
    an embedded newline, empty notes, and the duplicate-code rule (two rows that
    disagree about a code prove nothing, so the code is dropped from the notes but
    is still PRESENT in the catalog)."""
    text = ("code;pairCode;internalNote;supplier\r\n"
            "1/M;P1;https://s/x;\r\n"
            "2/M;P2;\"riadok\r\ns novým riadkom\";\r\n"
            "3/M;P3;;\r\n"
            "4/M;P4;https://s/A;\r\n"
            "4/M;P4;https://s/B;\r\n"
            "5/M;P5;\"samotný \r návrat vozíka\";\r\n")
    p = tmp_path / "products.csv"
    p.write_bytes(text.encode("cp1250"))
    monkeypatch.setattr(webapp, "SRC", str(p))

    notes, codes = webapp._export_note_index()

    assert codes == {"1/M", "2/M", "3/M", "4/M", "5/M"}
    assert notes["1/M"] == "https://s/x"
    assert "novým riadkom" in notes["2/M"]
    assert notes["3/M"] == ""
    assert "4/M" not in notes                      # conflicting rows prove nothing
    # a LONE \r inside a quoted field: line iteration with newline='' breaks there,
    # csv reassembles it — the value must come back byte-for-byte, as a whole-text
    # parse produced it
    assert notes["5/M"] == "samotný \r návrat vozíka"
    # the whole-text reader (still used by the scraping automations) is unchanged
    assert webapp._read_export_for_links() == text


def test_a_missing_export_yields_an_empty_index_and_never_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "SRC", str(tmp_path / "nope.csv"))
    assert webapp._export_note_index() == ({}, set())
    assert webapp._read_export_for_links() == ""
