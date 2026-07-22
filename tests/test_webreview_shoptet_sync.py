"""Hourly „Sync zo Shoptetu" automation (#119) — orders export + full catalog
export refresh, in-memory CODE2PAIR/CATALOG rebuild, and review_data.json
price/stock resync, wired through the generic automation runner (#93).

Hermetic: the two Shoptet fetch functions (_fetch_orders_csv / _fetch_export_csv)
are monkeypatched with canned cp1250 CSV bytes / a raising stub — no network, no
browser automation. Every store path (SRC/DATA/ORDERS_CACHE + the 5 manager
decision stores) is redirected to tmp, mirroring test_webreview_automations.py's
isolation pattern.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webreview"))
import app as webapp  # noqa: E402

from tests.conftest import authed_client  # noqa: E402

ORDERS_CSV = (
    "code;date;statusName;email;phone;billFullName;packageNumber;itemCode\r\n"
    "2026300;2026-07-22 10:00:00;Vybavuje sa;x@example.com;;X Y;;1/M\r\n"
).encode("cp1250")

# two variants of the SAME product (shared pairCode) — proves CODE2PAIR/CATALOG
# rebuild groups per-product, and review resync aggregates both variant codes.
EXPORT_CSV = (
    "code;pairCode;name;supplier;productVisibility;availabilityInStock;"
    "availabilityOutOfStock;price;standardPrice;stock;defaultImage\r\n"
    "1/M;P1;Bunda Test;BETALOV;visible;Skladom;;59,90;69,90;5;https://x/a.jpg\r\n"
    "1/L;P1;Bunda Test;BETALOV;visible;Skladom;;59,90;69,90;2;https://x/a.jpg\r\n"
).encode("cp1250")

_MANAGER_STORES = (("DECISIONS", "decisions.json"), ("ORDERED", "ordered_items.json"),
                   ("WAITING", "waiting_items.json"),
                   ("ORDER_PAIRINGS", "order_pairings.json"),
                   ("SUPPLIER_ASSIGN", "supplier_assignments.json"))


def _product():
    return {"key": "BETALOV|P1", "idx": 0, "supplier": "BETALOV", "name": "Bunda Test",
            "pairCode": "P1", "variant_codes": ["1/M"], "our_url": "", "our_images": [],
            "ai_status": "matched", "ai_chosen_url": "", "ai_reason": "",
            "candidates": [], "current": {}}


@pytest.fixture
def iso(tmp_path, monkeypatch):
    """Isolate every store this automation can touch + the network edges."""
    monkeypatch.setattr(webapp.RUNNER, "state_path", str(tmp_path / "automations.json"))
    src = tmp_path / "products.csv"
    data = tmp_path / "review_data.json"
    orders_cache = tmp_path / "orders_cache.csv"
    products = [_product()]
    data.write_text(json.dumps(products, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(webapp, "SRC", str(src))
    monkeypatch.setattr(webapp, "DATA", str(data))
    monkeypatch.setattr(webapp, "ORDERS_CACHE", str(orders_cache))
    monkeypatch.setattr(webapp, "PRODUCTS", products)
    monkeypatch.setattr(webapp, "CODE2PAIR", {})
    monkeypatch.setattr(webapp, "CATALOG", {})
    monkeypatch.setattr(webapp, "_fetch_orders_csv", lambda: ORDERS_CSV)
    monkeypatch.setattr(webapp, "_fetch_export_csv", lambda: EXPORT_CSV)
    sentinel_paths = {}
    for name, fname in _MANAGER_STORES:
        p = tmp_path / fname
        p.write_text('{"sentinel": true}', encoding="utf-8")
        monkeypatch.setattr(webapp, name, str(p))
        sentinel_paths[name] = p
    return {"tmp": tmp_path, "src": src, "data": data, "orders_cache": orders_cache,
            "manager_stores": sentinel_paths}


# ── registration + status ─────────────────────────────────────────────────────
def test_shoptet_sync_registered_disabled_hourly(iso):
    c = authed_client()
    j = c.get("/api/automations").get_json()
    (a,) = [x for x in j["automations"] if x["key"] == "shoptet_sync"]
    assert a["name"] == "Sync zo Shoptetu"
    assert a["enabled"] is False           # SAFETY: deploy starts stopped (#93 contract)
    assert a["schedule"] == "každú hodinu"
    assert a["running"] is False


# ── successful sync ────────────────────────────────────────────────────────────
def test_run_now_success_refreshes_orders_catalog_and_review(iso):
    result = webapp.run_shoptet_sync()

    assert result["orders_bytes"] == len(ORDERS_CSV)
    assert result["catalog_codes"] == 2            # 1/M + 1/L
    assert result["catalog_products"] == 1          # grouped under shared pairCode P1
    assert result["review_synced"] == 1
    assert result["review_stale"] == 0

    # fetch-then-swap: both files actually written to disk
    assert iso["orders_cache"].read_bytes() == ORDERS_CSV
    assert iso["src"].read_bytes() == EXPORT_CSV

    # in-memory search index rebuilt (no restart needed)
    assert webapp.CODE2PAIR["1/M"] == "P1"
    assert webapp.CODE2PAIR["1/L"] == "P1"
    assert "P1" in webapp.CATALOG

    # review_data.json's price/stock snapshot resynced — file AND in-memory PRODUCTS
    on_disk = json.loads(iso["data"].read_text(encoding="utf-8"))
    assert on_disk[0]["current"]["price"] == "59,90"
    assert on_disk[0]["current"]["state"] == 1
    assert on_disk[0]["variant_codes"] == ["1/M", "1/L"]
    assert webapp.PRODUCTS[0]["current"]["price"] == "59,90"


def test_run_now_via_http_endpoint_and_runner(iso):
    c = authed_client()
    r = c.post("/api/automations/shoptet_sync/run")
    assert r.status_code == 200
    assert r.get_json()["started"] is True
    webapp.RUNNER._threads["shoptet_sync"].join(timeout=10)
    (st,) = [x for x in webapp.RUNNER.status() if x["key"] == "shoptet_sync"]
    assert st["last_status"] == "ok"
    assert st["last_result"]["review_synced"] == 1


# ── failure degrades gracefully — never crashes, never partial-writes ─────────
def test_run_fails_gracefully_when_orders_creds_missing(iso, monkeypatch):
    iso["orders_cache"].write_bytes(b"OLD ORDERS DATA")
    iso["src"].write_bytes(b"OLD EXPORT DATA")

    def boom():
        raise RuntimeError("SHOPTET_ORDERS_URL chyba v data/.shoptet_admin")
    monkeypatch.setattr(webapp, "_fetch_orders_csv", boom)

    assert webapp.RUNNER._execute("shoptet_sync") is True   # runner survives
    (st,) = [x for x in webapp.RUNNER.status() if x["key"] == "shoptet_sync"]
    assert st["last_status"] == "error"
    assert "SHOPTET_ORDERS_URL" in st["last_error"]
    assert st["running"] is False

    # nothing on disk changed — the fetch raised before any write
    assert iso["orders_cache"].read_bytes() == b"OLD ORDERS DATA"
    assert iso["src"].read_bytes() == b"OLD EXPORT DATA"


def test_run_fails_on_catalog_fetch_after_orders_already_refreshed(iso, monkeypatch):
    # orders succeed, catalog export fails — proves each file swap is independently
    # atomic (never a half-written products.csv), even though the two steps
    # aren't a single transaction across both files.
    iso["src"].write_bytes(b"OLD EXPORT DATA")

    def boom():
        raise RuntimeError("SHOPTET_EXPORT_URL chyba v data/.shoptet_admin")
    monkeypatch.setattr(webapp, "_fetch_export_csv", boom)

    with pytest.raises(RuntimeError, match="SHOPTET_EXPORT_URL"):
        webapp.run_shoptet_sync()

    assert iso["orders_cache"].read_bytes() == ORDERS_CSV      # step 1 completed
    assert iso["src"].read_bytes() == b"OLD EXPORT DATA"       # step 2 never landed
    assert webapp.PRODUCTS[0]["current"] == {}                 # review resync never ran


def test_run_via_runner_after_export_failure_records_error(iso, monkeypatch):
    def boom():
        raise RuntimeError("SHOPTET_EXPORT_URL chyba")
    monkeypatch.setattr(webapp, "_fetch_export_csv", boom)

    assert webapp.RUNNER._execute("shoptet_sync") is True
    (st,) = [x for x in webapp.RUNNER.status() if x["key"] == "shoptet_sync"]
    assert st["last_status"] == "error"
    assert "SHOPTET_EXPORT_URL" in st["last_error"]


# ── never touches the manager's live decision stores ───────────────────────────
def test_run_never_touches_manager_decision_stores(iso):
    webapp.run_shoptet_sync()
    for _name, path in iso["manager_stores"].items():
        assert path.read_text(encoding="utf-8") == '{"sentinel": true}'
