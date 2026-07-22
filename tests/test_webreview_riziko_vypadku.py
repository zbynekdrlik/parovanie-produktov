"""„Riziko výpadku" supply-risk automation (#107) — Flask wiring: run function,
store, endpoints, registration, wired through the generic automation runner
(#93). READ-ONLY / advisory — it makes NO network calls and NEVER touches the
manager's decision stores or the eshop; it only reads the on-disk export +
#106's already-persisted supplier_stock.json.

Hermetic: SRC (the export) and SUPPLIER_STOCK_STATE are redirected to tmp fixture
content — no real scrape, no real export needed. Mirrors
test_webreview_supplier_stock.py's isolation pattern.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webreview"))
import app as webapp  # noqa: E402

from tests.conftest import authed_client  # noqa: E402

EXPORT_CSV = (
    "code;pairCode;name;supplier;productVisibility;availabilityInStock;"
    "availabilityOutOfStock;price;stock;internalNote\r\n"
    "1/M;P1;Bunda risk;TESTSUP;visible;Skladom;;99.90;5;https://supplier.test/p/1\r\n"
    "2/S;P2;Noz ok;TESTSUP;visible;Skladom;;19.90;3;https://supplier.test/p/2\r\n"
).encode("cp1250")

_MANAGER_STORES = (("DECISIONS", "decisions.json"), ("ORDERED", "ordered_items.json"),
                   ("WAITING", "waiting_items.json"),
                   ("ORDER_PAIRINGS", "order_pairings.json"),
                   ("SUPPLIER_ASSIGN", "supplier_assignments.json"))


@pytest.fixture
def iso(tmp_path, monkeypatch):
    """Isolate every store this automation touches (it touches none of the
    manager's — asserted below) + the export + the supplier_stock store."""
    monkeypatch.setattr(webapp.RUNNER, "state_path", str(tmp_path / "automations.json"))
    src = tmp_path / "products.csv"
    src.write_bytes(EXPORT_CSV)
    monkeypatch.setattr(webapp, "SRC", str(src))
    monkeypatch.setattr(webapp, "SUPPLIER_STOCK_STATE", str(tmp_path / "supplier_stock.json"))
    monkeypatch.setattr(webapp, "RIZIKO_STATE", str(tmp_path / "riziko_vypadku.json"))

    sentinels = {}
    for name, fname in _MANAGER_STORES:
        p = tmp_path / fname
        p.write_text('{"sentinel": true}', encoding="utf-8")
        monkeypatch.setattr(webapp, name, str(p))
        sentinels[name] = p
    return {"tmp": tmp_path, "manager_stores": sentinels}


def _seed_supplier_stock(available_p1=False, available_p2=True):
    webapp._save_supplier_stock({
        "last_check": "2026-07-22T05:00:00+02:00",
        "rows": [
            {"link": "https://supplier.test/p/1", "ok": True, "available": available_p1,
             "availabilityText": "Vypredané" if not available_p1 else "Skladom",
             "supplier": "TESTSUP", "checkedAt": "2026-07-22T05:00:00+02:00"},
            {"link": "https://supplier.test/p/2", "ok": True, "available": available_p2,
             "availabilityText": "Skladom" if available_p2 else "Vypredané",
             "supplier": "TESTSUP", "checkedAt": "2026-07-22T05:00:00+02:00"},
        ],
        "stats": {},
    })


# ── registration + status ──────────────────────────────────────────────────────
def test_registered_disabled_daily_615(iso):
    c = authed_client()
    (a,) = [x for x in c.get("/api/automations").get_json()["automations"]
            if x["key"] == "riziko_vypadku"]
    assert a["name"] == "Riziko výpadku"
    assert a["enabled"] is False            # SAFETY: deploy starts stopped (#93 contract)
    assert a["schedule"] == "denne o 06:15"
    assert a["running"] is False


def test_disabled_automation_does_not_run(iso):
    webapp.RUNNER.tick_once()
    (a,) = [x for x in webapp.RUNNER.status() if x["key"] == "riziko_vypadku"]
    assert a["last_run"] == ""
    assert not os.path.exists(webapp.RIZIKO_STATE)


# ── the run: join against #106's already-scraped data ──────────────────────────
def test_run_flags_only_the_risky_product(iso):
    _seed_supplier_stock(available_p1=False, available_p2=True)
    stats = webapp.run_riziko_vypadku()
    assert stats == {"risks": 1, "has_supplier_data": True}

    st = json.loads(open(webapp.RIZIKO_STATE, encoding="utf-8").read())
    assert st["has_supplier_data"] is True
    assert [r["code"] for r in st["risks"]] == ["1/M"]
    assert st["risks"][0]["supplierAvailabilityText"] == "Vypredané"


def test_run_no_supplier_data_yet_surfaces_flag_not_false_all_clear(iso):
    # #106 has never run -> SUPPLIER_STOCK_STATE doesn't exist
    stats = webapp.run_riziko_vypadku()
    assert stats == {"risks": 0, "has_supplier_data": False}
    st = json.loads(open(webapp.RIZIKO_STATE, encoding="utf-8").read())
    assert st["has_supplier_data"] is False
    assert st["risks"] == []


def test_run_via_runner_records_ok_status(iso):
    _seed_supplier_stock()
    c = authed_client()
    r = c.post("/api/automations/riziko_vypadku/run")
    assert r.get_json()["started"] is True
    webapp.RUNNER._threads["riziko_vypadku"].join(timeout=15)
    (st,) = [x for x in webapp.RUNNER.status() if x["key"] == "riziko_vypadku"]
    assert st["last_status"] == "ok"
    assert st["last_result"]["risks"] == 1
    assert st["enabled"] is False           # run-now must not enable the schedule


# ── endpoints ─────────────────────────────────────────────────────────────────
def test_endpoint_requires_login(iso):
    anon = webapp.app.test_client()
    assert anon.get("/api/riziko-vypadku").status_code == 401
    assert anon.get("/api/riziko-vypadku/csv").status_code == 401


def test_endpoint_serves_rows(iso):
    _seed_supplier_stock()
    webapp.run_riziko_vypadku()
    c = authed_client()
    j = c.get("/api/riziko-vypadku").get_json()
    assert j["has_supplier_data"] is True and len(j["risks"]) == 1 and j["last_check"]


def test_csv_endpoint_download(iso):
    _seed_supplier_stock()
    webapp.run_riziko_vypadku()
    c = authed_client()
    r = c.get("/api/riziko-vypadku/csv")
    assert r.status_code == 200
    assert r.mimetype == "text/csv"
    assert "attachment" in r.headers["Content-Disposition"]
    body = r.data.decode("utf-8-sig")
    assert "1/M" in body and "Bunda risk" in body


def test_csv_endpoint_empty_when_no_risks(iso):
    _seed_supplier_stock(available_p1=True, available_p2=True)   # nothing risky
    webapp.run_riziko_vypadku()
    c = authed_client()
    r = c.get("/api/riziko-vypadku/csv")
    body = r.data.decode("utf-8-sig")
    lines = [line for line in body.strip().splitlines() if line]
    assert len(lines) == 1                  # header only, no data rows


# ── isolation: never touches the manager's live decision stores ────────────────
def test_run_never_touches_manager_stores(iso):
    _seed_supplier_stock()
    webapp.run_riziko_vypadku()
    for _name, path in iso["manager_stores"].items():
        assert path.read_text(encoding="utf-8") == '{"sentinel": true}'
