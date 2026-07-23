"""In-app „GRUBE kódy → eshop" automation (#62) — the nightly push of the GRUBE
per-size externalCodes (grube itemId → the eshop `externalCode` field), the cron
follow-up to the MVP manual zip, on the generic automation runner (#93).

Hermetic: run_import (the careful Shoptet import subprocess) is monkeypatched —
NO real eshop write ever happens in a test. Every store path is redirected to
tmp. Mirrors test_webreview_parovania_eshop.py's isolation pattern; the automation
reuses the SAME upload core (_do_upload_externalcodes) as the n8n endpoint, so the
logic lives in one place (NEkopíruj logiku).
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
    """Isolate every store the automation reads/writes + the import subprocess."""
    monkeypatch.setattr(webapp.RUNNER, "state_path", str(tmp_path / "automations.json"))
    monkeypatch.setattr(webapp, "OUT", str(tmp_path))
    monkeypatch.setattr(webapp, "GRUBE_CODES", str(tmp_path / "grube_codes.json"))
    monkeypatch.setattr(webapp, "EXTERNALCODES_STATE", str(tmp_path / "uploaded_externalcodes.json"))
    monkeypatch.setattr(webapp, "CODE2PAIR", {"60645/L": "395", "60645/S": "395", "70000/M": "700"})
    return {"tmp": tmp_path}


def _seed_grube(codes):
    """codes = {code: itemId} → the grube_codes.json store shape {code: {itemId, ...}}."""
    with open(webapp.GRUBE_CODES, "w", encoding="utf-8") as f:
        json.dump({c: {"itemId": iid, "size": "L", "deUrl": "https://grube.de/x",
                       "productId": "3959"} for c, iid in codes.items()}, f)


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


# ── successful nightly push ─────────────────────────────────────────────────────
def test_run_pushes_externalcodes_and_records_counts(iso, monkeypatch):
    _seed_grube({"60645/L": "1547734519", "60645/S": "1547734523"})
    fake_run, calls = _ok_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)

    result = webapp.run_grube_externalcode()

    assert result["status"] == "ok"
    e = result["externalcodes"]
    assert e["count"] == 2
    assert e["total_uploaded"] == 2
    assert e["total_codes"] == 2
    assert e["remaining"] == 0
    assert result["review_url"].startswith("https://")

    # the careful import ran with the externalCode header, itemId in the value column
    (call,) = calls
    assert call["header"] == ["code", "pairCode", "externalCode"]
    assert ["60645/L", "395", "1547734519"] in call["rows"]
    assert ["60645/S", "395", "1547734523"] in call["rows"]
    assert call["dry_run"] is False               # a nightly write is NEVER a dry run

    # its OWN incremental state written (idempotency) — {code: itemId}
    st = json.loads((iso["tmp"] / "uploaded_externalcodes.json").read_text())
    assert st == {"60645/L": "1547734519", "60645/S": "1547734523"}


def test_run_is_incremental_only_new_or_changed_itemid(iso, monkeypatch):
    _seed_grube({"60645/L": "111", "60645/S": "222"})
    fake_run, calls = _ok_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)
    webapp.run_grube_externalcode()
    assert len(calls) == 1

    # second run, nothing changed → the careful import must NOT run again
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not re-import")))
    r2 = webapp.run_grube_externalcode()
    assert r2["status"] == "ok" and r2["externalcodes"]["count"] == 0

    # change ONE itemId + add a NEW code → only those two go up (not the unchanged one)
    _seed_grube({"60645/L": "111", "60645/S": "999", "70000/M": "333"})
    fake_run2, calls2 = _ok_import()
    monkeypatch.setattr(webapp, "run_import", fake_run2)
    r3 = webapp.run_grube_externalcode()
    assert r3["externalcodes"]["count"] == 2
    (call,) = calls2
    pushed = {r[0]: r[2] for r in call["rows"]}
    assert pushed == {"60645/S": "999", "70000/M": "333"}   # 60645/L (unchanged) skipped


def test_run_zero_new_reports_ok_without_importing(iso, monkeypatch):
    # no grube_codes.json at all → clean no-op run (never touches the eshop)
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
    result = webapp.run_grube_externalcode()
    assert result["status"] == "ok"
    assert result["externalcodes"]["count"] == 0
    assert result["externalcodes"]["total_codes"] == 0


def test_nonnumeric_itemid_never_pushed(iso, monkeypatch):
    # a non-numeric / empty itemId is junk (possible formula-injection lead) — it must
    # never reach the eshop AND must not count toward totals (never uploadable).
    _seed_grube({"60645/L": "=EVIL", "60645/S": "", "70000/M": "42"})
    fake_run, calls = _ok_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)

    result = webapp.run_grube_externalcode()

    (call,) = calls
    assert [r[0] for r in call["rows"]] == ["70000/M"]   # ONLY the numeric one
    assert result["externalcodes"]["count"] == 1
    assert result["externalcodes"]["total_codes"] == 1   # non-numeric excluded from total
    st = json.loads((iso["tmp"] / "uploaded_externalcodes.json").read_text())
    assert st == {"70000/M": "42"}


# ── graceful degradation ────────────────────────────────────────────────────────
def test_import_failure_surfaces_failed_status_and_does_not_mark_uploaded(iso, monkeypatch):
    _seed_grube({"60645/L": "111"})
    monkeypatch.setattr(webapp, "run_import",
                        lambda p, dry_run=False, timeout=300: (1, "chyba", "boom"))
    result = webapp.run_grube_externalcode()
    assert result["status"] == "failed"
    assert result["externalcodes"]["ok"] is False
    # a failed import never records the code as uploaded → retried next run
    assert (not (iso["tmp"] / "uploaded_externalcodes.json").exists()
            or json.loads((iso["tmp"] / "uploaded_externalcodes.json").read_text()) == {})


# ── #156: a large batch is split into chunked imports ──────────────────────────
def test_large_batch_split_into_chunks(iso, monkeypatch):
    n = 650
    codes = {f"{i}/M": str(1000000 + i) for i in range(n)}
    monkeypatch.setattr(webapp, "CODE2PAIR", {c: "P" for c in codes})
    _seed_grube(codes)
    fake_run, calls = _recording_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)

    result = webapp.run_grube_externalcode()

    assert len(calls) >= 2                                   # split, not one giant import
    assert max(len(c["rows"]) for c in calls) <= webapp.IMPORT_CHUNK_ROWS
    imported = [r[0] for c in calls for r in c["rows"]]
    assert sorted(imported) == sorted(codes)                # each code exactly once
    assert result["status"] == "ok"
    assert result["externalcodes"]["ok"] is True and result["externalcodes"]["count"] == n


def test_mid_batch_chunk_failure_records_partial_and_releases_lock(iso, monkeypatch):
    # #156: a chunk failing mid-batch → failed status, record ONLY the codes from the
    # SUCCESSFUL chunk(s) (resumable — never all-or-nothing silent success), and
    # release the import lock (no stuck lock → no cascade failure).
    n = 650
    codes = {f"{i}/M": str(1000000 + i) for i in range(n)}
    monkeypatch.setattr(webapp, "CODE2PAIR", {c: "P" for c in codes})
    _seed_grube(codes)
    fake_run, calls = _recording_import(fail_on_call=2)     # 1st chunk ok, 2nd fails
    monkeypatch.setattr(webapp, "run_import", fake_run)

    result = webapp.run_grube_externalcode()

    assert result["status"] == "failed"
    assert result["externalcodes"]["ok"] is False
    assert "časti 2/" in result["externalcodes"]["error"]
    assert "z 650 riadkov" in result["externalcodes"]["error"]
    assert len(calls) == 2                                  # batch STOPS after the failing chunk
    uploaded = json.loads((iso["tmp"] / "uploaded_externalcodes.json").read_text())
    chunk1_codes = {r[0] for r in calls[0]["rows"]}
    chunk2_codes = {r[0] for r in calls[1]["rows"]}
    assert set(uploaded) == chunk1_codes
    assert not (set(uploaded) & chunk2_codes)
    assert 0 < len(uploaded) < n
    # the import lock was released despite the failure (else the next import 409s)
    assert webapp._import_lock.acquire(blocking=False)
    webapp._import_lock.release()


def test_run_via_runner_records_error_when_import_raises(iso, monkeypatch):
    _seed_grube({"60645/L": "111"})

    def boom(*a, **k):
        raise RuntimeError("shoptet_import.py spadol")
    monkeypatch.setattr(webapp, "run_import", boom)

    assert webapp.RUNNER._execute("grube_externalcode") is True    # runner survives
    (st,) = [x for x in webapp.RUNNER.status() if x["key"] == "grube_externalcode"]
    assert st["last_status"] == "error"
    assert "shoptet_import.py spadol" in st["last_error"]
    assert st["running"] is False


# ── disabled automation never runs on a scheduler tick ──────────────────────────
def test_disabled_automation_is_not_ticked(iso, monkeypatch):
    _seed_grube({"60645/L": "111"})
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("disabled must not run")))
    webapp.RUNNER.tick_once()                    # default state = disabled
    (st,) = [x for x in webapp.RUNNER.status() if x["key"] == "grube_externalcode"]
    assert st["enabled"] is False
    assert st["last_run"] == ""                  # never ran


# ── http run endpoint + runner integration ──────────────────────────────────────
def test_run_now_via_http_endpoint_and_runner(iso, monkeypatch):
    _seed_grube({"60645/L": "111"})
    fake_run, _calls = _ok_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)
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
def test_run_reads_but_never_writes_grube_codes_store(iso, monkeypatch):
    _seed_grube({"60645/L": "111"})
    gc_before = (iso["tmp"] / "grube_codes.json").read_text()
    fake_run, _calls = _ok_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)

    webapp.run_grube_externalcode()

    assert (iso["tmp"] / "grube_codes.json").read_text() == gc_before


# ── the n8n Bearer endpoint delegates to the same core ──────────────────────────
def test_n8n_endpoint_requires_bearer_token(iso, monkeypatch):
    monkeypatch.setattr(webapp, "_import_token", lambda: "SEKRET")
    c = authed_client()
    assert c.post("/api/n8n/upload-externalcode").status_code == 401           # no token
    assert c.post("/api/n8n/upload-externalcode",
                  headers={"Authorization": "Bearer WRONG"}).status_code == 401


def test_n8n_endpoint_dry_run_reaches_import_without_recording(iso, monkeypatch):
    _seed_grube({"60645/L": "111"})
    monkeypatch.setattr(webapp, "_import_token", lambda: "SEKRET")
    fake_run, calls = _ok_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)
    c = authed_client()
    r = c.post("/api/n8n/upload-externalcode?dry_run=1",
               headers={"Authorization": "Bearer SEKRET"})
    assert r.status_code == 200
    assert r.get_json()["dry_run"] is True
    assert calls and calls[0]["dry_run"] is True
    # dry run records NOTHING (so the real nightly run still pushes it)
    assert not (iso["tmp"] / "uploaded_externalcodes.json").exists()
