"""In-app „Máme skladom → Skladom" auto-restock (#98) — Flask wiring: run function,
store, endpoint, registration, wired through the generic automation runner (#93).
WRITES to the eshop — but every test monkeypatches run_import (the careful Shoptet
import subprocess), so NO real eshop write ever happens.

Distinct from #108 restock_skladom: the trigger is Shoptet's OWN physical stock
(stock>0), not a scraped supplier confirmation — so there is NO supplier_stock
dependency here. Hermetic: SRC (the export) and STOCK_SKLADOM_STATE redirected to
tmp fixture content; run_import stubbed. Reuses the SAME chunked import path as the
n8n endpoint (import_builder.skladom_rows → _import_rows_chunked → run_import).
"""
import csv as _csv
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webreview"))
import app as webapp  # noqa: E402

from tests.conftest import authed_client  # noqa: E402

# 1/M = we physically HAVE it (stock 5) but still show Vypredané → the ONE candidate.
# 2/S = Vypredané but stock 0 (nothing to sell) → not a candidate.
# 3/L = already Skladom → not a candidate (idempotent).
# 4/X = detailOnly + discontinued WITH residual stock → conscious off, never flipped.
EXPORT_CSV = (
    "code;pairCode;name;supplier;productVisibility;availabilityInStock;"
    "availabilityOutOfStock;price;stock;internalNote\r\n"
    "1/M;P1;Mame ale vypredane;TESTSUP;visible;Vypredané;Vypredané;99.90;5;\r\n"
    "2/S;P2;Bez skladu;TESTSUP;visible;Vypredané;Vypredané;49.90;0;\r\n"
    "3/L;P3;Uz skladom;TESTSUP;visible;Skladom;Skladom;19.90;5;\r\n"
    "4/X;P4;Ukoncene;TESTSUP;detailOnly;Predaj výrobku skončil;"
    "Predaj výrobku skončil;39.90;5;\r\n"
).encode("cp1250")

_MANAGER_STORES = (("DECISIONS", "decisions.json"), ("ORDERED", "ordered_items.json"),
                   ("WAITING", "waiting_items.json"),
                   ("ORDER_PAIRINGS", "order_pairings.json"),
                   ("SUPPLIER_ASSIGN", "supplier_assignments.json"))


@pytest.fixture
def iso(tmp_path, monkeypatch):
    """Isolate the store this automation touches + the export + the import
    subprocess. Manager stores get sentinels (asserted untouched)."""
    monkeypatch.setattr(webapp.RUNNER, "state_path", str(tmp_path / "automations.json"))
    monkeypatch.setattr(webapp, "OUT", str(tmp_path))
    src = tmp_path / "products.csv"
    src.write_bytes(EXPORT_CSV)
    monkeypatch.setattr(webapp, "SRC", str(src))
    monkeypatch.setattr(webapp, "STOCK_SKLADOM_STATE", str(tmp_path / "stock_skladom.json"))
    monkeypatch.setattr(webapp, "CODE2PAIR", {})

    sentinels = {}
    for name, fname in _MANAGER_STORES:
        p = tmp_path / fname
        p.write_text('{"sentinel": true}', encoding="utf-8")
        monkeypatch.setattr(webapp, name, str(p))
        sentinels[name] = p
    return {"tmp": tmp_path, "manager_stores": sentinels}


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
def test_registered_disabled_daily_0645(iso):
    c = authed_client()
    (a,) = [x for x in c.get("/api/automations").get_json()["automations"]
            if x["key"] == "stock_skladom"]
    assert a["name"] == "Máme skladom → Skladom"
    # SAFETY: this automation WRITES to the live eshop → deploy starts stopped (#93 contract)
    assert a["enabled"] is False
    assert a["schedule"] == "denne o 06:45"
    assert a["running"] is False
    assert a["description"]                         # #173 plain-language description present


def test_disabled_automation_does_not_run(iso, monkeypatch):
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("disabled must not run")))
    webapp.RUNNER.tick_once()                       # default state = disabled
    (a,) = [x for x in webapp.RUNNER.status() if x["key"] == "stock_skladom"]
    assert a["last_run"] == ""
    assert not os.path.exists(webapp.STOCK_SKLADOM_STATE)


# ── the run: detection + import ─────────────────────────────────────────────────
def test_run_flips_only_have_but_vypredane_product(iso, monkeypatch):
    fake_run, calls = _ok_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)

    stats = webapp.run_stock_skladom()
    assert stats["status"] == "ok"
    assert stats["candidates"] == 1
    assert stats["imported_rows"] == 1

    # exactly one import ran, with the whitelisted SKLADOM_COLS header
    assert len(calls) == 1
    assert calls[0]["header"] == webapp.import_builder.SKLADOM_COLS
    assert calls[0]["dry_run"] is False
    # only 1/M (have stock but show Vypredané); NOT 2/S (no stock), 3/L (already
    # skladom), 4/X (discontinued with residual stock — conscious off)
    assert [r[0] for r in calls[0]["rows"]] == ["1/M"]

    st = json.loads(open(webapp.STOCK_SKLADOM_STATE, encoding="utf-8").read())
    assert st["status"] == "ok"
    assert [c["code"] for c in st["candidates"]] == ["1/M"]
    assert st["candidates"][0]["stock"] == "5"


def test_import_row_sets_both_availability_visible_and_no_stock_column(iso, monkeypatch):
    # #98 invariant: the row sets visible + BOTH availability fields 'Skladom'
    # (CEO 2026-07-14) but does NOT carry a stock column — the real positive stock
    # must never be overwritten.
    fake_run, calls = _ok_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)
    webapp.run_stock_skladom()

    hdr = calls[0]["header"]
    assert "stock" not in hdr                       # stock column deliberately absent
    row = calls[0]["rows"][0]
    got = dict(zip(hdr, row))
    assert got["productVisibility"] == "visible"
    assert got["availabilityInStock"] == "Skladom"
    assert got["availabilityOutOfStock"] == "Skladom"


def test_run_no_candidates_flips_nothing(iso, monkeypatch):
    # an export with no have-but-vypredané products → no import at all
    src = iso["tmp"] / "products.csv"
    src.write_bytes(
        "code;pairCode;name;supplier;productVisibility;availabilityInStock;"
        "availabilityOutOfStock;price;stock;internalNote\r\n"
        "3/L;P3;Uz skladom;TESTSUP;visible;Skladom;Skladom;19.90;5;\r\n".encode("cp1250"))
    monkeypatch.setattr(webapp, "SRC", str(src))
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not import")))
    stats = webapp.run_stock_skladom()
    assert stats["candidates"] == 0 and stats["imported_rows"] == 0
    assert stats["status"] == "ok"
    st = json.loads(open(webapp.STOCK_SKLADOM_STATE, encoding="utf-8").read())
    assert st["candidates"] == []


def test_run_never_flips_discontinued_with_residual_stock(iso, monkeypatch):
    # the „neprepíše vedomé off rozhodnutie manažéra" invariant at the Flask level:
    # an export whose ONLY stocked row is a discontinued (detailOnly) product must
    # import nothing.
    src = iso["tmp"] / "products.csv"
    src.write_bytes(
        "code;pairCode;name;supplier;productVisibility;availabilityInStock;"
        "availabilityOutOfStock;price;stock;internalNote\r\n"
        "4/X;P4;Ukoncene;TESTSUP;detailOnly;Predaj výrobku skončil;"
        "Predaj výrobku skončil;39.90;5;\r\n".encode("cp1250"))
    monkeypatch.setattr(webapp, "SRC", str(src))
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not touch discontinued")))
    stats = webapp.run_stock_skladom()
    assert stats["candidates"] == 0 and stats["imported_rows"] == 0


def test_failed_import_read_back_records_error_not_success(iso, monkeypatch):
    # a HARD Shoptet abort (rc!=0, no summary) must land status=error, not 'ok'
    def boom(csv_path, dry_run=False, timeout=300):
        return 1, "Chyba | Číslo riadku: 1 - Data in column code are not unique", "boom"
    monkeypatch.setattr(webapp, "run_import", boom)

    stats = webapp.run_stock_skladom()
    assert stats["status"] == "error"
    st = json.loads(open(webapp.STOCK_SKLADOM_STATE, encoding="utf-8").read())
    assert st["status"] == "error"
    assert "not unique" in st["error_detail"]
    # candidates are still recorded so the tab shows what it TRIED to flip
    assert [c["code"] for c in st["candidates"]] == ["1/M"]


def test_run_busy_when_another_import_holds_the_lock(iso, monkeypatch):
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not import while locked")))
    webapp._import_lock.acquire()
    try:
        stats = webapp.run_stock_skladom()
    finally:
        webapp._import_lock.release()
    assert stats["status"] == "busy"
    st = json.loads(open(webapp.STOCK_SKLADOM_STATE, encoding="utf-8").read())
    assert st["status"] == "busy" and "iný import" in st["error_detail"]


def test_run_via_runner_records_ok_status(iso, monkeypatch):
    fake_run, _calls = _ok_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)
    c = authed_client()
    r = c.post("/api/automations/stock_skladom/run")
    assert r.get_json()["started"] is True
    webapp.RUNNER._threads["stock_skladom"].join(timeout=15)
    (st,) = [x for x in webapp.RUNNER.status() if x["key"] == "stock_skladom"]
    assert st["last_status"] == "ok"
    assert st["last_result"]["candidates"] == 1
    assert st["enabled"] is False                   # run-now must not enable the schedule


# ── endpoint ────────────────────────────────────────────────────────────────────
def test_endpoint_requires_login(iso):
    anon = webapp.app.test_client()
    assert anon.get("/api/stock-skladom").status_code == 401


def test_endpoint_serves_candidates(iso, monkeypatch):
    fake_run, _calls = _ok_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)
    webapp.run_stock_skladom()
    c = authed_client()
    j = c.get("/api/stock-skladom").get_json()
    assert len(j["candidates"]) == 1
    assert j["status"] == "ok" and j["last_check"]
    assert j["candidates"][0]["code"] == "1/M"


# ── isolation: never touches the manager's live decision stores ────────────────
def test_run_never_touches_manager_stores(iso, monkeypatch):
    fake_run, _calls = _ok_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)
    webapp.run_stock_skladom()
    for _name, path in iso["manager_stores"].items():
        assert path.read_text(encoding="utf-8") == '{"sentinel": true}'


# ── the batch routes through the SAME chunked import helper (#156/#158 pattern) ──
def _large_fixture(n):
    """n have-but-vypredané products (stock>0 + visible + Vypredané) — every one a
    candidate. Returns export_csv_bytes."""
    header = ("code;pairCode;name;supplier;productVisibility;availabilityInStock;"
              "availabilityOutOfStock;price;stock;internalNote\r\n")
    lines = [f"{i}/M;P{i};Bunda {i};TESTSUP;visible;Vypredané;Vypredané;9.90;5;\r\n"
             for i in range(n)]
    return (header + "".join(lines)).encode("cp1250")


def _recording_import(fail_on_call=None):
    """run_import stub recording each chunk CSV's rows; optionally FAIL the Nth call
    (1-based) to simulate a mid-batch chunk failure."""
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


def test_large_batch_split_into_chunks(iso, monkeypatch):
    n = 650
    src = iso["tmp"] / "products.csv"
    src.write_bytes(_large_fixture(n))
    monkeypatch.setattr(webapp, "SRC", str(src))
    fake_run, calls = _recording_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)

    stats = webapp.run_stock_skladom()

    assert len(calls) >= 2                                   # split, not one giant import
    assert max(len(c["rows"]) for c in calls) <= webapp.IMPORT_CHUNK_ROWS
    imported = [r[0] for c in calls for r in c["rows"]]
    assert sorted(imported) == sorted(f"{i}/M" for i in range(n))
    assert all(c["dry_run"] is False for c in calls)
    assert stats["status"] == "ok"
    assert stats["candidates"] == n and stats["imported_rows"] == n


def test_mid_batch_chunk_failure_records_error_and_releases_lock(iso, monkeypatch):
    n = 650
    src = iso["tmp"] / "products.csv"
    src.write_bytes(_large_fixture(n))
    monkeypatch.setattr(webapp, "SRC", str(src))
    fake_run, calls = _recording_import(fail_on_call=2)      # 1st chunk ok, 2nd fails
    monkeypatch.setattr(webapp, "run_import", fake_run)

    stats = webapp.run_stock_skladom()

    assert stats["status"] == "error"
    assert len(calls) == 2                                   # batch STOPS after the failing chunk
    st = json.loads(open(webapp.STOCK_SKLADOM_STATE, encoding="utf-8").read())
    assert st["status"] == "error"
    assert "časti 2/" in st["error_detail"]
    assert len(st["candidates"]) == n
    # the import lock was released despite the failure
    assert webapp._import_lock.acquire(blocking=False)
    webapp._import_lock.release()
