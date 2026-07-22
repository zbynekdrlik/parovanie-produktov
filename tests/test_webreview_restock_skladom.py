"""In-app „Vypredané → Skladom" restock automation (#108) — Flask wiring: run
function, store, endpoints, registration, wired through the generic automation
runner (#93). WRITES to the eshop — but every test monkeypatches run_import (the
careful Shoptet import subprocess), so NO real eshop write ever happens.

Hermetic: SRC (the export), SUPPLIER_STOCK_STATE and RESTOCK_STATE are redirected
to tmp fixture content; run_import is stubbed. Mirrors
test_webreview_riziko_vypadku.py's isolation + test_webreview_parovania_eshop.py's
import-stub pattern. Reuses the SAME import path as the n8n endpoint
(import_builder.restock_rows → run_import), so the existing import tests stay green.
"""
import csv as _csv
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webreview"))
import app as webapp  # noqa: E402

from tests.conftest import authed_client  # noqa: E402

# Two Vypredané+visible products (CEO-canonical: both availability fields Vypredané,
# stock 0) with supplier links, plus one already-Skladom control.
EXPORT_CSV = (
    "code;pairCode;name;supplier;productVisibility;availabilityInStock;"
    "availabilityOutOfStock;price;stock;internalNote\r\n"
    "1/M;P1;Bunda restock;TESTSUP;visible;Vypredané;Vypredané;99.90;0;https://supplier.test/p/1\r\n"
    "2/S;P2;Vesta stale;TESTSUP;visible;Vypredané;Vypredané;49.90;0;https://supplier.test/p/2\r\n"
    "3/L;P3;Uz skladom;TESTSUP;visible;Skladom;Skladom;19.90;5;https://supplier.test/p/3\r\n"
).encode("cp1250")

_MANAGER_STORES = (("DECISIONS", "decisions.json"), ("ORDERED", "ordered_items.json"),
                   ("WAITING", "waiting_items.json"),
                   ("ORDER_PAIRINGS", "order_pairings.json"),
                   ("SUPPLIER_ASSIGN", "supplier_assignments.json"))


@pytest.fixture
def iso(tmp_path, monkeypatch):
    """Isolate every store this automation touches + the export + supplier_stock +
    the import subprocess. Manager stores get sentinels (asserted untouched)."""
    monkeypatch.setattr(webapp.RUNNER, "state_path", str(tmp_path / "automations.json"))
    monkeypatch.setattr(webapp, "OUT", str(tmp_path))
    src = tmp_path / "products.csv"
    src.write_bytes(EXPORT_CSV)
    monkeypatch.setattr(webapp, "SRC", str(src))
    monkeypatch.setattr(webapp, "SUPPLIER_STOCK_STATE", str(tmp_path / "supplier_stock.json"))
    monkeypatch.setattr(webapp, "RESTOCK_STATE", str(tmp_path / "restock_skladom.json"))
    monkeypatch.setattr(webapp, "CODE2PAIR", {})

    sentinels = {}
    for name, fname in _MANAGER_STORES:
        p = tmp_path / fname
        p.write_text('{"sentinel": true}', encoding="utf-8")
        monkeypatch.setattr(webapp, name, str(p))
        sentinels[name] = p
    return {"tmp": tmp_path, "manager_stores": sentinels}


def _seed_supplier_stock(p1_available=True, p2_fresh=True):
    """p/1 available+fresh (a restock candidate); p/2 available but STALE (>48h old,
    must NOT flip); p/3 available (control — but our product is already Skladom)."""
    stale = "2026-07-01T05:00:00+02:00"          # weeks old -> not fresh
    fresh = "2026-07-22T05:00:00+02:00"
    webapp._save_supplier_stock({
        "last_check": fresh,
        "rows": [
            {"link": "https://supplier.test/p/1", "ok": True, "available": p1_available,
             "price": 79.90, "availabilityText": "Skladom", "supplier": "TESTSUP",
             "checkedAt": fresh},
            {"link": "https://supplier.test/p/2", "ok": True, "available": True,
             "price": 39.90, "availabilityText": "Skladom", "supplier": "TESTSUP",
             "checkedAt": fresh if p2_fresh else stale},
            {"link": "https://supplier.test/p/3", "ok": True, "available": True,
             "price": 15.00, "availabilityText": "Skladom", "supplier": "TESTSUP",
             "checkedAt": fresh},
        ],
        "stats": {},
    })


def _ok_import():
    """A run_import stub that records every CSV it was handed (header + rows) and
    reports a clean success."""
    calls = []

    def fake_run(csv_path, dry_run=False, timeout=300):
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            rd = list(_csv.reader(f, delimiter=";"))
        calls.append({"header": rd[0], "rows": rd[1:], "dry_run": dry_run})
        return 0, "Spracované: 1. Upravené: 1. Zlyhanie variantov: 0.", ""
    return fake_run, calls


# ── registration + status ──────────────────────────────────────────────────────
def test_registered_disabled_daily_0600(iso):
    c = authed_client()
    (a,) = [x for x in c.get("/api/automations").get_json()["automations"]
            if x["key"] == "restock_skladom"]
    assert a["name"] == "Vypredané → Skladom"
    # SAFETY: this automation WRITES to the live eshop → deploy starts stopped (#93 contract)
    assert a["enabled"] is False
    assert a["schedule"] == "denne o 06:00"
    assert a["running"] is False


def test_disabled_automation_does_not_run(iso, monkeypatch):
    _seed_supplier_stock()
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("disabled must not run")))
    webapp.RUNNER.tick_once()                    # default state = disabled
    (a,) = [x for x in webapp.RUNNER.status() if x["key"] == "restock_skladom"]
    assert a["last_run"] == ""
    assert not os.path.exists(webapp.RESTOCK_STATE)


# ── the run: JOIN detection + import ────────────────────────────────────────────
def test_run_flips_only_fresh_available_vypredane_product(iso, monkeypatch):
    # p/1 = vypredané + supplier fresh+available -> flip. p/2 = vypredané + supplier
    # available but STALE -> NOT flipped. p/3 = already Skladom -> NOT a candidate.
    _seed_supplier_stock(p1_available=True, p2_fresh=False)
    fake_run, calls = _ok_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)

    stats = webapp.run_restock_skladom()
    assert stats["status"] == "ok"
    assert stats["candidates"] == 1
    assert stats["imported_rows"] == 1
    assert stats["has_supplier_data"] is True

    # exactly one import ran, with the whitelisted RESTOCK_COLS header
    assert len(calls) == 1
    assert calls[0]["header"] == webapp.import_builder.RESTOCK_COLS
    assert calls[0]["dry_run"] is False
    # only the fresh+available vypredané product 1/M was flipped
    assert [r[0] for r in calls[0]["rows"]] == ["1/M"]

    st = json.loads(open(webapp.RESTOCK_STATE, encoding="utf-8").read())
    assert st["has_supplier_data"] is True and st["status"] == "ok"
    assert [c["code"] for c in st["candidates"]] == ["1/M"]
    assert st["candidates"][0]["supplierPrice"] == "79.9"


def test_import_row_sets_both_availability_fields_to_skladom(iso, monkeypatch):
    # regression guard (CEO 2026-07-14): the restock import row must set BOTH
    # availabilityInStock AND availabilityOutOfStock to 'Skladom', visible, stock
    _seed_supplier_stock(p1_available=True, p2_fresh=False)
    fake_run, calls = _ok_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)
    webapp.run_restock_skladom()

    hdr = calls[0]["header"]
    row = calls[0]["rows"][0]
    got = dict(zip(hdr, row))
    assert got["productVisibility"] == "visible"
    assert got["availabilityInStock"] == "Skladom"
    assert got["availabilityOutOfStock"] == "Skladom"
    assert got["stock"] == "5"


def test_run_no_supplier_data_yet_flips_nothing(iso, monkeypatch):
    # #106 has never run -> SUPPLIER_STOCK_STATE doesn't exist -> flip nothing
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not import")))
    stats = webapp.run_restock_skladom()
    assert stats["candidates"] == 0 and stats["has_supplier_data"] is False
    assert stats["status"] == "ok"
    st = json.loads(open(webapp.RESTOCK_STATE, encoding="utf-8").read())
    assert st["has_supplier_data"] is False and st["candidates"] == []


def test_run_supplier_sold_out_flips_nothing(iso, monkeypatch):
    # supplier NOT available for p/1, p/2 stale -> zero candidates -> no import
    _seed_supplier_stock(p1_available=False, p2_fresh=False)
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not import")))
    stats = webapp.run_restock_skladom()
    assert stats["candidates"] == 0 and stats["imported_rows"] == 0
    assert stats["status"] == "ok"


def test_failed_import_read_back_records_error_not_success(iso, monkeypatch):
    # a HARD Shoptet abort (rc!=0, no summary) must land status=error, not 'ok'
    _seed_supplier_stock(p1_available=True, p2_fresh=False)

    def boom(csv_path, dry_run=False, timeout=300):
        return 1, "Chyba | Číslo riadku: 1 - Data in column code are not unique", "boom"
    monkeypatch.setattr(webapp, "run_import", boom)

    stats = webapp.run_restock_skladom()
    assert stats["status"] == "error"
    st = json.loads(open(webapp.RESTOCK_STATE, encoding="utf-8").read())
    assert st["status"] == "error"
    assert "not unique" in st["error_detail"]
    # candidates are still recorded so the tab shows what it TRIED to flip
    assert [c["code"] for c in st["candidates"]] == ["1/M"]


def test_run_busy_when_another_import_holds_the_lock(iso, monkeypatch):
    _seed_supplier_stock(p1_available=True, p2_fresh=False)
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not import while locked")))
    webapp._import_lock.acquire()
    try:
        stats = webapp.run_restock_skladom()
    finally:
        webapp._import_lock.release()
    assert stats["status"] == "busy"
    st = json.loads(open(webapp.RESTOCK_STATE, encoding="utf-8").read())
    assert st["status"] == "busy" and "iný import" in st["error_detail"]


def test_run_via_runner_records_ok_status(iso, monkeypatch):
    _seed_supplier_stock(p1_available=True, p2_fresh=False)
    fake_run, _calls = _ok_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)
    c = authed_client()
    r = c.post("/api/automations/restock_skladom/run")
    assert r.get_json()["started"] is True
    webapp.RUNNER._threads["restock_skladom"].join(timeout=15)
    (st,) = [x for x in webapp.RUNNER.status() if x["key"] == "restock_skladom"]
    assert st["last_status"] == "ok"
    assert st["last_result"]["candidates"] == 1
    assert st["enabled"] is False               # run-now must not enable the schedule


# ── endpoints ─────────────────────────────────────────────────────────────────
def test_endpoint_requires_login(iso):
    anon = webapp.app.test_client()
    assert anon.get("/api/restock-skladom").status_code == 401


def test_endpoint_serves_candidates(iso, monkeypatch):
    _seed_supplier_stock(p1_available=True, p2_fresh=False)
    fake_run, _calls = _ok_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)
    webapp.run_restock_skladom()
    c = authed_client()
    j = c.get("/api/restock-skladom").get_json()
    assert j["has_supplier_data"] is True and len(j["candidates"]) == 1
    assert j["status"] == "ok" and j["last_check"]
    assert j["candidates"][0]["code"] == "1/M"


# ── isolation: never touches the manager's live decision stores ────────────────
def test_run_never_touches_manager_stores(iso, monkeypatch):
    _seed_supplier_stock(p1_available=True, p2_fresh=False)
    fake_run, _calls = _ok_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)
    webapp.run_restock_skladom()
    for _name, path in iso["manager_stores"].items():
        assert path.read_text(encoding="utf-8") == '{"sentinel": true}'


# ── #158: the restock batch has the SAME 120s browser-redirect-timeout risk #156
#    fixed for the pairings/suppliers pushes — must route through the SAME chunked
#    import helper (_import_rows_chunked). Hermetic: run_import stubbed. ──────────
def _large_restock_fixture(n):
    """n Vypredané+visible products, each with a fresh+available supplier link —
    every one is a restock candidate. Returns (export_csv_bytes, supplier_stock_rows)."""
    header = ("code;pairCode;name;supplier;productVisibility;availabilityInStock;"
              "availabilityOutOfStock;price;stock;internalNote\r\n")
    lines = []
    stock_rows = []
    fresh = "2026-07-22T05:00:00+02:00"
    for i in range(n):
        code = f"{i}/M"
        link = f"https://supplier.test/p/{i}"
        lines.append(f"{code};P{i};Bunda {i};TESTSUP;visible;Vypredané;Vypredané;"
                     f"9.90;0;{link}\r\n")
        stock_rows.append({"link": link, "ok": True, "available": True,
                           "price": 9.0, "availabilityText": "Skladom",
                           "supplier": "TESTSUP", "checkedAt": fresh})
    return (header + "".join(lines)).encode("cp1250"), stock_rows


def _recording_import(fail_on_call=None):
    """run_import stub recording each chunk CSV's rows; optionally FAIL the Nth call
    (1-based) to simulate a mid-batch chunk failure (mirrors
    test_webreview_parovania_eshop.py's #156 pattern)."""
    calls = []

    def fake_run(csv_path, dry_run=False, timeout=300):
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            rd = list(_csv.reader(f, delimiter=";"))
        rows = rd[1:]
        calls.append({"header": rd[0], "rows": rows, "dry_run": dry_run})
        if fail_on_call is not None and len(calls) == fail_on_call:
            return 2, "POZOR: Shoptet hlási zlyhania", "boom"
        return 0, f"VÝSLEDOK: spracované={len(rows)} upravené={len(rows)} zlyhania=0", ""
    return fake_run, calls


def test_large_restock_batch_split_into_chunks(iso, monkeypatch):
    # 650 candidates -> must be imported in >=2 chunks, each <= IMPORT_CHUNK_ROWS.
    # RED before the fix: a single 650-row import call.
    n = 650
    export_csv, stock_rows = _large_restock_fixture(n)
    src = iso["tmp"] / "products.csv"
    src.write_bytes(export_csv)
    monkeypatch.setattr(webapp, "SRC", str(src))
    webapp._save_supplier_stock({"last_check": "now", "rows": stock_rows, "stats": {}})
    fake_run, calls = _recording_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)

    stats = webapp.run_restock_skladom()

    assert len(calls) >= 2                                   # split, not one giant import
    assert max(len(c["rows"]) for c in calls) <= webapp.IMPORT_CHUNK_ROWS
    imported = [r[0] for c in calls for r in c["rows"]]
    assert sorted(imported) == sorted(f"{i}/M" for i in range(n))
    assert all(c["dry_run"] is False for c in calls)          # never a dry run
    assert stats["status"] == "ok"
    assert stats["candidates"] == n and stats["imported_rows"] == n


def test_restock_mid_batch_chunk_failure_records_error_and_releases_lock(iso, monkeypatch):
    # a chunk failing mid-batch must -> status='error' with a clear tab-surfaced
    # message, STOP after the failing chunk, and release the import lock (no stuck
    # lock -> no cascade failure on the next scheduled run).
    n = 650
    export_csv, stock_rows = _large_restock_fixture(n)
    src = iso["tmp"] / "products.csv"
    src.write_bytes(export_csv)
    monkeypatch.setattr(webapp, "SRC", str(src))
    webapp._save_supplier_stock({"last_check": "now", "rows": stock_rows, "stats": {}})
    fake_run, calls = _recording_import(fail_on_call=2)      # 1st chunk ok, 2nd fails
    monkeypatch.setattr(webapp, "run_import", fake_run)

    stats = webapp.run_restock_skladom()

    assert stats["status"] == "error"
    assert len(calls) == 2                                   # batch STOPS after the failing chunk
    st = json.loads(open(webapp.RESTOCK_STATE, encoding="utf-8").read())
    assert st["status"] == "error"
    assert "časti 2/" in st["error_detail"]
    # candidates are still recorded (what the run TRIED to flip), even on failure
    assert len(st["candidates"]) == n
    # the import lock was released despite the failure
    assert webapp._import_lock.acquire(blocking=False)
    webapp._import_lock.release()


def test_small_restock_batch_still_single_import(iso, monkeypatch):
    # a small batch must NOT be needlessly chunked — one import call, as before.
    _seed_supplier_stock(p1_available=True, p2_fresh=False)
    fake_run, calls = _recording_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)
    webapp.run_restock_skladom()
    assert len(calls) == 1 and len(calls[0]["rows"]) == 1
