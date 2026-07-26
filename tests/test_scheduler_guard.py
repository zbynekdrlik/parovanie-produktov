"""#262 — only ONE instance may run the automation scheduler over a data dir.

A throwaway instance booted for a screenshot on another port (the recipe in
.claude/skills/webreview/SKILL.md) was never killed and ran for four days beside the
real service, on four-week-old code, with its scheduler ENABLED: two schedulers racing
the same nightly jobs over the same data/out, unlogged. Nothing stops that today —
`RUNNER.start()` runs unconditionally at boot.

Two guards are pinned here: a cross-process claim (a second instance refuses to start
the scheduler and says who holds it), and an explicit off switch so the preview boot
carries no scheduler at all and can never mail a customer or write to the eshop.
"""
import fcntl
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webreview"))
import app as webapp  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def scheduler_out(tmp_path, monkeypatch):
    """Isolated data dir + a guaranteed-released claim, whatever the test does."""
    monkeypatch.setattr(webapp, "OUT", str(tmp_path))
    monkeypatch.setattr(webapp, "_scheduler_claim_fd", None, raising=False)
    yield tmp_path
    fd = getattr(webapp, "_scheduler_claim_fd", None)
    if fd is not None:
        os.close(fd)
        webapp._scheduler_claim_fd = None


def test_the_scheduler_is_claimed_when_nothing_holds_it(scheduler_out):
    assert webapp._claim_scheduler() is True
    assert (scheduler_out / ".scheduler.lock").exists()


def test_a_second_instance_refuses_to_start_the_scheduler(scheduler_out, caplog):
    lock = scheduler_out / ".scheduler.lock"
    holder_fd = os.open(str(lock), os.O_RDWR | os.O_CREAT, 0o600)
    os.write(holder_fd, b"pid=999999 port=8801\n")
    fcntl.flock(holder_fd, fcntl.LOCK_EX)
    try:
        with caplog.at_level("ERROR"):
            assert webapp._claim_scheduler() is False
    finally:
        fcntl.flock(holder_fd, fcntl.LOCK_UN)
        os.close(holder_fd)
    assert "999999" in caplog.text, "the log must name the instance that holds the claim"


def test_a_blocked_instance_never_starts_the_runner(scheduler_out, monkeypatch):
    started = []
    monkeypatch.setattr(webapp.RUNNER, "start", lambda: started.append(1))
    holder_fd = os.open(str(scheduler_out / ".scheduler.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(holder_fd, fcntl.LOCK_EX)
    try:
        assert webapp._start_scheduler() is False
    finally:
        fcntl.flock(holder_fd, fcntl.LOCK_UN)
        os.close(holder_fd)
    assert started == [], "a second instance must not run the nightly jobs"


def test_the_scheduler_starts_normally_for_the_only_instance(scheduler_out, monkeypatch):
    started = []
    monkeypatch.setattr(webapp.RUNNER, "start", lambda: started.append(1))
    assert webapp._start_scheduler() is True
    assert started == [1]


def test_a_preview_boot_carries_no_scheduler_at_all(scheduler_out, monkeypatch):
    """The forgotten throwaway instance must not be able to mail anyone even if it
    outlives the session that booted it."""
    monkeypatch.setenv("WEBREVIEW_NO_SCHEDULER", "1")
    started = []
    monkeypatch.setattr(webapp.RUNNER, "start", lambda: started.append(1))
    assert webapp._start_scheduler() is False
    assert started == []
    assert not (scheduler_out / ".scheduler.lock").exists(), \
        "a preview boot must not even claim the scheduler (it never runs one)"


def test_the_off_switch_is_opt_in(scheduler_out, monkeypatch):
    """An empty or 0 value is not „disabled" — only an explicit switch is."""
    started = []
    monkeypatch.setattr(webapp.RUNNER, "start", lambda: started.append(1))
    monkeypatch.setenv("WEBREVIEW_NO_SCHEDULER", "0")
    assert webapp._start_scheduler() is True
    assert started == [1]


def test_every_e2e_fixture_server_boots_without_a_scheduler():
    """Each e2e fixture launches a real `python webreview/app.py`, so each one starts
    a real scheduler over its own data dir. They are inert only because no fixture
    happens to seed `enabled: true` today — one seeded automation and a CI run would
    scrape a paid API or mail a customer. Same category as the forgotten preview."""
    conftest = os.path.join(ROOT, "tests", "e2e", "conftest.py")
    with open(conftest, encoding="utf-8") as f:
        text = f.read()
    assert '"WEBREVIEW_NO_SCHEDULER": "1"' in text, \
        "the shared fixture-server env must pin the scheduler off"
    servers = text.count('"WEBREVIEW_OUT": str(out)')
    pinned = text.count("**_AUTH_ENV")
    assert pinned >= servers, \
        f"{servers - pinned} fixture server(s) build an env without the shared pins"


def test_run_now_is_refused_on_an_instance_without_a_scheduler(monkeypatch):
    """„⚡ Spustiť teraz" runs the automation IN THIS PROCESS, so a preview instance
    could mail customers from its own UI however loudly the boot log says it runs
    no automation."""
    from tests.conftest import authed_client
    monkeypatch.setenv("WEBREVIEW_NO_SCHEDULER", "1")
    called = []
    monkeypatch.setattr(webapp.RUNNER, "run_now", lambda key: called.append(key) or True)
    r = authed_client().post("/api/automations/posta_uncollected/run")
    assert r.status_code == 503, r.data
    assert called == []


def test_the_playbook_preview_recipe_disables_the_scheduler():
    """The recipe that produced the four-day orphan must boot without a scheduler."""
    skill = os.path.join(ROOT, ".claude", "skills", "webreview", "SKILL.md")
    with open(skill, encoding="utf-8") as f:
        text = f.read()
    recipe = [ln for ln in text.splitlines() if "WEBREVIEW_PORT=8811" in ln]
    assert recipe, "the throwaway-preview recipe disappeared from the playbook"
    assert all("WEBREVIEW_NO_SCHEDULER=1" in ln for ln in recipe), \
        "the throwaway preview must be booted with WEBREVIEW_NO_SCHEDULER=1"
