"""Generic in-app automation runner (#93, foundation for #103 / #105-#111).

The app had NO scheduler — n8n automations are being migrated in-app one by one,
each as a registered Automation with its own tab. This module is the REUSABLE
core: a registry of automations + a stoppable background-thread scheduler +
persisted per-automation state (data/out/automations.json).

Safety rule (dohodnuté na #93): a newly-deployed automation starts DISABLED
(enabled=false) and runs only after the manager clicks Štart in the web UI —
a deploy must never start e-mailing real customers on its own.

Pure-python (no Flask imports) so it is unit-testable without the web app.
"""
import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

log = logging.getLogger("automations")

DEFAULT_TZ = "Europe/Bratislava"


@dataclass
class Automation:
    """One registered automation: key (id + state key), human name (SK, shown in
    the UI), schedule ({"daily_at": "HH:MM", "tz": ...}) and the run function.
    run_fn takes no args and returns a JSON-serializable summary dict (stored as
    last_result); raising marks the run as error (message kept in last_error)."""
    key: str
    name: str
    schedule: dict
    run_fn: Callable[[], dict]


def schedule_label(schedule: dict) -> str:
    """Human label for the UI, e.g. 'denne o 09:00' or 'každú hodinu' (#119 —
    interval-based schedules, {"interval_minutes": N}, added alongside the
    original daily_at form; both keys stay supported, never both at once)."""
    if "interval_minutes" in schedule:
        mins = int(schedule["interval_minutes"])
        if mins % 60 == 0:
            hrs = mins // 60
            if hrs == 1:
                return "každú hodinu"
            if 2 <= hrs <= 4:
                return f"každé {hrs} hodiny"
            return f"každých {hrs} hodín"
        return f"každých {mins} min"
    return f"denne o {schedule.get('daily_at', '?')}"


def next_run_at(schedule: dict, now: datetime | None = None) -> datetime:
    """Next run time in the schedule's timezone (tz-aware). Two schedule shapes:
    daily_at "HH:MM" (next occurrence of that clock time — a run missed while the
    app was down is SKIPPED, the next future slot is good enough) or interval_minutes
    N (#119 — simple now+N, used for the hourly Shoptet sync; no clock alignment,
    so re-enabling just restarts the N-minute countdown from that moment)."""
    tz = ZoneInfo(schedule.get("tz", DEFAULT_TZ))
    now = (now or datetime.now(tz)).astimezone(tz)
    if "interval_minutes" in schedule:
        return now + timedelta(minutes=int(schedule["interval_minutes"]))
    hh, mm = (int(x) for x in schedule["daily_at"].split(":"))
    candidate = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


class AutomationRunner:
    """Registry + scheduler. State file (atomic, 0600 — same pattern as the other
    data/out stores): {key: {enabled, last_run, last_status, last_error,
    last_result, next_run}}. enabled persists across restarts; next_run is
    recomputed forward on enable and after every run."""

    def __init__(self, state_path: str, automations: list[Automation], tick: float = 30.0):
        self.state_path = state_path
        self.automations = {a.key: a for a in automations}
        self.tick = tick
        self._lock = threading.Lock()      # guards state-file read-modify-write
        self._running: dict[str, bool] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- state ------------------------------------------------------------ #
    def _load(self) -> dict:
        try:
            with open(self.state_path, encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save(self, st: dict) -> None:
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.state_path)

    # -- controls ----------------------------------------------------------- #
    def set_enabled(self, key: str, enabled: bool) -> None:
        """Štart/Stop. Enabling schedules the next future run; disabling clears
        it. Persisted — survives an app restart."""
        a = self.automations[key]          # KeyError for an unknown key
        with self._lock:
            st = self._load()
            ent = st.setdefault(key, {})
            ent["enabled"] = bool(enabled)
            if enabled:
                ent["next_run"] = next_run_at(a.schedule).isoformat(timespec="seconds")
            else:
                ent.pop("next_run", None)
            self._save(st)
        log.info("automation %s %s (next_run=%s)", key,
                 "ENABLED" if enabled else "DISABLED",
                 ent.get("next_run", "-"))

    def run_now(self, key: str) -> bool:
        """Manual 'Spustiť teraz' — runs in a background thread even when the
        automation is disabled (an explicit manager action). False when a run
        of this automation is already in flight."""
        if key not in self.automations:
            raise KeyError(key)
        with self._lock:
            if self._running.get(key):
                return False
            self._running[key] = True
        t = threading.Thread(target=self._execute, args=(key,),
                             kwargs={"claimed": True}, daemon=True,
                             name=f"automation-{key}")
        self._threads[key] = t
        t.start()
        return True

    def status(self) -> list[dict]:
        st = self._load()
        out = []
        for key, a in self.automations.items():
            ent = st.get(key) or {}
            enabled = bool(ent.get("enabled"))
            out.append({
                "key": key,
                "name": a.name,
                "schedule": schedule_label(a.schedule),
                "enabled": enabled,
                "running": bool(self._running.get(key)),
                "last_run": ent.get("last_run", ""),
                "last_status": ent.get("last_status", ""),
                "last_error": ent.get("last_error", ""),
                "last_result": ent.get("last_result") or {},
                "next_run": ent.get("next_run", "") if enabled else "",
            })
        return out

    # -- execution ---------------------------------------------------------- #
    def _execute(self, key: str, claimed: bool = False) -> bool:
        """Run one automation now (in the calling thread) and persist the
        outcome. Full context is logged — a failed 3am run must be debuggable
        from the log + state file alone."""
        a = self.automations[key]
        if not claimed:
            with self._lock:
                if self._running.get(key):
                    return False
                self._running[key] = True
        tz = ZoneInfo(a.schedule.get("tz", DEFAULT_TZ))
        started = datetime.now(tz)
        log.info("automation %s: run starting", key)
        try:
            result = a.run_fn()
            status, err = "ok", ""
            log.info("automation %s: run OK result=%s", key, result)
        except Exception as e:  # noqa: BLE001 — any failure = recorded error, runner survives
            result, status, err = None, "error", f"{type(e).__name__}: {e}"
            log.exception("automation %s: run FAILED", key)
        with self._lock:
            self._running[key] = False
            st = self._load()
            ent = st.setdefault(key, {})
            ent["last_run"] = started.isoformat(timespec="seconds")
            ent["last_status"] = status
            ent["last_error"] = err
            if result is not None:
                ent["last_result"] = result
            if ent.get("enabled"):
                ent["next_run"] = next_run_at(a.schedule).isoformat(timespec="seconds")
            self._save(st)
        return True

    def tick_once(self, now: datetime | None = None) -> None:
        """One scheduler pass: run every ENABLED automation whose next_run is
        due. Called by the background loop; callable directly in tests."""
        st = self._load()
        for key, a in self.automations.items():
            ent = st.get(key) or {}
            if not ent.get("enabled") or self._running.get(key):
                continue
            tz = ZoneInfo(a.schedule.get("tz", DEFAULT_TZ))
            now_k = (now or datetime.now(tz)).astimezone(tz)
            nr = ent.get("next_run")
            if not nr:
                # enabled but unscheduled (legacy/hand-edited state) → schedule forward
                with self._lock:
                    st2 = self._load()
                    st2.setdefault(key, {})["next_run"] = \
                        next_run_at(a.schedule, now_k).isoformat(timespec="seconds")
                    self._save(st2)
                continue
            try:
                due = datetime.fromisoformat(nr)
                if due.tzinfo is None:
                    due = due.replace(tzinfo=tz)
            except ValueError:
                log.error("automation %s: unparsable next_run %r — rescheduling", key, nr)
                due = None
            if due is None or due <= now_k:
                self._execute(key)

    # -- background loop ----------------------------------------------------- #
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="automation-runner")
        self._thread.start()
        log.info("automation runner started (%d registered: %s, tick=%ss)",
                 len(self.automations), ", ".join(self.automations), self.tick)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.tick + 5)

    def _loop(self) -> None:
        while not self._stop.wait(self.tick):
            try:
                self.tick_once()
            except Exception:  # noqa: BLE001 — scheduler must never die silently
                log.exception("automation runner: tick failed")
