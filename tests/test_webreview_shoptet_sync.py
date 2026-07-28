"""Hourly „Sync zo Shoptetu" automation (#119) — orders export + full catalog
export + customer export refresh, in-memory CODE2PAIR/CATALOG rebuild, and
review_data.json price/stock resync, wired through the generic automation runner (#93).

Hermetic: the three Shoptet fetch functions (_fetch_orders_csv / _fetch_export_csv /
_fetch_customers_csv) are monkeypatched with canned cp1250 CSV bytes / a raising stub
— no network, no browser automation. Every store path (SRC/DATA/ORDERS_CACHE/
CUSTOMERS_CACHE + the 5 manager decision stores) is redirected to tmp, mirroring
test_webreview_automations.py's isolation pattern. The customer CSV fixture is
SYNTHETIC — never real PII.
"""
import json
import os
import sys
from datetime import date, timedelta

import pytest
import requests

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

# SYNTHETIC customer export (never real PII) — a few Shoptet customer columns, one fake row.
CUSTOMERS_CSV = (
    "guid;registrationDate;billingFullName;email;phone;customerGroup\r\n"
    "g-test-1;2026-01-02;Test Zákazník;t@example.com;;Veľkoobchod\r\n"
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
    customers_cache = tmp_path / "customers_cache.csv"
    products = [_product()]
    data.write_text(json.dumps(products, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(webapp, "SRC", str(src))
    monkeypatch.setattr(webapp, "DATA", str(data))
    monkeypatch.setattr(webapp, "ORDERS_CACHE", str(orders_cache))
    monkeypatch.setattr(webapp, "CUSTOMERS_CACHE", str(customers_cache))
    monkeypatch.setattr(webapp, "PRODUCTS", products)
    monkeypatch.setattr(webapp, "CODE2PAIR", {})
    monkeypatch.setattr(webapp, "CATALOG", {})
    monkeypatch.setattr(webapp, "_fetch_orders_csv", lambda: ORDERS_CSV)
    monkeypatch.setattr(webapp, "_fetch_export_csv", lambda: EXPORT_CSV)
    monkeypatch.setattr(webapp, "_fetch_customers_csv", lambda: CUSTOMERS_CSV)
    sentinel_paths = {}
    for name, fname in _MANAGER_STORES:
        p = tmp_path / fname
        p.write_text('{"sentinel": true}', encoding="utf-8")
        monkeypatch.setattr(webapp, name, str(p))
        sentinel_paths[name] = p
    return {"tmp": tmp_path, "src": src, "data": data, "orders_cache": orders_cache,
            "customers_cache": customers_cache, "manager_stores": sentinel_paths}


# ── secret hygiene: a network failure must never leak the partner-hash URL ────
# (the same rule scripts/shoptet_import.py::_backup_export already follows for
# this exact credential — "NEvkladaj `e` do hlášky, obsahuje URL s tajným hashom")
def test_fetch_export_csv_sanitizes_secret_url_on_network_failure(monkeypatch):
    secret_url = "https://www.forestshop.sk/export/products.csv?hash=TOTALLY-SECRET-HASH"
    monkeypatch.setattr(webapp, "_cred",
                        lambda key: secret_url if key == "SHOPTET_EXPORT_URL" else None)

    def boom(*a, **kw):
        raise requests.ConnectionError(f"Failed to connect to {secret_url}")
    monkeypatch.setattr(webapp.requests, "get", boom)

    with pytest.raises(RuntimeError) as exc_info:
        webapp._fetch_export_csv()

    msg = str(exc_info.value)
    assert "TOTALLY-SECRET-HASH" not in msg
    assert "ConnectionError" in msg
    # the chain is suppressed too (`from None`) — never leaks via a traceback/log.exception
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True


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
    assert result["customers_bytes"] == len(CUSTOMERS_CSV)

    # fetch-then-swap: all three exports actually written to disk
    assert iso["orders_cache"].read_bytes() == ORDERS_CSV
    assert iso["src"].read_bytes() == EXPORT_CSV
    assert iso["customers_cache"].read_bytes() == CUSTOMERS_CSV

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
    """Still true after #212 gave the sync a prune, and true for two independent reasons:
    this fixture's one-order export is far under `ORDERS_PRUNE_MIN_ORDERS`, and the
    sentinel key is not `<order>|<item>` shaped so no export could ever judge it."""
    webapp.run_shoptet_sync()
    for _name, path in iso["manager_stores"].items():
        assert path.read_text(encoding="utf-8") == '{"sentinel": true}'


# ── #212: the hourly refresh is where the orphan prune runs ────────────────────
def test_run_prunes_orphan_line_flags_from_the_freshly_downloaded_export(iso, monkeypatch):
    """The prune must be WIRED, not merely written — and wired to the bytes just
    downloaded, which is the only copy guaranteed to be current.

    The export below closes `99002002` and keeps `99002001` open, plus enough other orders
    to clear the plausibility floor. Only the closed order's key may go; the one whose
    order is still open, and the one no export row mentions, must both survive.
    """
    # dated relative to TODAY: the closed order must be past the reopen grace period
    # (`ORDERS_PRUNE_MIN_AGE_DAYS`), which a fixed date silently stops being
    recent = (date.today() - timedelta(days=2)).isoformat()
    old_day = (date.today() - timedelta(days=120)).isoformat()
    rows = (f"99002001;{recent} 09:00:00;Vybavuje sa;a@x.sk;;X Y;;A1\r\n"
            f"99002002;{old_day} 09:00:00;Vybavená;a@x.sk;;X Y;;B1\r\n"
            + "".join(f"99003{i:03d};{old_day} 09:00:00;Vybavená;a@x.sk;;X Y;;Z{i}\r\n"
                      for i in range(60)))
    monkeypatch.setattr(webapp, "_fetch_orders_csv",
                        lambda: (ORDERS_CSV.decode("cp1250") + rows).encode("cp1250"))
    ordered = iso["manager_stores"]["ORDERED"]
    ordered.write_text(json.dumps({"99002001|A1": True, "99002002|B1": True,
                                   "99001500|C1": True}), encoding="utf-8")

    result = webapp.run_shoptet_sync()

    assert result["flags_pruned"] == 1, result
    assert "flags_prune_skipped" not in result, result
    assert sorted(json.loads(ordered.read_text(encoding="utf-8"))) == \
        ["99001500|C1", "99002001|A1"]


# ── #293: a refused prune is a DEGRADED run, and says what it fired on ─────────
def test_a_refused_prune_marks_the_run_degraded_and_returns_its_numbers(iso, monkeypatch):
    """`no-open-orders` / `no-status-column` are PERMANENT: until the export is fixed the
    prune never runs once and the flag stores grow exactly as before #212. The run itself
    legitimately ends `ok` (orders, catalogue and review all landed), so without a signal of
    its own the card reads as a healthy hour — the „quietly dead automation" shape from
    `.claude/rules/automation-health.md` §3, reached from the other side.

    It rides the SAME `source_degraded` flag #282 introduced, which `navError()` already
    reads, rather than inventing a second predicate the sidebar would have to learn."""
    old_day = (date.today() - timedelta(days=120)).isoformat()
    # a plausible export in which NOTHING is open: the open literal has been renamed. Built
    # WITHOUT the fixture's own open order, which is the whole point of this shape.
    head = ORDERS_CSV.decode("cp1250").splitlines(keepends=True)[0]
    rows = "".join(f"99003{i:03d};{old_day} 09:00:00;Vybavená;a@x.sk;;X Y;;Z{i}\r\n"
                   for i in range(60))
    monkeypatch.setattr(webapp, "_fetch_orders_csv",
                        lambda: (head + rows).encode("cp1250"))

    result = webapp.run_shoptet_sync()

    assert result["flags_prune_skipped"] == "no-open-orders", result
    assert result["source_degraded"] is True, result
    # a refusal has to return the number it fired on, or the operator is told „your export
    # is wrong" with nothing to go and look at
    assert result["flags_orders_seen"] >= 60, result
    assert result["flags_orders_open"] == 0, result


def test_a_healthy_run_is_NOT_marked_degraded_and_reports_the_unknown_statuses(
        iso, monkeypatch):
    """The other half: a run that pruned normally must not carry the degraded flag, or the ⚠
    badge becomes permanent noise and stops meaning anything. It still reports the statuses
    the allow-list does not know — the honest cost of that list."""
    recent = (date.today() - timedelta(days=2)).isoformat()
    old_day = (date.today() - timedelta(days=120)).isoformat()
    rows = (f"99002001;{recent} 09:00:00;Vybavuje sa;a@x.sk;;X Y;;A1\r\n"
            f"99002002;{old_day} 09:00:00;Osob. odber;a@x.sk;;X Y;;B1\r\n"
            + "".join(f"99003{i:03d};{old_day} 09:00:00;Vybavená;a@x.sk;;X Y;;Z{i}\r\n"
                      for i in range(60)))
    monkeypatch.setattr(webapp, "_fetch_orders_csv",
                        lambda: (ORDERS_CSV.decode("cp1250") + rows).encode("cp1250"))

    result = webapp.run_shoptet_sync()

    assert "flags_prune_skipped" not in result, result
    assert "source_degraded" not in result, result
    assert result["flags_unknown_statuses"] == ["Osob. odber"], result
    assert result["flags_orders_open"] == 2, result   # 99002001 + the fixture's own


# ── customer export: secret hygiene (same rule as the catalog export) ──────────
def test_fetch_customers_csv_sanitizes_secret_url_on_network_failure(monkeypatch):
    secret_url = "https://www.forestshop.sk/export/customers.csv?hash=TOTALLY-SECRET-HASH"
    monkeypatch.setattr(webapp, "_cred",
                        lambda key: secret_url if key == "SHOPTET_CUSTOMERS_URL" else None)

    def boom(*a, **kw):
        raise requests.ConnectionError(f"Failed to connect to {secret_url}")
    monkeypatch.setattr(webapp.requests, "get", boom)

    with pytest.raises(RuntimeError) as exc_info:
        webapp._fetch_customers_csv()

    msg = str(exc_info.value)
    assert "TOTALLY-SECRET-HASH" not in msg
    assert "ConnectionError" in msg
    # chain suppressed (`from None`) — hash never leaks via traceback/log.exception
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True


# ── customers fetched LAST + NON-FATAL: a failure never rolls back the critical
#    refresh AND never turns the whole sync red (nothing consumes customers yet) ──
def test_customers_fetch_failure_is_non_fatal_critical_refresh_survives(iso, monkeypatch):
    iso["customers_cache"].write_bytes(b"OLD CUSTOMERS DATA")

    def boom():
        raise RuntimeError("stiahnutie exportu zákazníkov zlyhalo: ConnectionError (URL skrytá)")
    monkeypatch.setattr(webapp, "_fetch_customers_csv", boom)

    result = webapp.run_shoptet_sync()          # does NOT raise (non-fatal)

    # the critical exports DID refresh (customers runs after them)
    assert iso["orders_cache"].read_bytes() == ORDERS_CSV
    assert iso["src"].read_bytes() == EXPORT_CSV
    assert webapp.PRODUCTS[0]["current"]["price"] == "59,90"
    # the customers problem is SURFACED in the result, not raised; cache untouched
    assert "customers_error" in result
    assert result["customers_bytes"] == 0
    assert iso["customers_cache"].read_bytes() == b"OLD CUSTOMERS DATA"


def test_run_via_runner_customers_failure_stays_ok_with_error_surfaced(iso, monkeypatch):
    # Non-fatal: runner records last_status="ok" (critical refresh succeeded) and
    # surfaces the customers problem in last_result["customers_error"] — never a red
    # sync status for an auxiliary export nothing consumes yet.
    def boom():
        raise RuntimeError("stiahnutie exportu zákazníkov zlyhalo: ConnectionError (URL skrytá)")
    monkeypatch.setattr(webapp, "_fetch_customers_csv", boom)

    assert webapp.RUNNER._execute("shoptet_sync") is True
    (st,) = [x for x in webapp.RUNNER.status() if x["key"] == "shoptet_sync"]
    assert st["last_status"] == "ok"
    assert "customers_error" in st["last_result"]
    assert st["last_result"]["review_synced"] == 1     # critical refresh happened


# ── a REFUSED export download must not take the whole hourly sync down (PR #280
#    review, MUST FIX 2) — and must not observe the watermark from stale bytes ──
def test_a_refused_export_download_does_not_kill_the_rest_of_the_sync(iso, monkeypatch):
    """`_refuse_implausible_export_download` raises out of `_fetch_export_csv`, and
    `run_shoptet_sync` did not guard that call. Measured on dev before the fix:

        run_shoptet_sync RAISED: RuntimeError: stiahnutý export katalógu je
                                 nepravdepodobne malý (…)
        stages that ran        : ['orders']    # resync_current SKIPPED, customers SKIPPED
        watermark observed     : False

    So every review card's price/stock refresh and the customer export died every hour
    until the on-disk export aged past EXPORT_MAX_AGE_S — which is PRECISELY the
    staleness that used to disarm the supplier hold (MUST FIX 1). The two new gates
    created each other's blind spot.

    The refusal is a guard we raise ON PURPOSE, and only ever when the bytes already on
    disk are fresh AND plausible — i.e. exactly when carrying on with them is safe. So it
    degrades like the customer export does: surfaced in the result, never fatal."""
    iso["src"].write_bytes(EXPORT_CSV)          # the good bytes the guard is protecting

    def refuse():
        raise webapp.ExportDownloadRefused(
            "stiahnutý export katalógu je nepravdepodobne malý (10 B oproti 8585 B "
            "na disku, limit 4292 B) — vyzerá useknuto, nechávam na disku ten predošlý")
    monkeypatch.setattr(webapp, "_fetch_export_csv", refuse)

    result = webapp.run_shoptet_sync()          # does NOT raise

    # the refusal is surfaced, not swallowed
    assert "export_error" in result
    assert "nepravdepodobne malý" in result["export_error"]
    # the good bytes on disk are untouched — the whole point of the guard
    assert iso["src"].read_bytes() == EXPORT_CSV
    # …and EVERYTHING downstream still ran on them
    assert result["catalog_codes"] == 2                       # index rebuilt
    assert result["review_synced"] == 1                       # resync_current ran
    assert webapp.PRODUCTS[0]["current"]["price"] == "59,90"
    assert iso["orders_cache"].read_bytes() == ORDERS_CSV     # orders landed
    assert iso["customers_cache"].read_bytes() == CUSTOMERS_CSV   # customers still ran


def test_a_refused_export_download_does_not_observe_the_watermark(iso, monkeypatch):
    """The watermark may only be measured from FRESHLY DOWNLOADED bytes (playbook, #277).
    Re-observing the on-disk export after a refusal would let a stale export keep
    re-asserting the old size for ever, which is exactly what disables the ratio floor's
    time-based self-healing."""
    iso["src"].write_bytes(EXPORT_CSV)
    wm = iso["tmp"] / "export_watermark.json"
    monkeypatch.setattr(webapp, "EXPORT_WATERMARK", str(wm))

    def refuse():
        raise webapp.ExportDownloadRefused("stiahnutý export katalógu je nepravdepodobne malý")
    monkeypatch.setattr(webapp, "_fetch_export_csv", refuse)

    webapp.run_shoptet_sync()

    assert not wm.exists(), "the watermark was observed from bytes we did NOT just download"


def test_a_network_failure_on_the_export_stays_fatal(iso, monkeypatch):
    """Deliberately NOT widened to every fetch error. The refusal is self-inflicted and
    proves the on-disk copy is fresh + plausible; a network failure proves nothing about
    it, and a sync that quietly runs for a week on an old export while reporting OK is
    worse than a red row."""
    def boom():
        raise RuntimeError("stiahnutie katalógového exportu zlyhalo: ConnectionError "
                           "(URL skrytá — over SHOPTET_EXPORT_URL)")
    monkeypatch.setattr(webapp, "_fetch_export_csv", boom)

    with pytest.raises(RuntimeError, match="stiahnutie katalógového exportu zlyhalo"):
        webapp.run_shoptet_sync()


def test_run_via_runner_refused_export_stays_ok_with_error_surfaced(iso, monkeypatch):
    iso["src"].write_bytes(EXPORT_CSV)

    def refuse():
        raise webapp.ExportDownloadRefused("stiahnutý export katalógu je nepravdepodobne malý")
    monkeypatch.setattr(webapp, "_fetch_export_csv", refuse)

    assert webapp.RUNNER._execute("shoptet_sync") is True
    (st,) = [x for x in webapp.RUNNER.status() if x["key"] == "shoptet_sync"]
    assert st["last_status"] == "ok"
    assert "export_error" in st["last_result"]
    assert st["last_result"]["review_synced"] == 1     # critical refresh happened


def test_the_refusal_guard_raises_the_dedicated_type(iso, monkeypatch):
    """The non-fatal branch keys on the TYPE, so the guard must actually raise it —
    otherwise the catch above silently degrades to catching nothing."""
    iso["src"].write_bytes(EXPORT_CSV)
    now = __import__("time").time()
    os.utime(iso["src"], (now, now))

    with pytest.raises(webapp.ExportDownloadRefused):
        webapp._refuse_implausible_export_download(10)

    # still a RuntimeError, so every existing caller/except keeps working
    assert issubclass(webapp.ExportDownloadRefused, RuntimeError)
