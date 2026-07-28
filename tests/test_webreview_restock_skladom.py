"""In-app „Vypredané → Skladom" restock automation (#108) — Flask wiring: run
function, store, endpoints, registration, wired through the generic automation
runner (#93).

#299 Task 9 rewrite: since the migration this automation no longer imports
directly — `run_restock_skladom` only QUEUES rows into the shared pending_shoptet
table for the next hourly „Sync do Shoptetu" drain
(`tests/test_webreview_shoptet_upload.py` covers that drain). This producer never
credits itself and needs no dedup store (unlike grube_externalcode/split_links): a
candidate is entirely state-driven (Vypredané+visible in the LIVE export), so once
Shoptet confirms the flip and the export next refreshes, `_restock_candidates`
simply stops selecting it. This file keeps the registration/JOIN-detection/
endpoint/runner-integration tests (the detection logic itself, `_restock_candidates`,
is UNCHANGED by this task), adapted to assert against the pending table and
`queued`/`candidates` instead of a completed import. The chunked-import-batch tests
(#156/#158) are gone — chunking existed only for Shoptet's own import subprocess,
which this producer no longer calls; that same protection now lives ONE layer up,
already covered for the shared queue by
`test_webreview_shoptet_upload.py::test_a_queued_change_goes_up_in_ONE_import_and_leaves_the_table`
and the hourly drain's own `_import_rows_chunked` call.

Hermetic: SRC (the export), SUPPLIER_STOCK_STATE, RESTOCK_STATE and the shared
pending_shoptet table are all redirected to tmp fixture content.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webreview"))
import app as webapp  # noqa: E402

from datetime import datetime, timedelta, timezone  # noqa: E402

# Freshness fixtures are RELATIVE to real 'now' — the webreview restock automation
# compares each supplier checkedAt against datetime.now() (a 48h window, MAX_PAIR_AGE_H).
# A hardcoded date silently crosses the window as the clock advances (the 2026-07-24
# time-bomb: a "2026-07-22" fresh ts was <48h on the 23rd, >48h on the 24th → 0 candidates).
# now-1h is comfortably fresh; 3 weeks old is unambiguously stale, on any day.
_TZ = timezone(timedelta(hours=2))


def _fresh_ts():
    return (datetime.now(_TZ) - timedelta(hours=1)).isoformat()


def _stale_ts():
    return (datetime.now(_TZ) - timedelta(days=21)).isoformat()

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
    """Isolate every store this automation touches, incl. the shared pending_shoptet
    table the queue drops rows into (#299 Task 9). Manager stores get sentinels
    (asserted untouched)."""
    monkeypatch.setattr(webapp.RUNNER, "state_path", str(tmp_path / "automations.json"))
    monkeypatch.setattr(webapp, "OUT", str(tmp_path))
    src = tmp_path / "products.csv"
    src.write_bytes(EXPORT_CSV)
    monkeypatch.setattr(webapp, "SRC", str(src))
    monkeypatch.setattr(webapp, "SUPPLIER_STOCK_STATE", str(tmp_path / "supplier_stock.json"))
    monkeypatch.setattr(webapp, "RESTOCK_STATE", str(tmp_path / "restock_skladom.json"))
    monkeypatch.setattr(webapp, "PENDING_SHOPTET", str(tmp_path / "pending_shoptet.json"))
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
    stale = _stale_ts()          # weeks old -> not fresh
    fresh = _fresh_ts()
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


# ── registration + status ──────────────────────────────────────────────────────
def test_registered_disabled_daily_0600(iso):
    c = authed_client()
    (a,) = [x for x in c.get("/api/automations").get_json()["automations"]
            if x["key"] == "restock_skladom"]
    assert a["name"] == "Vypredané → Skladom"
    # SAFETY: this automation feeds the live eshop → deploy starts stopped (#93 contract)
    assert a["enabled"] is False
    assert a["schedule"] == "denne o 06:00"
    assert a["running"] is False


def test_disabled_automation_does_not_run(iso, monkeypatch):
    _seed_supplier_stock()
    monkeypatch.setattr(webapp, "queue_shoptet_fields",
                        lambda *a, **k: pytest.fail("disabled must not run"))
    webapp.RUNNER.tick_once()                    # default state = disabled
    (a,) = [x for x in webapp.RUNNER.status() if x["key"] == "restock_skladom"]
    assert a["last_run"] == ""
    assert not os.path.exists(webapp.RESTOCK_STATE)


# ── the run: JOIN detection + queueing (#299 Task 9) ────────────────────────────
def test_run_queues_only_fresh_available_vypredane_product(iso, monkeypatch):
    # p/1 = vypredané + supplier fresh+available -> candidate. p/2 = vypredané +
    # supplier available but STALE -> NOT a candidate. p/3 = already Skladom -> NOT
    # a candidate.
    _seed_supplier_stock(p1_available=True, p2_fresh=False)
    monkeypatch.setattr(webapp, "_import_rows_chunked",
                        lambda *a, **k: pytest.fail("producer must not import"))

    stats = webapp.run_restock_skladom()
    assert stats["ok"] is True
    assert stats["status"] == "ok"
    assert stats["candidates"] == 1
    assert stats["has_supplier_data"] is True
    # #299 Task 8 review finding: `candidates` (rows) and `queued` (field values)
    # are DELIBERATELY different units, unlike the sibling producers' ambiguous
    # `would_queue` — one restock candidate carries FOUR writable columns
    # (productVisibility/availabilityInStock/availabilityOutOfStock/stock), so a
    # regression that confuses the two units is visible here (1 != 4).
    assert stats["queued"] == 4

    d = webapp._load_pending()
    assert list(d) == ["1/M"]                     # only the fresh+available product
    f = d["1/M"]["fields"]
    assert f["productVisibility"]["value"] == "visible"
    assert f["availabilityInStock"]["value"] == "Skladom"
    assert f["availabilityOutOfStock"]["value"] == "Skladom"
    assert f["stock"]["value"] == "5"
    # #299 Task 9 decision — this producer has NO credit/dedup store: a candidate
    # stops being a candidate once the export reflects the flip, so nothing is
    # ever credited by this producer itself.
    for col in f.values():
        assert "credit" not in col
    assert f["stock"]["source"] == "restock_skladom"

    st = json.loads(open(webapp.RESTOCK_STATE, encoding="utf-8").read())
    assert st["has_supplier_data"] is True and st["status"] == "ok"
    assert [c["code"] for c in st["candidates"]] == ["1/M"]
    assert st["candidates"][0]["supplierPrice"] == "79.9"
    assert st["queued"] == 4


def test_run_no_supplier_data_yet_queues_nothing(iso, monkeypatch):
    # #106 has never run -> SUPPLIER_STOCK_STATE doesn't exist -> queue nothing
    monkeypatch.setattr(webapp, "queue_shoptet_fields",
                        lambda *a, **k: pytest.fail("must not queue"))
    stats = webapp.run_restock_skladom()
    assert stats["candidates"] == 0 and stats["has_supplier_data"] is False
    assert stats["queued"] == 0
    assert stats["status"] == "ok"
    st = json.loads(open(webapp.RESTOCK_STATE, encoding="utf-8").read())
    assert st["has_supplier_data"] is False and st["candidates"] == []
    assert not os.path.exists(webapp.PENDING_SHOPTET)


def test_run_supplier_sold_out_queues_nothing(iso, monkeypatch):
    # supplier NOT available for p/1, p/2 stale -> zero candidates -> nothing queued
    _seed_supplier_stock(p1_available=False, p2_fresh=False)
    monkeypatch.setattr(webapp, "queue_shoptet_fields",
                        lambda *a, **k: pytest.fail("must not queue"))
    stats = webapp.run_restock_skladom()
    assert stats["candidates"] == 0 and stats["queued"] == 0
    assert stats["status"] == "ok"


def test_run_via_runner_records_ok_status(iso, monkeypatch):
    _seed_supplier_stock(p1_available=True, p2_fresh=False)
    c = authed_client()
    r = c.post("/api/automations/restock_skladom/run")
    assert r.get_json()["started"] is True
    webapp.RUNNER._threads["restock_skladom"].join(timeout=15)
    (st,) = [x for x in webapp.RUNNER.status() if x["key"] == "restock_skladom"]
    assert st["last_status"] == "ok"
    assert st["last_result"]["candidates"] == 1
    assert st["last_result"]["queued"] == 4
    assert st["enabled"] is False               # run-now must not enable the schedule


# ── endpoints ─────────────────────────────────────────────────────────────────
def test_endpoint_requires_login(iso):
    anon = webapp.app.test_client()
    assert anon.get("/api/restock-skladom").status_code == 401


def test_endpoint_serves_candidates(iso, monkeypatch):
    _seed_supplier_stock(p1_available=True, p2_fresh=False)
    webapp.run_restock_skladom()
    c = authed_client()
    j = c.get("/api/restock-skladom").get_json()
    assert j["has_supplier_data"] is True and len(j["candidates"]) == 1
    assert j["status"] == "ok" and j["last_check"]
    assert j["candidates"][0]["code"] == "1/M"
    assert j["queued"] == 4


# ── isolation: never touches the manager's live decision stores ────────────────
def test_run_never_touches_manager_stores(iso, monkeypatch):
    _seed_supplier_stock(p1_available=True, p2_fresh=False)
    webapp.run_restock_skladom()
    for _name, path in iso["manager_stores"].items():
        assert path.read_text(encoding="utf-8") == '{"sentinel": true}'


# ── #299 Task 9: an empty cell in a candidate row must be SKIPPED, never queued ─
def test_a_candidate_row_with_an_empty_cell_never_queues_that_field(iso, monkeypatch):
    """`queue_fields`'s own contract: an empty cell means "leave this field alone"
    in a Shoptet import, so queueing it would be a promise this producer cannot
    keep. Exercised end-to-end through `run_restock_skladom` (not just at the
    `queue_shoptet_fields` unit level in test_webreview_shoptet_upload.py) via
    `_restock_candidate_rows`, mirroring the Step 1 fixture in
    test_webreview_shoptet_upload.py but with ONE cell deliberately blank.
    #299 Task 9 review I1: the seam now takes the already-computed `candidates`
    (unused here, hence `_c`) instead of recomputing its own JOIN."""
    monkeypatch.setattr(webapp, "_restock_candidate_rows",
                        lambda _c: [["A", "P", "visible", "Skladom", "", "5"]])
    stats = webapp.run_restock_skladom()
    assert stats["queued"] == 3                  # 4 columns, 1 empty -> 3 queued
    f = webapp._load_pending()["A"]["fields"]
    assert set(f) == {"productVisibility", "availabilityInStock", "stock"}
    assert "availabilityOutOfStock" not in f      # the empty cell never landed


# ── #299 Task 9 review I1 — candidates (audit/store) and rows (what actually ─── #
# ── queues) must come from ONE read of the export + supplier_stock, never two ── #
def test_run_reads_the_export_and_supplier_stock_only_once(iso, monkeypatch):
    """Before this fix, `candidates` (the audit/store snapshot) and `rows` (what
    actually gets queued) came from TWO independent `_restock_candidates()` calls —
    a second full products.csv + supplier_stock.json read. Between the two reads
    `run_shoptet_sync` could atomically swap products.csv (its own hourly schedule,
    not mutually exclusive with this producer), so the queued rows could diverge
    from what the card/store recorded. Pin the read count to ONE of each."""
    _seed_supplier_stock(p1_available=True, p2_fresh=False)
    calls = {"export": 0, "stock": 0}
    real_export = webapp._read_export_for_links
    real_stock = webapp._load_supplier_stock

    def counted_export():
        calls["export"] += 1
        return real_export()

    def counted_stock():
        calls["stock"] += 1
        return real_stock()

    monkeypatch.setattr(webapp, "_read_export_for_links", counted_export)
    monkeypatch.setattr(webapp, "_load_supplier_stock", counted_stock)
    stats = webapp.run_restock_skladom()
    assert stats["queued"] == 4                  # the run still did real work
    assert calls["export"] == 1
    assert calls["stock"] == 1


# ── #299 Task 9 review I2 — export-age gate: stale/unknown age must refuse ───── #
# ── EVERYTHING (never a silent "0 candidates" success) ────────────────────────── #
def test_run_refuses_everything_when_the_export_is_stale(iso, monkeypatch):
    _seed_supplier_stock(p1_available=True, p2_fresh=False)
    monkeypatch.setattr(webapp, "_export_age_s", lambda: 6 * 3600 + 1)   # > limit
    monkeypatch.setattr(webapp, "queue_shoptet_fields",
                        lambda *a, **k: pytest.fail("must not queue on a stale export"))
    stats = webapp.run_restock_skladom()
    assert stats["ok"] is False
    assert stats["queued"] == 0 and stats["candidates"] == 0
    assert stats["status"] == "error"
    assert "starý" in stats["error"] and "6.0 h" in stats["error"]
    assert not os.path.exists(webapp.PENDING_SHOPTET)
    st = json.loads(open(webapp.RESTOCK_STATE, encoding="utf-8").read())
    assert st["status"] == "error"
    assert st["error"]


def test_run_refuses_everything_when_the_export_age_is_unknown(iso, monkeypatch):
    """Unknown age (file missing / unstat-able) is NEVER treated as fresh — same
    fail-closed stance as every other EXPORT_MAX_AGE_S gate in this file."""
    _seed_supplier_stock(p1_available=True, p2_fresh=False)
    monkeypatch.setattr(webapp, "_export_age_s", lambda: None)
    monkeypatch.setattr(webapp, "queue_shoptet_fields",
                        lambda *a, **k: pytest.fail("unknown age must not be treated as fresh"))
    stats = webapp.run_restock_skladom()
    assert stats["ok"] is False
    assert stats["queued"] == 0
    assert "nedá zistiť" in stats["error"]


def test_run_still_queues_when_the_export_is_explicitly_fresh(iso, monkeypatch):
    """The gate must not become fail-closed-always: an export inside the window
    still queues normally. Freshness is pinned explicitly (not left to the
    fixture's write-time mtime), so this proves the GATE lets it through."""
    _seed_supplier_stock(p1_available=True, p2_fresh=False)
    monkeypatch.setattr(webapp, "_export_age_s", lambda: 60.0)
    stats = webapp.run_restock_skladom()
    assert stats["ok"] is True
    assert stats["queued"] == 4


# ── graceful degradation ─────────────────────────────────────────────────────── #
# ── #299 Task 8 review finding: `last_error` is the ONLY place the runner tells ─ #
# ── the manager WHY a run failed — a test that only checks last_status='error' ── #
# ── would pass even if `last_error` were silently left empty. ────────────────── #
def test_a_corrupt_pending_table_makes_the_runner_record_error_not_crash(iso, monkeypatch):
    """#299 Task 9: queueing can no longer "fail" via a returned dict (there is no
    import to fail) — the ONE way it can fail now is `queue_shoptet_fields` refusing
    to write on top of an unreadable pending table (`StoreWipeRefused`). The runner
    must still survive that (records last_status='error') AND must give the manager
    a meaningful reason, not just an opaque failure flag."""
    _seed_supplier_stock(p1_available=True, p2_fresh=False)
    with open(webapp.PENDING_SHOPTET, "w", encoding="utf-8") as f:
        f.write("{ this is not json")

    # #299 Task 11 finding 1 — the RETURN value now reports whether the run
    # SUCCEEDED, not merely whether it ran; this run raised, so it is False.
    # The runner surviving (not crashing, `last_status`/`last_error` recorded)
    # is still pinned by the assertions right below.
    assert webapp.RUNNER._execute("restock_skladom") is False
    (st,) = [x for x in webapp.RUNNER.status() if x["key"] == "restock_skladom"]
    assert st["last_status"] == "error"
    assert st["running"] is False
    assert st["last_error"]                        # non-empty
    assert "poškoden" in st["last_error"].lower() or "StoreWipeRefused" in st["last_error"]
