"""PR #265 review — the cross-process store lock must not be able to kill an
automation, or the service's boot.

#264 injected the app's `_StoreLock` into the automation runner so `automations.json`
is serialised across processes too. That lock can RAISE (`StoreLockTimeout` after 30 s)
where the `threading.Lock` it replaced never could — and two places took it without a
thought for that:

  * `AutomationRunner._execute` cleared its „already running" claim as the FIRST
    statement inside the lock it takes to persist the outcome. A raise there leaves the
    claim set for the whole process lifetime: the scheduler skips that automation
    forever, „⚡ Spustiť teraz" answers „už beží" forever, and the run's outcome is
    never recorded — while the mails it sent already went out.
  * `_bootstrap_admin()` runs at IMPORT and takes the same lock. A raise there does not
    degrade anything, it aborts the module import: the service does not start at all
    (systemd restart loop), where before it would merely have blocked.
"""
import fcntl
import os
import subprocess
import sys
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webreview"))
import app as webapp  # noqa: E402
from parovanie.automation_runner import Automation, AutomationRunner  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _LockHeldByAnother:
    """Stands in for the injected store lock once another process is sitting on it:
    the acquisitions named in `raise_on` fail the way `_StoreLock` really fails."""

    def __init__(self, raise_on):
        self.raise_on = set(raise_on)
        self.calls = 0
        self._r = threading.RLock()

    def __enter__(self):
        self.calls += 1
        if self.calls in self.raise_on:
            raise webapp.StoreLockTimeout("iný proces drží úložisko")
        self._r.acquire()
        return self

    def __exit__(self, *exc):
        self._r.release()
        return False


def _runner(tmp_path, lock, run_fn):
    return AutomationRunner(
        str(tmp_path / "automations.json"),
        [Automation("a", "Testovacia", {"daily_at": "09:00"}, run_fn)],
        lock=lock)


def test_a_lock_timeout_while_recording_the_outcome_never_wedges_the_automation(tmp_path):
    """The run HAPPENED (mails are out). Failing to write its outcome must cost the
    outcome line, never the automation itself."""
    ran = []
    lock = _LockHeldByAnother(raise_on=[1])      # claimed=True → the persist is the 1st
    r = _runner(tmp_path, lock, lambda: ran.append(1) or {"ok": 1})

    assert r._execute("a", claimed=True) is True
    assert ran == [1], "the run itself must still happen"
    assert not r._running.get("a"), "the automation is claimed forever — it can never run again"
    assert r.status()[0]["running"] is False


def test_the_automation_can_run_again_after_the_other_process_lets_go(tmp_path):
    ran = []
    lock = _LockHeldByAnother(raise_on=[1])
    r = _runner(tmp_path, lock, lambda: ran.append(1) or {"ok": 1})
    r._execute("a", claimed=True)

    r._execute("a")                              # …the lock is free again now
    assert ran == [1, 1]
    assert r.status()[0]["last_status"] == "ok", "the second run's outcome must persist"


def test_a_failing_run_still_clears_its_claim(tmp_path):
    """Unchanged behaviour, pinned: a run that RAISES is recorded as an error and the
    automation stays runnable."""
    def boom():
        raise RuntimeError("nope")

    r = _runner(tmp_path, _LockHeldByAnother(raise_on=[]), boom)
    assert r._execute("a", claimed=True) is True
    assert not r._running.get("a")
    assert r.status()[0]["last_status"] == "error"


def test_the_service_still_boots_while_another_process_holds_the_store_lock(tmp_path):
    """The proof this is about booting, not tidiness: a real `import app` with
    ADMIN_EMAIL/ADMIN_PW set (as on the live box) while the data dir's store lock is
    held. It must come up and serve — logging the skipped bootstrap — not die."""
    out = tmp_path / "out"
    out.mkdir()
    fd = os.open(str(out / ".store.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        code = ("import os, sys; sys.path.insert(0, os.path.join(sys.argv[1], 'webreview')); "
                "import app; print('BOOTED', app.__version__)")
        env = dict(os.environ,
                   WEBREVIEW_OUT=str(out),
                   WEBREVIEW_PRODUCTS=str(tmp_path / "nonexistent.csv"),
                   WEBREVIEW_NO_SCHEDULER="1",
                   WEBREVIEW_STORE_LOCK_TIMEOUT="1",
                   ADMIN_EMAIL="admin@test.local", ADMIN_PW="dost-dlhe-heslo",
                   PYTHONPATH=os.path.join(ROOT, "src"))
        p = subprocess.run([sys.executable, "-c", code, ROOT], env=env,
                           capture_output=True, timeout=120)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    assert p.returncode == 0, p.stderr.decode()[-3000:]
    assert b"BOOTED" in p.stdout, p.stderr.decode()[-3000:]


@pytest.mark.parametrize("broken", ["decisions.json", "users.json"])
def test_the_service_still_boots_with_an_unreadable_store(tmp_path, broken):
    """`_read_json_store` deliberately re-raises an OSError on a store that IS there,
    and the boot prune / admin bootstrap both touch such stores. Neither may take the
    whole web UI down with it — with no UI there is no way to even see the problem."""
    out = tmp_path / "out"
    out.mkdir()
    (out / broken).mkdir()                       # open() → IsADirectoryError
    code = ("import os, sys; sys.path.insert(0, os.path.join(sys.argv[1], 'webreview')); "
            "import app; print('BOOTED', app.__version__)")
    env = dict(os.environ,
               WEBREVIEW_OUT=str(out),
               WEBREVIEW_PRODUCTS=str(tmp_path / "nonexistent.csv"),
               WEBREVIEW_NO_SCHEDULER="1",
               ADMIN_EMAIL="admin@test.local", ADMIN_PW="dost-dlhe-heslo",
               PYTHONPATH=os.path.join(ROOT, "src"))
    p = subprocess.run([sys.executable, "-c", code, ROOT], env=env,
                       capture_output=True, timeout=120)
    assert p.returncode == 0, p.stderr.decode()[-3000:]
    assert b"BOOTED" in p.stdout, p.stderr.decode()[-3000:]


@pytest.mark.parametrize("broken", ["decisions.json", "users.json"])
def test_the_service_still_boots_with_a_TRUNCATED_store(tmp_path, broken):
    """The unreadable-store boot test above uses a DIRECTORY in the store's place —
    an OSError. The corruption the fsync work exists to make less likely is a write
    cut mid-JSON or mid-UTF-8, which is a ValueError, and neither was caught: a
    truncated users.json took `_bootstrap_admin` (and therefore the whole import)
    down with a JSONDecodeError — no web UI, systemd restart loop (PR #265 second
    review, C2)."""
    out = tmp_path / "out"
    out.mkdir()
    (out / broken).write_text('{"admin@test.local": {"pw_ha', encoding="utf-8")
    code = ("import os, sys; sys.path.insert(0, os.path.join(sys.argv[1], 'webreview')); "
            "import app; print('BOOTED', app.__version__)")
    env = dict(os.environ,
               WEBREVIEW_OUT=str(out),
               WEBREVIEW_PRODUCTS=str(tmp_path / "nonexistent.csv"),
               WEBREVIEW_NO_SCHEDULER="1",
               ADMIN_EMAIL="admin@test.local", ADMIN_PW="dost-dlhe-heslo",
               PYTHONPATH=os.path.join(ROOT, "src"))
    p = subprocess.run([sys.executable, "-c", code, ROOT], env=env,
                       capture_output=True, timeout=120)
    assert p.returncode == 0, p.stderr.decode()[-3000:]
    assert b"BOOTED" in p.stdout, p.stderr.decode()[-3000:]
    # and the corrupt store is still there for repair, never replaced
    assert (out / broken).read_text(encoding="utf-8") == '{"admin@test.local": {"pw_ha'


def test_a_corrupt_user_store_answers_503_with_something_to_fix(tmp_path, monkeypatch):
    """A per-request 500 reads as a transient glitch and invites another click; an
    unreadable account list is neither transient nor clickable-away (PR #265, C2)."""
    import sys as _sys
    _sys.path.insert(0, os.path.join(ROOT, "webreview"))
    import app as webapp

    broken = tmp_path / "users.json"
    broken.write_text('{"someone@test.local": {"pw_ha', encoding="utf-8")
    monkeypatch.setattr(webapp, "USERS", str(broken))
    c = webapp.app.test_client()
    with c.session_transaction() as s:
        s["user"] = "someone@test.local"
    r = c.get("/api/orders")
    assert r.status_code == 503, r.status_code
    assert r.get_json()["ok"] is False and r.get_json()["error"]
