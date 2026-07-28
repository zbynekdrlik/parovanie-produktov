"""In-app „Veľkostné linky → eshop" automation (#192) — the nightly push of the
per-size SPLIT links (#174 „✂ Rozdeliť na veľkosti": a product whose supplier lists a
DIFFERENT product URL per size), grube/split variant URLs → the eshop `internalNote`
field per variant, the cron follow-up to the MVP manual zip, on the generic automation
runner (#93).

#299 Task 8 rewrite: mirrors `test_webreview_grube_externalcode.py`'s rewrite — since
the migration this automation no longer imports directly, `_do_upload_variant_links`
(driven here via `run_split_links`) only QUEUES rows into the shared pending_shoptet
table for the next hourly „Sync do Shoptetu" drain
(`tests/test_webreview_shoptet_upload.py` covers that drain + the credit path). This
file keeps the registration/endpoint/runner-integration/row-builder tests, adapted to
assert against the pending table and against `queued`/`count` instead of a completed
import.

Hermetic: every store path (incl. the shared pending_shoptet table) is redirected to
tmp. The automation reuses the SAME upload core (_do_upload_variant_links) as the n8n
endpoint AND the SAME row builder (import_builder.link_rows) as the manual zip, so the
logic lives in one place (NEkopíruj logiku).
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webreview"))
import app as webapp  # noqa: E402

from tests.conftest import authed_client  # noqa: E402


@pytest.fixture
def iso(tmp_path, monkeypatch):
    """Isolate every store the automation reads/writes, incl. the shared
    pending_shoptet table the queue drops rows into (#299 Task 8).

    PRODUCTS = two split products (a plain-supplier size run + a GRUBE knife) plus a
    good-decision product; DECISIONS gives the two their `split` status; CODE2PAIR maps
    each variant code to its pairCode."""
    monkeypatch.setattr(webapp.RUNNER, "state_path", str(tmp_path / "automations.json"))
    monkeypatch.setattr(webapp, "OUT", str(tmp_path))
    monkeypatch.setattr(webapp, "VARIANT_LINKS", str(tmp_path / "variant_links.json"))
    monkeypatch.setattr(webapp, "VARIANT_LINKS_STATE", str(tmp_path / "uploaded_variant_links.json"))
    monkeypatch.setattr(webapp, "DECISIONS", str(tmp_path / "decisions.json"))
    monkeypatch.setattr(webapp, "PENDING_SHOPTET", str(tmp_path / "pending_shoptet.json"))
    monkeypatch.setattr(webapp, "PRODUCTS", [
        {"key": "TRIGONA|395", "supplier": "TRIGONA",
         "variant_codes": ["60645/S", "60645/M", "60645/L"]},
        {"key": "GRUBE|700", "supplier": "GRUBE",
         "variant_codes": ["70000/S", "70000/L"]},
        {"key": "OTHER|900", "supplier": "OTHER", "variant_codes": ["90000/M"]},
    ])
    monkeypatch.setattr(webapp, "CODE2PAIR", {
        "60645/S": "395", "60645/M": "395", "60645/L": "395",
        "70000/S": "700", "70000/L": "700", "90000/M": "900"})
    return {"tmp": tmp_path}


def _seed(vlinks, split_keys=("TRIGONA|395",), good=None):
    """vlinks = {variant_code: url} (variant_links.json); split_keys = product keys with
    a `split` decision; good = {key: url} good/manual decisions (must be IGNORED here)."""
    with open(webapp.VARIANT_LINKS, "w", encoding="utf-8") as f:
        json.dump(vlinks, f)
    dec = {k: {"status": "split", "url": ""} for k in split_keys}
    for k, u in (good or {}).items():
        dec[k] = {"status": "good", "url": u}
    with open(webapp.DECISIONS, "w", encoding="utf-8") as f:
        json.dump(dec, f)


# ── registration + status ──────────────────────────────────────────────────────
def test_split_links_registered_disabled_daily_0345(iso):
    c = authed_client()
    j = c.get("/api/automations").get_json()
    (a,) = [x for x in j["automations"] if x["key"] == "split_links"]
    assert a["name"] == "Veľkostné linky → eshop"
    # SAFETY: this automation WRITES to the live eshop → deploy starts stopped (#93)
    assert a["enabled"] is False
    assert a["schedule"] == "denne o 03:45"
    assert a["running"] is False
    assert a["description"]                       # #173 plain-language description present


# ── successful nightly queueing ─────────────────────────────────────────────────
def test_run_queues_split_links_and_records_counts(iso, monkeypatch):
    """#299 Task 8: the automation no longer imports — it queues into the shared
    pending table. Kills a regression that re-introduces a direct import call."""
    monkeypatch.setattr(webapp, "_import_rows_chunked",
                        lambda *a, **k: pytest.fail("must not import — must queue"))
    _seed({"60645/S": "https://trigona.sk/s", "60645/L": "https://trigona.sk/l"})

    result = webapp.run_split_links()

    assert result["status"] == "ok"
    v = result["variantlinks"]
    assert v["count"] == 2
    assert v["total_codes"] == 2
    # nothing is CREDITED yet — that only happens once the hourly drain's OWN
    # import confirms the row (the #257 class of bug)
    assert v["total_uploaded"] == 0
    assert v["remaining"] == 2
    assert result["review_url"].startswith("https://")

    d = webapp._load_pending()
    assert d["60645/S"]["fields"]["internalNote"]["value"] == "https://trigona.sk/s"
    assert d["60645/S"]["fields"]["internalNote"]["source"] == "split_links"
    assert d["60645/L"]["fields"]["internalNote"]["value"] == "https://trigona.sk/l"
    # #299 review I2/C1 — field["credit"]["value"] was never asserted anywhere in
    # the whole suite; that gap is exactly how C1 (the credit carrying the
    # NORMALIZED cell value instead of the RAW variant_links.json value) shipped
    # unnoticed. TRIGONA doesn't normalize, so raw == cell value here — the
    # GRUBE case where they DIFFER is `test_grube_split_link_credit_is_RAW_not_
    # normalized_and_is_not_requeued_once_credited` below.
    assert d["60645/S"]["fields"]["internalNote"]["credit"]["value"] == "https://trigona.sk/s"
    assert d["60645/L"]["fields"]["internalNote"]["credit"]["value"] == "https://trigona.sk/l"

    # the producer itself never writes its own "uploaded" state
    assert not (iso["tmp"] / "uploaded_variant_links.json").exists()


def test_run_requeues_the_same_link_until_it_is_actually_credited(iso, monkeypatch):
    """Mirrors the GRUBE externalcode producer's equivalent test: without a credited
    entry in uploaded_variant_links.json (only the hourly drain's `_credit_producer`
    writes it), the SAME link is a "new" candidate on every call."""
    monkeypatch.setattr(webapp, "_import_rows_chunked",
                        lambda *a, **k: pytest.fail("must not import — must queue"))
    _seed({"60645/S": "https://trigona.sk/s", "60645/L": "https://trigona.sk/l"})

    r1 = webapp.run_split_links()
    assert r1["variantlinks"]["count"] == 2
    r2 = webapp.run_split_links()
    assert r2["variantlinks"]["count"] == 2          # re-queued again, not skipped

    # simulate what the hourly drain's `_credit_producer` does once Shoptet confirms
    with open(webapp.VARIANT_LINKS_STATE, "w", encoding="utf-8") as f:
        json.dump({"60645/S": "https://trigona.sk/s",
                   "60645/L": "https://trigona.sk/l"}, f)

    r3 = webapp.run_split_links()
    assert r3["variantlinks"]["count"] == 0           # now genuinely unchanged → skipped

    # change ONE url + add a NEW variant link → only those two are candidates
    _seed({"60645/S": "https://trigona.sk/s", "60645/L": "https://trigona.sk/L-NEW",
           "60645/M": "https://trigona.sk/m"})
    r4 = webapp.run_split_links()
    assert r4["variantlinks"]["count"] == 2
    d = webapp._load_pending()
    assert d["60645/L"]["fields"]["internalNote"]["value"] == "https://trigona.sk/L-NEW"
    assert d["60645/M"]["fields"]["internalNote"]["value"] == "https://trigona.sk/m"
    # 60645/S unchanged this round — still whatever r1/r2 queued
    assert d["60645/S"]["fields"]["internalNote"]["value"] == "https://trigona.sk/s"


def test_run_split_links_grube_requeue_compares_the_raw_stored_url_not_the_normalized_cell(
        iso, monkeypatch):
    """#299 Task 9 review finding (carried over from Task 8) — this test's PREVIOUS
    name (`test_grube_split_link_credit_is_RAW_not_normalized_and_is_not_requeued_
    once_credited`) claimed to pin that the QUEUED CREDIT VALUE is raw, but it never
    once inspects `field["credit"]`, so a regression that credited the normalized
    value would NOT turn this test red — it only manually seeds `VARIANT_LINKS_STATE`
    (simulating what a write of either shape would leave behind) and checks whether
    `run_split_links()` still treats the link as a candidate. That assertion — the
    QUEUED credit is genuinely the raw value — is pinned separately, at the
    `_do_upload_variant_links` unit level, by
    `test_split_links_grube_credit_value_is_RAW_not_normalized` in
    test_webreview_shoptet_upload.py. What THIS test actually is, and remains, the
    ONLY exerciser of `run_split_links()` on a GRUBE (normalizing) link at all:
    `new_variant_link_keys`'s incremental-requeue COMPARISON must key off the RAW
    variant_links.json URL, never the normalized `.de` cell `link_rows` builds for
    Shoptet — proven by (1) crediting the NORMALIZED `.de` url (what the historical
    C1 bug wrote) does NOT stop the requeue — the same link keeps coming back
    forever, exactly the original review's own probe (`SECOND RUN count: 1`,
    `total_uploaded: 0` — never confirmed); (2) crediting the RAW url (what a
    correct write contains) makes the link genuinely skipped on the next run."""
    monkeypatch.setattr(webapp, "_import_rows_chunked",
                        lambda *a, **k: pytest.fail("must not import — must queue"))
    raw = "https://www.grube.sk/p/grand-nord/154773/?q=a#itemId=1"
    normalized = "https://www.grube.de/p/x/154773/"
    _seed({"70000/S": raw}, split_keys=("GRUBE|700",))

    r1 = webapp.run_split_links()
    assert r1["variantlinks"]["count"] == 1
    d = webapp._load_pending()
    assert d["70000/S"]["fields"]["internalNote"]["value"] == normalized

    # credit the NORMALIZED value (what the C1 bug wrote) → must still requeue
    with open(webapp.VARIANT_LINKS_STATE, "w", encoding="utf-8") as f:
        json.dump({"70000/S": normalized}, f)
    r2 = webapp.run_split_links()
    assert r2["variantlinks"]["count"] == 1, (
        "crediting the normalized value must NOT stop the requeue — "
        "new_variant_link_keys compares the RAW store, never the normalized one")

    # credit the RAW value (what a correct write contains) → now genuinely skipped
    with open(webapp.VARIANT_LINKS_STATE, "w", encoding="utf-8") as f:
        json.dump({"70000/S": raw}, f)
    r3 = webapp.run_split_links()
    assert r3["variantlinks"]["count"] == 0


def test_run_zero_new_reports_ok_without_queuing(iso, monkeypatch):
    # no variant_links.json at all → clean no-op run (never touches the shared table)
    monkeypatch.setattr(webapp, "queue_shoptet_fields",
                        lambda *a, **k: pytest.fail("must not queue"))
    result = webapp.run_split_links()
    assert result["status"] == "ok"
    assert result["variantlinks"]["count"] == 0
    assert result["variantlinks"]["total_codes"] == 0
    assert not (iso["tmp"] / "pending_shoptet.json").exists()


def test_non_http_url_never_queued(iso, monkeypatch):
    # a non-http(s) / empty URL must never reach the eshop internalNote AND must not
    # count toward totals (never uploadable — fail-safe, matching /api/variant-link).
    _seed({"60645/S": "javascript:alert(1)", "60645/M": "", "60645/L": "https://ok.sk/l"})

    result = webapp.run_split_links()

    assert result["variantlinks"]["count"] == 1
    assert result["variantlinks"]["total_codes"] == 1    # non-http excluded from total
    d = webapp._load_pending()
    assert list(d) == ["60645/L"]                          # ONLY the http one queued
    assert d["60645/L"]["fields"]["internalNote"]["value"] == "https://ok.sk/l"


def test_non_split_variant_link_never_queued(iso, monkeypatch):
    # a variant link stored for a product with a GOOD (not split) decision is NEVER
    # queued by this automation — good/manual links go via „Párovania → eshop".
    _seed({"60645/S": "https://trigona.sk/s", "90000/M": "https://other.sk/m"},
          split_keys=("TRIGONA|395",), good={"OTHER|900": "https://other.sk/whole"})

    result = webapp.run_split_links()

    assert result["variantlinks"]["count"] == 1
    assert result["variantlinks"]["total_codes"] == 1
    d = webapp._load_pending()
    assert list(d) == ["60645/S"]                          # 90000/M's product is good, skipped
    assert d["60645/S"]["fields"]["internalNote"]["value"] == "https://trigona.sk/s"


def test_grube_split_url_normalized_to_de(iso):
    # a GRUBE split product's per-size .sk URL is rebuilt to the canonical grube.de
    # detail URL by link_rows (the SAME normalization the manual zip applies) — proves
    # this automation reuses the row builder, not a copy.
    _seed({"70000/S": "https://www.grube.sk/p/grand-nord/154773/?q=a#itemId=1"},
          split_keys=("GRUBE|700",))

    webapp.run_split_links()

    d = webapp._load_pending()
    assert d["70000/S"]["fields"]["internalNote"]["value"] \
        == "https://www.grube.de/p/x/154773/"


# ── graceful degradation ────────────────────────────────────────────────────────
def test_a_corrupt_pending_table_makes_the_runner_record_error_not_crash(iso):
    """#299 Task 8: queueing can no longer "fail" via a returned dict — the ONE way
    it can fail now is `queue_shoptet_fields` refusing to write on top of an
    unreadable pending table (`StoreWipeRefused`). The runner must still survive
    that (records last_status='error'), same contract as an import raising used to
    have. #299 Task 9 review finding — `last_error` is the ONLY place the manager
    learns WHY a run failed; a test that stops at last_status='error' would pass
    even if `last_error` were silently left empty."""
    _seed({"60645/S": "https://trigona.sk/s"})
    with open(webapp.PENDING_SHOPTET, "w", encoding="utf-8") as f:
        f.write("{ this is not json")

    assert webapp.RUNNER._execute("split_links") is True    # runner survives
    (st,) = [x for x in webapp.RUNNER.status() if x["key"] == "split_links"]
    assert st["last_status"] == "error"
    assert st["running"] is False
    assert st["last_error"]                        # non-empty
    assert "poškoden" in st["last_error"].lower() or "StoreWipeRefused" in st["last_error"]


# ── disabled automation never runs on a scheduler tick ──────────────────────────
def test_disabled_automation_is_not_ticked(iso, monkeypatch):
    _seed({"60645/S": "https://trigona.sk/s"})
    monkeypatch.setattr(webapp, "queue_shoptet_fields",
                        lambda *a, **k: pytest.fail("disabled must not run"))
    webapp.RUNNER.tick_once()                    # default state = disabled
    (st,) = [x for x in webapp.RUNNER.status() if x["key"] == "split_links"]
    assert st["enabled"] is False
    assert st["last_run"] == ""                  # never ran


# ── http run endpoint + runner integration ──────────────────────────────────────
def test_run_now_via_http_endpoint_and_runner(iso):
    _seed({"60645/S": "https://trigona.sk/s"})
    c = authed_client()
    r = c.post("/api/automations/split_links/run")
    assert r.status_code == 200 and r.get_json()["started"] is True
    webapp.RUNNER._threads["split_links"].join(timeout=15)
    (st,) = [x for x in webapp.RUNNER.status() if x["key"] == "split_links"]
    assert st["last_status"] == "ok"
    assert st["last_result"]["status"] == "ok"
    assert st["last_result"]["variantlinks"]["count"] == 1
    assert st["enabled"] is False                # run-now must not enable the schedule


# ── never modifies the durable variant_links store (reads only) ─────────────────
def test_run_reads_but_never_writes_variant_links_store(iso):
    _seed({"60645/S": "https://trigona.sk/s"})
    vl_before = (iso["tmp"] / "variant_links.json").read_text()

    webapp.run_split_links()

    assert (iso["tmp"] / "variant_links.json").read_text() == vl_before


# ── the n8n Bearer endpoint delegates to the same core ──────────────────────────
def test_n8n_endpoint_requires_bearer_token(iso, monkeypatch):
    monkeypatch.setattr(webapp, "_import_token", lambda: "SEKRET")
    c = authed_client()
    assert c.post("/api/n8n/upload-variant-links").status_code == 401           # no token
    assert c.post("/api/n8n/upload-variant-links",
                  headers={"Authorization": "Bearer WRONG"}).status_code == 401


def test_n8n_endpoint_dry_run_queues_nothing(iso, monkeypatch):
    """#299 review m3 — dry_run used to reach a real Shoptet dry-run import; the
    Task 8 rewrite made it an early-return no-op always reporting `queued: 0`
    regardless of how many links were actually candidates — a silent contract
    change. `would_queue` restores a genuine preview without any live write."""
    _seed({"60645/S": "https://trigona.sk/s"})
    monkeypatch.setattr(webapp, "_import_token", lambda: "SEKRET")
    c = authed_client()
    r = c.post("/api/n8n/upload-variant-links?dry_run=1",
               headers={"Authorization": "Bearer SEKRET"})
    assert r.status_code == 200
    j = r.get_json()
    assert j["dry_run"] is True
    assert j["queued"] == 0
    assert j["would_queue"] == 1        # the honest preview of what WOULD be queued
    # dry run queues NOTHING (so the real nightly run still pushes it)
    assert not (iso["tmp"] / "pending_shoptet.json").exists()
    assert not (iso["tmp"] / "uploaded_variant_links.json").exists()
