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
from parovanie.automation_runner import (  # noqa: E402
    Automation, AutomationRunner, AutomationStateCorrupt)

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


@pytest.mark.parametrize("broken", ["decisions.json", "users.json"])
def test_the_service_still_boots_with_a_store_that_parses_to_the_WRONG_SHAPE(
        tmp_path, broken):
    """A store that parses fine but is not a MAP is the one shape C2 left open:
    `_load_users` was the single loader in this PR with no `isinstance` check, so a
    `users.json` holding `[]` reached `_bootstrap_admin`, which then did
    `users[email] = {...}` → `TypeError: list indices must be integers`. TypeError is
    not in the boot except tuple, so the import died: systemd restart loop, no web UI.
    Reachable from a wrong hand-repair or a wrong restore — precisely what the 503
    repair message invites an operator to attempt (PR #265 third review, I2)."""
    out = tmp_path / "out"
    out.mkdir()
    (out / broken).write_text("[]", encoding="utf-8")
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
    assert (out / broken).read_text(encoding="utf-8") == "[]", "the store was rewritten"


def test_a_user_store_of_the_wrong_shape_answers_503_not_500(tmp_path, monkeypatch):
    """With ADMIN_EMAIL unset the boot survives, but `_current_user` then did
    `[].get(email)` → `AttributeError`, which is outside `_require_login`'s
    `except (ValueError, OSError)` — so EVERY request 500'd. That is the „reads as a
    transient glitch, invites another click" outcome C2 replaced with a 503."""
    import sys as _sys
    _sys.path.insert(0, os.path.join(ROOT, "webreview"))
    import app as webapp

    broken = tmp_path / "users.json"
    broken.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(webapp, "USERS", str(broken))
    with pytest.raises(ValueError):
        webapp._load_users()
    c = webapp.app.test_client()
    with c.session_transaction() as s:
        s["user"] = "someone@test.local"
    r = c.get("/api/orders")
    assert r.status_code == 503, r.status_code
    assert r.get_json()["ok"] is False and "NEMAŽ" in r.get_json()["error"]


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


# --------------------------------------------------------------------------- #
# …and clearing the claim BEFORE the outcome write opened a duplicate-run window
#
# The fix above moved `self._running[key] = False` into a `finally` that runs before a
# SECOND `with self._lock:` persists the outcome — and that acquisition is the
# cross-process store lock, blockable for up to 30 s by another instance. In that
# window the claim is clear while `automations.json` still holds the old, already-past
# `next_run`: the scheduler ticks (every 30 s), sees neither, and runs the automation a
# second time — duplicate customer mails, with only the dedup store between them and
# the customer (PR #265 second review, C3).
# --------------------------------------------------------------------------- #
class _GateLock:
    """A store lock whose FIRST armed acquisition blocks until the test releases it."""

    def __init__(self):
        self._l = threading.Lock()
        self.arm = False
        self.gate = threading.Event()
        self.blocked = threading.Event()

    def __enter__(self):
        if self.arm:
            self.arm = False          # only the outcome write waits, not the retry
            self.blocked.set()
            self.gate.wait(15)
        self._l.acquire()
        return self

    def __exit__(self, *exc) -> bool:
        self._l.release()
        return False


def _due_runner(tmp_path, run_fn, lock):
    a = Automation(key="orders_reminder", name="Pripomienka objednávok",
                   schedule={"interval_minutes": 60, "tz": "Europe/Bratislava"},
                   run_fn=run_fn)
    r = AutomationRunner(str(tmp_path / "automations.json"), [a], tick=999.0, lock=lock)
    r.set_enabled("orders_reminder", True)
    st = r._load()
    st["orders_reminder"]["next_run"] = "2020-01-01T00:00:00"      # long overdue
    r._save(st)
    return r


def test_a_blocked_outcome_write_cannot_let_the_scheduler_run_it_twice(tmp_path):
    runs = []
    lock = _GateLock()

    def run_fn():
        runs.append(1)
        lock.arm = True               # the NEXT acquisition is the outcome write
        return {"sent": 1}

    r = _due_runner(tmp_path, run_fn, lock)
    first = threading.Thread(target=r._execute, args=("orders_reminder",),
                             name="first-run", daemon=True)
    first.start()
    assert lock.blocked.wait(15), "the outcome write never blocked — test is not testing"

    # the scheduler's own tick, while the first run is still recording its outcome
    tick = threading.Thread(target=r.tick_once, name="scheduler-tick", daemon=True)
    tick.start()
    tick.join(5)
    try:
        assert runs == [1], (f"the automation ran {len(runs)}× — the scheduler started a "
                             "second run while the first was still persisting its outcome")
        assert r.status()[0]["running"] is True, "the claim was dropped before the write"
    finally:
        lock.gate.set()
        first.join(15)
        tick.join(15)
    assert r.status()[0]["running"] is False, "the claim was never cleared"
    assert r._load()["orders_reminder"]["last_status"] == "ok"


def test_the_claim_still_clears_when_the_outcome_write_raises(tmp_path):
    """The property the previous fix bought must survive: a StoreLockTimeout while
    persisting the outcome may not leave the automation claimed forever."""
    class _RaisingLock:
        def __init__(self):
            self.calls = 0

        def __enter__(self):
            self.calls += 1
            if self.calls > 1:          # let the claim be taken, fail the outcome write
                raise webapp.StoreLockTimeout("iná inštancia drží dáta")
            return self

        def __exit__(self, *exc) -> bool:
            return False

    lock = _RaisingLock()
    a = Automation(key="demo", name="Demo", schedule={"interval_minutes": 60},
                   run_fn=lambda: {"n": 1})
    r = AutomationRunner(str(tmp_path / "automations.json"), [a], tick=999.0, lock=lock)
    assert r._execute("demo") is True
    assert r._running["demo"] is False, "the automation stayed claimed forever"


# --------------------------------------------------------------------------- #
# automations.json got neither of this PR's own two principles
#
# `_load` answered `{}` on a parse error — „unreadable means empty", the exact defect
# the rest of this work exists to kill, on the file that decides which automations are
# ENABLED. A truncated state file silently reverts every automation to
# disabled/unscheduled (no reminder mails, no pošta escalations, no hourly sync) while
# the tab renders a clean first-run state. And `_save` had no fsync before its
# `os.replace`, so that truncation is reachable on power loss — while the same PR added
# fsync to both app.py writers and put automations.json in the backup rotation
# (PR #265 second review, C4).
# --------------------------------------------------------------------------- #
def _state_runner(tmp_path, **kw):
    a = Automation(key="demo", name="Demo", schedule={"interval_minutes": 60},
                   run_fn=lambda: {"n": 1})
    return AutomationRunner(str(tmp_path / "automations.json"), [a], tick=999.0, **kw)


def test_a_truncated_automation_state_is_never_read_as_no_automations(tmp_path):
    r = _state_runner(tmp_path)
    r.set_enabled("demo", True)
    p = tmp_path / "automations.json"
    raw = p.read_text(encoding="utf-8")
    p.write_text(raw[:len(raw) // 2], encoding="utf-8")          # cut mid-write
    with pytest.raises(AutomationStateCorrupt):
        r._load()
    assert p.read_text(encoding="utf-8") == raw[:len(raw) // 2], "the original was touched"
    assert list(tmp_path.glob("automations.json.corrupt-*")), "no copy kept for repair"


def test_a_state_file_that_is_not_a_map_fails_closed_too(tmp_path):
    r = _state_runner(tmp_path)
    (tmp_path / "automations.json").write_text("[]", encoding="utf-8")
    with pytest.raises(AutomationStateCorrupt):
        r.status()


def test_a_corrupt_state_file_never_silently_reschedules_or_disables(tmp_path):
    """The dangerous consequence, not just the exception: a tick over a corrupt state
    must not decide „nothing is enabled" — and must not overwrite it either."""
    r = _state_runner(tmp_path)
    r.set_enabled("demo", True)
    p = tmp_path / "automations.json"
    p.write_text('{"demo": {"enab', encoding="utf-8")
    with pytest.raises(AutomationStateCorrupt):
        r.tick_once()
    assert p.read_text(encoding="utf-8") == '{"demo": {"enab'


def test_the_state_file_is_fsynced_before_it_replaces_the_old_one(tmp_path, monkeypatch):
    """Without this the rename can be durable while the bytes are not — which is
    exactly how the truncated file above appears after a power loss."""
    calls = []
    real_fsync, real_replace = os.fsync, os.replace
    monkeypatch.setattr(os, "fsync", lambda fd: (calls.append("fsync"), real_fsync(fd))[1])
    monkeypatch.setattr(os, "replace",
                        lambda a, b: (calls.append("replace"), real_replace(a, b))[1])
    _state_runner(tmp_path).set_enabled("demo", True)
    assert "fsync" in calls, "the state file was replaced without ever being fsynced"
    assert calls.index("fsync") < calls.index("replace"), calls


def test_a_corrupt_state_file_answers_503_and_does_not_stop_the_boot(tmp_path, monkeypatch):
    """Failing closed must not brick the service: the module still imports, and the
    automations tab says what to fix instead of 500-ing."""
    monkeypatch.setattr(webapp.RUNNER, "state_path", str(tmp_path / "automations.json"))
    (tmp_path / "automations.json").write_text('{"demo": {"enab', encoding="utf-8")
    from tests.conftest import authed_client
    r = authed_client().get("/api/automations")
    assert r.status_code == 503, r.status_code
    assert r.get_json()["ok"] is False and "automations.json" in r.get_json()["error"]
