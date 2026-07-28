"""In-app „GRUBE kódy → eshop" automation (#62) — the nightly push of the GRUBE
per-size externalCodes (grube itemId → the eshop `externalCode` field), the cron
follow-up to the MVP manual zip, on the generic automation runner (#93).

#299 Task 8 rewrite: since the migration this automation no longer imports directly
— `_do_upload_externalcodes` (the SAME core function this file drives, via
`run_grube_externalcode`) only QUEUES rows into the shared pending_shoptet table for
the next hourly „Sync do Shoptetu" drain (`tests/test_webreview_shoptet_upload.py`
covers that drain + the credit path). This file therefore no longer monkeypatches
`run_import` for the success path — there is no import left to intercept at this
layer — but keeps the registration/endpoint/runner-integration tests, adapted to
assert against the pending table and against `queued`/`count` instead of a
completed import.

Hermetic: every store path (incl. the shared pending_shoptet table) is redirected to
tmp — no real eshop write, and no accidental write to the shared table another test
might read.
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
    pending_shoptet table the queue drops rows into (#299 Task 8)."""
    monkeypatch.setattr(webapp.RUNNER, "state_path", str(tmp_path / "automations.json"))
    monkeypatch.setattr(webapp, "OUT", str(tmp_path))
    monkeypatch.setattr(webapp, "GRUBE_CODES", str(tmp_path / "grube_codes.json"))
    monkeypatch.setattr(webapp, "EXTERNALCODES_STATE", str(tmp_path / "uploaded_externalcodes.json"))
    monkeypatch.setattr(webapp, "PENDING_SHOPTET", str(tmp_path / "pending_shoptet.json"))
    monkeypatch.setattr(webapp, "CODE2PAIR", {"60645/L": "395", "60645/S": "395", "70000/M": "700"})
    return {"tmp": tmp_path}


def _seed_grube(codes):
    """codes = {code: itemId} → the grube_codes.json store shape {code: {itemId, ...}}."""
    with open(webapp.GRUBE_CODES, "w", encoding="utf-8") as f:
        json.dump({c: {"itemId": iid, "size": "L", "deUrl": "https://grube.de/x",
                       "productId": "3959"} for c, iid in codes.items()}, f)


# ── registration + status ──────────────────────────────────────────────────────
def test_grube_externalcode_registered_disabled_daily_0330(iso):
    c = authed_client()
    j = c.get("/api/automations").get_json()
    (a,) = [x for x in j["automations"] if x["key"] == "grube_externalcode"]
    assert a["name"] == "GRUBE kódy → eshop"
    # SAFETY: this automation WRITES to the live eshop → deploy starts stopped (#93 contract)
    assert a["enabled"] is False
    assert a["schedule"] == "denne o 03:30"
    assert a["running"] is False
    assert a["description"]                       # #173 plain-language description present


# ── successful nightly queueing ─────────────────────────────────────────────────
def test_run_queues_externalcodes_and_records_counts(iso, monkeypatch):
    """#299 Task 8: the automation no longer imports — it queues into the shared
    pending table. Kills a regression that re-introduces a direct import call."""
    monkeypatch.setattr(webapp, "_import_rows_chunked",
                        lambda *a, **k: pytest.fail("must not import — must queue"))
    _seed_grube({"60645/L": "1547734519", "60645/S": "1547734523"})

    result = webapp.run_grube_externalcode()

    assert result["status"] == "ok"
    e = result["externalcodes"]
    assert e["count"] == 2                         # 2 field values queued
    assert e["total_codes"] == 2
    # nothing is CREDITED yet — that only happens once the hourly drain's OWN
    # import confirms the row (the #257 class of bug: an "uploaded" mark written
    # on our own say-so, before Shoptet confirmed anything)
    assert e["total_uploaded"] == 0
    assert e["remaining"] == 2
    assert result["review_url"].startswith("https://")

    d = webapp._load_pending()
    assert d["60645/L"]["fields"]["externalCode"]["value"] == "1547734519"
    assert d["60645/L"]["fields"]["externalCode"]["source"] == "grube_externalcode"
    assert d["60645/S"]["fields"]["externalCode"]["value"] == "1547734523"
    # #299 review I2 — field["credit"]["value"] was never asserted anywhere in the
    # whole suite (the exact gap C1 slipped through in the sibling split_links
    # producer). Here it's the same raw itemId as the queued value (no
    # normalization happens for externalCode), but it must still be pinned.
    assert d["60645/L"]["fields"]["externalCode"]["credit"]["value"] == "1547734519"
    assert d["60645/S"]["fields"]["externalCode"]["credit"]["value"] == "1547734523"

    # the producer itself never writes its own "uploaded" state (decision carried
    # over from Task 6/7, not in this task's brief — see automation-health.md)
    assert not (iso["tmp"] / "uploaded_externalcodes.json").exists()


def test_run_requeues_the_same_code_until_it_is_actually_credited(iso, monkeypatch):
    """Without a credited entry in uploaded_externalcodes.json (only the hourly
    drain's `_credit_producer` writes it, after Shoptet confirms), the SAME code is
    a "new" candidate on every call — harmless (queueing just overwrites the same
    pending field), but proves the incremental check now keys off the credited
    store, not off anything this function writes itself."""
    monkeypatch.setattr(webapp, "_import_rows_chunked",
                        lambda *a, **k: pytest.fail("must not import — must queue"))
    _seed_grube({"60645/L": "111"})

    r1 = webapp.run_grube_externalcode()
    assert r1["externalcodes"]["count"] == 1
    r2 = webapp.run_grube_externalcode()
    assert r2["externalcodes"]["count"] == 1        # re-queued again, not skipped

    # simulate what the hourly drain's `_credit_producer` does once Shoptet confirms
    with open(webapp.EXTERNALCODES_STATE, "w", encoding="utf-8") as f:
        json.dump({"60645/L": "111"}, f)

    r3 = webapp.run_grube_externalcode()
    assert r3["externalcodes"]["count"] == 0         # now genuinely unchanged → skipped

    # change the itemId + add a new code → only those two are candidates
    _seed_grube({"60645/L": "111", "60645/S": "999", "70000/M": "333"})
    r4 = webapp.run_grube_externalcode()
    assert r4["externalcodes"]["count"] == 2          # only the 2 CHANGED/new codes
    d = webapp._load_pending()
    assert d["60645/S"]["fields"]["externalCode"]["value"] == "999"
    assert d["70000/M"]["fields"]["externalCode"]["value"] == "333"
    # 60645/L's queued field is untouched by this run (still the value r1/r2 queued —
    # queueing never removes a pending entry; only the drain's settle() does that)
    assert d["60645/L"]["fields"]["externalCode"]["value"] == "111"


def test_run_zero_new_reports_ok_without_queuing(iso, monkeypatch):
    # no grube_codes.json at all → clean no-op run (never touches the shared table)
    monkeypatch.setattr(webapp, "queue_shoptet_fields",
                        lambda *a, **k: pytest.fail("must not queue"))
    result = webapp.run_grube_externalcode()
    assert result["status"] == "ok"
    assert result["externalcodes"]["count"] == 0
    assert result["externalcodes"]["total_codes"] == 0
    assert not (iso["tmp"] / "pending_shoptet.json").exists()


def test_nonnumeric_itemid_never_queued(iso, monkeypatch):
    # a non-numeric / empty itemId is junk (possible formula-injection lead) — it must
    # never reach the eshop AND must not count toward totals (never uploadable).
    _seed_grube({"60645/L": "=EVIL", "60645/S": "", "70000/M": "42"})

    result = webapp.run_grube_externalcode()

    assert result["externalcodes"]["count"] == 1
    assert result["externalcodes"]["total_codes"] == 1   # non-numeric excluded from total
    d = webapp._load_pending()
    assert list(d) == ["70000/M"]                          # ONLY the numeric one queued
    assert d["70000/M"]["fields"]["externalCode"]["value"] == "42"


# ── graceful degradation ────────────────────────────────────────────────────────
def test_a_corrupt_pending_table_makes_the_runner_record_error_not_crash(iso):
    """#299 Task 8: queueing can no longer "fail" via a returned dict (there is no
    import to fail) — the ONE way it can fail now is `queue_shoptet_fields` refusing
    to write on top of an unreadable pending table (`StoreWipeRefused`). The runner
    must still survive that (records last_status='error'), same contract as an
    import raising used to have."""
    _seed_grube({"60645/L": "111"})
    with open(webapp.PENDING_SHOPTET, "w", encoding="utf-8") as f:
        f.write("{ this is not json")

    assert webapp.RUNNER._execute("grube_externalcode") is True    # runner survives
    (st,) = [x for x in webapp.RUNNER.status() if x["key"] == "grube_externalcode"]
    assert st["last_status"] == "error"
    assert st["running"] is False


# ── disabled automation never runs on a scheduler tick ──────────────────────────
def test_disabled_automation_is_not_ticked(iso, monkeypatch):
    _seed_grube({"60645/L": "111"})
    monkeypatch.setattr(webapp, "queue_shoptet_fields",
                        lambda *a, **k: pytest.fail("disabled must not run"))
    webapp.RUNNER.tick_once()                    # default state = disabled
    (st,) = [x for x in webapp.RUNNER.status() if x["key"] == "grube_externalcode"]
    assert st["enabled"] is False
    assert st["last_run"] == ""                  # never ran


# ── http run endpoint + runner integration ──────────────────────────────────────
def test_run_now_via_http_endpoint_and_runner(iso):
    _seed_grube({"60645/L": "111"})
    c = authed_client()
    r = c.post("/api/automations/grube_externalcode/run")
    assert r.status_code == 200 and r.get_json()["started"] is True
    webapp.RUNNER._threads["grube_externalcode"].join(timeout=15)
    (st,) = [x for x in webapp.RUNNER.status() if x["key"] == "grube_externalcode"]
    assert st["last_status"] == "ok"
    assert st["last_result"]["status"] == "ok"
    assert st["last_result"]["externalcodes"]["count"] == 1
    assert st["enabled"] is False                # run-now must not enable the schedule


# ── never modifies the durable grube_codes store (reads only) ───────────────────
def test_run_reads_but_never_writes_grube_codes_store(iso):
    _seed_grube({"60645/L": "111"})
    gc_before = (iso["tmp"] / "grube_codes.json").read_text()

    webapp.run_grube_externalcode()

    assert (iso["tmp"] / "grube_codes.json").read_text() == gc_before


# ── the n8n Bearer endpoint delegates to the same core ──────────────────────────
def test_n8n_endpoint_requires_bearer_token(iso, monkeypatch):
    monkeypatch.setattr(webapp, "_import_token", lambda: "SEKRET")
    c = authed_client()
    assert c.post("/api/n8n/upload-externalcode").status_code == 401           # no token
    assert c.post("/api/n8n/upload-externalcode",
                  headers={"Authorization": "Bearer WRONG"}).status_code == 401


def test_n8n_endpoint_dry_run_queues_nothing(iso, monkeypatch):
    """#299 review m3 — dry_run used to reach a real Shoptet dry-run import
    (`test_n8n_endpoint_dry_run_reaches_import_without_recording`, pre-migration);
    the Task 8 rewrite made it an early-return no-op that ALWAYS said `queued: 0`
    regardless of how many codes were actually candidates — a silent contract
    change nobody could tell from the response. `would_queue` restores that
    meaning (a genuine preview) without reintroducing any live write."""
    _seed_grube({"60645/L": "111"})
    monkeypatch.setattr(webapp, "_import_token", lambda: "SEKRET")
    c = authed_client()
    r = c.post("/api/n8n/upload-externalcode?dry_run=1",
               headers={"Authorization": "Bearer SEKRET"})
    assert r.status_code == 200
    j = r.get_json()
    assert j["dry_run"] is True
    assert j["queued"] == 0
    assert j["would_queue"] == 1        # the honest preview of what WOULD be queued
    # dry run queues NOTHING (so the real nightly run still pushes it)
    assert not (iso["tmp"] / "pending_shoptet.json").exists()
    assert not (iso["tmp"] / "uploaded_externalcodes.json").exists()
