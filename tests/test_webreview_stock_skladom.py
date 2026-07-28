"""In-app „Máme skladom → Skladom" auto-restock (#98) — Flask wiring: run function,
store, endpoint, registration, wired through the generic automation runner (#93).

#299 Task 9 rewrite: since the migration this automation no longer imports directly
— `run_stock_skladom` only QUEUES rows into the shared pending_shoptet table for the
next hourly „Sync do Shoptetu" drain (`tests/test_webreview_shoptet_upload.py`
covers that drain). This producer never credits itself and needs no dedup store
(unlike grube_externalcode/split_links): a candidate is entirely state-driven
(Vypredané+visible with real stock in the LIVE export), so once Shoptet confirms the
flip and the export next refreshes, `_stock_skladom_candidates` simply stops
selecting it. This file keeps the registration/JOIN-detection/endpoint/
runner-integration tests (the detection logic itself, `_stock_skladom_candidates`,
is UNCHANGED by this task), adapted to assert against the pending table and
`queued`/`candidates` instead of a completed import. The chunked-import-batch tests
are gone for the same reason as the sibling restock_skladom rewrite — see that
file's module docstring.

Distinct from #108 restock_skladom: the trigger is Shoptet's OWN physical stock
(stock>0), not a scraped supplier confirmation — so there is NO supplier_stock
dependency here. Hermetic: SRC (the export), STOCK_SKLADOM_STATE and the shared
pending_shoptet table are all redirected to tmp fixture content.
"""
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
    """Isolate every store this automation touches, incl. the shared pending_shoptet
    table the queue drops rows into (#299 Task 9). Manager stores get sentinels
    (asserted untouched)."""
    monkeypatch.setattr(webapp.RUNNER, "state_path", str(tmp_path / "automations.json"))
    monkeypatch.setattr(webapp, "OUT", str(tmp_path))
    src = tmp_path / "products.csv"
    src.write_bytes(EXPORT_CSV)
    monkeypatch.setattr(webapp, "SRC", str(src))
    monkeypatch.setattr(webapp, "STOCK_SKLADOM_STATE", str(tmp_path / "stock_skladom.json"))
    monkeypatch.setattr(webapp, "PENDING_SHOPTET", str(tmp_path / "pending_shoptet.json"))
    monkeypatch.setattr(webapp, "CODE2PAIR", {})

    sentinels = {}
    for name, fname in _MANAGER_STORES:
        p = tmp_path / fname
        p.write_text('{"sentinel": true}', encoding="utf-8")
        monkeypatch.setattr(webapp, name, str(p))
        sentinels[name] = p
    return {"tmp": tmp_path, "manager_stores": sentinels}


# ── registration + status ──────────────────────────────────────────────────────
def test_registered_disabled_daily_0645(iso):
    c = authed_client()
    (a,) = [x for x in c.get("/api/automations").get_json()["automations"]
            if x["key"] == "stock_skladom"]
    assert a["name"] == "Máme skladom → Skladom"
    # SAFETY: this automation feeds the live eshop → deploy starts stopped (#93 contract)
    assert a["enabled"] is False
    assert a["schedule"] == "denne o 06:45"
    assert a["running"] is False
    assert a["description"]                         # #173 plain-language description present


def test_disabled_automation_does_not_run(iso, monkeypatch):
    monkeypatch.setattr(webapp, "queue_shoptet_fields",
                        lambda *a, **k: pytest.fail("disabled must not run"))
    webapp.RUNNER.tick_once()                       # default state = disabled
    (a,) = [x for x in webapp.RUNNER.status() if x["key"] == "stock_skladom"]
    assert a["last_run"] == ""
    assert not os.path.exists(webapp.STOCK_SKLADOM_STATE)


# ── the run: detection + queueing (#299 Task 9) ─────────────────────────────────
def test_run_queues_only_have_but_vypredane_product(iso, monkeypatch):
    monkeypatch.setattr(webapp, "_import_rows_chunked",
                        lambda *a, **k: pytest.fail("producer must not import"))

    stats = webapp.run_stock_skladom()
    assert stats["ok"] is True
    assert stats["status"] == "ok"
    assert stats["candidates"] == 1
    # #299 Task 8 review finding: `candidates` (rows) vs `queued` (field values) —
    # DELIBERATELY different units, see the sibling restock_skladom test for why.
    # SKLADOM_COLS carries THREE writable columns (no `stock` — see decision below).
    assert stats["queued"] == 3

    d = webapp._load_pending()
    # only 1/M (have stock but show Vypredané); NOT 2/S (no stock), 3/L (already
    # skladom), 4/X (discontinued with residual stock — conscious off)
    assert list(d) == ["1/M"]
    f = d["1/M"]["fields"]
    assert f["productVisibility"]["value"] == "visible"
    assert f["availabilityInStock"]["value"] == "Skladom"
    assert f["availabilityOutOfStock"]["value"] == "Skladom"
    # #98 invariant: the real positive stock must NEVER be overwritten — SKLADOM_COLS
    # deliberately has no `stock` column, so nothing queues one.
    assert "stock" not in f
    # #299 Task 9 decision — no credit/dedup store for this producer either.
    for col in f.values():
        assert "credit" not in col

    st = json.loads(open(webapp.STOCK_SKLADOM_STATE, encoding="utf-8").read())
    assert st["status"] == "ok"
    assert [c["code"] for c in st["candidates"]] == ["1/M"]
    assert st["candidates"][0]["stock"] == "5"
    assert st["queued"] == 3


def test_run_no_candidates_queues_nothing(iso, monkeypatch):
    # an export with no have-but-vypredané products → nothing queued at all
    src = iso["tmp"] / "products.csv"
    src.write_bytes(
        "code;pairCode;name;supplier;productVisibility;availabilityInStock;"
        "availabilityOutOfStock;price;stock;internalNote\r\n"
        "3/L;P3;Uz skladom;TESTSUP;visible;Skladom;Skladom;19.90;5;\r\n".encode("cp1250"))
    monkeypatch.setattr(webapp, "SRC", str(src))
    monkeypatch.setattr(webapp, "queue_shoptet_fields",
                        lambda *a, **k: pytest.fail("must not queue"))
    stats = webapp.run_stock_skladom()
    assert stats["candidates"] == 0 and stats["queued"] == 0
    assert stats["status"] == "ok"
    st = json.loads(open(webapp.STOCK_SKLADOM_STATE, encoding="utf-8").read())
    assert st["candidates"] == []
    assert not os.path.exists(webapp.PENDING_SHOPTET)


def test_run_never_queues_discontinued_with_residual_stock(iso, monkeypatch):
    # the „neprepíše vedomé off rozhodnutie manažéra" invariant at the Flask level:
    # an export whose ONLY stocked row is a discontinued (detailOnly) product must
    # queue nothing.
    src = iso["tmp"] / "products.csv"
    src.write_bytes(
        "code;pairCode;name;supplier;productVisibility;availabilityInStock;"
        "availabilityOutOfStock;price;stock;internalNote\r\n"
        "4/X;P4;Ukoncene;TESTSUP;detailOnly;Predaj výrobku skončil;"
        "Predaj výrobku skončil;39.90;5;\r\n".encode("cp1250"))
    monkeypatch.setattr(webapp, "SRC", str(src))
    monkeypatch.setattr(webapp, "queue_shoptet_fields",
                        lambda *a, **k: pytest.fail("must not touch discontinued"))
    stats = webapp.run_stock_skladom()
    assert stats["candidates"] == 0 and stats["queued"] == 0


def test_run_via_runner_records_ok_status(iso, monkeypatch):
    c = authed_client()
    r = c.post("/api/automations/stock_skladom/run")
    assert r.get_json()["started"] is True
    webapp.RUNNER._threads["stock_skladom"].join(timeout=15)
    (st,) = [x for x in webapp.RUNNER.status() if x["key"] == "stock_skladom"]
    assert st["last_status"] == "ok"
    assert st["last_result"]["candidates"] == 1
    assert st["last_result"]["queued"] == 3
    assert st["enabled"] is False                   # run-now must not enable the schedule


# ── endpoint ────────────────────────────────────────────────────────────────────
def test_endpoint_requires_login(iso):
    anon = webapp.app.test_client()
    assert anon.get("/api/stock-skladom").status_code == 401


def test_endpoint_serves_candidates(iso, monkeypatch):
    webapp.run_stock_skladom()
    c = authed_client()
    j = c.get("/api/stock-skladom").get_json()
    assert len(j["candidates"]) == 1
    assert j["status"] == "ok" and j["last_check"]
    assert j["candidates"][0]["code"] == "1/M"
    assert j["queued"] == 3


# ── isolation: never touches the manager's live decision stores ────────────────
def test_run_never_touches_manager_stores(iso, monkeypatch):
    webapp.run_stock_skladom()
    for _name, path in iso["manager_stores"].items():
        assert path.read_text(encoding="utf-8") == '{"sentinel": true}'


# ── #299 Task 9: an empty cell in a candidate row must be SKIPPED, never queued ─
def test_a_candidate_row_with_an_empty_cell_never_queues_that_field(iso, monkeypatch):
    """Mirrors the sibling restock_skladom test — `queue_fields`'s own contract:
    an empty cell means "leave this field alone" in a Shoptet import.
    #299 Task 9 review I1: the seam now takes the already-computed `candidates`
    (unused here, hence `_c`) instead of recomputing its own JOIN."""
    monkeypatch.setattr(webapp, "_stock_skladom_candidate_rows",
                        lambda _c: [["A", "P", "visible", "", "Skladom"]])
    stats = webapp.run_stock_skladom()
    assert stats["queued"] == 2                  # 3 columns, 1 empty -> 2 queued
    f = webapp._load_pending()["A"]["fields"]
    assert set(f) == {"productVisibility", "availabilityOutOfStock"}
    assert "availabilityInStock" not in f         # the empty cell never landed


# ── #299 Task 9 review I1 — candidates and rows must come from ONE export read ── #
def test_run_reads_the_export_only_once(iso, monkeypatch):
    """Before this fix, `_stock_skladom_candidate_rows` recomputed the whole JOIN
    via its own `_stock_skladom_candidates()` call — a second full products.csv
    read over data already read once this run. Pin the read count to ONE."""
    calls = {"export": 0}
    real_export = webapp._read_export_for_links

    def counted_export():
        calls["export"] += 1
        return real_export()

    monkeypatch.setattr(webapp, "_read_export_for_links", counted_export)
    stats = webapp.run_stock_skladom()
    assert stats["queued"] == 3                  # the run still did real work
    assert calls["export"] == 1


# ── #299 Task 9 review I2 — export-age gate: stale/unknown age must refuse ───── #
# ── EVERYTHING (never a silent "0 candidates" success) ────────────────────────── #
def test_run_refuses_everything_when_the_export_is_stale(iso, monkeypatch):
    monkeypatch.setattr(webapp, "_export_age_s", lambda: 6 * 3600 + 1)   # > limit
    monkeypatch.setattr(webapp, "queue_shoptet_fields",
                        lambda *a, **k: pytest.fail("must not queue on a stale export"))
    stats = webapp.run_stock_skladom()
    assert stats["ok"] is False
    assert stats["queued"] == 0 and stats["candidates"] == 0
    assert stats["status"] == "error"
    assert "starý" in stats["error"] and "6.0 h" in stats["error"]
    assert not os.path.exists(webapp.PENDING_SHOPTET)
    st = json.loads(open(webapp.STOCK_SKLADOM_STATE, encoding="utf-8").read())
    assert st["status"] == "error"
    assert st["error"]


def test_run_refuses_everything_when_the_export_age_is_unknown(iso, monkeypatch):
    """Unknown age (file missing / unstat-able) is NEVER treated as fresh — same
    fail-closed stance as every other EXPORT_MAX_AGE_S gate in this file."""
    monkeypatch.setattr(webapp, "_export_age_s", lambda: None)
    monkeypatch.setattr(webapp, "queue_shoptet_fields",
                        lambda *a, **k: pytest.fail("unknown age must not be treated as fresh"))
    stats = webapp.run_stock_skladom()
    assert stats["ok"] is False
    assert stats["queued"] == 0
    assert "nedá zistiť" in stats["error"]


def test_run_still_queues_when_the_export_is_explicitly_fresh(iso, monkeypatch):
    """Freshness is pinned explicitly (not left to the fixture's write-time mtime),
    so this proves the GATE lets a fresh export through rather than being
    fail-closed-always."""
    monkeypatch.setattr(webapp, "_export_age_s", lambda: 60.0)
    stats = webapp.run_stock_skladom()
    assert stats["ok"] is True
    assert stats["queued"] == 3


# ── graceful degradation ─────────────────────────────────────────────────────── #
# ── #299 Task 8 review finding: `last_error` is the ONLY place the runner tells ─ #
# ── the manager WHY a run failed — pin it non-empty AND meaningful, not just ──── #
# ── that last_status flipped to 'error'. ──────────────────────────────────────── #
def test_a_corrupt_pending_table_makes_the_runner_record_error_not_crash(iso):
    """#299 Task 9: queueing can no longer "fail" via a returned dict (there is no
    import to fail) — the ONE way it can fail now is `queue_shoptet_fields` refusing
    to write on top of an unreadable pending table (`StoreWipeRefused`). The runner
    must still survive that (records last_status='error') AND must give the manager
    a meaningful reason."""
    with open(webapp.PENDING_SHOPTET, "w", encoding="utf-8") as f:
        f.write("{ this is not json")

    assert webapp.RUNNER._execute("stock_skladom") is True    # runner survives
    (st,) = [x for x in webapp.RUNNER.status() if x["key"] == "stock_skladom"]
    assert st["last_status"] == "error"
    assert st["running"] is False
    assert st["last_error"]                        # non-empty
    assert "poškoden" in st["last_error"].lower() or "StoreWipeRefused" in st["last_error"]
