"""Unit tests for the generic in-app automation runner (#93 Part A).

Covers the safety contract (new automation starts DISABLED, a disabled
automation never runs on tick), state persistence across restarts (fresh
runner instance on the same file), scheduling math, error capture, and the
manual 'Spustiť teraz' path.
"""
import json
import os
import stat
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from parovanie.automation_runner import (
    Automation, AutomationRunner, next_run_at, schedule_label)

TZ = ZoneInfo("Europe/Bratislava")
SCHED = {"daily_at": "09:00", "tz": "Europe/Bratislava"}
HOURLY = {"interval_minutes": 60, "tz": "Europe/Bratislava"}


def _runner(tmp_path, run_fn=None, tick=30.0):
    return AutomationRunner(
        str(tmp_path / "automations.json"),
        [Automation(key="demo", name="Demo automatizácia", schedule=SCHED,
                    run_fn=run_fn or (lambda: {"n": 1}))],
        tick=tick)


# ── scheduling math ────────────────────────────────────────────────────────────
def test_next_run_before_time_is_today():
    now = datetime(2026, 7, 22, 8, 0, tzinfo=TZ)
    assert next_run_at(SCHED, now) == datetime(2026, 7, 22, 9, 0, tzinfo=TZ)


def test_next_run_after_time_is_tomorrow():
    now = datetime(2026, 7, 22, 9, 30, tzinfo=TZ)
    assert next_run_at(SCHED, now) == datetime(2026, 7, 23, 9, 0, tzinfo=TZ)


def test_next_run_exactly_at_time_is_tomorrow():
    now = datetime(2026, 7, 22, 9, 0, tzinfo=TZ)
    assert next_run_at(SCHED, now) == datetime(2026, 7, 23, 9, 0, tzinfo=TZ)


def test_schedule_label():
    assert schedule_label(SCHED) == "denne o 09:00"


# ── interval-based scheduling (#119 — hourly Shoptet sync) ───────────────────
def test_next_run_interval_minutes_is_now_plus_interval():
    now = datetime(2026, 7, 22, 8, 17, tzinfo=TZ)
    assert next_run_at(HOURLY, now) == datetime(2026, 7, 22, 9, 17, tzinfo=TZ)


def test_next_run_interval_minutes_respects_tz_arg_default():
    # no explicit `now` -> uses "now" in the schedule's own tz, still +interval
    before = datetime.now(TZ)
    got = next_run_at(HOURLY)
    assert got - before >= timedelta(minutes=59, seconds=55)
    assert got - before <= timedelta(minutes=60, seconds=5)


def test_schedule_label_interval_hourly():
    assert schedule_label(HOURLY) == "každú hodinu"


def test_schedule_label_interval_minutes_non_hour():
    assert schedule_label({"interval_minutes": 15}) == "každých 15 min"


def test_schedule_label_interval_multiple_hours():
    assert schedule_label({"interval_minutes": 120}) == "každé 2 hodiny"


# ── safety: default DISABLED ──────────────────────────────────────────────────
def test_new_automation_starts_disabled(tmp_path):
    r = _runner(tmp_path)
    st = r.status()[0]
    assert st["enabled"] is False
    assert st["next_run"] == ""
    assert st["last_run"] == ""


def test_disabled_automation_never_runs_on_tick(tmp_path):
    ran = []
    r = _runner(tmp_path, run_fn=lambda: ran.append(1) or {})
    # even with a hand-planted past next_run, disabled means NO run
    r._save({"demo": {"enabled": False, "next_run": "2020-01-01T09:00:00+01:00"}})
    r.tick_once()
    assert ran == []


# ── enable/disable persistence ────────────────────────────────────────────────
def test_enable_persists_across_restart(tmp_path):
    r = _runner(tmp_path)
    r.set_enabled("demo", True)
    st = r.status()[0]
    assert st["enabled"] is True
    assert st["next_run"] != ""
    # fresh instance on the same state file = app restart
    r2 = _runner(tmp_path)
    assert r2.status()[0]["enabled"] is True
    # state file is private (0600) like the other data/out stores
    mode = stat.S_IMODE(os.stat(tmp_path / "automations.json").st_mode)
    assert mode == 0o600


def test_disable_clears_next_run(tmp_path):
    r = _runner(tmp_path)
    r.set_enabled("demo", True)
    r.set_enabled("demo", False)
    st = r.status()[0]
    assert st["enabled"] is False and st["next_run"] == ""


def test_set_enabled_unknown_key_raises(tmp_path):
    with pytest.raises(KeyError):
        _runner(tmp_path).set_enabled("neexistuje", True)


# ── scheduled tick execution ──────────────────────────────────────────────────
def test_tick_runs_due_enabled_automation(tmp_path):
    ran = []
    r = _runner(tmp_path, run_fn=lambda: (ran.append(1), {"count": 7})[1])
    r.set_enabled("demo", True)
    past = (datetime.now(TZ) - timedelta(minutes=5)).isoformat(timespec="seconds")
    st = json.loads((tmp_path / "automations.json").read_text())
    st["demo"]["next_run"] = past
    r._save(st)
    r.tick_once()
    assert ran == [1]
    out = r.status()[0]
    assert out["last_status"] == "ok"
    assert out["last_result"] == {"count": 7}
    assert out["last_run"] != ""
    # next_run moved into the future (no re-run on the next tick)
    assert datetime.fromisoformat(out["next_run"]) > datetime.now(TZ)
    r.tick_once()
    assert ran == [1]


def test_tick_does_not_run_future_schedule(tmp_path):
    ran = []
    r = _runner(tmp_path, run_fn=lambda: ran.append(1) or {})
    r.set_enabled("demo", True)          # next_run = next 09:00 (future)
    r.tick_once()
    assert ran == []


def test_run_error_captured_and_runner_survives(tmp_path):
    def boom():
        raise RuntimeError("SHOPTET_ORDERS_URL chýba")
    r = _runner(tmp_path, run_fn=boom)
    assert r._execute("demo") is True
    st = r.status()[0]
    assert st["last_status"] == "error"
    assert "SHOPTET_ORDERS_URL" in st["last_error"]
    assert st["running"] is False


# ── manual run ('Spustiť teraz') ──────────────────────────────────────────────
def test_run_now_works_even_when_disabled(tmp_path):
    ran = []
    r = _runner(tmp_path, run_fn=lambda: (ran.append(1), {"ok": 1})[1])
    assert r.run_now("demo") is True
    r._threads["demo"].join(timeout=10)
    assert ran == [1]
    st = r.status()[0]
    assert st["last_status"] == "ok"
    assert st["enabled"] is False        # manual run does NOT enable the schedule
    assert st["next_run"] == ""


def test_run_now_refuses_parallel_run(tmp_path):
    gate = threading.Event()
    r = _runner(tmp_path, run_fn=lambda: gate.wait(10) or {})
    assert r.run_now("demo") is True
    try:
        assert r.run_now("demo") is False        # already in flight
        assert r.status()[0]["running"] is True
    finally:
        gate.set()
        r._threads["demo"].join(timeout=10)
    assert r.status()[0]["running"] is False


def test_run_now_unknown_key_raises(tmp_path):
    with pytest.raises(KeyError):
        _runner(tmp_path).run_now("neexistuje")


# ── corrupt state tolerance ───────────────────────────────────────────────────
def test_corrupt_state_file_tolerated(tmp_path):
    p = tmp_path / "automations.json"
    p.write_text("{not json", encoding="utf-8")
    r = _runner(tmp_path)
    assert r.status()[0]["enabled"] is False
    r.set_enabled("demo", True)          # recovers by rewriting the store
    assert r.status()[0]["enabled"] is True


def test_enabled_without_next_run_gets_rescheduled_not_run(tmp_path):
    ran = []
    r = _runner(tmp_path, run_fn=lambda: ran.append(1) or {})
    r._save({"demo": {"enabled": True}})     # legacy/hand-edited state
    r.tick_once()
    assert ran == []                          # no surprise run
    assert r.status()[0]["next_run"] != ""    # scheduled forward instead
