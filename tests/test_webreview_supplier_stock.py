"""„Dodávateľský sklad" scraper automation (#106) — Flask wiring: run function,
store, endpoint, registration, wired through the generic automation runner (#93).

Hermetic: the supplier fetch (_fetch_supplier_html) and the OpenAI call
(_llm_extract) are monkeypatched — NO real network, NO OpenAI. The export source
(SRC), the supplier_stock store and the 5 manager decision stores are redirected
to tmp. Mirrors test_webreview_shoptet_sync.py's isolation pattern.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webreview"))
import app as webapp  # noqa: E402

from tests.conftest import authed_client  # noqa: E402

# two links: /p/a resolves fully via JSON-LD (no LLM), /p/b needs the LLM fallback.
EXPORT_CSV = (
    "code;pairCode;name;supplier;productVisibility;internalNote\r\n"
    "1/M;P1;Bunda;TESTSUP;visible;https://www.example-supplier.com/p/a\r\n"
    "2/S;P2;Nôž;TESTSUP;visible;https://www.example-supplier.com/p/b\r\n"
).encode("cp1250")

HTML_A = ('<html><head><script type="application/ld+json">'
          '{"@type":"Product","offers":{"@type":"Offer","price":"129.90",'
          '"priceCurrency":"EUR","availability":"https://schema.org/InStock"}}'
          '</script></head><body>Bunda</body></html>')
HTML_B = "<html><body>len marketingový text, žiadne dáta</body></html>"

_MANAGER_STORES = (("DECISIONS", "decisions.json"), ("ORDERED", "ordered_items.json"),
                   ("WAITING", "waiting_items.json"),
                   ("ORDER_PAIRINGS", "order_pairings.json"),
                   ("SUPPLIER_ASSIGN", "supplier_assignments.json"))


@pytest.fixture
def iso(tmp_path, monkeypatch):
    """Isolate every store this automation touches + the network/LLM edges."""
    monkeypatch.setattr(webapp.RUNNER, "state_path", str(tmp_path / "automations.json"))
    src = tmp_path / "products.csv"
    src.write_bytes(EXPORT_CSV)
    store = tmp_path / "supplier_stock.json"
    monkeypatch.setattr(webapp, "SRC", str(src))
    monkeypatch.setattr(webapp, "SUPPLIER_STOCK_STATE", str(store))
    monkeypatch.setattr(webapp, "SUPPLIER_FETCH_DELAY_S", 0)     # no real sleeps
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")            # deterministic key
    fetched = []

    def fake_fetch(url):
        fetched.append(url)
        return HTML_A if url.endswith("/p/a") else HTML_B
    monkeypatch.setattr(webapp, "_fetch_supplier_html", fake_fetch)

    llm_calls = []

    def fake_llm(text, url):
        llm_calls.append(url)
        return {"available": True, "price": 99.0, "currency": "EUR",
                "availabilityText": "Skladom", "variants": [{"size": "M", "available": True}]}
    monkeypatch.setattr(webapp, "_llm_extract", fake_llm)

    sentinels = {}
    for name, fname in _MANAGER_STORES:
        p = tmp_path / fname
        p.write_text('{"sentinel": true}', encoding="utf-8")
        monkeypatch.setattr(webapp, name, str(p))
        sentinels[name] = p
    return {"tmp": tmp_path, "store": store, "fetched": fetched, "llm_calls": llm_calls,
            "manager_stores": sentinels}


# ── registration + status ─────────────────────────────────────────────────────
def test_registered_disabled_daily_5am(iso):
    c = authed_client()
    (a,) = [x for x in c.get("/api/automations").get_json()["automations"]
            if x["key"] == "dodavatelsky_sklad"]
    assert a["name"] == "Dodávateľský sklad"
    assert a["enabled"] is False            # SAFETY: deploy starts stopped (#93 contract)
    assert a["schedule"] == "denne o 05:00"
    assert a["running"] is False


def test_disabled_automation_does_not_run(iso):
    # default = no state = DISABLED → a scheduler tick must NOT execute the scraper
    webapp.RUNNER.tick_once()
    (a,) = [x for x in webapp.RUNNER.status() if x["key"] == "dodavatelsky_sklad"]
    assert a["last_run"] == ""
    assert iso["fetched"] == []             # nothing was scraped
    assert not iso["store"].exists()


# ── the run: static tier resolves, LLM only when static fails ─────────────────
def test_run_static_resolves_and_llm_only_when_needed(iso):
    stats = webapp.run_supplier_stock()
    assert stats["total"] == 2 and stats["checked"] == 2 and stats["errors"] == 0
    assert stats["static"] == 1 and stats["llm"] == 1 and stats["llm_calls"] == 1
    assert stats["available"] == 2

    # LLM was called for /p/b ONLY (static resolved /p/a fully → ~2/3 cost saved)
    assert iso["llm_calls"] == ["https://www.example-supplier.com/p/b"]

    st = json.loads(iso["store"].read_text(encoding="utf-8"))
    by = {r["link"]: r for r in st["rows"]}
    a = by["https://www.example-supplier.com/p/a"]
    assert a["extractedBy"] == "jsonld" and a["available"] is True and a["price"] == 129.90
    assert a["supplier"] == "TESTSUP" and a["codes"] == ["1/M"]
    b = by["https://www.example-supplier.com/p/b"]
    assert b["extractedBy"] == "llm" and b["available"] is True and b["price"] == 99.0
    assert b["variants"] == [{"size": "M", "available": True}]


def test_run_no_openai_key_degrades_to_static_only(iso, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    stats = webapp.run_supplier_stock()
    assert stats["llm_calls"] == 0 and iso["llm_calls"] == []   # never called the LLM
    st = json.loads(iso["store"].read_text(encoding="utf-8"))
    b = {r["link"]: r for r in st["rows"]}["https://www.example-supplier.com/p/b"]
    assert b["extractedBy"] == "static-only" and b["available"] is None
    assert b["ok"] is True                  # graceful degrade, NOT an error


# ── a failing supplier fetch is recorded, never crashes the run ───────────────
def test_run_fetch_error_recorded_not_crash(iso, monkeypatch):
    def flaky(url):
        if url.endswith("/p/b"):
            raise RuntimeError("HTTP 503 od dodávateľa")
        return HTML_A
    monkeypatch.setattr(webapp, "_fetch_supplier_html", flaky)

    stats = webapp.run_supplier_stock()     # must NOT raise
    assert stats["errors"] == 1 and stats["checked"] == 1
    st = json.loads(iso["store"].read_text(encoding="utf-8"))
    b = {r["link"]: r for r in st["rows"]}["https://www.example-supplier.com/p/b"]
    assert b["ok"] is False and b["extractedBy"] == "error" and "503" in b["error"]


def test_run_via_runner_records_ok_status(iso):
    c = authed_client()
    r = c.post("/api/automations/dodavatelsky_sklad/run")
    assert r.get_json()["started"] is True
    webapp.RUNNER._threads["dodavatelsky_sklad"].join(timeout=15)
    (st,) = [x for x in webapp.RUNNER.status() if x["key"] == "dodavatelsky_sklad"]
    assert st["last_status"] == "ok"
    assert st["last_result"]["checked"] == 2
    assert st["enabled"] is False           # run-now must not enable the schedule


# ── stale-skip: a recently-checked OK link is not re-fetched ──────────────────
def test_run_skips_recently_checked_links(iso):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    iso["store"].write_text(json.dumps({"rows": [{
        "link": "https://www.example-supplier.com/p/a", "ok": True,
        "available": True, "price": 10.0, "extractedBy": "jsonld", "checkedAt": now,
    }]}), encoding="utf-8")

    stats = webapp.run_supplier_stock()
    assert stats["skipped"] == 1
    assert iso["fetched"] == ["https://www.example-supplier.com/p/b"]   # /p/a NOT re-fetched
    st = json.loads(iso["store"].read_text(encoding="utf-8"))
    a = {r["link"]: r for r in st["rows"]}["https://www.example-supplier.com/p/a"]
    assert a["price"] == 10.0               # old row kept verbatim


# ── endpoint ──────────────────────────────────────────────────────────────────
def test_endpoint_requires_login(iso):
    anon = webapp.app.test_client()
    assert anon.get("/api/supplier-stock").status_code == 401


def test_endpoint_serves_rows(iso):
    webapp.run_supplier_stock()
    c = authed_client()
    j = c.get("/api/supplier-stock").get_json()
    assert j["stats"]["checked"] == 2 and len(j["rows"]) == 2 and j["last_check"]


# ── isolation: never touches the manager's live decision stores ───────────────
def test_run_never_touches_manager_stores(iso):
    webapp.run_supplier_stock()
    for _name, path in iso["manager_stores"].items():
        assert path.read_text(encoding="utf-8") == '{"sentinel": true}'
