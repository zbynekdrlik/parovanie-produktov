"""In-app „Veľkostné linky → eshop" automation (#192) — the nightly push of the
per-size SPLIT links (#174 „✂ Rozdeliť na veľkosti": a product whose supplier lists a
DIFFERENT product URL per size), grube/split variant URLs → the eshop `internalNote`
field per variant, the cron follow-up to the MVP manual zip, on the generic automation
runner (#93).

Hermetic: run_import (the careful Shoptet import subprocess) is monkeypatched — NO real
eshop write ever happens in a test. Every store path is redirected to tmp. Mirrors
test_webreview_grube_externalcode.py's isolation pattern; the automation reuses the SAME
upload core (_do_upload_variant_links) as the n8n endpoint AND the SAME row builder
(import_builder.link_rows) as the manual zip, so the logic lives in one place
(NEkopíruj logiku).
"""
import csv as _csv
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webreview"))
import app as webapp  # noqa: E402

from tests.conftest import authed_client  # noqa: E402


@pytest.fixture
def iso(tmp_path, monkeypatch):
    """Isolate every store the automation reads/writes + the import subprocess.

    PRODUCTS = two split products (a plain-supplier size run + a GRUBE knife) plus a
    good-decision product; DECISIONS gives the two their `split` status; CODE2PAIR maps
    each variant code to its pairCode."""
    monkeypatch.setattr(webapp.RUNNER, "state_path", str(tmp_path / "automations.json"))
    monkeypatch.setattr(webapp, "OUT", str(tmp_path))
    monkeypatch.setattr(webapp, "VARIANT_LINKS", str(tmp_path / "variant_links.json"))
    monkeypatch.setattr(webapp, "VARIANT_LINKS_STATE", str(tmp_path / "uploaded_variant_links.json"))
    monkeypatch.setattr(webapp, "DECISIONS", str(tmp_path / "decisions.json"))
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


def _ok_import():
    """A run_import stub that records every CSV it was handed (header + rows) and
    reports a clean success."""
    calls = []

    def fake_run(csv_path, dry_run=False, timeout=300):
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            rd = list(_csv.reader(f, delimiter=";"))
        calls.append({"header": rd[0], "rows": rd[1:], "dry_run": dry_run})
        return 0, "VÝSLEDOK: spracované=1 upravené=1 zlyhania=0", ""
    return fake_run, calls


def _recording_import(fail_on_call=None):
    """run_import stub recording each chunk CSV's rows; optionally FAIL the Nth call
    (1-based) to simulate a mid-batch chunk failure."""
    calls = []

    def fake_run(csv_path, dry_run=False, timeout=300):
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            rd = list(_csv.reader(f, delimiter=";"))
        rows = rd[1:]
        calls.append({"header": rd[0], "rows": rows})
        if fail_on_call is not None and len(calls) == fail_on_call:
            return 2, "POZOR: Shoptet hlási zlyhania", "boom"
        return 0, f"VÝSLEDOK: spracované={len(rows)} upravené={len(rows)} zlyhania=0", ""
    return fake_run, calls


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


# ── successful nightly push ─────────────────────────────────────────────────────
def test_run_pushes_split_links_and_records_counts(iso, monkeypatch):
    _seed({"60645/S": "https://trigona.sk/s", "60645/L": "https://trigona.sk/l"})
    fake_run, calls = _ok_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)

    result = webapp.run_split_links()

    assert result["status"] == "ok"
    v = result["variantlinks"]
    assert v["count"] == 2
    assert v["total_uploaded"] == 2
    assert v["total_codes"] == 2
    assert v["remaining"] == 0
    assert result["review_url"].startswith("https://")

    # the careful import ran with the internalNote (link) header, url in the value column
    (call,) = calls
    assert call["header"] == ["code", "pairCode", "internalNote"]
    assert ["60645/S", "395", "https://trigona.sk/s"] in call["rows"]
    assert ["60645/L", "395", "https://trigona.sk/l"] in call["rows"]
    assert call["dry_run"] is False               # a nightly write is NEVER a dry run

    # its OWN incremental state written (idempotency) — {code: url}
    st = json.loads((iso["tmp"] / "uploaded_variant_links.json").read_text())
    assert st == {"60645/S": "https://trigona.sk/s", "60645/L": "https://trigona.sk/l"}


def test_run_is_incremental_only_new_or_changed_url(iso, monkeypatch):
    _seed({"60645/S": "https://trigona.sk/s", "60645/L": "https://trigona.sk/l"})
    fake_run, calls = _ok_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)
    webapp.run_split_links()
    assert len(calls) == 1

    # second run, nothing changed → the careful import must NOT run again
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not re-import")))
    r2 = webapp.run_split_links()
    assert r2["status"] == "ok" and r2["variantlinks"]["count"] == 0

    # change ONE url + add a NEW variant link → only those two go up (not the unchanged)
    _seed({"60645/S": "https://trigona.sk/s", "60645/L": "https://trigona.sk/L-NEW",
           "60645/M": "https://trigona.sk/m"})
    fake_run2, calls2 = _ok_import()
    monkeypatch.setattr(webapp, "run_import", fake_run2)
    r3 = webapp.run_split_links()
    assert r3["variantlinks"]["count"] == 2
    (call,) = calls2
    pushed = {r[0]: r[2] for r in call["rows"]}
    assert pushed == {"60645/L": "https://trigona.sk/L-NEW",
                      "60645/M": "https://trigona.sk/m"}   # 60645/S (unchanged) skipped


def test_run_zero_new_reports_ok_without_importing(iso, monkeypatch):
    # no variant_links.json at all → clean no-op run (never touches the eshop)
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
    result = webapp.run_split_links()
    assert result["status"] == "ok"
    assert result["variantlinks"]["count"] == 0
    assert result["variantlinks"]["total_codes"] == 0


def test_non_http_url_never_pushed(iso, monkeypatch):
    # a non-http(s) / empty URL must never reach the eshop internalNote AND must not
    # count toward totals (never uploadable — fail-safe, matching /api/variant-link).
    _seed({"60645/S": "javascript:alert(1)", "60645/M": "", "60645/L": "https://ok.sk/l"})
    fake_run, calls = _ok_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)

    result = webapp.run_split_links()

    (call,) = calls
    assert [r[0] for r in call["rows"]] == ["60645/L"]   # ONLY the http one
    assert result["variantlinks"]["count"] == 1
    assert result["variantlinks"]["total_codes"] == 1    # non-http excluded from total
    st = json.loads((iso["tmp"] / "uploaded_variant_links.json").read_text())
    assert st == {"60645/L": "https://ok.sk/l"}


def test_non_split_variant_link_never_pushed(iso, monkeypatch):
    # a variant link stored for a product with a GOOD (not split) decision is NEVER
    # pushed by this automation — good/manual links go via „Párovania → eshop".
    _seed({"60645/S": "https://trigona.sk/s", "90000/M": "https://other.sk/m"},
          split_keys=("TRIGONA|395",), good={"OTHER|900": "https://other.sk/whole"})
    fake_run, calls = _ok_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)

    result = webapp.run_split_links()

    (call,) = calls
    assert [r[0] for r in call["rows"]] == ["60645/S"]   # 90000/M's product is good, skipped
    assert result["variantlinks"]["count"] == 1
    assert result["variantlinks"]["total_codes"] == 1
    st = json.loads((iso["tmp"] / "uploaded_variant_links.json").read_text())
    assert st == {"60645/S": "https://trigona.sk/s"}


def test_grube_split_url_normalized_to_de(iso, monkeypatch):
    # a GRUBE split product's per-size .sk URL is rebuilt to the canonical grube.de
    # detail URL by link_rows (the SAME normalization the manual zip applies) — proves
    # this automation reuses the row builder, not a copy.
    _seed({"70000/S": "https://www.grube.sk/p/grand-nord/154773/?q=a#itemId=1"},
          split_keys=("GRUBE|700",))
    fake_run, calls = _ok_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)

    webapp.run_split_links()

    (call,) = calls
    assert ["70000/S", "700", "https://www.grube.de/p/x/154773/"] in call["rows"]


# ── graceful degradation ────────────────────────────────────────────────────────
def test_import_failure_surfaces_failed_status_and_does_not_mark_uploaded(iso, monkeypatch):
    _seed({"60645/S": "https://trigona.sk/s"})
    monkeypatch.setattr(webapp, "run_import",
                        lambda p, dry_run=False, timeout=300: (1, "chyba", "boom"))
    result = webapp.run_split_links()
    assert result["status"] == "failed"
    assert result["variantlinks"]["ok"] is False
    # a failed import never records the code as uploaded → retried next run
    assert (not (iso["tmp"] / "uploaded_variant_links.json").exists()
            or json.loads((iso["tmp"] / "uploaded_variant_links.json").read_text()) == {})


# ── #156: a large batch is split into chunked imports ──────────────────────────
def test_large_batch_split_into_chunks(iso, monkeypatch):
    n = 650
    codes = [f"{i}/M" for i in range(n)]
    monkeypatch.setattr(webapp, "PRODUCTS",
                        [{"key": "BIG|1", "supplier": "TRIGONA", "variant_codes": codes}])
    monkeypatch.setattr(webapp, "CODE2PAIR", {c: "P" for c in codes})
    _seed({c: f"https://x.sk/{c}" for c in codes}, split_keys=("BIG|1",))
    fake_run, calls = _recording_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)

    result = webapp.run_split_links()

    assert len(calls) >= 2                                   # split, not one giant import
    assert max(len(c["rows"]) for c in calls) <= webapp.IMPORT_CHUNK_ROWS
    imported = [r[0] for c in calls for r in c["rows"]]
    assert sorted(imported) == sorted(codes)                # each code exactly once
    assert result["status"] == "ok"
    assert result["variantlinks"]["ok"] is True and result["variantlinks"]["count"] == n


def test_mid_batch_chunk_failure_records_partial_and_releases_lock(iso, monkeypatch):
    # #156: a chunk failing mid-batch → failed status, record ONLY the codes from the
    # SUCCESSFUL chunk(s) (resumable — never all-or-nothing silent success), and release
    # the import lock (no stuck lock → no cascade failure).
    n = 650
    codes = [f"{i}/M" for i in range(n)]
    monkeypatch.setattr(webapp, "PRODUCTS",
                        [{"key": "BIG|1", "supplier": "TRIGONA", "variant_codes": codes}])
    monkeypatch.setattr(webapp, "CODE2PAIR", {c: "P" for c in codes})
    _seed({c: f"https://x.sk/{c}" for c in codes}, split_keys=("BIG|1",))
    fake_run, calls = _recording_import(fail_on_call=2)     # 1st chunk ok, 2nd fails
    monkeypatch.setattr(webapp, "run_import", fake_run)

    result = webapp.run_split_links()

    assert result["status"] == "failed"
    assert result["variantlinks"]["ok"] is False
    assert "časti 2/" in result["variantlinks"]["error"]
    assert "z 650 riadkov" in result["variantlinks"]["error"]
    assert len(calls) == 2                                  # batch STOPS after the failing chunk
    uploaded = json.loads((iso["tmp"] / "uploaded_variant_links.json").read_text())
    chunk1_codes = {r[0] for r in calls[0]["rows"]}
    chunk2_codes = {r[0] for r in calls[1]["rows"]}
    assert set(uploaded) == chunk1_codes
    assert not (set(uploaded) & chunk2_codes)
    assert 0 < len(uploaded) < n
    # the import lock was released despite the failure (else the next import 409s)
    assert webapp._import_lock.acquire(blocking=False)
    webapp._import_lock.release()


def test_run_via_runner_records_error_when_import_raises(iso, monkeypatch):
    _seed({"60645/S": "https://trigona.sk/s"})

    def boom(*a, **k):
        raise RuntimeError("shoptet_import.py spadol")
    monkeypatch.setattr(webapp, "run_import", boom)

    assert webapp.RUNNER._execute("split_links") is True    # runner survives
    (st,) = [x for x in webapp.RUNNER.status() if x["key"] == "split_links"]
    assert st["last_status"] == "error"
    assert "shoptet_import.py spadol" in st["last_error"]
    assert st["running"] is False


# ── disabled automation never runs on a scheduler tick ──────────────────────────
def test_disabled_automation_is_not_ticked(iso, monkeypatch):
    _seed({"60645/S": "https://trigona.sk/s"})
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("disabled must not run")))
    webapp.RUNNER.tick_once()                    # default state = disabled
    (st,) = [x for x in webapp.RUNNER.status() if x["key"] == "split_links"]
    assert st["enabled"] is False
    assert st["last_run"] == ""                  # never ran


# ── http run endpoint + runner integration ──────────────────────────────────────
def test_run_now_via_http_endpoint_and_runner(iso, monkeypatch):
    _seed({"60645/S": "https://trigona.sk/s"})
    fake_run, _calls = _ok_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)
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
def test_run_reads_but_never_writes_variant_links_store(iso, monkeypatch):
    _seed({"60645/S": "https://trigona.sk/s"})
    vl_before = (iso["tmp"] / "variant_links.json").read_text()
    fake_run, _calls = _ok_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)

    webapp.run_split_links()

    assert (iso["tmp"] / "variant_links.json").read_text() == vl_before


# ── the n8n Bearer endpoint delegates to the same core ──────────────────────────
def test_n8n_endpoint_requires_bearer_token(iso, monkeypatch):
    monkeypatch.setattr(webapp, "_import_token", lambda: "SEKRET")
    c = authed_client()
    assert c.post("/api/n8n/upload-variant-links").status_code == 401           # no token
    assert c.post("/api/n8n/upload-variant-links",
                  headers={"Authorization": "Bearer WRONG"}).status_code == 401


def test_n8n_endpoint_dry_run_reaches_import_without_recording(iso, monkeypatch):
    _seed({"60645/S": "https://trigona.sk/s"})
    monkeypatch.setattr(webapp, "_import_token", lambda: "SEKRET")
    fake_run, calls = _ok_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)
    c = authed_client()
    r = c.post("/api/n8n/upload-variant-links?dry_run=1",
               headers={"Authorization": "Bearer SEKRET"})
    assert r.status_code == 200
    assert r.get_json()["dry_run"] is True
    assert calls and calls[0]["dry_run"] is True
    # dry run records NOTHING (so the real nightly run still pushes it)
    assert not (iso["tmp"] / "uploaded_variant_links.json").exists()
