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
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webreview"))
import app as webapp  # noqa: E402

from tests.conftest import authed_client  # noqa: E402


def _product(variant_codes=("1/M",)):
    return {"key": "BETALOV|P1", "idx": 0, "supplier": "BETALOV", "name": "Bunda Test",
            "pairCode": "P1", "variant_codes": list(variant_codes),
            "our_url": "https://forestshop/x", "ai_status": "matched",
            "ai_chosen_url": "", "ai_reason": "", "candidates": [], "current": {}}


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
        return 0, "VÝSLEDOK: spracované=1 upravené=1 zlyhania=0", ""
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
