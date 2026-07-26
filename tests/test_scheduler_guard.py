"""#262 — only ONE instance may run the automation scheduler over a data dir.

A throwaway instance booted for a screenshot on another port (the recipe in
.claude/skills/webreview/SKILL.md) was never killed and ran for four days beside the
real service, on four-week-old code, with its scheduler ENABLED: two schedulers racing
the same nightly jobs over the same data/out, unlogged. Nothing stops that today —
`RUNNER.start()` runs unconditionally at boot.

Two guards are pinned here: a cross-process claim (a second instance refuses to start
the scheduler and says who holds it), and an explicit off switch so the preview boot
carries no scheduler at all — nothing in it ever fires on a timer, however long it is
forgotten. (A manual „Spustiť teraz" click by a logged-in human still runs; the flag
removes the unattended schedule, which is what the orphan was doing.)
"""
import ast
import fcntl
import os
import re
import sys

import pytest

from parovanie.automation_runner import Automation, AutomationRunner

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


def _e2e_server_envs():
    """Every `subprocess.Popen` in the e2e conftest, paired with the env dict literal it
    is handed. Structural on purpose: the string-counting version this replaces compared
    occurrences of the literal `'"WEBREVIEW_OUT": str(out)'`, so a fixture that named its
    dir anything but `out` counted as ZERO servers and the assertion held vacuously."""
    path = os.path.join(ROOT, "tests", "e2e", "conftest.py")
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())

    def dotted(node):
        if isinstance(node, ast.Attribute):
            return f"{dotted(node.value)}.{node.attr}"
        return node.id if isinstance(node, ast.Name) else ""

    out = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        # name -> the dict literal assigned to it inside this fixture
        envs = {t.id: n.value for n in ast.walk(fn) if isinstance(n, ast.Assign)
                for t in n.targets
                if isinstance(t, ast.Name) and isinstance(n.value, ast.Dict)}
        for call in ast.walk(fn):
            if not (isinstance(call, ast.Call) and dotted(call.func) == "subprocess.Popen"):
                continue
            kw = next((k.value for k in call.keywords if k.arg == "env"), None)
            if isinstance(kw, ast.Name):
                kw = envs.get(kw.id)
            out.append((fn.name, call.lineno, kw))
    return out


def test_every_e2e_fixture_server_boots_with_the_shared_pins():
    """Each e2e fixture launches a real `python webreview/app.py`, so each one starts a
    real scheduler over its own data dir AND resolves OUT itself. They are inert only
    because no fixture happens to seed `enabled: true` today — one seeded automation and
    a CI run would scrape a paid API or mail a customer. Same category as the forgotten
    preview instance (#262). Every one of them must unpack the shared pins (which carry
    WEBREVIEW_NO_SCHEDULER) and set its own WEBREVIEW_OUT."""
    servers = _e2e_server_envs()
    assert len(servers) >= 13, f"only {len(servers)} fixture servers found — parser drift?"
    for name, lineno, env in servers:
        where = f"tests/e2e/conftest.py:{lineno} ({name})"
        assert isinstance(env, ast.Dict), f"{where}: Popen without an inspectable env dict"
        unpacked = [ast.unparse(v) for k, v in zip(env.keys, env.values) if k is None]
        keys = [k.value for k in env.keys if isinstance(k, ast.Constant)]
        assert "_AUTH_ENV" in unpacked, f"{where}: env does not unpack **_AUTH_ENV"
        assert "WEBREVIEW_OUT" in keys, f"{where}: env does not pin WEBREVIEW_OUT"


def test_the_shared_e2e_pins_switch_the_scheduler_off():
    path = os.path.join(ROOT, "tests", "e2e", "conftest.py")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    assert '"WEBREVIEW_NO_SCHEDULER": "1"' in text, \
        "the shared fixture-server env must pin the scheduler off"


def test_a_manual_run_still_works_without_a_scheduler(monkeypatch):
    """Deliberate boundary: the flag removes the unattended TIMER (what an orphaned
    instance does on its own), not an explicit click by a logged-in human. Three e2e
    fixtures depend on a hermetic „⚡ Spustiť teraz", and blocking it would say the
    forgotten-instance risk lives in the click rather than in the schedule."""
    from tests.conftest import authed_client
    monkeypatch.setenv("WEBREVIEW_NO_SCHEDULER", "1")
    called = []
    monkeypatch.setattr(webapp.RUNNER, "run_now", lambda key: called.append(key) or True)
    r = authed_client().post("/api/automations/posta_uncollected/run")
    assert r.status_code == 200, r.data
    assert called == ["posta_uncollected"]


def test_the_playbook_preview_recipe_disables_the_scheduler():
    """The recipe that produced the four-day orphan must boot without a scheduler."""
    skill = os.path.join(ROOT, ".claude", "skills", "webreview", "SKILL.md")
    with open(skill, encoding="utf-8") as f:
        text = f.read()
    recipe = [ln for ln in text.splitlines() if "WEBREVIEW_PORT=8811" in ln]
    assert recipe, "the throwaway-preview recipe disappeared from the playbook"
    assert all("WEBREVIEW_NO_SCHEDULER=1" in ln for ln in recipe), \
        "the throwaway preview must be booted with WEBREVIEW_NO_SCHEDULER=1"


# --------------------------------------------------------------------------- #
# The state of the scheduler must be VISIBLE, not just logged once at boot
# --------------------------------------------------------------------------- #
def _automations_json():
    from tests.conftest import authed_client
    r = authed_client().get("/api/automations")
    assert r.status_code == 200, r.data
    return r.get_json()


def test_a_running_scheduler_reports_itself_as_running(scheduler_out, monkeypatch):
    """Starts a REAL loop thread (stubbing `RUNNER.start` away is precisely the state
    the API could not tell apart from a healthy one — see the dead-thread test below),
    but over a STUB runner, never the production `RUNNER` (PR #265 third review).

    The production one carries the real mail-sending automations, and starting it
    inside the pytest process was safe only by accident: `tick=30.0` outran the test
    and the fixture dir happened to seed no `automations.json` with `enabled: true`.
    One seeded fixture and a CI run mails a customer — the same category as the
    forgotten preview instance this whole file exists for. The module global is pinned
    through monkeypatch so the test cannot leave the process reporting a scheduler it
    did not start."""
    monkeypatch.setattr(webapp, "SCHEDULER_INTENT", webapp.SCHEDULER_INTENT)
    stub = AutomationRunner(
        str(scheduler_out / "automations.json"),
        [Automation(key="stub", name="Stub", schedule={"daily_at": "09:00",
                                                       "tz": "Europe/Bratislava"},
                    run_fn=lambda: {"ok": True})],   # touches nothing outside the test
        tick=30.0)
    monkeypatch.setattr(webapp, "RUNNER", stub)
    try:
        assert webapp._start_scheduler() is True
        assert _automations_json()["scheduler"] == "running"
    finally:
        stub.stop()


def test_a_scheduler_whose_thread_is_gone_stops_claiming_to_run(scheduler_out, monkeypatch):
    """`SCHEDULER_STATE` was assigned once at boot and never re-derived, so a runner
    loop that died (an unhandled error escaping the thread, a stop nobody restarted)
    left /api/automations reporting „running" forever — the healthy-looking tab this
    whole banner exists to prevent, one level deeper (PR #265 second review)."""
    monkeypatch.setattr(webapp, "SCHEDULER_INTENT", webapp.SCHEDULER_INTENT)
    monkeypatch.setattr(webapp.RUNNER, "start", lambda: None)   # a loop that never runs
    assert webapp._start_scheduler() is True
    assert webapp.RUNNER.is_alive() is False
    assert _automations_json()["scheduler"] == "dead"


def test_a_blocked_scheduler_is_visible_in_the_api(scheduler_out, monkeypatch):
    """The boot ERROR line is the ONLY signal today, and `next_run` comes straight from
    the persisted state file — so the tab renders every enabled automation with a healthy
    future „Ďalší beh" while nothing will ever fire. That is the same silent business
    failure the store guard exists to prevent, one level up (PR #265 review)."""
    monkeypatch.setattr(webapp, "SCHEDULER_INTENT", webapp.SCHEDULER_INTENT)
    monkeypatch.setattr(webapp.RUNNER, "start", lambda: None)
    holder_fd = os.open(str(scheduler_out / ".scheduler.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(holder_fd, fcntl.LOCK_EX)
    try:
        assert webapp._start_scheduler() is False
        assert _automations_json()["scheduler"] == "blocked"
    finally:
        fcntl.flock(holder_fd, fcntl.LOCK_UN)
        os.close(holder_fd)


def test_a_preview_instance_reports_its_scheduler_as_off(scheduler_out, monkeypatch):
    monkeypatch.setenv("WEBREVIEW_NO_SCHEDULER", "1")
    monkeypatch.setattr(webapp, "SCHEDULER_INTENT", webapp.SCHEDULER_INTENT)
    monkeypatch.setattr(webapp.RUNNER, "start", lambda: None)
    assert webapp._start_scheduler() is False
    assert _automations_json()["scheduler"] == "off"


def test_the_frontend_renders_the_scheduler_warning():
    """The API field is only half the fix — the manager must SEE it. Pinned structurally
    so the banner cannot be dropped while the endpoint keeps reporting."""
    with open(os.path.join(ROOT, "webreview", "static", "app.js"), encoding="utf-8") as f:
        js = f.read()
    with open(os.path.join(ROOT, "webreview", "templates", "index.html"),
              encoding="utf-8") as f:
        html = f.read()
    assert 'id="schedWarn"' in html, "no banner element on the page"
    assert "schedWarn" in js and "SCHEDULER" in js, "app.js never fills the banner in"
    assert "sa nespustia" in js, "the banner must say plainly that nothing will run"
    assert "'dead'" in js or '"dead"' in js, "a died-in-flight scheduler renders no banner"


def _js_function_body(js: str, signature: str) -> str:
    """Source of the function that starts with `signature`, by brace matching."""
    start = js.index(signature)
    i = js.index("{", start)
    depth = 0
    for j in range(i, len(js)):
        if js[j] == "{":
            depth += 1
        elif js[j] == "}":
            depth -= 1
            if depth == 0:
                return js[i:j + 1]
    raise AssertionError(f"unbalanced braces after {signature!r}")


def test_the_frontend_fails_closed_when_the_automation_state_is_unreadable():
    """C4 fails closed on the SERVER (503 + a Slovak repair message over a corrupt
    `automations.json`) — and the tab rendered that answer as the clean first-run state
    the guard exists to prevent. `loadAutomations` never checked the status, and the
    global fetch wrapper only handles 401, so `j.automations || []` and
    `j.scheduler || 'running'` yielded „no automations configured, scheduler healthy"
    while every reminder mail, pošta escalation and hourly sync was off —
    `AutomationStateCorrupt`'s own banned outcome, one layer up (third review, I3)."""
    with open(os.path.join(ROOT, "webreview", "static", "app.js"), encoding="utf-8") as f:
        js = f.read()
    body = _js_function_body(js, "async function loadAutomations()")
    assert re.search(r"\.ok\b|\.status\b", body), \
        "loadAutomations ignores the HTTP status — a 503 renders as a healthy tab"
    assert "corrupt" in body, "a fail-closed answer must set a distinct scheduler state"
    assert re.search(r"\berror\b", body), "the server's repair message is never read"
    warn = _js_function_body(js, "function renderSchedulerWarning(")
    assert "corrupt" in warn, "the corrupt state renders no banner"
