import json
import logging
from datetime import datetime, timedelta, timezone

import pytest
from webreview import app as webapp


@pytest.fixture
def pend(tmp_path, monkeypatch):
    p = tmp_path / "pending_shoptet.json"
    monkeypatch.setattr(webapp, "PENDING_SHOPTET", str(p))
    return p


@pytest.fixture
def claim(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "CYCLE_CLAIM", str(tmp_path / ".cycle.lock"))


def test_queue_shoptet_fields_persists_and_counts(pend):
    n = webapp.queue_shoptet_fields(
        "parovania_eshop", "code;pairCode;internalNote",
        [["A", "P", "https://x"]])
    assert n == 1
    d = json.loads(pend.read_text(encoding="utf-8"))
    assert d["A"]["fields"]["internalNote"]["value"] == "https://x"
    assert d["A"]["fields"]["internalNote"]["queued_at"]


def test_queueing_twice_keeps_both_sources(pend):
    webapp.queue_shoptet_fields("parovania_eshop", "code;pairCode;internalNote",
                                [["A", "P", "https://x"]])
    webapp.queue_shoptet_fields("restock_skladom",
                                "code;pairCode;availabilityInStock",
                                [["A", "P", "Skladom"]])
    d = webapp._load_pending()
    assert set(d["A"]["fields"]) == {"internalNote", "availabilityInStock"}


def test_an_unreadable_table_refuses_the_write_rather_than_wiping_it(pend):
    """A table we cannot parse is NOT an empty table — writing on top of it would
    silently drop everything already queued. The refusal comes from
    `_atomic_write_json(protect=True)` inside `_save_pending`
    (`webapp.StoreWipeRefused`), not from a hand-rolled check in
    `queue_shoptet_fields` — so this pins the exact exception type, that the file on
    disk is left byte-for-byte untouched, and that the unreadable bytes are preserved
    in a quarantine copy before the write is refused."""
    pend.write_text("{ this is not json", encoding="utf-8")
    before = pend.read_bytes()
    with pytest.raises(webapp.StoreWipeRefused):
        webapp.queue_shoptet_fields("s", "code;pairCode;internalNote",
                                    [["A", "P", "u"]])
    assert pend.read_bytes() == before                      # original untouched
    assert list(pend.parent.glob("pending_shoptet.json.corrupt-*")), \
        "the unreadable bytes must be preserved before the write is refused"


def test_queueing_a_new_code_does_not_wipe_what_is_already_waiting(pend):
    """The whole point of the table: independent producers queue at any time, so
    queueing a DIFFERENT code must never disturb what an EARLIER call already put on
    disk and is waiting for the next hourly upload (the risk #299 exists to end)."""
    webapp.queue_shoptet_fields("parovania_eshop", "code;pairCode;internalNote",
                                [["B", "P", "https://existing"]])
    existing_b = webapp._load_pending()["B"]

    webapp.queue_shoptet_fields("restock_skladom",
                                "code;pairCode;availabilityInStock",
                                [["A", "P", "Skladom"]])

    d = webapp._load_pending()
    assert d["B"] == existing_b
    assert d["A"]["fields"]["availabilityInStock"]["value"] == "Skladom"


def test_the_whole_read_modify_write_runs_under_the_store_lock(pend, monkeypatch):
    """Pins the CAUSE, not the symptom. `_lock` is depth-counted, so a spy on
    `_save_pending` observes depth 0 the moment the write is moved outside its
    `with _lock:` block — the exact shape a future refactor could introduce."""
    depths = []
    real = webapp._save_pending

    def spy(d):
        depths.append(webapp._lock._depth)
        return real(d)
    monkeypatch.setattr(webapp, "_save_pending", spy)

    n = webapp.queue_shoptet_fields(
        "parovania_eshop", "code;pairCode;internalNote",
        [["A", "P", "https://x"]])

    assert n == 1
    assert depths == [1], (
        "_save_pending ran while the store lock was NOT held", depths)


def test_the_claim_is_exclusive_and_reports_busy_while_held(claim):
    with webapp._shoptet_cycle_claim() as got:
        assert got is True
        assert webapp._cycle_busy() is True
        with webapp._shoptet_cycle_claim() as second:
            assert second is False
    assert webapp._cycle_busy() is False


def test_the_claim_is_released_even_when_the_body_raises(claim):
    with pytest.raises(ValueError):
        with webapp._shoptet_cycle_claim():
            raise ValueError("boom")
    assert webapp._cycle_busy() is False


# --------------------------------------------------------------------------- #
# Task 6 (#299) — the hourly cycle itself: run_shoptet_upload.
# --------------------------------------------------------------------------- #
@pytest.fixture
def cycle(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "PENDING_SHOPTET", str(tmp_path / "pending.json"))
    monkeypatch.setattr(webapp, "CYCLE_CLAIM", str(tmp_path / ".cycle.lock"))
    # #299 opravné kolo 1 review C1 — `run_shoptet_upload` now reads
    # `RUNNER.status()` (via `_stale_producer_warnings`/`_disabled_producer_names`),
    # which is backed by the CROSS-PROCESS `automations.json` state — isolate it
    # exactly like `automations_iso` does below, or the session-wide default file
    # (the backend suite only isolates `WEBREVIEW_OUT` once, for the whole run)
    # would leak `enabled`/`last_run` between unrelated tests. With a FRESH,
    # empty state file every automation defaults to disabled (the #93 contract's
    # own default), so every existing test below keeps seeing an empty
    # `warnings`/`producers_disabled` contribution from this unless it opts in.
    monkeypatch.setattr(webapp.RUNNER, "state_path", str(tmp_path / "automations.json"))
    calls = {"import": [], "run_sync": []}
    monkeypatch.setattr(webapp, "_import_rows_chunked",
                        lambda rows, header, dry, prefix, csv_safe=False, timeout=900: (
                            calls["import"].append((header, [list(r) for r in rows], csv_safe))
                            or {"ok": True, "partial": False,
                                "success_codes": {r[0] for r in rows},
                                "partial_codes": set(), "partial_failed": 0,
                                "chunks_total": 1, "chunks_ok": 1,
                                "processed": len(rows), "updated": len(rows),
                                "failed": 0, "rc": 0, "error_detail": None,
                                "stdout_tail": "", "err": "", "unreadable": False}))
    monkeypatch.setattr(webapp, "_export_row_verdicts",
                        lambda rows, note_col=2: {"confirmed": set(), "absent": set()})
    # #299 review N3: the cycle now also asks `_export_age_s()` whether to skip
    # the PRE-import download. Default to "unknown age" (never fresh) so every
    # test below keeps its pre-N3 behaviour (PRE-import download always runs)
    # regardless of the REAL data/products.csv on whatever machine runs this
    # suite — a test that wants the freshness branch overrides this itself.
    monkeypatch.setattr(webapp, "_export_age_s", lambda: None)
    # #299 review I3: the cycle now runs everything through the SYNCHRONOUS
    # RUNNER.run_sync (never the fire-and-forget run_now) — see automation_runner.py.
    monkeypatch.setattr(webapp.RUNNER, "run_sync",
                        lambda key: calls["run_sync"].append(key) or True)
    # (Opravné kolo 1 review I2 removed the cycle's own producer-running loop, so
    # `RUNNER.status()` is no longer read by run_shoptet_upload at all — the old
    # `RUNNER.status` stub that lived here has nothing left to serve.)
    return calls


# ── #299 Task 11 — "hlasné tiché smrti": say out loud what a run could not do ─ #
def test_unconfirmed_rows_make_the_run_degraded_with_a_slovak_warning(cycle, monkeypatch):
    # The brief's own literal lambda signature (`rows, header, dry, prefix,
    # timeout=900`) predates M3's `csv_safe=True` kwarg on the real call site
    # (`run_shoptet_upload`'s `_import_rows_chunked(rows, header, False,
    # prefix="import_sync_", csv_safe=True, timeout=900)`) — without `csv_safe`
    # in the signature this stub raises `TypeError: unexpected keyword argument
    # 'csv_safe'` the moment the cycle actually calls it. Matches the `cycle`
    # fixture's own stub signature instead.
    monkeypatch.setattr(webapp, "_import_rows_chunked",
                        lambda rows, header, dry, prefix, csv_safe=False, timeout=900: {
                            "ok": False, "partial": True, "success_codes": set(),
                            "partial_codes": {"A"}, "partial_failed": 1,
                            "chunks_total": 1, "chunks_ok": 0, "chunks_partial": 1,
                            "rows_ok": 0, "rows_partial": 1, "processed": 0,
                            "updated": 0, "failed": 1, "rc": 1, "error_detail": None,
                            "stdout_tail": "", "err": "", "unreadable": False})
    webapp.queue_shoptet_fields("parovania_eshop", "code;pairCode;internalNote",
                                [["A", "P", "u"]])
    res = webapp.run_shoptet_upload()
    assert res["degraded"] is True
    assert any("nepotvrdil" in w for w in res["warnings"])


# ── #299 záverečná recenzia I2 — a hard chunk failure (login/auth, a timeout ── #
# ── whose answer could not even be read) must say so, not collapse into the ── #
# ── SAME generic sentence a merely stale/blocked queue gets. ───────────────── #
def test_a_hard_chunk_failure_reports_the_REAL_reason_not_a_generic_sentence(
        cycle, monkeypatch):
    monkeypatch.setattr(webapp, "_import_rows_chunked",
                        lambda rows, header, dry, prefix, csv_safe=False, timeout=900: {
                            "ok": False, "partial": False, "success_codes": set(),
                            "partial_codes": set(), "partial_failed": 0,
                            "chunks_total": 1, "chunks_ok": 0, "chunks_partial": 0,
                            "rows_ok": 0, "rows_partial": 0, "processed": 0,
                            "updated": 0, "failed": 0, "rc": 1,
                            "error_detail": "prihlásenie zlyhalo", "stdout_tail": "",
                            "err": "", "unreadable": False})
    webapp.queue_shoptet_fields("parovania_eshop", "code;pairCode;internalNote",
                                [["A", "P", "u"]])

    res = webapp.run_shoptet_upload()

    assert res["ok"] is False
    assert "prihlásenie zlyhalo" in res["error"], res["error"]
    assert res["error"] != "nepotvrdené alebo zablokované riadky", res["error"]


# ── #299 záverečná recenzia I3 — restock_skladom/stock_skladom are the FIRST ── #
# ── two producers a manager enables in the rollout order, so their card text ── #
# ── is the first thing he reads; it must never promise an eshop write that ─── #
# ── this migration turned into a queue instead. ─────────────────────────────  #
def test_restock_and_stock_skladom_descriptions_promise_queueing_not_a_write():
    for key in ("restock_skladom", "stock_skladom"):
        text = webapp.AUTOMATION_DESCRIPTIONS[key]
        assert "rovno" not in text, (key, text)
        assert "spoločnej tabuľky čakajúcich zmien" in text, (key, text)
        assert "Sync do Shoptetu" in text, (key, text)


# ── #299 opravné kolo 1 review C1 (Critical) — replaces the deleted queue-based ─
# ── streak signal ("3 hourly cycles with 0 fields of its own queued"), which ── #
# ── fired on EVERY healthy install: these producers run DAILY, this drain runs ─
# ── HOURLY, and a confirmed field drops out of the queue on the very next ──── #
# ── settle — so 3 empty hourly cycles was the NORMAL state, not a symptom. ─── #
# ── The replacement measures ONLY `RUNNER.status()` — last_run + enabled — ─── #
# ── never the queue at all. `_fake_status` below stands in for RUNNER.status() ─
# ── so each test controls exactly one producer's enabled/last_run without ──── #
# ── touching the other four or any real automation state. ──────────────────── #
def _fake_status(overrides):
    """A `RUNNER.status()`-shaped list built from the REAL registered automations
    (so names/keys always match production), with `enabled`/`last_run`/
    `last_result`/`next_run` overridden per key. Every other field is a
    harmless default — `_stale_producer_warnings`/`_disabled_producer_names`
    never read them.

    #299 opravné kolo 2 review N1 — `next_run` used to be hard-coded to `""`
    here regardless of `enabled`, which is NOT what the real `AutomationRunner.
    status()` returns (it persists `next_run` the moment `set_enabled(True)`
    runs) — every test below that cares about the grace window must be able
    to set it explicitly."""
    out = []
    for key, a in webapp.RUNNER.automations.items():
        ov = overrides.get(key, {})
        out.append({"key": key, "name": a.name, "enabled": ov.get("enabled", False),
                    "running": False, "last_run": ov.get("last_run", ""),
                    "last_status": ov.get("last_status", ""), "last_error": "",
                    "last_result": ov.get("last_result", {}),
                    "next_run": ov.get("next_run", "")})
    return out


def test_a_healthy_daily_producer_does_not_alarm_on_repeated_hourly_drains(
        cycle, monkeypatch):
    """The exact false-positive the deleted signal reproduced: an ENABLED
    producer that just ran (well inside its own daily schedule) must not
    degrade the cycle no matter how many times the hourly drain itself runs
    with nothing new queued."""
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    monkeypatch.setattr(webapp.RUNNER, "status", lambda: _fake_status(
        {k: {"enabled": True, "last_run": now_iso}
         for k in webapp.PRODUCER_QUEUE_KEYS}))
    for _ in range(4):
        res = webapp.run_shoptet_upload()
    assert res["degraded"] is False
    assert res["warnings"] == []
    assert res["producers_disabled"] == []


def test_an_enabled_producer_stale_past_its_own_schedule_is_reported(cycle, monkeypatch):
    old = (datetime.now(timezone.utc)
           - timedelta(hours=24 * webapp.PRODUCER_STALE_RUN_MULTIPLIER + 1)
           ).isoformat(timespec="seconds")
    monkeypatch.setattr(webapp.RUNNER, "status", lambda: _fake_status(
        {"parovania_eshop": {"enabled": True, "last_run": old}}))
    res = webapp.run_shoptet_upload()
    assert res["degraded"] is True
    assert any("Párovania" in w and "zapnutá" in w for w in res["warnings"]), res["warnings"]


def test_an_enabled_producer_well_within_its_schedule_is_not_reported(cycle, monkeypatch):
    recent = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="seconds")
    monkeypatch.setattr(webapp.RUNNER, "status", lambda: _fake_status(
        {"parovania_eshop": {"enabled": True, "last_run": recent}}))
    res = webapp.run_shoptet_upload()
    assert res["degraded"] is False
    assert res["warnings"] == []


def test_an_enabled_producer_that_has_never_run_is_reported(cycle, monkeypatch):
    """#299 opravné kolo 2 review N1 — `next_run` must have genuinely passed
    ITS OWN grace threshold too, not just be in the past: 50h (24h daily
    schedule × 2 = 48h threshold, plus 2h margin) since the scheduled first
    run, still never having run."""
    stale_next_run = (datetime.now(timezone.utc)
                      - timedelta(hours=24 * webapp.PRODUCER_STALE_RUN_MULTIPLIER + 2)
                      ).isoformat(timespec="seconds")
    monkeypatch.setattr(webapp.RUNNER, "status", lambda: _fake_status(
        {"grube_externalcode": {"enabled": True, "last_run": "",
                                 "next_run": stale_next_run}}))
    res = webapp.run_shoptet_upload()
    assert res["degraded"] is True
    assert any("GRUBE" in w and "ešte ani raz nebežala" in w for w in res["warnings"]), (
        res["warnings"])


# ── #299 opravné kolo 2 review N1 (Important) — a freshly ENABLED producer ─── #
# ── must not warn the instant it appears here: a daily producer's first run ── #
# ── is up to ~24h away, and the OLD "empty last_run = warn immediately" rule ─ #
# ── lit the card red for a whole day right after the manager did the ──────── #
# ── correct thing (clicked ▶ Štart) — precisely the moment this rollout plan ─
# ── has him watching it. `AutomationRunner.set_enabled` persists `next_run` ── #
# ── at the moment of enabling; the fix stays silent until next_run PLUS the ── #
# ── SAME staleness threshold has genuinely passed. ─────────────────────────── #
def test_an_enabled_producer_that_has_never_run_and_whose_next_run_is_still_ahead_is_silent(
        cycle, monkeypatch):
    """The exact deploy-day scenario: enabled moments ago, next scheduled run
    still ~20h away (well within a daily schedule's window) — must not warn
    or degrade the cycle at all."""
    soon = (datetime.now(timezone.utc) + timedelta(hours=20)).isoformat(timespec="seconds")
    monkeypatch.setattr(webapp.RUNNER, "status", lambda: _fake_status(
        {"grube_externalcode": {"enabled": True, "last_run": "", "next_run": soon}}))
    res = webapp.run_shoptet_upload()
    assert res["degraded"] is False
    assert res["warnings"] == []


def test_an_enabled_producer_that_has_never_run_stays_silent_until_grace_past_next_run(
        cycle, monkeypatch):
    """`next_run` itself already passed (the scheduled first run came and
    went) but the EXTRA grace threshold on top of it has not — still silent,
    proving the grace is `next_run + threshold`, not just `next_run` alone."""
    just_past_next_run = (datetime.now(timezone.utc) - timedelta(hours=10)
                          ).isoformat(timespec="seconds")
    monkeypatch.setattr(webapp.RUNNER, "status", lambda: _fake_status(
        {"grube_externalcode": {"enabled": True, "last_run": "",
                                 "next_run": just_past_next_run}}))
    res = webapp.run_shoptet_upload()
    assert res["degraded"] is False
    assert res["warnings"] == []


def test_an_enabled_producer_that_has_never_run_with_no_next_run_fires_immediately(
        cycle, monkeypatch):
    """A hand-edited state file could set `enabled: true` without ever going
    through `set_enabled` (which always persists `next_run`) — with nothing
    here to grant grace against, this falls back to the pre-N1 immediate
    warning (fail-safe direction), never a silent indefinite wait."""
    monkeypatch.setattr(webapp.RUNNER, "status", lambda: _fake_status(
        {"grube_externalcode": {"enabled": True, "last_run": ""}}))   # next_run defaults ""
    res = webapp.run_shoptet_upload()
    assert res["degraded"] is True
    assert any("GRUBE" in w and "ešte ani raz nebežala" in w for w in res["warnings"]), (
        res["warnings"])


def test_an_enabled_producer_that_has_never_run_with_an_unparsable_next_run_fires_immediately(
        cycle, monkeypatch):
    monkeypatch.setattr(webapp.RUNNER, "status", lambda: _fake_status(
        {"grube_externalcode": {"enabled": True, "last_run": "",
                                 "next_run": "not-a-date"}}))
    res = webapp.run_shoptet_upload()
    assert res["degraded"] is True
    assert any("GRUBE" in w and "ešte ani raz nebežala" in w for w in res["warnings"]), (
        res["warnings"])


def test_an_enabled_producer_with_an_unparsable_last_run_is_reported_loud(
        cycle, monkeypatch):
    monkeypatch.setattr(webapp.RUNNER, "status", lambda: _fake_status(
        {"restock_skladom": {"enabled": True, "last_run": "not-a-date"}}))
    res = webapp.run_shoptet_upload()
    assert res["degraded"] is True
    assert any("nečitateľný" in w for w in res["warnings"]), res["warnings"]


# ── #299 opravné kolo 2 review N2 (Minor) — pins the ABSOLUTE 48h threshold ── #
# ── (24h daily schedule × PRODUCER_STALE_RUN_MULTIPLIER=2) so a mutation of ── #
# ── the constant itself (e.g. →1000, "alarm dead for 1000 days") is caught: ── #
# ── `test_an_enabled_producer_stale_past_its_own_schedule_is_reported` above ─ #
# ── derives its age FROM the constant, so it moves in lockstep with any ────── #
# ── mutation of it and never actually pins an alarm-worthy VALUE — the exact ─ #
# ── shape of test that let PRODUCER_STALE_RUN_MULTIPLIER=1000 leave 159 ────── #
# ── tests green. ────────────────────────────────────────────────────────────── #
def test_a_stale_producer_alarm_fires_past_48h_and_stays_silent_at_47h(cycle, monkeypatch):
    just_under = (datetime.now(timezone.utc) - timedelta(hours=47)
                 ).isoformat(timespec="seconds")
    monkeypatch.setattr(webapp.RUNNER, "status", lambda: _fake_status(
        {"parovania_eshop": {"enabled": True, "last_run": just_under}}))
    res = webapp.run_shoptet_upload()
    assert res["degraded"] is False, res["warnings"]

    just_over = (datetime.now(timezone.utc) - timedelta(hours=49)
                ).isoformat(timespec="seconds")
    monkeypatch.setattr(webapp.RUNNER, "status", lambda: _fake_status(
        {"parovania_eshop": {"enabled": True, "last_run": just_over}}))
    res = webapp.run_shoptet_upload()
    assert res["degraded"] is True
    assert any("Párovania" in w for w in res["warnings"]), res["warnings"]


def test_a_disabled_producer_is_its_own_category_never_a_warning(cycle, monkeypatch):
    """#299 opravné kolo 1 review C1 — "producent, ktorý je VYPNUTÝ, je vlastná
    kategória (nie chyba, ale nech je to vidieť)": a disabled producer, however
    long it has never run, must not contribute to `warnings`/`degraded` — it is
    surfaced separately, in `producers_disabled`."""
    monkeypatch.setattr(webapp.RUNNER, "status", lambda: _fake_status(
        {"stock_skladom": {"enabled": False, "last_run": ""}}))
    res = webapp.run_shoptet_upload()
    assert res["degraded"] is False
    assert res["warnings"] == []
    assert webapp.RUNNER.automations["stock_skladom"].name in res["producers_disabled"]


def test_a_renamed_or_removed_producer_key_never_crashes_the_cycle(cycle, monkeypatch):
    """#299 opravné kolo 1 review m5 — the OLD code indexed
    `RUNNER.automations[k].name` over a hard-coded key list and would raise
    `KeyError` the moment a producer key was renamed/removed. The replacement
    reads names straight off `RUNNER.status()` and simply skips a key that is
    no longer a registered automation.

    #299 opravné kolo 2 review N6 — the ORIGINAL version of this test left
    EVERY producer disabled (the `cycle` fixture's fresh, isolated
    `automations.json` default), so the loop body PAST the `enabled` guard —
    where m5's actual fix lives (`automation = RUNNER.automations.get(key)`,
    `name = s.get("name") or key`, the `last_run` parse) — never executed for
    ANY key, real or phantom; the cycle finished before it got anywhere near
    a name. The test therefore passed against a hypothetical regression back
    to unguarded `RUNNER.automations[key]` indexing inside that body just as
    readily as against the real fix. This now ALSO enables a real producer
    with a fresh `last_run`, so the loop genuinely walks that body — reads
    its `automation`, resolves its `name`, parses its `last_run` — for one
    real key while the renamed/removed key sits right next to it in
    `PRODUCER_QUEUE_KEYS`."""
    monkeypatch.setattr(webapp, "PRODUCER_QUEUE_KEYS",
                        webapp.PRODUCER_QUEUE_KEYS + ("no_longer_exists",))
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    monkeypatch.setattr(webapp.RUNNER, "status", lambda: _fake_status(
        {"parovania_eshop": {"enabled": True, "last_run": now_iso}}))
    res = webapp.run_shoptet_upload()          # must not raise
    assert res["ok"] is True
    assert res["degraded"] is False, res["warnings"]   # the real producer is healthy


# ── #299 Task 11 — the NAJDÔLEŽITEJŠIA POŽIADAVKA: the hourly cycle deploys ─── #
# ── DISABLED and is the ONLY path anything reaches the eshop by. The alarm ─── #
# ── below must fire from the pending table + the `enabled` flag ALONE — it ─── #
# ── must NEVER need the disabled cycle itself to run (a safeguard guarding ─── #
# ── itself is no safeguard at all). ─────────────────────────────────────────── #
@pytest.fixture
def automations_iso(tmp_path, monkeypatch):
    """`_queue_stale_while_disabled_warning` reads `RUNNER`'s `enabled` flag
    through `api_automations`, which persists to the CROSS-PROCESS automations
    state — isolate it exactly like `test_webreview_automations.py`'s own `iso`
    fixture does, or the session-wide default `automations.json` would leak
    `enabled` between unrelated tests (the backend suite only isolates
    `WEBREVIEW_OUT` once, for the whole session — conftest.py's own comment)."""
    monkeypatch.setattr(webapp.RUNNER, "state_path", str(tmp_path / "automations.json"))


def _queue_one_field(pend, queued_at):
    pend.write_text(json.dumps({"A": {"fields": {"internalNote": {
        "value": "u", "source": "parovania_eshop", "queued_at": queued_at}}}}),
        encoding="utf-8")


def test_queue_stale_warning_is_empty_when_the_cycle_is_enabled(pend):
    _queue_one_field(pend, "2000-01-01T00:00:00+00:00")   # ancient, but ENABLED
    assert webapp._queue_stale_while_disabled_warning(enabled=True) == ""


def test_queue_stale_warning_is_empty_when_the_queue_is_empty(pend):
    assert webapp._queue_stale_while_disabled_warning(enabled=False) == ""


def test_queue_stale_warning_is_empty_while_still_fresh(pend):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _queue_one_field(pend, now)
    assert webapp._queue_stale_while_disabled_warning(enabled=False) == ""


def test_queue_stale_warning_fires_past_the_threshold_while_disabled(pend):
    old = (datetime.now(timezone.utc)
           - timedelta(seconds=webapp.QUEUE_STALE_WHILE_DISABLED_AFTER_S + 60)
           ).isoformat(timespec="seconds")
    _queue_one_field(pend, old)
    w = webapp._queue_stale_while_disabled_warning(enabled=False)
    assert w and "vypnutý" in w and "Sync do Shoptetu" in w


def test_queue_stale_warning_an_unparsable_timestamp_fires_loud_not_silent(pend):
    """A corrupt/unexpected `queued_at` must never read as "must be fine" — the
    fail-safe direction for THIS alarm is loud, the opposite of every other
    degrade-to-empty reader in this module (deliberately: staying quiet here is
    exactly the silent death this task exists to end)."""
    pend.write_text(json.dumps({"A": {"fields": {"internalNote": {
        "value": "u", "source": "parovania_eshop", "queued_at": "not-a-date"}}}}),
        encoding="utf-8")
    assert webapp._queue_stale_while_disabled_warning(enabled=False) != ""


# ── #299 opravné kolo 1 review I1 (Important) — a genuinely UNREADABLE queue ── #
def test_queue_stale_warning_an_unreadable_pending_table_fires_loud_not_silent(pend):
    """Before this fix `_load_pending` degraded a corrupt/unparsable
    `pending_shoptet.json` to `{}` — indistinguishable from a legitimately
    EMPTY queue, so this alarm silently read "nothing waiting" for the one
    input state it genuinely cannot judge. Loud is the correct direction:
    the file IS there, we just cannot read it."""
    pend.write_text("{ this is not json", encoding="utf-8")
    w = webapp._queue_stale_while_disabled_warning(enabled=False)
    assert w != "" and "nedá prečítať" in w, w


def test_queue_stale_warning_a_missing_file_stays_quiet_a_fresh_install(pend):
    """The mirror check for I1: a MISSING file (fresh install, nothing ever
    queued) is legitimately silent — `os.path.exists` is what tells the two
    apart, never `from_disk` alone (missing and corrupt both read `from_disk=
    False`)."""
    assert not pend.exists()
    assert webapp._queue_stale_while_disabled_warning(enabled=False) == ""


# ── #299 opravné kolo 1 review I2 (Important) — a field with NO queued_at ───── #
def test_queue_stale_warning_a_missing_timestamp_fires_loud_not_silent(pend):
    """`if f.get("queued_at")` used to silently DROP a field with no timestamp
    from the "oldest" computation — if EVERY field lacked one, the whole alarm
    read "nothing queued" even with real work waiting. Must behave exactly
    like an unparsable timestamp: loud."""
    pend.write_text(json.dumps({"A": {"fields": {"internalNote": {
        "value": "u", "source": "parovania_eshop"}}}}), encoding="utf-8")   # no queued_at at all
    w = webapp._queue_stale_while_disabled_warning(enabled=False)
    assert w != "" and "čitateľný čas zaradenia" in w, w


# ── #299 opravné kolo 2 review N3 (Minor) — valid JSON, but a SHAPE this ────── #
# ── code never wrote: before this fix each of these raised straight out of ─── #
# ── `.get()`/`.values()` (AttributeError/TypeError, never `ValueError`, so ─── #
# ── `except ValueError` never caught them) — uncaught, `/api/automations` ──── #
# ── returned 500 and EVERY automation card vanished from the manager's ─────── #
# ── screen, not just this one. Must degrade to a loud warning instead. ─────── #
def test_queue_stale_warning_an_entry_that_is_not_a_dict_fires_loud_not_crashes(pend):
    pend.write_text(json.dumps({"A": "x"}), encoding="utf-8")
    w = webapp._queue_stale_while_disabled_warning(enabled=False)   # must not raise
    assert w != "" and "poškoden" in w, w


def test_queue_stale_warning_a_fields_that_is_a_list_fires_loud_not_crashes(pend):
    pend.write_text(json.dumps({"A": {"fields": ["x"]}}), encoding="utf-8")
    w = webapp._queue_stale_while_disabled_warning(enabled=False)   # must not raise
    assert w != "" and "poškoden" in w, w


def test_queue_stale_warning_a_field_entry_that_is_not_a_dict_fires_loud_not_crashes(pend):
    pend.write_text(json.dumps({"A": {"fields": {"n": "x"}}}), encoding="utf-8")
    w = webapp._queue_stale_while_disabled_warning(enabled=False)   # must not raise
    assert w != "" and "poškoden" in w, w


def test_queue_stale_warning_a_numeric_queued_at_fires_loud_not_crashes(pend):
    """`datetime.fromisoformat(12345)` raises `TypeError`, not `ValueError` —
    the old `except ValueError` let it straight through."""
    pend.write_text(json.dumps({"A": {"fields": {"internalNote": {
        "value": "u", "source": "s", "queued_at": 12345}}}}), encoding="utf-8")
    w = webapp._queue_stale_while_disabled_warning(enabled=False)   # must not raise
    assert w != "" and "poškoden" in w, w


# ── #299 opravné kolo 2 review N4 (Minor) — a `queued_at` in the FUTURE (a ─── #
# ── clock step, an NTP jump, a hand-edited value) gives a NEGATIVE age; every ─
# ── comparison below is against a POSITIVE threshold, so a negative age read ── #
# ── as "very fresh" and, as the ONLY field in the table, silenced the alarm ── #
# ── entirely — the one remaining branch that stayed quiet on data this ─────── #
# ── function cannot trust. ──────────────────────────────────────────────────── #
def test_queue_stale_warning_a_future_queued_at_fires_loud_not_silent(pend):
    future = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat(timespec="seconds")
    _queue_one_field(pend, future)
    w = webapp._queue_stale_while_disabled_warning(enabled=False)
    assert w != "", "a queued_at in the future must never read as fine"


def test_queue_stale_warning_a_future_queued_at_does_not_hide_a_genuinely_stale_one(pend):
    """The future-dated field must not silently WIN against a genuinely stale
    one either — both must be treated as untrustworthy, loud."""
    old = (datetime.now(timezone.utc)
           - timedelta(seconds=webapp.QUEUE_STALE_WHILE_DISABLED_AFTER_S + 60)
           ).isoformat(timespec="seconds")
    future = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat(timespec="seconds")
    pend.write_text(json.dumps({
        "A": {"fields": {"internalNote": {
            "value": "u", "source": "s", "queued_at": old}}},
        "B": {"fields": {"internalNote": {
            "value": "u", "source": "s", "queued_at": future}}},
    }), encoding="utf-8")
    w = webapp._queue_stale_while_disabled_warning(enabled=False)
    assert w != "", w


# ── #299 opravné kolo 1 review m1 (Minor) — min() over ISO STRINGS is ───────── #
# ── lexicographic, not chronological; a UTC-offset difference (e.g. across a ── #
# ── DST boundary) can sort backwards from the real elapsed time. ────────────── #
def test_queue_stale_warning_compares_REAL_elapsed_time_not_iso_string_order(pend):
    """Two fields, ONE genuinely past the threshold and ONE genuinely well
    inside it — but written with a ±10h UTC-offset SWING (a real, if extreme,
    case of what a DST boundary does on a smaller scale) chosen so the
    genuinely-stale field's ISO STRING sorts AFTER the genuinely-fresh one's,
    the OPPOSITE of their real chronological order. Old code's `min()` over
    the raw strings would pick the fresh field's string as "oldest", read it
    as well inside the threshold, and silence the alarm — even though the
    OTHER field genuinely is stale. This is deliberately NOT a 1-hour DST-sized
    swing (flaky near the exact boundary the review's own wording implies);
    a ±10h swing safely dominates the threshold gap regardless of the moment
    `now()` is captured, while proving the identical class of bug."""
    now = datetime.now(timezone.utc)
    threshold = webapp.QUEUE_STALE_WHILE_DISABLED_AFTER_S
    # genuinely STALE (past the threshold), rendered at UTC+10:00 — its LOCAL
    # wall-clock digits read far HIGHER than the fresh field's below, even
    # though this moment is REALLY earlier.
    really_stale = (now - timedelta(seconds=threshold + 60)).astimezone(
        timezone(timedelta(hours=10)))
    # genuinely FRESH (well inside the threshold), rendered at UTC-10:00 — its
    # LOCAL wall-clock digits read far LOWER, so the raw ISO STRING sorts
    # BEFORE the stale field's string above — exactly backwards from real time.
    really_fresh = (now - timedelta(seconds=60)).astimezone(
        timezone(timedelta(hours=-10)))
    stale_str = really_stale.isoformat(timespec="seconds")
    fresh_str = really_fresh.isoformat(timespec="seconds")
    assert fresh_str < stale_str, (
        "test setup check: the FRESH field's string must sort BEFORE the "
        "STALE field's — otherwise this does not reproduce m1 at all",
        fresh_str, stale_str)
    pend.write_text(json.dumps({
        "A": {"fields": {"internalNote": {
            "value": "u", "source": "s", "queued_at": stale_str}}},
        "B": {"fields": {"internalNote": {
            "value": "u", "source": "s", "queued_at": fresh_str}}},
    }), encoding="utf-8")
    w = webapp._queue_stale_while_disabled_warning(enabled=False)
    assert w != "", (
        "the genuinely stale field must fire the alarm even though its ISO "
        "STRING sorts BEFORE the fresh field's", stale_str, fresh_str)


# ── #299 opravné kolo 1 review C2 (Critical) — `queued_at` must be the time ── #
# ── of the FIRST queue of a field's CURRENT value, never overwritten by a ──── #
# ── later re-queue of the SAME value (the disabled-cycle alarm above reads ── #
# ── exactly this field, so a re-queue that keeps resetting it silences the ── #
# ── alarm forever without the cycle ever actually running). Pure-logic unit ── #
# ── coverage of `shoptet_outbox.queue_fields` itself lives in ──────────────── #
# ── test_shoptet_outbox.py (controlled `now=` values, no wall-clock race); ── #
# ── this end-to-end test proves the ACTUAL regression it fixes: the alarm ─── #
# ── this module owns. ────────────────────────────────────────────────────── #
def test_a_disabled_cycles_stale_alarm_survives_repeated_same_value_requeues_end_to_end(
        pend):
    """The actual regression C2 fixes, proven end to end: an old field, queued
    again and again with the SAME value while the cycle stays disabled (exactly
    `run_parovania_eshop`'s daily re-send of its whole backlog, per the
    review's own repro) — the alarm must stay lit throughout, not go dark the
    moment ANY producer happens to run."""
    old = (datetime.now(timezone.utc)
           - timedelta(seconds=webapp.QUEUE_STALE_WHILE_DISABLED_AFTER_S + 60)
           ).isoformat(timespec="seconds")
    _queue_one_field(pend, old)
    assert webapp._queue_stale_while_disabled_warning(enabled=False) != ""

    # a producer re-queues the identical value — simulating its own daily tick
    # while the cycle is still disabled and never confirms anything
    webapp.queue_shoptet_fields("parovania_eshop", "code;pairCode;internalNote",
                                [["A", "P", "u"]])

    w = webapp._queue_stale_while_disabled_warning(enabled=False)
    assert w != "", "a re-queue of the SAME value must not silence the alarm"


def test_api_automations_surfaces_the_stale_warning_for_shoptet_upload(pend, automations_iso):
    """The end-to-end wiring the sidebar badge + card actually depend on:
    `/api/automations` must carry `queue_stale_warning` on the `shoptet_upload`
    entry EVEN THOUGH that automation has never run (no `run_shoptet_upload`
    call anywhere in this test) — and starting the cycle (never anything else)
    is what silences it on the very next poll."""
    old = (datetime.now(timezone.utc)
           - timedelta(seconds=webapp.QUEUE_STALE_WHILE_DISABLED_AFTER_S + 60)
           ).isoformat(timespec="seconds")
    _queue_one_field(pend, old)
    with webapp.app.test_request_context():
        j = webapp.api_automations().get_json()
    (a,) = [x for x in j["automations"] if x["key"] == "shoptet_upload"]
    assert a["last_run"] == "", "this alarm must fire even if the cycle NEVER ran"
    assert a["queue_stale_warning"]

    # Turning the cycle ON silences the alarm on the very NEXT poll. Nothing
    # else does — this is the "cannot be silenced by renaming state or turning
    # off the reporter" property: `/api/automations` itself is not a toggle,
    # and the warning has no enable/disable knob of its own.
    webapp.RUNNER.set_enabled("shoptet_upload", True)
    with webapp.app.test_request_context():
        j2 = webapp.api_automations().get_json()
    (a2,) = [x for x in j2["automations"] if x["key"] == "shoptet_upload"]
    assert a2["queue_stale_warning"] == ""


def test_an_empty_table_uploads_nothing_and_skips_the_second_download(cycle):
    res = webapp.run_shoptet_upload()
    assert res["ok"] is True
    assert res["sent"] == 0
    assert cycle["import"] == []
    assert res["skipped_second_sync"] is True
    assert cycle["run_sync"].count("shoptet_sync") == 1
    assert res["resynced"] == 1


def test_a_queued_change_goes_up_in_ONE_import_and_leaves_the_table(cycle):
    webapp.queue_shoptet_fields("parovania_eshop", "code;pairCode;internalNote",
                                [["A", "P", "https://x"]])
    webapp.queue_shoptet_fields("restock_skladom",
                                "code;pairCode;availabilityInStock",
                                [["B", "P2", "Skladom"]])
    res = webapp.run_shoptet_upload()
    assert len(cycle["import"]) == 1, "the whole table must ride in ONE import"
    header, rows, csv_safe = cycle["import"][0]
    assert header == "code;pairCode;availabilityInStock;internalNote"
    assert sorted(r[0] for r in rows) == ["A", "B"]
    assert csv_safe is True, "M3 — the combined import must keep the formula-injection guard"
    assert res["sent"] == 2 and res["confirmed"] == 2
    assert webapp._load_pending() == {}
    assert res["skipped_second_sync"] is False
    assert cycle["run_sync"].count("shoptet_sync") == 2
    assert res["resynced"] == 2


def test_a_code_missing_from_the_catalogue_is_blocked_and_kept(cycle, monkeypatch):
    monkeypatch.setattr(webapp, "_export_row_verdicts",
                        lambda rows, note_col=2: {"confirmed": set(), "absent": {"A"}})
    webapp.queue_shoptet_fields("parovania_eshop", "code;pairCode;internalNote",
                                [["A", "P", "https://x"]])
    res = webapp.run_shoptet_upload()
    assert res["blocked"] == 1
    assert cycle["import"] == []
    assert webapp._load_pending()["A"]["blocked"]["reason"] == "not-in-catalog"


# ── #299 záverečná recenzia I1 — a code that never gets a clean chunk ──────── #
# ── confirmation (it keeps sharing a chunk with a row Shoptet permanently ──── #
# ── rejects, so the whole chunk stays "partial" forever) must not hold the ─── #
# ── queue hostage — after STALE_UNCONFIRMED_MIN_ATTEMPTS consecutive ───────── #
# ── unconfirmed runs it is excluded from the NEXT send and reported LOUDLY. ── #
def test_a_code_stuck_unconfirmed_for_many_runs_is_excluded_and_reported(
        cycle, monkeypatch):
    monkeypatch.setattr(webapp, "STALE_UNCONFIRMED_MIN_ATTEMPTS", 2)
    calls = []

    def always_partial(rows, header, dry, prefix, csv_safe=False, timeout=900):
        calls.append([list(r) for r in rows])
        codes = {r[0] for r in rows}
        return {"ok": False, "partial": True, "success_codes": set(),
                "partial_codes": codes, "partial_failed": 1,
                "chunks_total": 1, "chunks_ok": 0, "chunks_partial": 1,
                "rows_ok": 0, "rows_partial": len(rows), "processed": 0,
                "updated": 0, "failed": 1, "rc": 1, "error_detail": None,
                "stdout_tail": "", "err": "", "unreadable": False}
    monkeypatch.setattr(webapp, "_import_rows_chunked", always_partial)
    webapp.queue_shoptet_fields("parovania_eshop", "code;pairCode;internalNote",
                                [["A", "P", "u"]])

    res1 = webapp.run_shoptet_upload()          # attempts: 0 -> 1, not yet stuck
    assert res1["stuck_unconfirmed"] == []
    assert webapp._load_pending()["A"]["attempts"] == 1
    assert len(calls) == 1

    res2 = webapp.run_shoptet_upload()          # attempts: 1 -> 2, still sent this run
    assert res2["stuck_unconfirmed"] == []
    assert webapp._load_pending()["A"]["attempts"] == 2
    assert len(calls) == 2

    res3 = webapp.run_shoptet_upload()          # attempts was 2 >= threshold: excluded
    assert res3["stuck_unconfirmed"] == ["A"]
    assert any("nepotvrdených" in w for w in res3["warnings"]), res3["warnings"]
    assert len(calls) == 2, "the stuck code must not have been sent a 3rd time"
    assert webapp._load_pending()["A"]["blocked"]["reason"] == "stuck-unconfirmed"


def test_the_cycle_refuses_to_run_twice_at_once(cycle):
    with webapp._shoptet_cycle_claim():
        res = webapp.run_shoptet_upload()
    assert res["ok"] is False
    assert res["error"] == "cyklus už beží"          # #299 review M2 — Slovak, like the rest
    assert cycle["run_sync"] == []


def test_resynced_reflects_run_sync_s_ACTUAL_return_value_not_a_hard_coded_guess(cycle, monkeypatch):
    """#299 review I3 — `run_now`'s fire-and-forget True made `resynced` a lie the
    moment two hourly automations with the same interval overlapped (shoptet_sync
    already running for its own schedule). run_sync can genuinely return False
    (another run of the SAME automation already in flight) — resynced must reflect
    that, both for the first download and (when it happens) the second."""
    monkeypatch.setattr(webapp.RUNNER, "run_sync", lambda key: False)
    res = webapp.run_shoptet_upload()
    assert res["resynced"] == 0
    assert res["skipped_second_sync"] is True   # nothing was sent -> no 2nd sync attempted


# ── #299 opravné kolo 1 review m3 (Minor) — a REFUSED catalogue download must ─ #
# ── not count as a real sync, even though `run_shoptet_sync` reports its own ── #
# ── run as `ok` for it (PR #280's deliberate non-fatal `ExportDownloadRefused` ─
# ── — the run keeps serving the on-disk copy). Runs the REAL `shoptet_sync` ─── #
# ── automation (its `run_fn` stubbed) rather than the `cycle` fixture's ────── #
# ── `RUNNER.run_sync` stub, so its state is genuinely persisted and ────────── #
# ── `_sync_downloaded_fresh_export` reads it back for real. ─────────────────── #
def test_resynced_does_not_count_a_refused_download_as_a_real_sync(
        pend, automations_iso, monkeypatch):
    monkeypatch.setattr(webapp, "CYCLE_CLAIM", str(pend.parent / ".cycle.lock"))
    monkeypatch.setattr(webapp, "_export_age_s", lambda: None)
    monkeypatch.setattr(webapp.RUNNER.automations["shoptet_sync"], "run_fn",
                        lambda: {"catalog_codes": 1, "export_error": "refused: stale"})

    res = webapp.run_shoptet_upload()

    assert res["resynced"] == 0, "a REFUSED download must not be counted as a real sync"
    assert res["skipped_second_sync"] is True     # nothing queued -> nothing sent


def test_resynced_still_counts_a_genuine_successful_download(
        pend, automations_iso, monkeypatch):
    """The other side of m3 — a run that genuinely downloaded (no export_error at
    all) must still count, proving the fix does not just zero `resynced` out
    unconditionally."""
    monkeypatch.setattr(webapp, "CYCLE_CLAIM", str(pend.parent / ".cycle.lock"))
    monkeypatch.setattr(webapp, "_export_age_s", lambda: None)
    monkeypatch.setattr(webapp.RUNNER.automations["shoptet_sync"], "run_fn",
                        lambda: {"catalog_codes": 5})

    res = webapp.run_shoptet_upload()

    assert res["resynced"] == 1


# ── #299 review N3 — shoptet_sync and shoptet_upload share the same 60-minute ─ #
# ── schedule, so a tick that ran shoptet_sync moments ago must not re-fetch ─── #
# ── the 57 MB catalogue a second time via the PRE-import download. ─────────── #

def test_a_fresh_export_skips_the_PRE_import_download_but_not_the_POST_import_one(
        cycle, monkeypatch):
    monkeypatch.setattr(webapp, "_export_age_s", lambda: 5 * 60)  # 5 min old
    webapp.queue_shoptet_fields("parovania_eshop", "code;pairCode;internalNote",
                                [["A", "P", "https://x"]])
    res = webapp.run_shoptet_upload()
    assert cycle["run_sync"].count("shoptet_sync") == 1, (
        "only the POST-import download must have run")
    assert res["resynced"] == 1
    assert res["skipped_second_sync"] is False


def test_a_stale_export_still_runs_the_PRE_import_download(cycle, monkeypatch):
    monkeypatch.setattr(webapp, "_export_age_s", lambda: 20 * 60)  # 20 min old
    res = webapp.run_shoptet_upload()
    assert cycle["run_sync"].count("shoptet_sync") == 1, (
        "the PRE-import download must have run")
    assert res["resynced"] == 1
    assert res["skipped_second_sync"] is True  # empty table -> nothing sent


def test_an_unknown_export_age_is_never_treated_as_fresh(cycle, monkeypatch):
    """No export on disk yet (or a test double that cannot stat it) must NEVER
    be read as "fresh" — the fail-safe direction is always to download."""
    monkeypatch.setattr(webapp, "_export_age_s", lambda: None)
    res = webapp.run_shoptet_upload()
    assert cycle["run_sync"].count("shoptet_sync") == 1
    assert res["resynced"] == 1


def test_the_freshness_boundary_itself_still_counts_as_stale(cycle, monkeypatch):
    # exactly SHOPTET_UPLOAD_SKIP_PRESYNC_FRESHER_THAN_S old is NOT "younger
    # than" the limit -> the download must still run.
    monkeypatch.setattr(webapp, "_export_age_s",
                        lambda: webapp.SHOPTET_UPLOAD_SKIP_PRESYNC_FRESHER_THAN_S)
    res = webapp.run_shoptet_upload()
    assert cycle["run_sync"].count("shoptet_sync") == 1
    assert res["resynced"] == 1


# ── review carry-overs (Task 4 M2 / Task 5 minor / brief note on note_col) ──── #

def test_a_corrupt_pending_table_refuses_the_drain_instead_of_pretending_it_is_empty(cycle):
    """#299 review (Task 4 minor, deferred here as M2): the drain must carry the
    SAME strictness as queueing. A table we cannot parse is NOT an empty table —
    settling against {} would credit nothing while completing as if the hour were
    clean. The unconditional `_save_pending(settled)` at the end of the cycle
    re-validates the REAL file on disk (not what an earlier degraded read handed
    back) and must refuse loudly — proving `run_shoptet_upload` never swallows
    that refusal into a quiet no-op."""
    with open(webapp.PENDING_SHOPTET, "w", encoding="utf-8") as f:
        f.write("{ this is not json")
    with pytest.raises(webapp.StoreWipeRefused):
        webapp.run_shoptet_upload()


def test_a_claim_file_that_cannot_be_opened_skips_the_run_instead_of_crashing(
        cycle, tmp_path, monkeypatch):
    """#299 review (Task 5 minor, deferred to Task 6): os.open on the claim file
    was unguarded — a PermissionError/OSError (full disk, wrong perms) crashed the
    whole hourly run instead of the clean "skip this hour, try again next" the
    busy-claim path already gives. A claim path whose parent directory does not
    exist is a real, reproducible OSError (FileNotFoundError) from os.open — no
    permission trickery needed."""
    monkeypatch.setattr(webapp, "CYCLE_CLAIM",
                        str(tmp_path / "no-such-dir" / ".cycle.lock"))

    with webapp._shoptet_cycle_claim() as got:
        assert got is False        # never raises just to enter the context

    res = webapp.run_shoptet_upload()          # must not raise either
    assert res["ok"] is False
    assert cycle["run_sync"] == []              # nothing was even attempted


def test_the_import_is_skipped_when_another_import_is_already_running(cycle, monkeypatch):
    """`_import_rows_chunked`'s own docstring: the caller MUST hold `_import_lock`
    across the call — every other of its 7 call sites in this module does. Not
    holding it here would let this cycle's import race a manual "Spustiť teraz"
    of a producer still on its OLD direct-import path (parovania_eshop etc. are
    migrated to the queue in a later task). Simulate that race by making the lock
    already held: the cycle must skip ITS import (never call
    `_import_rows_chunked`), leave the row queued for the next hour, and report
    a non-ok, non-crashing result."""
    webapp.queue_shoptet_fields("parovania_eshop", "code;pairCode;internalNote",
                                [["A", "P", "https://x"]])
    assert webapp._import_lock.acquire(blocking=False) is True
    try:
        res = webapp.run_shoptet_upload()
    finally:
        webapp._import_lock.release()

    assert res["ok"] is False
    assert res["confirmed"] == 0
    assert res["sent"] == 1
    assert cycle["import"] == []                     # _import_rows_chunked never ran
    assert "A" in webapp._load_pending()              # row stays queued for next hour


def test_note_col_none_asks_only_whether_the_code_is_in_the_catalogue(tmp_path, monkeypatch):
    """The combined-import verdict pass has no single fixed note column (every row
    in the drain can carry a different set of queued fields), so it must never
    index `r[note_col]` with `note_col=None` — it only withholds codes the eshop
    does not carry at all."""
    export = tmp_path / "products.csv"
    export.write_bytes(("code;pairCode;internalNote;supplier\r\n"
                        "A;P;whatever;\r\n").encode("cp1250"))
    monkeypatch.setattr(webapp, "SRC", str(export))
    monkeypatch.setattr(webapp, "EXPORT_MIN_CODES", 1)

    v = webapp._export_row_verdicts(
        [["A", "P", "Skladom"], ["B", "P2", "Skladom"]], note_col=None)

    assert v == {"confirmed": set(), "absent": {"B"}}


# ── #299 opravné kolo 1 review I2 — the cycle no longer runs producers at all ─ #
# ── (each queues on its OWN schedule now; see run_shoptet_upload's docstring). #
# ── The two tests that used to live here —                                    #
# ── test_only_an_ENABLED_and_QUEUE_MIGRATED_producer_runs_and_producers_       #
# ── reflects_it and                                                           #
# ── test_a_producer_not_yet_migrated_to_the_queue_never_runs_even_when_        #
# ── enabled — pinned the `QUEUE_MIGRATED`/`CYCLE_PRODUCERS` gate that decided  #
# ── WHICH producer this cycle was allowed to start. That whole code path is   #
# ── gone (deleted together with the two constants), so there is nothing left  #
# ── for those tests to guard — REMOVED, not weakened. What they protected     #
# ── against (a still-direct-import producer running 24x/day, or a DISABLED    #
# ── one running at all) is now structurally impossible: this cycle never      #
# ── calls RUNNER.run_sync on a producer key, full stop. `_credit_producer`'s   #
# ── own tests below still pin that a QUEUED, CONFIRMED group is credited      #
# ── correctly, and each producer's own "must not call _import_rows_chunked    #
# ── directly" test (scattered per-producer through this file and              #
# ── test_webreview_restock_skladom.py / test_webreview_stock_skladom.py)      #
# ── still pins that no producer imports on its own — together a STRONGER      #
# ── guarantee than the old gate (per the review's own instruction).           #




def test_a_confirmed_credit_for_parovania_eshop_actually_writes_PAIRINGS_STATE(
        cycle, tmp_path, monkeypatch):
    """Kills the mutation that empties `_credit_producer`'s body — without it a
    confirmed group would never be recorded and the producer would re-upload the
    same link forever."""
    state = tmp_path / "uploaded_pairings.json"
    monkeypatch.setattr(webapp, "PAIRINGS_STATE", str(state))
    webapp.queue_shoptet_fields(
        "parovania_eshop", "code;pairCode;internalNote",
        [["A", "P", "https://dodavatel.sk/x"]],
        credit_group={"A": "FOREST|60648"},
        credit_value={"A": "https://dodavatel.sk/x"})

    res = webapp.run_shoptet_upload()

    assert res["confirmed"] == 1
    d = json.loads(state.read_text(encoding="utf-8"))
    assert d["FOREST|60648"] == "https://dodavatel.sk/x"


def test_a_credit_for_an_unknown_store_is_dropped_with_a_log_not_a_crash(
        cycle, caplog):
    """The credit map only knows `parovania_eshop` today — a group credited by
    any of the other four producers must be discarded with a log line, not crash
    the cycle (a silent loss for 4 of 5 future producers otherwise)."""
    webapp.queue_shoptet_fields(
        "restock_skladom", "code;pairCode;availabilityInStock",
        [["A", "P", "Skladom"]],
        credit_group={"A": "G"}, credit_value={"A": "Skladom"})

    with caplog.at_level(logging.WARNING, logger="webreview"):
        res = webapp.run_shoptet_upload()

    assert res["ok"] is True
    assert res["confirmed"] == 1
    assert any("neznámy kredit store" in r.message for r in caplog.records)


# ── #299 Task 8 — the first two producers switch to the queue: GRUBE ─────────── #
# ── externalCode write-back and per-size split links. Both automations start ── #
# ── DISABLED (#93 contract), so this is zero live-write risk. ───────────────── #

def test_grube_producer_queues_instead_of_importing(pend, monkeypatch):
    """The brief's own fixture used `{"pairCode": ..., "externalCode": ...}` for the
    grube_codes.json entry shape — but `new_externalcode_keys`/`externalcode_rows`
    read `info["itemId"]` (confirmed against the real grube_codes.json producer,
    `.claude/skills/grube` + `test_webreview_grube_externalcode.py`'s own
    `_seed_grube` helper: `{itemId, size, deUrl, productId}`). The brief's literal
    fixture has no `itemId` key at all, so `new_externalcode_keys` would filter code
    "A" out entirely (empty itemId) and the assertion `queued == 1` would never even
    exercise the guard it exists to pin — corrected here to the real shape."""
    monkeypatch.setattr(webapp, "_import_rows_chunked",
                        lambda *a, **k: pytest.fail("producer must not import"))
    monkeypatch.setattr(webapp, "_load_grube_codes",
                        lambda: {"A": {"itemId": "12345"}})
    monkeypatch.setattr(webapp, "_load_uploaded_externalcodes", lambda: {})
    res, status = webapp._do_upload_externalcodes(False)
    assert status == 200
    assert res["queued"] == 1
    assert res["count"] == res["queued"]           # #299 review m1 — never allowed to drift
    d = webapp._load_pending()
    assert d["A"]["fields"]["externalCode"]["value"] == "12345"
    assert d["A"]["fields"]["externalCode"]["source"] == "grube_externalcode"
    # #299 review I2 — field["credit"]["value"] was never asserted anywhere in the
    # whole suite; that gap is exactly how C1 (split_links crediting the wrong,
    # normalized value) shipped unnoticed. For grube_externalcode the credit value
    # is the same raw itemId in both places (no normalization happens), so this
    # pins the healthy case; the GRUBE-normalizing case lives in
    # test_split_links_grube_credit_value_is_RAW_not_normalized below.
    assert d["A"]["fields"]["externalCode"]["credit"]["value"] == "12345"


def test_grube_producer_never_credits_itself(pend, monkeypatch):
    """#299 Task 8 decision #2 (carried over from earlier tasks, not in this task's
    brief): the producer must not write its own uploaded_externalcodes.json anymore
    — that credit belongs to the drain's `_credit_producer`, called only AFTER
    Shoptet's import actually confirms the row (the #257 class of bug: an
    "uploaded" mark written on our own say-so). Kills a regression that re-adds
    `_record_uploaded`/`_save_uploaded_externalcodes` to the producer itself."""
    monkeypatch.setattr(webapp, "_load_grube_codes",
                        lambda: {"A": {"itemId": "12345"}})
    monkeypatch.setattr(webapp, "_load_uploaded_externalcodes", lambda: {})
    monkeypatch.setattr(webapp, "_save_uploaded_externalcodes",
                        lambda d: pytest.fail("producer must not credit itself"))
    res, status = webapp._do_upload_externalcodes(False)
    assert status == 200
    assert res["queued"] == 1


def test_split_links_producer_queues_instead_of_importing(pend, monkeypatch):
    monkeypatch.setattr(webapp, "_import_rows_chunked",
                        lambda *a, **k: pytest.fail("producer must not import"))
    monkeypatch.setattr(webapp, "PRODUCTS",
                        [{"key": "TRIGONA|395", "variant_codes": ["60645/S"]}])
    monkeypatch.setattr(webapp, "CODE2PAIR", {"60645/S": "395"})
    monkeypatch.setattr(webapp, "_load_variant_links",
                        lambda: {"60645/S": "https://trigona.sk/s"})
    monkeypatch.setattr(webapp, "_load_uploaded_variant_links", lambda: {})
    monkeypatch.setattr(webapp, "_load_decisions",
                        lambda: {"TRIGONA|395": {"status": "split", "url": ""}})
    res, status = webapp._do_upload_variant_links(False)
    assert status == 200
    assert res["queued"] == 1
    assert res["count"] == res["queued"]           # #299 review m1 — never allowed to drift
    d = webapp._load_pending()
    assert d["60645/S"]["fields"]["internalNote"]["value"] == "https://trigona.sk/s"
    assert d["60645/S"]["fields"]["internalNote"]["source"] == "split_links"
    # #299 review I2 — TRIGONA doesn't normalize, so raw == queued value here; the
    # GRUBE case (where they DIFFER) is the actual C1 regression test below.
    assert d["60645/S"]["fields"]["internalNote"]["credit"]["value"] == "https://trigona.sk/s"


def test_split_links_grube_credit_value_is_RAW_not_normalized(pend, monkeypatch):
    """#299 review C1 (Critical) — the split-link credit value must be the RAW
    variant_links.json URL that `import_builder.new_variant_link_keys` compares
    against `uploaded_variant_links.json`, never the normalized `.de` URL
    `link_rows` builds for the eshop's `internalNote` cell. The old code credited
    `r[2]` (the normalized cell value) — for GRUBE that is a DIFFERENT string than
    the raw stored link, so the incremental check (`uploaded.get(c) != u`, always
    comparing against RAW) could never see a match: the same link would queue
    again on every single hourly run, forever, and `total_uploaded` would stay 0
    even after the drain "confirmed" it. Regression: revert `credit_value` back to
    `r[0]: r[2]` and this fails (credit.value becomes the normalized .de URL)."""
    monkeypatch.setattr(webapp, "PRODUCTS", [
        {"key": "GRUBE|700", "supplier": "GRUBE", "variant_codes": ["70000/S"]}])
    monkeypatch.setattr(webapp, "CODE2PAIR", {"70000/S": "700"})
    raw = "https://www.grube.sk/p/grand-nord/154773/?q=a#itemId=1"
    monkeypatch.setattr(webapp, "_load_variant_links", lambda: {"70000/S": raw})
    monkeypatch.setattr(webapp, "_load_uploaded_variant_links", lambda: {})
    monkeypatch.setattr(webapp, "_load_decisions",
                        lambda: {"GRUBE|700": {"status": "split", "url": ""}})

    res, status = webapp._do_upload_variant_links(False)

    assert status == 200
    assert res["queued"] == 1
    d = webapp._load_pending()
    field = d["70000/S"]["fields"]["internalNote"]
    # the CELL sent to Shoptet is normalized (proves link_rows/to_grube_de still ran)
    assert field["value"] == "https://www.grube.de/p/x/154773/"
    # the CREDIT must be the RAW value — what new_variant_link_keys will compare
    # against uploaded_variant_links.json on the next run
    assert field["credit"]["value"] == raw


def test_split_links_producer_never_credits_itself(pend, monkeypatch):
    """Mirrors `test_grube_producer_never_credits_itself` for the second Task 8
    producer — same #257-class reasoning."""
    monkeypatch.setattr(webapp, "PRODUCTS",
                        [{"key": "TRIGONA|395", "variant_codes": ["60645/S"]}])
    monkeypatch.setattr(webapp, "CODE2PAIR", {"60645/S": "395"})
    monkeypatch.setattr(webapp, "_load_variant_links",
                        lambda: {"60645/S": "https://trigona.sk/s"})
    monkeypatch.setattr(webapp, "_load_uploaded_variant_links", lambda: {})
    monkeypatch.setattr(webapp, "_load_decisions",
                        lambda: {"TRIGONA|395": {"status": "split", "url": ""}})
    monkeypatch.setattr(webapp, "_save_uploaded_variant_links",
                        lambda d: pytest.fail("producer must not credit itself"))
    res, status = webapp._do_upload_variant_links(False)
    assert status == 200
    assert res["queued"] == 1


# #299 opravné kolo 1 review I2/m4 — `test_all_five_migrated_producers_are_open_
# in_the_queue_migration_gate` used to live here, exercising `QUEUE_MIGRATED`/
# `CYCLE_PRODUCERS` end-to-end with all five producers ENABLED. Both constants
# and the loop that read them are gone (the cycle never runs a producer at all
# any more), so there is nothing left for it to guard — REMOVED, not weakened;
# see the longer note above `test_a_confirmed_credit_for_parovania_eshop_
# actually_writes_PAIRINGS_STATE`'s section header for what still covers this.


def test_a_confirmed_credit_for_grube_externalcode_actually_writes_EXTERNALCODES_STATE(
        cycle, tmp_path, monkeypatch):
    """Mirrors `test_a_confirmed_credit_for_parovania_eshop_actually_writes_
    PAIRINGS_STATE` for the `_credit_producer` mapping this task adds. Kills the
    mutation that drops "grube_externalcode" from that mapping."""
    state = tmp_path / "uploaded_externalcodes.json"
    monkeypatch.setattr(webapp, "EXTERNALCODES_STATE", str(state))
    webapp.queue_shoptet_fields(
        "grube_externalcode", "code;pairCode;externalCode",
        [["A", "P", "12345"]], credit_group={"A": "A"}, credit_value={"A": "12345"})

    res = webapp.run_shoptet_upload()

    assert res["confirmed"] == 1
    d = json.loads(state.read_text(encoding="utf-8"))
    assert d["A"] == "12345"


def test_a_confirmed_credit_for_split_links_actually_writes_VARIANT_LINKS_STATE(
        cycle, tmp_path, monkeypatch):
    """Kills the mutation that drops "split_links" from the `_credit_producer`
    mapping this task adds."""
    state = tmp_path / "uploaded_variant_links.json"
    monkeypatch.setattr(webapp, "VARIANT_LINKS_STATE", str(state))
    webapp.queue_shoptet_fields(
        "split_links", "code;pairCode;internalNote",
        [["60645/S", "395", "https://trigona.sk/s"]],
        credit_group={"60645/S": "60645/S"},
        credit_value={"60645/S": "https://trigona.sk/s"})

    res = webapp.run_shoptet_upload()

    assert res["confirmed"] == 1
    d = json.loads(state.read_text(encoding="utf-8"))
    assert d["60645/S"] == "https://trigona.sk/s"


def test_a_GRUBE_split_link_is_credited_and_never_requeued_again_end_to_end(
        cycle, tmp_path, monkeypatch):
    """#299 review C1/I2 — the full producer→drain→credit loop for the GRUBE
    normalizing case, proving the fix closes the actual live-eshop-facing bug (not
    just the isolated credit-value assert above): queue the GRUBE split link via
    the REAL producer (`_do_upload_variant_links`, normalized cell + raw credit),
    run the hourly cycle so `_credit_producer` writes `uploaded_variant_links.json`
    from that same credit value, then run the producer AGAIN and confirm the link
    is now genuinely skipped (queued == 0) instead of being queued forever (the
    review's own probe measured `SECOND RUN count: 1` against the buggy code —
    this reproduces that exact scenario end to end)."""
    monkeypatch.setattr(webapp, "VARIANT_LINKS_STATE", str(tmp_path / "uploaded_variant_links.json"))
    monkeypatch.setattr(webapp, "PRODUCTS", [
        {"key": "GRUBE|700", "supplier": "GRUBE", "variant_codes": ["70000/S"]}])
    monkeypatch.setattr(webapp, "CODE2PAIR", {"70000/S": "700"})
    raw = "https://www.grube.sk/p/grand-nord/154773/?q=a#itemId=1"
    monkeypatch.setattr(webapp, "_load_variant_links", lambda: {"70000/S": raw})
    # NOT monkeypatched: `_load_uploaded_variant_links` stays the REAL function
    # (`_read_json_store(VARIANT_LINKS_STATE, {})`) — it must re-read the state
    # `_credit_producer` writes below, or the second call below proves nothing.
    monkeypatch.setattr(webapp, "_load_decisions",
                        lambda: {"GRUBE|700": {"status": "split", "url": ""}})

    res1, _ = webapp._do_upload_variant_links(False)
    assert res1["queued"] == 1

    cycle_res = webapp.run_shoptet_upload()
    assert cycle_res["confirmed"] == 1

    res2, _ = webapp._do_upload_variant_links(False)
    assert res2["queued"] == 0, (
        "the same GRUBE split link must NOT be a candidate again once the drain "
        "confirmed it — a non-zero queued here is the C1 infinite-requeue bug")


# ── #299 review m3 — dry_run must PREVIEW what would be queued, not just say ── #
# ── "0" unconditionally. The old direct-import producers' dry_run reached a ── #
# ── real Shoptet dry-run validation; these two no longer import at all, so ── #
# ── the honest equivalent is: report how many field values WOULD be queued, ── #
# ── while genuinely queueing nothing. ────────────────────────────────────── #

def test_dry_run_previews_the_grube_queue_count_without_queuing_anything(pend, monkeypatch):
    monkeypatch.setattr(webapp, "_load_grube_codes",
                        lambda: {"A": {"itemId": "12345"}, "B": {"itemId": "999"}})
    monkeypatch.setattr(webapp, "_load_uploaded_externalcodes", lambda: {})

    res, status = webapp._do_upload_externalcodes(True)

    assert status == 200
    assert res["dry_run"] is True
    assert res["queued"] == 0                 # nothing was ACTUALLY queued
    assert res["would_queue"] == 2             # but the preview says what WOULD be
    assert webapp._load_pending() == {}        # and the pending table proves it


def test_dry_run_previews_the_split_links_queue_count_without_queuing_anything(pend, monkeypatch):
    monkeypatch.setattr(webapp, "PRODUCTS",
                        [{"key": "TRIGONA|395", "variant_codes": ["60645/S"]}])
    monkeypatch.setattr(webapp, "CODE2PAIR", {"60645/S": "395"})
    monkeypatch.setattr(webapp, "_load_variant_links",
                        lambda: {"60645/S": "https://trigona.sk/s"})
    monkeypatch.setattr(webapp, "_load_uploaded_variant_links", lambda: {})
    monkeypatch.setattr(webapp, "_load_decisions",
                        lambda: {"TRIGONA|395": {"status": "split", "url": ""}})

    res, status = webapp._do_upload_variant_links(True)

    assert status == 200
    assert res["dry_run"] is True
    assert res["queued"] == 0
    assert res["would_queue"] == 1
    assert webapp._load_pending() == {}


# ── #299 Task 9 — restock_skladom / stock_skladom switch to the queue ────────── #
# ── Neither producer imports directly any more, and neither credits itself — a ── #
# ── candidate is entirely state-driven (Vypredané+visible in the LIVE export), ── #
# ── so once Shoptet confirms the flip the product's own state stops it being a ── #
# ── candidate again; no separate "already uploaded" bookkeeping exists or is ──── #
# ── needed for either producer (unlike grube_externalcode/split_links). ──────── #

def test_restock_queues_availability_and_stock_without_a_credit(pend, monkeypatch):
    # explicit freshness (#299 Task 9 review I2 gate) — this test is about the
    # queueing contract, not the export-age check, so pin the export as fresh
    # rather than depending on whatever WEBREVIEW_PRODUCTS happens to hold.
    monkeypatch.setattr(webapp, "_export_age_s", lambda: 60.0)
    monkeypatch.setattr(webapp, "_import_rows_chunked",
                        lambda *a, **k: pytest.fail("producer must not import"))
    # #299 Task 9 review I1 — the seam now takes the ALREADY-COMPUTED candidates
    # (unused here, hence `_c`), never recomputes its own JOIN.
    # RESTOCK_COLS order: code, pairCode, productVisibility, availabilityInStock,
    # availabilityOutOfStock, stock (#299 Task 9 review m1 — the brief's own fixture
    # had productVisibility/availabilityOutOfStock swapped).
    monkeypatch.setattr(webapp, "_restock_candidate_rows",
                        lambda _c: [["A", "P", "visible", "Skladom", "Skladom", "5"]])
    res = webapp.run_restock_skladom()
    assert res["queued"] == 4
    f = webapp._load_pending()["A"]["fields"]
    # #299 Task 10 (leftover from Task 9's fix round 1 review, m1 residual) — the
    # test's own NAME promises it guards "availability and stock", but it never
    # asserted productVisibility/availabilityOutOfStock, so a column swap between
    # those two and their neighbours would pass unnoticed HERE (the production
    # guard already lives elsewhere — test_run_queues_only_fresh_available_vypredane_product
    # and test_import_builder.py::test_restock_rows_both_availability_columns_are_skladom
    # both kill a RESTOCK_COLS mutation — this was a cosmetic gap, not a safety one).
    assert f["productVisibility"]["value"] == "visible"
    assert f["availabilityOutOfStock"]["value"] == "Skladom"
    assert f["availabilityInStock"]["value"] == "Skladom"
    assert f["stock"]["value"] == "5"
    assert "credit" not in f["stock"]


# --------------------------------------------------------------------------- #
# #299 Task 10 — parovania_eshop (pairing decisions + inline order pairings via
# `_do_upload_pairings`, plus supplier assignments via `_do_upload_suppliers`)
# switches to the queue: the LAST of the five #299 producers, and the ONLY one
# already ENABLED on forestshop.sk (the other four start DISABLED per the #93
# contract). Its links feed the automatic re-ordering, so crediting the WRONG
# shape (Task 8's C1 lesson — the same class of bug that made every GRUBE split
# link requeue forever) would silently freeze real reorder links on the live
# eshop, which is why the two tests below (from the task brief, corrected for two
# real bugs in its own snippets — see the report) are the single most important
# regression coverage in this task.
# --------------------------------------------------------------------------- #

def test_a_pairing_key_is_credited_only_after_ALL_its_codes_are_confirmed(cycle, monkeypatch):
    """#299 Task 8's C1 lesson, generalized to a credit GROUP: a pairing decision
    key shared by two variant codes must be credited only once EVERY one of its
    own queued codes is confirmed — never partially, never on a guess.

    Corrected from the task brief's own snippet, which used a raw module
    attribute assignment (`w._credit_producer = lambda …`) instead of
    `monkeypatch.setattr` — a raw assignment is NEVER reverted between tests and
    would have left every LATER test in this file (including the real
    `_credit_producer` tests just above) running against a stub that silently
    swallows every credit, a cross-test contamination bug the brief's own
    snippet would have introduced into the suite."""
    webapp.queue_shoptet_fields(
        "parovania_eshop", "code;pairCode;internalNote",
        [["A", "P", "u"], ["B", "P", "u"]],
        credit_group={"A": "BETALOV|P", "B": "BETALOV|P"},
        credit_value={"A": "u", "B": "u"})
    credited = {}
    monkeypatch.setattr(webapp, "_credit_producer",
                        lambda store, entries: credited.setdefault(store, {}).update(entries))
    webapp.run_shoptet_upload()
    assert credited == {"parovania_eshop": {"BETALOV|P": "u"}}


def test_a_pairing_key_whose_second_code_failed_is_NOT_credited(cycle, monkeypatch):
    """The other half of the same rule: ONE code of a group landing in a
    partially-rejected chunk must withhold the WHOLE group's credit — crediting
    it anyway would mark a pairing "uploaded" (and stop retrying it) when the
    eshop never actually received the full group, the #257 class of bug one
    level up from a single row.

    Corrected from the task brief's own snippet in TWO ways: (1) the same raw
    `_credit_producer` assignment bug as the test above, fixed the same way;
    (2) the fake `_import_rows_chunked` had the signature
    `(rows, header, dry, prefix, timeout=900)` — but `run_shoptet_upload`'s real
    call site passes `csv_safe=True` as a keyword (M3 — the combined import must
    keep the formula-injection guard), which a fake missing that parameter
    rejects with `TypeError: unexpected keyword argument 'csv_safe'`. Run
    UNCHANGED against the brief's literal snippet to confirm: it fails on that
    TypeError, not on the assertion it was written to pin — it would have
    pinned nothing at all."""
    monkeypatch.setattr(webapp, "_import_rows_chunked",
                        lambda rows, header, dry, prefix, csv_safe=False, timeout=900: {
                            "ok": False, "partial": True, "success_codes": {"A"},
                            "partial_codes": {"B"}, "partial_failed": 1,
                            "chunks_total": 1, "chunks_ok": 0, "chunks_partial": 1,
                            "rows_ok": 0, "rows_partial": 2, "processed": 1,
                            "updated": 1, "failed": 1, "rc": 1,
                            "error_detail": None, "stdout_tail": "", "err": "",
                            "unreadable": False})
    webapp.queue_shoptet_fields(
        "parovania_eshop", "code;pairCode;internalNote",
        [["A", "P", "u"], ["B", "P", "u"]],
        credit_group={"A": "K", "B": "K"}, credit_value={"A": "u", "B": "u"})
    credited = {}
    monkeypatch.setattr(webapp, "_credit_producer",
                        lambda store, entries: credited.setdefault(store, {}).update(entries))
    res = webapp.run_shoptet_upload()
    assert credited == {}
    assert res["unconfirmed"] == 1
    assert "B" in webapp._load_pending()


@pytest.fixture
def pairings_iso(tmp_path, monkeypatch):
    """Isolate every store `_do_upload_pairings` reads/writes, incl. the shared
    pending_shoptet table (mirrors the `pend` fixture's isolation, plus the
    producer's own inputs — PRODUCTS/CODE2PAIR/decisions/order_pairings)."""
    monkeypatch.setattr(webapp, "PENDING_SHOPTET", str(tmp_path / "pending_shoptet.json"))
    monkeypatch.setattr(webapp, "PAIRINGS_STATE", str(tmp_path / "uploaded_pairings.json"))
    monkeypatch.setattr(webapp, "PRODUCTS",
                        [{"key": "BETALOV|P1", "name": "X", "our_url": "u",
                          "variant_codes": ["1/M"]}])
    monkeypatch.setattr(webapp, "CODE2PAIR", {"1/M": "P1"})
    monkeypatch.setattr(webapp, "_load_decisions",
                        lambda: {"BETALOV|P1": {"status": "good", "url": "https://supplier/x"}})
    monkeypatch.setattr(webapp, "_load_order_pairings", lambda: {})
    # default: nothing confirmed, nothing absent — every candidate row is queued.
    # Tests that need the confirmed/absent branches override this themselves.
    monkeypatch.setattr(webapp, "_export_row_verdicts",
                        lambda rows, note_col=2: {"confirmed": set(), "absent": set()})
    return tmp_path


def test_parovania_eshop_pairings_producer_queues_instead_of_importing(pairings_iso, monkeypatch):
    monkeypatch.setattr(webapp, "_import_rows_chunked",
                        lambda *a, **k: pytest.fail("producer must not import"))
    res, status = webapp._do_upload_pairings(dry=False)
    assert status == 200
    assert res["queued"] == 1
    d = webapp._load_pending()
    assert d["1/M"]["pairCode"] == "P1"                # #299 opravné kolo 1 review m2 —
    # the pre-migration producer-level test asserted the whole row shape it sent to
    # import, INCLUDING pairCode; the equivalent post-migration check is that the
    # queued entry carries it too (queue_fields() stores it on the entry itself,
    # never inside `fields`).
    assert d["1/M"]["fields"]["internalNote"]["value"] == "https://supplier/x"
    assert d["1/M"]["fields"]["internalNote"]["source"] == "parovania_eshop"
    assert d["1/M"]["fields"]["internalNote"]["credit"]["group"] == "BETALOV|P1"
    assert d["1/M"]["fields"]["internalNote"]["credit"]["value"] == "https://supplier/x"


def test_parovania_eshop_pairings_producer_never_credits_itself_for_a_queued_code(
        pairings_iso, monkeypatch):
    """#299 Task 10 core decision — a code this run only QUEUES (never confirmed
    by the eshop's own export) must NOT be written to uploaded_pairings.json by
    the producer itself; that credit belongs solely to the hourly drain's
    `_credit_producer`, once Shoptet's own import actually confirms it (the
    #257 lesson — never mark 'uploaded' on our own say-so)."""
    monkeypatch.setattr(webapp, "_save_uploaded",
                        lambda d: pytest.fail("producer must not credit an unconfirmed code"))
    res, status = webapp._do_upload_pairings(dry=False)
    assert status == 200
    assert res["queued"] == 1
    assert not (pairings_iso / "uploaded_pairings.json").exists()


def test_a_confirmed_pairing_row_is_credited_immediately_without_going_through_the_queue(
        pairings_iso, monkeypatch):
    """#299 Task 10's explicit brief instruction — leave `_export_row_verdicts`
    CONFIRMED rows exactly as they were before this migration: a row the eshop's
    OWN export already carries exactly as we would write it is proof from
    Shoptet's own state, not "our own say-so", so it is recorded RIGHT AWAY and
    never lands in the pending table at all (`queue_shoptet_fields` itself is a
    no-op on an empty row list — this pins the observable OUTCOME, the empty
    pending table, not the implementation detail of whether the zero-cost no-op
    call happens)."""
    monkeypatch.setattr(webapp, "_export_row_verdicts",
                        lambda rows, note_col=2: {"confirmed": {"1/M"}, "absent": set()})
    res, status = webapp._do_upload_pairings(dry=False)
    assert status == 200
    assert res["count"] == 1
    assert res["queued"] == 0
    d = json.loads((pairings_iso / "uploaded_pairings.json").read_text(encoding="utf-8"))
    assert d == {"BETALOV|P1": "https://supplier/x"}
    assert webapp._load_pending() == {}


def test_a_key_with_an_absent_code_is_not_credited_until_the_missing_code_is_fixed(
        pairings_iso, monkeypatch):
    """#299 opravné kolo 1 review C1 (Critical) — a decision key whose codes span
    a REAL code and one the eshop's catalogue does not carry at all must not be
    credited just because the real code's queued field got confirmed. Before this
    fix `send_rows` excluded the absent code entirely, while `credit_group` (built
    upstream, from `written_codes`) still carried it for the SAME key — so once
    the drain confirmed the other code, the WHOLE key was credited even though the
    absent code's link was never written to the eshop, and the code silently
    dropped out of "chýba v eshope" too (a credited key is no longer "new"). The
    fix queues the absent code as well; the drain's own `build_import` holds it as
    `not-in-catalog`, so `settle` sees the group straddling a confirmed and an
    unconfirmed code and withholds the WHOLE key's credit (the #49 rule)."""
    monkeypatch.setattr(webapp, "PRODUCTS", [
        {"key": "BETALOV|P1", "name": "X", "our_url": "u",
         "variant_codes": ["1/M", "2/L"]}])
    monkeypatch.setattr(webapp, "CODE2PAIR", {"1/M": "P1", "2/L": "P1"})
    # the producer's own check (_do_upload_pairings) sees 2/L as absent from the catalogue
    monkeypatch.setattr(webapp, "_export_row_verdicts",
                        lambda rows, note_col=2: {"confirmed": set(), "absent": {"2/L"}})

    res, status = webapp._do_upload_pairings(dry=False)

    assert status == 200
    assert res["queued"] == 2, "BOTH codes must be queued — this is C1's actual fix"
    assert res["count"] == 0, "nothing may be credited yet — 2/L is still unconfirmed"
    assert res["missing_count"] == 1
    assert res["missing_in_eshop"][0]["code"] == "2/L"
    d = webapp._load_pending()
    assert set(d) == {"1/M", "2/L"}
    assert d["1/M"]["fields"]["internalNote"]["credit"]["group"] == "BETALOV|P1"
    assert d["2/L"]["fields"]["internalNote"]["credit"]["group"] == "BETALOV|P1"
    assert not (pairings_iso / "uploaded_pairings.json").exists()

    # now the hourly drain: its OWN catalogue check (note_col=None) sees 2/L as
    # absent too (same export, same catalogue) — 1/M is sent and Shoptet confirms
    # it, 2/L never enters `success`.
    monkeypatch.setattr(webapp, "CYCLE_CLAIM", str(pairings_iso / ".cycle.lock"))
    monkeypatch.setattr(webapp, "_export_age_s", lambda: None)
    monkeypatch.setattr(webapp.RUNNER, "run_sync", lambda key: True)
    monkeypatch.setattr(webapp, "_export_row_verdicts",
                        lambda rows, note_col=None: {"confirmed": set(), "absent": {"2/L"}})
    monkeypatch.setattr(webapp, "_import_rows_chunked",
                        lambda rows, header, dry, prefix, csv_safe=False, timeout=900: {
                            "ok": True, "partial": False, "success_codes": {"1/M"},
                            "partial_codes": set(), "partial_failed": 0,
                            "chunks_total": 1, "chunks_ok": 1, "processed": 1,
                            "updated": 1, "failed": 0, "rc": 0, "error_detail": None,
                            "stdout_tail": "", "err": "", "unreadable": False})

    webapp.run_shoptet_upload()

    assert not (pairings_iso / "uploaded_pairings.json").exists(), (
        "the key must NOT be credited — 2/L is still unconfirmed")
    pending = webapp._load_pending()
    assert set(pending) == {"2/L"}, "1/M is confirmed+unchanged and drops out; 2/L stays"
    assert pending["2/L"]["blocked"]["reason"] == "not-in-catalog"
    assert pending["2/L"]["blocked_runs"] == 1


def test_parovania_eshop_pairing_GRUBE_credit_value_is_RAW_not_normalized(
        pairings_iso, monkeypatch):
    """#299 Task 8's C1 — the SAME class of bug found in split_links applies here:
    `import_builder.link_rows` rewrites a GRUBE product's URL to the canonical
    grube.de detail page for EVERY status (not just `split`), so the QUEUED cell
    value is normalized while `new_pairing_keys` compares the RAW decision URL
    against uploaded_pairings.json. Crediting the normalized cell (`r[2]`) would
    mean the same GRUBE reorder link re-queues to the live eshop every hour,
    forever, with total_uploaded stuck at 0 — this is why the brief's own
    instruction ("kredituj presne ten istý tvar") calls this the single most
    important test to write."""
    monkeypatch.setattr(webapp, "PRODUCTS", [
        {"key": "GRUBE|700", "supplier": "GRUBE", "name": "X", "our_url": "u",
         "variant_codes": ["70000/S"]}])
    monkeypatch.setattr(webapp, "CODE2PAIR", {"70000/S": "700"})
    raw = "https://www.grube.sk/p/grand-nord/154773/?q=a#itemId=1"
    monkeypatch.setattr(webapp, "_load_decisions",
                        lambda: {"GRUBE|700": {"status": "good", "url": raw}})

    res, status = webapp._do_upload_pairings(dry=False)

    assert status == 200
    assert res["queued"] == 1
    d = webapp._load_pending()
    field = d["70000/S"]["fields"]["internalNote"]
    # the CELL sent to Shoptet is normalized (proves link_rows/to_grube_de ran)
    assert field["value"] == "https://www.grube.de/p/x/154773/"
    # the CREDIT is the RAW value — what new_pairing_keys compares against
    # uploaded_pairings.json on the next run
    assert field["credit"]["value"] == raw


def test_dry_run_previews_the_pairings_queue_count_without_queuing_anything(
        pairings_iso, monkeypatch):
    monkeypatch.setattr(webapp, "_import_rows_chunked",
                        lambda *a, **k: pytest.fail("a dry run must never import"))
    res, status = webapp._do_upload_pairings(dry=True)
    assert status == 200
    assert res["dry_run"] is True
    assert res["queued"] == 0
    assert res["would_queue"] == 1
    assert webapp._load_pending() == {}
    assert not (pairings_iso / "uploaded_pairings.json").exists()


@pytest.fixture
def suppliers_iso(tmp_path, monkeypatch):
    """Isolate every store `_do_upload_suppliers` reads/writes. BUG 1 fail-closed
    needs a USABLE catalog export — a small but plausible fixture that lists the
    one code these tests push, with no own supplier, so nothing is excluded."""
    monkeypatch.setattr(webapp, "PENDING_SHOPTET", str(tmp_path / "pending_shoptet.json"))
    monkeypatch.setattr(webapp, "SUPPLIERS_STATE", str(tmp_path / "uploaded_suppliers.json"))
    monkeypatch.setattr(webapp, "SUPPLIER_ASSIGN", str(tmp_path / "supplier_assignments.json"))
    monkeypatch.setattr(webapp, "CODE2PAIR", {"9/Z": "777"})
    monkeypatch.setattr(webapp, "_iter_export_lines",
                        lambda: iter(["code;pairCode;supplier\r\n", "9/Z;777;\r\n"]))
    monkeypatch.setattr(webapp, "EXPORT_MIN_CODES", 1)
    webapp._save_supplier_assign({"9/Z": "BETALOV"})
    return tmp_path


def test_do_upload_suppliers_producer_queues_instead_of_importing(suppliers_iso, monkeypatch):
    monkeypatch.setattr(webapp, "_import_rows_chunked",
                        lambda *a, **k: pytest.fail("producer must not import"))
    res, status = webapp._do_upload_suppliers(dry=False)
    assert status == 200
    assert res["queued"] == 1
    assert res["count"] == res["queued"]
    d = webapp._load_pending()
    assert d["9/Z"]["pairCode"] == "777"               # #299 opravné kolo 1 review m2 —
    # see the matching note on the pairings producer's own "queues instead of
    # importing" test above.
    assert d["9/Z"]["fields"]["supplier"]["value"] == "BETALOV"
    assert d["9/Z"]["fields"]["supplier"]["source"] == "parovania_eshop_suppliers"
    assert d["9/Z"]["fields"]["supplier"]["credit"]["group"] == "9/Z"
    assert d["9/Z"]["fields"]["supplier"]["credit"]["value"] == "BETALOV"


def test_do_upload_suppliers_producer_never_credits_itself(suppliers_iso, monkeypatch):
    """Same #257-class reasoning as the pairings producer above — but suppliers
    has NO "already confirmed in the export" fast path at all (unlike pairings),
    so EVERY new assignment must go through the queue and the producer must
    NEVER write uploaded_suppliers.json itself, on any run."""
    monkeypatch.setattr(webapp, "_save_uploaded_suppliers",
                        lambda d: pytest.fail("producer must not credit itself"))
    res, status = webapp._do_upload_suppliers(dry=False)
    assert status == 200
    assert res["queued"] == 1


def test_dry_run_previews_the_suppliers_queue_count_without_queuing_anything(
        suppliers_iso, monkeypatch):
    monkeypatch.setattr(webapp, "_import_rows_chunked",
                        lambda *a, **k: pytest.fail("a dry run must never import"))
    res, status = webapp._do_upload_suppliers(dry=True)
    assert status == 200
    assert res["dry_run"] is True
    assert res["queued"] == 0
    assert res["would_queue"] == 1
    assert webapp._load_pending() == {}
    assert not (suppliers_iso / "uploaded_suppliers.json").exists()


def test_a_confirmed_credit_for_parovania_eshop_suppliers_actually_writes_SUPPLIERS_STATE(
        cycle, tmp_path, monkeypatch):
    """Mirrors `test_a_confirmed_credit_for_parovania_eshop_actually_writes_
    PAIRINGS_STATE` for the "parovania_eshop_suppliers" mapping this task adds to
    `_credit_producer` — the supplier write-back's OWN credit store, distinct
    from the pairings push even though both share the SAME automation key on the
    runner. Kills the mutation that drops "parovania_eshop_suppliers" from that
    mapping."""
    state = tmp_path / "uploaded_suppliers.json"
    monkeypatch.setattr(webapp, "SUPPLIERS_STATE", str(state))
    webapp.queue_shoptet_fields(
        "parovania_eshop_suppliers", "code;pairCode;supplier",
        [["9/Z", "777", "BETALOV"]],
        credit_group={"9/Z": "9/Z"}, credit_value={"9/Z": "BETALOV"})

    res = webapp.run_shoptet_upload()

    assert res["confirmed"] == 1
    d = json.loads(state.read_text(encoding="utf-8"))
    assert d["9/Z"] == "BETALOV"
