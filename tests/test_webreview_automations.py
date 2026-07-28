"""Webreview tests for the automations API + the Pošta SK run wiring (#93).

Hermetic: the Pošta API is monkeypatched with saved fixtures, SMTP is
monkeypatched to a capturing stub (asserts a mail WOULD go out — nothing is
ever sent), the orders export is a fixture CSV, and every store path is
redirected to tmp. Mirrors the test_webreview.py import pattern.
"""
import json
import logging
import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webreview"))
import app as webapp  # noqa: E402

from parovanie import posta_uncollected  # noqa: E402
from tests.conftest import authed_client  # noqa: E402

# the UNPATCHED helper — the „BCC vždy" wiring test needs the real SMTP path (behind a fake
# smtplib), not the capturing stub the `iso` fixture installs.
_REAL_SEND_MAIL_HTML = webapp._send_mail_html

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "posta")


def _fix(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return json.load(f)


TODAY = date.today()
D = (TODAY - timedelta(days=3)).isoformat()          # order date inside the 30-day window

ORDERS_CSV = (
    "code;date;statusName;email;phone;billFullName;packageNumber;itemCode\r\n"
    f"2026100;{D} 10:00:00;Vybavená;jan@example.com;+421900111222;Ján Vzor;EF000000002SK;1/M\r\n"
    f"2026101;{D} 09:00:00;Vybavená;eva@example.com;;Eva Testová;00000000000003;3/S\r\n"
    f"2026105;{D} 08:00:00;Vybavená;peter@example.com;;Peter Prevzatý;EF000000001SK;7/A\r\n"
).encode("cp1250")

TRACKING = {
    "EF000000002SK": _fix("tracking_notified_znp.json"),     # uncollected → mail
    "00000000000003": _fix("tracking_invalid_format.json"),  # the n8n-breaking class
    "EF000000001SK": _fix("tracking_delivered.json"),        # delivered → nothing
}


@pytest.fixture
def iso(tmp_path, monkeypatch):
    """Isolate all automation stores + the network/SMTP edges."""
    monkeypatch.setattr(webapp.RUNNER, "state_path", str(tmp_path / "automations.json"))
    monkeypatch.setattr(webapp, "POSTA_STATE", str(tmp_path / "posta_uncollected.json"))
    monkeypatch.setattr(webapp, "_orders_csv_cached", lambda: ORDERS_CSV)
    monkeypatch.setattr(webapp, "_fetch_tracking", lambda pkg: TRACKING[pkg])
    # PIN MAIL_BCC: app.py loads the repo's data/.mail_env into the environment, so a dev box
    # (which has one) and CI (which does not) would report a different `bcc_missing` — green
    # here, red there. Tests that need the missing-BCC branch delenv it explicitly.
    monkeypatch.setenv("MAIL_BCC", "owner@example.com")
    sent = []
    monkeypatch.setattr(webapp, "_send_mail_html",
                        lambda to, subject, body, bcc=None, **kw:
                        sent.append({"to": to, "subject": subject, "body": body,
                                     "bcc": bcc, **kw}) or True)
    return {"tmp": tmp_path, "sent": sent}


# ── auth gate ─────────────────────────────────────────────────────────────────
def test_automations_endpoints_require_login(iso):
    anon = webapp.app.test_client()
    assert anon.get("/api/automations").status_code == 401
    assert anon.get("/api/posta-uncollected").status_code == 401
    assert anon.post("/api/automations/posta_uncollected/toggle",
                     json={"enabled": True}).status_code == 401
    assert anon.post("/api/automations/posta_uncollected/run").status_code == 401


# ── registry + status ─────────────────────────────────────────────────────────
def test_automations_status_default_disabled(iso):
    c = authed_client()
    j = c.get("/api/automations").get_json()
    (a,) = [x for x in j["automations"] if x["key"] == "posta_uncollected"]
    assert a["name"] == "Nevyzdvihnuté zásielky — Pošta SK"
    assert a["enabled"] is False                 # SAFETY: deploy starts stopped
    assert a["schedule"] == "denne o 09:00"
    assert a["running"] is False


def test_toggle_persists_enabled(iso):
    c = authed_client()
    r = c.post("/api/automations/posta_uncollected/toggle", json={"enabled": True})
    assert r.get_json()["ok"] is True
    (a,) = [x for x in c.get("/api/automations").get_json()["automations"]
            if x["key"] == "posta_uncollected"]
    assert a["enabled"] is True and a["next_run"] != ""
    st = json.loads((iso["tmp"] / "automations.json").read_text())
    assert st["posta_uncollected"]["enabled"] is True
    c.post("/api/automations/posta_uncollected/toggle", json={"enabled": False})
    (a,) = [x for x in c.get("/api/automations").get_json()["automations"]
            if x["key"] == "posta_uncollected"]
    assert a["enabled"] is False and a["next_run"] == ""


def test_toggle_unknown_automation_404(iso):
    c = authed_client()
    assert c.post("/api/automations/nope/toggle", json={"enabled": True}).status_code == 404
    assert c.post("/api/automations/nope/run").status_code == 404


# ── the full Pošta run (mocked edges) ─────────────────────────────────────────
def test_posta_run_sends_first_mail_and_surfaces_invalid(iso):
    stats = webapp.run_posta_uncollected()
    # api_skipped (#222) is 0 on a first run — nothing is in the terminal cache yet, so every
    # shipment is genuinely fetched. Kept as an EXACT dict on purpose: a new stats key has to be
    # a deliberate change, not something that quietly appears.
    assert stats == {"checked": 3, "uncollected": 1, "invalid": 1, "errors": 0,
                     "emails_sent": 1, "emails_failed": 0, "bcc_missing": False,
                     "api_skipped": 0,
                     # #282 — all three orders in this window are dispatched AND carry a package
                     # number, and the newest is 3 days old, so the source is healthy and the run
                     # is NOT degraded. The alarm has to be quiet here or it is worthless.
                     # PR #298 review, A2 — the Pošta escalation is fail-CLOSED on an unusable
                     # status configuration, like the reminders already were. This fixture's
                     # configuration is healthy, so both keys report the open path: nothing was
                     # blocked and the run is not degraded for that reason either.
                     "status_config_broken": False, "emails_blocked": 0,
                     "source_degraded": False, "dispatched_orders": 3,
                     "dispatched_without_package": 0, "missing_package": 0,
                     "days_since_last_package": 3, "dispatched_status_unknown": False}
    # exactly ONE customer mail, template #1. run_posta_uncollected no longer
    # passes an explicit bcc — _send_mail_html itself defaults it to MAIL_BCC
    # (tested directly below); here it's stubbed, so bcc arrives as None.
    (m,) = iso["sent"]
    assert m["to"] == "jan@example.com"
    assert m["subject"] == "Vaša zásielka čaká na vyzdvihnutie | EF000000002SK"
    assert "Skalica 1" in m["body"]
    assert m["bcc"] is None
    # state file: escalation bumped for the mailed order only
    st = json.loads((iso["tmp"] / "posta_uncollected.json").read_text())
    assert st["escalation"] == {"2026100": f"1|{TODAY.isoformat()}"}
    (u,) = st["uncollected"]
    assert u["packageNumber"] == "EF000000002SK"
    assert u["count"] == 1 and u["call_needed"] is False
    (i,) = st["invalid"]
    assert i["packageNumber"] == "00000000000003"    # surfaced, never silent
    assert st["errors"] == []
    # the tab endpoint serves the same data
    c = authed_client()
    j = c.get("/api/posta-uncollected").get_json()
    assert j["stats"]["uncollected"] == 1 and len(j["invalid"]) == 1


def test_posta_run_same_day_does_not_remail(iso):
    webapp.run_posta_uncollected()
    stats = webapp.run_posta_uncollected()           # second run the same day
    assert stats["emails_sent"] == 0
    assert len(iso["sent"]) == 1                     # still just the first mail
    st = json.loads((iso["tmp"] / "posta_uncollected.json").read_text())
    assert st["escalation"] == {"2026100": f"1|{TODAY.isoformat()}"}


def test_posta_run_smtp_failure_keeps_state_for_retry(iso, monkeypatch):
    monkeypatch.setattr(webapp, "_send_mail_html",
                        lambda to, subject, body, bcc=None, **kw: False)
    stats = webapp.run_posta_uncollected()
    assert stats["emails_sent"] == 0 and stats["emails_failed"] == 1
    st = json.loads((iso["tmp"] / "posta_uncollected.json").read_text())
    assert st["escalation"] == {}                    # NOT bumped → retried next run
    (u,) = st["uncollected"]
    assert u["count"] == 0                           # displayed honestly


def test_posta_crash_mid_run_keeps_sent_mail_state(iso, monkeypatch):
    """A crash AFTER a customer mail went out must never lose the escalation
    bump (a lost bump = the same customer gets the same mail again tomorrow).
    The bump is persisted immediately per send, not only at run end."""
    real_eval = webapp.posta_uncollected.evaluate_shipment

    def boom(shipment, tracking_json, state_value, today=None):
        if shipment["packageNumber"] == "EF000000001SK":     # third shipment
            raise RuntimeError("simulovaný pád uprostred behu")
        return real_eval(shipment, tracking_json, state_value, today)

    monkeypatch.setattr(webapp.posta_uncollected, "evaluate_shipment", boom)
    with pytest.raises(RuntimeError):
        webapp.run_posta_uncollected()
    assert len(iso["sent"]) == 1                             # mail did go out
    st = json.loads((iso["tmp"] / "posta_uncollected.json").read_text())
    assert st["escalation"] == {"2026100": f"1|{TODAY.isoformat()}"}


def test_posta_run_tracking_error_recorded(iso, monkeypatch):
    def flaky(pkg):
        if pkg == "EF000000002SK":
            raise RuntimeError("api.posta.sk timeout po 3 pokusoch")
        return TRACKING[pkg]
    monkeypatch.setattr(webapp, "_fetch_tracking", flaky)
    stats = webapp.run_posta_uncollected()
    assert stats["errors"] == 1 and stats["emails_sent"] == 0
    st = json.loads((iso["tmp"] / "posta_uncollected.json").read_text())
    (e,) = st["errors"]
    assert e["packageNumber"] == "EF000000002SK" and "timeout" in e["error"]


def test_posta_escalation_pruned_when_order_leaves_window(iso):
    (iso["tmp"] / "posta_uncollected.json").write_text(json.dumps({
        "escalation": {"2026100": "1|2026-06-01", "1999999": "3|2026-05-01"}}),
        encoding="utf-8")
    webapp.run_posta_uncollected()
    st = json.loads((iso["tmp"] / "posta_uncollected.json").read_text())
    assert "1999999" not in st["escalation"]          # gone from the source window
    assert "2026100" in st["escalation"]              # still tracked (and bumped to 2)


# ── #282 part 1 — a dead shipment source must not report a healthy run ────────
# From 2.7. the orders export stopped carrying package numbers. The automation's ONLY source of
# shipments is that column, so every run afterwards checked fewer and fewer parcels (21 → 13 → 9
# → 6 → 4) and still ended `ok`; the tab said „0 nevyzdvihnutých" while a real parcel ran out its
# 27.7. pickup deadline unnoticed. `checked` cannot tell that apart from a quiet week — only the
# coverage stats can.
def _dead_source_csv(days_ago_tracked=26, dispatched_without=87):
    """The live shape of the outage: ONE order still carrying a (long since delivered) package
    number and 87 dispatched orders carrying none."""
    d = (TODAY - timedelta(days=days_ago_tracked)).isoformat()
    rows = [f"7000;{d} 10:00:00;Vybavená;a@example.com;;Zákazník A;EF000000001SK;1/M"]
    for i in range(dispatched_without):
        di = (TODAY - timedelta(days=i % 30)).isoformat()
        rows.append(f"{7100 + i};{di} 10:00:00;Vybavená;b{i}@example.com;;Zákazník B{i};;1/M")
    return ("code;date;statusName;email;phone;billFullName;packageNumber;itemCode\r\n"
            + "\r\n".join(rows) + "\r\n").encode("cp1250")


def test_posta_run_flags_a_dead_shipment_source(iso, monkeypatch):
    monkeypatch.setattr(webapp, "_orders_csv_cached", _dead_source_csv)
    stats = webapp.run_posta_uncollected()
    assert stats["source_degraded"] is True
    assert stats["dispatched_orders"] == 88
    assert stats["dispatched_without_package"] == 87
    assert stats["days_since_last_package"] == 26
    # the number that used to be the ONLY visible signal — and it looks like a calm day
    assert stats["checked"] == 1 and stats["uncollected"] == 0
    # Also persisted into the run's own store. NOTE what this does and does not prove: the TAB
    # renders from `last_result` in automations.json (what the runner stores from this return
    # value), not from here — this copy is the diagnostic record that survives in the store next
    # to the shipment list the manager is looking at.
    st = json.loads((iso["tmp"] / "posta_uncollected.json").read_text())
    assert st["stats"]["source_degraded"] is True


def _renamed_status_csv():
    """The blind spot itself: six eligible orders in the window, every one of them carrying a
    package number, and NOT ONE in a status the code recognises as dispatched (Shoptet renamed
    „Vybavená")."""
    rows = [f"{7200 + i};{(TODAY - timedelta(days=i)).isoformat()} 10:00:00;Odoslaná;"
            f"c{i}@example.com;;Zákazník C{i};EF00000000{i}SK;1/M" for i in range(6)]
    return ("code;date;statusName;email;phone;billFullName;packageNumber;itemCode\r\n"
            + "\r\n".join(rows) + "\r\n").encode("cp1250")


def test_posta_blind_spot_log_states_the_true_order_count(iso, monkeypatch, caplog):
    """The ERROR that fires when the dispatched-status vocabulary moves must not contradict
    itself. It logged `missing_package + dispatched_orders`, but in the only branch that fires
    (dispatched == 0) that counts orders WITHOUT a package number only — so a window of six
    eligible orders that all carry one reported „v okne je 0 objednávok, ale ANI JEDNA nemá stav
    Vybavená". Zero orders and none-of-them are the same statement; the reader learns nothing and
    is told a falsehood about the one number that matters."""
    monkeypatch.setattr(webapp, "_orders_csv_cached", _renamed_status_csv)
    # every one of them already delivered — the run itself is a no-op, so the only ERROR this
    # test can see is the blind-spot one it is about
    monkeypatch.setattr(webapp, "_fetch_tracking", lambda pkg: _fix("tracking_delivered.json"))
    with caplog.at_level(logging.ERROR):
        stats = webapp.run_posta_uncollected()
    assert stats["dispatched_status_unknown"] is True
    blind = [r.getMessage() for r in caplog.records if "ANI JEDNA" in r.getMessage()]
    assert len(blind) == 1, blind
    assert "v okne je 6 objednávok" in blind[0]
    assert "v okne je 0 objednávok" not in blind[0]


def test_posta_source_alarm_never_widens_what_gets_mailed(iso, monkeypatch):
    """SAFETY: the alarm is a counter over the export, nothing more. Once the source is fixed, 130
    shipments become visible at once and an escalation avalanche is a real risk (#282) — so this
    pins that raising the alarm does not itself send anything, nor pull an order with no package
    number into the escalation."""
    monkeypatch.setattr(webapp, "_orders_csv_cached", _dead_source_csv)
    stats = webapp.run_posta_uncollected()
    assert stats["source_degraded"] is True
    assert iso["sent"] == []                          # not one mail
    assert stats["emails_sent"] == 0
    st = json.loads((iso["tmp"] / "posta_uncollected.json").read_text())
    assert st["escalation"] == {}                     # nobody entered the cadence


def test_run_now_endpoint_executes_in_background(iso):
    c = authed_client()
    r = c.post("/api/automations/posta_uncollected/run")
    assert r.get_json() == {"ok": True, "started": True}
    webapp.RUNNER._threads["posta_uncollected"].join(timeout=15)
    (a,) = [x for x in c.get("/api/automations").get_json()["automations"]
            if x["key"] == "posta_uncollected"]
    assert a["last_status"] == "ok"
    assert a["last_result"]["checked"] == 3
    assert a["enabled"] is False                     # run-now must not enable the schedule
    assert len(iso["sent"]) == 1


# ── "BCC vždy" convention (#126, decided on #105): every automation e-mail is
# BCC'd to MAIL_BCC (data/.mail_env) unless the caller explicitly overrides it.
class _FakeSMTP:
    """Captures the sendmail() recipient list; no real network."""
    def __init__(self, *a, **kw):
        pass

    def starttls(self):
        pass

    def login(self, user, pw):
        pass

    def sendmail(self, frm, rcpt, msg):
        _FakeSMTP.last_rcpt = rcpt

    def quit(self):
        pass


def test_send_mail_html_defaults_bcc_to_mail_bcc_env(monkeypatch):
    monkeypatch.setenv("MAIL_HOST", "smtp.example.test")
    monkeypatch.setenv("MAIL_BCC", "owner@example.com")
    monkeypatch.setattr(webapp.smtplib, "SMTP", _FakeSMTP)
    ok = webapp._send_mail_html("zak@example.com", "predmet", "<p>telo</p>")
    assert ok is True
    assert _FakeSMTP.last_rcpt == ["zak@example.com", "owner@example.com"]


def test_send_mail_html_explicit_bcc_overrides_mail_bcc_env(monkeypatch):
    monkeypatch.setenv("MAIL_HOST", "smtp.example.test")
    monkeypatch.setenv("MAIL_BCC", "owner@example.com")
    monkeypatch.setattr(webapp.smtplib, "SMTP", _FakeSMTP)
    webapp._send_mail_html("zak@example.com", "predmet", "<p>telo</p>", bcc="iny@example.com")
    assert _FakeSMTP.last_rcpt == ["zak@example.com", "iny@example.com"]


def test_send_mail_html_no_mail_bcc_env_sends_without_bcc(monkeypatch):
    monkeypatch.setenv("MAIL_HOST", "smtp.example.test")
    monkeypatch.delenv("MAIL_BCC", raising=False)
    monkeypatch.setattr(webapp.smtplib, "SMTP", _FakeSMTP)
    webapp._send_mail_html("zak@example.com", "predmet", "<p>telo</p>")
    assert _FakeSMTP.last_rcpt == ["zak@example.com"]


# ═════════════════════════════════════════════════════════════════════════════════
# DÁVKA A — e-mail safety of the Pošta run (VYLEPŠENIE 3/4). Both failures below end
# the same way: the customer gets the escalation mail a second time tomorrow.
# ═════════════════════════════════════════════════════════════════════════════════

# ── VYLEPŠENIE 3 — a failing store write must not blow up the whole run ────────────
def test_posta_persist_failure_is_logged_and_the_run_continues(iso, monkeypatch, caplog):
    """The escalation bump is persisted IMMEDIATELY after each successful send. If that write
    fails (full disk / permissions) the run must log the order code for manual follow-up and
    keep going — not abort and leave the remaining shipments unchecked."""
    real_save = webapp._save_posta_state
    n = {"calls": 0}

    def flaky_save(data):
        n["calls"] += 1
        if n["calls"] == 1:                        # the immediate-persist right after the send
            raise OSError("[Errno 28] No space left on device")
        return real_save(data)

    monkeypatch.setattr(webapp, "_save_posta_state", flaky_save)
    with caplog.at_level("ERROR"):
        stats = webapp.run_posta_uncollected()     # must NOT propagate
    assert stats["checked"] == 3                   # every shipment still processed
    assert stats["emails_sent"] == 1
    assert any("2026100" in r.getMessage() for r in caplog.records if r.levelname == "ERROR")


# ── VYLEPŠENIE 4 — „BCC vždy": no MAIL_BCC → the customer mail does not go out ─────
def test_posta_run_does_not_email_the_customer_without_mail_bcc(iso, monkeypatch):
    monkeypatch.delenv("MAIL_BCC", raising=False)
    monkeypatch.setenv("MAIL_HOST", "smtp.example.test")
    monkeypatch.setenv("MAIL_PORT", "587")
    monkeypatch.setattr(webapp.smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setattr(webapp.smtplib, "SMTP_SSL", _FakeSMTP)
    monkeypatch.setattr(webapp, "_send_mail_html", _REAL_SEND_MAIL_HTML)
    _FakeSMTP.last_rcpt = None
    stats = webapp.run_posta_uncollected()
    assert _FakeSMTP.last_rcpt is None             # nothing reached the wire
    assert stats["emails_sent"] == 0 and stats["emails_failed"] == 1
    # escalation NOT bumped → the mail is retried once MAIL_BCC is configured
    st = json.loads((iso["tmp"] / "posta_uncollected.json").read_text())
    assert st.get("escalation", {}) == {}


# ── PR #223 review, MINOR 7 — the dead automation must be visible in the UI ────────
def test_posta_run_surfaces_missing_mail_bcc_in_its_stats(iso, monkeypatch):
    """Refusing to send without MAIL_BCC is only an ERROR line in the log today, so the tab
    shows a run that quietly mailed nobody. The tab needs the reason."""
    monkeypatch.delenv("MAIL_BCC", raising=False)
    stats = webapp.run_posta_uncollected()
    assert stats["bcc_missing"] is True
    st = json.loads((iso["tmp"] / "posta_uncollected.json").read_text())
    assert st["stats"]["bcc_missing"] is True


def test_posta_persist_failure_still_shows_the_shipment_in_the_tab(iso, monkeypatch):
    """A failed immediate-persist must not hide the shipment from „Nevyzdvihnuté" — that row is
    exactly the one the error log tells the manager to check by hand. (The escalation bump also
    survives: it is held in memory and re-persisted by the run's final save.)"""
    real_save = webapp._save_posta_state
    n = {"calls": 0}

    def flaky_save(data):
        n["calls"] += 1
        if n["calls"] == 1:
            raise OSError("[Errno 28] No space left on device")
        return real_save(data)

    monkeypatch.setattr(webapp, "_save_posta_state", flaky_save)
    stats = webapp.run_posta_uncollected()
    assert stats["uncollected"] == 1
    st = json.loads((iso["tmp"] / "posta_uncollected.json").read_text())
    assert [u["orderCode"] for u in st["uncollected"]] == ["2026100"]
    assert st["escalation"] == {"2026100": f"1|{TODAY.isoformat()}"}


# ═════════════════════════════════════════════════════════════════════════════════
# #222 — the daily run re-fetched tracking for EVERY shipment in the 30-day window,
# including long-delivered ones, sequentially at up to 180 s each (60 s timeout ×
# 3 tries). A delivered/returned parcel can never change again, so that call buys
# nothing and on a slow Pošta SK day it is what makes the 09:00 run drag on.
# ═════════════════════════════════════════════════════════════════════════════════
def test_delivered_shipment_is_recorded_as_terminal_and_not_tracked_again(iso, monkeypatch):
    webapp.run_posta_uncollected()
    st = json.loads((iso["tmp"] / "posta_uncollected.json").read_text())
    assert st["terminal"]["EF000000001SK"]["state"] == "delivered"

    asked = []
    monkeypatch.setattr(webapp, "_fetch_tracking",
                        lambda pkg: asked.append(pkg) or TRACKING[pkg])
    stats = webapp.run_posta_uncollected()
    assert "EF000000001SK" not in asked               # the delivered parcel: no API call
    assert "EF000000002SK" in asked                   # the uncollected one: still checked daily
    assert stats["api_skipped"] == 1
    assert stats["checked"] == 3                      # every shipment is still accounted for


def test_an_uncollected_shipment_is_never_cached_as_terminal(iso):
    """'notified' is precisely the state the automation chases — caching it would freeze the
    escalation and the customer would never get mails #2-#4."""
    webapp.run_posta_uncollected()
    st = json.loads((iso["tmp"] / "posta_uncollected.json").read_text())
    assert "EF000000002SK" not in (st.get("terminal") or {})
    assert "00000000000003" not in (st.get("terminal") or {})   # invalid_format is not final


def test_terminal_cache_is_pruned_when_the_shipment_leaves_the_window(iso):
    (iso["tmp"] / "posta_uncollected.json").write_text(json.dumps({
        "terminal": {"EF999999999SK": {"state": "delivered", "at": "2026-01-01"}}}),
        encoding="utf-8")
    webapp.run_posta_uncollected()
    st = json.loads((iso["tmp"] / "posta_uncollected.json").read_text())
    assert "EF999999999SK" not in st["terminal"]      # bounded by the 30-day source window
    assert "EF000000001SK" in st["terminal"]


def test_a_corrupt_terminal_cache_does_not_skip_the_shipment(iso, monkeypatch):
    """Garbage in the cache must degrade to 'check it', never to 'silently ignore it'."""
    (iso["tmp"] / "posta_uncollected.json").write_text(json.dumps({
        "terminal": {"EF000000001SK": "nonsense"}}), encoding="utf-8")
    asked = []
    monkeypatch.setattr(webapp, "_fetch_tracking",
                        lambda pkg: asked.append(pkg) or TRACKING[pkg])
    webapp.run_posta_uncollected()
    assert "EF000000001SK" in asked


def test_a_terminal_cache_entry_for_a_different_order_does_not_skip_the_shipment(iso, monkeypatch):
    """Tracking numbers are typed into Shoptet by hand, so the same one can end up on a second
    order. The cached verdict must prove it belongs to THIS order — otherwise a stale
    „delivered" would silence a genuinely uncollected parcel and the customer would never be
    told. The cache is an optimisation; when it cannot prove itself it must defer to the API."""
    (iso["tmp"] / "posta_uncollected.json").write_text(json.dumps({
        "terminal": {"EF000000002SK": {"state": "delivered", "at": "2026-07-01",
                                       "code": "SOMEONE-ELSE"}}}), encoding="utf-8")
    asked = []
    monkeypatch.setattr(webapp, "_fetch_tracking",
                        lambda pkg: asked.append(pkg) or TRACKING[pkg])
    stats = webapp.run_posta_uncollected()
    assert "EF000000002SK" in asked                   # checked, not silently skipped
    assert stats["emails_sent"] == 1                  # …and the customer IS told
    st = json.loads((iso["tmp"] / "posta_uncollected.json").read_text())
    assert st["terminal"]["EF000000001SK"]["code"] == "2026105"   # entries carry their order


def test_a_stale_terminal_cache_entry_is_re_verified(iso, monkeypatch):
    """A cached verdict is trusted for POSTA_TERMINAL_RECHECK_DAYS, then checked once more. It
    may only ever save an API call — never silence a customer notice — so a single wrong or
    freak reading has to self-heal within a week instead of sticking for the whole 30-day
    source window (the `at` field was write-only before this)."""
    (iso["tmp"] / "posta_uncollected.json").write_text(json.dumps({
        "terminal": {"EF000000002SK": {"state": "delivered", "at": "2020-01-01",
                                       "code": "2026100"}}}), encoding="utf-8")
    asked = []
    monkeypatch.setattr(webapp, "_fetch_tracking",
                        lambda pkg: asked.append(pkg) or TRACKING[pkg])
    stats = webapp.run_posta_uncollected()
    assert "EF000000002SK" in asked                   # stale verdict → re-verified
    assert stats["emails_sent"] == 1                  # …and it was wrong: the customer IS told
    st = json.loads((iso["tmp"] / "posta_uncollected.json").read_text())
    assert "EF000000002SK" not in st["terminal"]      # the wrong entry is gone, not refreshed


def test_a_fresh_terminal_cache_entry_is_still_trusted(iso, monkeypatch):
    """…and the re-check window must not defeat the optimisation itself."""
    webapp.run_posta_uncollected()
    asked = []
    monkeypatch.setattr(webapp, "_fetch_tracking",
                        lambda pkg: asked.append(pkg) or TRACKING[pkg])
    webapp.run_posta_uncollected()
    assert "EF000000001SK" not in asked


@pytest.mark.parametrize("bad_at", ["zzz", "2099-01-01"])
def test_a_non_date_at_value_does_not_freeze_a_shipment(iso, monkeypatch, bad_at):
    """`at` is compared as a raw STRING, so ANY value sorting above the cutoff keeps the entry
    „fresh" forever: „zzz" left by a partial write, or a future date after a clock jump. The
    parcel would then be skipped for the whole 30-day source window with the weekly re-check net
    silently switched off — and an uncollected parcel would never be escalated. Corruption,
    ambiguity and age must ALL degrade to „check it" (the rule the block itself states), so the
    trusted range is bounded from BOTH sides. (PR #224 adversarial review.)"""
    (iso["tmp"] / "posta_uncollected.json").write_text(json.dumps({
        "terminal": {"EF000000002SK": {"state": "delivered", "at": bad_at,
                                       "code": "2026100"}}}), encoding="utf-8")
    asked = []
    monkeypatch.setattr(webapp, "_fetch_tracking",
                        lambda pkg: asked.append(pkg) or TRACKING[pkg])
    stats = webapp.run_posta_uncollected()
    assert "EF000000002SK" in asked                   # checked, not silently skipped
    assert stats["emails_sent"] == 1                  # …and the customer IS told
    st = json.loads((iso["tmp"] / "posta_uncollected.json").read_text())
    assert "EF000000002SK" not in st["terminal"]      # the bogus entry is dropped, not refreshed


# ═════════════════════════════════════════════════════════════════════════════════
# #225 — the ESCALATION store is a dedup store too: it records how many mails each
# uncollected shipment already got. Degrading a corrupt one to {} restarts every
# escalation at count 0, so every customer with a parcel at the post office is mailed
# AGAIN. Same fail-closed rule as data/out/orders_reminder.json: preserve the bytes,
# abort the run, mail nobody. A MISSING file remains a legitimate first run.
# ═════════════════════════════════════════════════════════════════════════════════
_TRUNCATED = '{"escalation": {"2026100": "1|2026-0'          # partial write / full disk


def test_corrupt_escalation_store_aborts_the_run_and_mails_nobody(iso, monkeypatch):
    webapp.run_posta_uncollected()                     # run 1: one escalation mail goes out
    assert len(iso["sent"]) == 1
    p = iso["tmp"] / "posta_uncollected.json"
    p.write_text(_TRUNCATED, encoding="utf-8")
    iso["sent"].clear()
    asked = []
    monkeypatch.setattr(webapp, "_fetch_tracking",
                        lambda pkg: asked.append(pkg) or TRACKING[pkg])

    with pytest.raises(webapp.DedupStoreCorrupt):
        webapp.run_posta_uncollected()

    assert iso["sent"] == []                           # NOTHING was mailed…
    assert asked == []                                 # …and the run stopped before any API call
    assert p.read_text(encoding="utf-8") == _TRUNCATED  # corrupt bytes preserved
    backups = list(iso["tmp"].glob("posta_uncollected.json.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == _TRUNCATED


def test_missing_posta_store_is_a_normal_first_run(iso):
    assert not (iso["tmp"] / "posta_uncollected.json").exists()
    stats = webapp.run_posta_uncollected()             # must NOT raise
    assert stats["emails_sent"] == 1


def test_corrupt_escalation_store_surfaces_as_an_automation_error(iso):
    c = authed_client()
    (iso["tmp"] / "posta_uncollected.json").write_text(_TRUNCATED, encoding="utf-8")
    assert c.post("/api/automations/posta_uncollected/run").get_json()["started"] is True
    webapp.RUNNER._threads["posta_uncollected"].join(timeout=15)
    (a,) = [x for x in c.get("/api/automations").get_json()["automations"]
            if x["key"] == "posta_uncollected"]
    assert a["last_status"] == "error"
    assert "poškoden" in a["last_error"].lower()
    assert iso["sent"] == []


def test_the_posta_tab_still_renders_with_a_corrupt_store(iso):
    """Read-only DISPLAY keeps degrading gracefully — fail-closed is about SENDING."""
    c = authed_client()
    webapp.run_posta_uncollected()
    (iso["tmp"] / "posta_uncollected.json").write_text(_TRUNCATED, encoding="utf-8")
    r = c.get("/api/posta-uncollected")
    assert r.status_code == 200
    assert r.get_json()["uncollected"] == []


# ═════════════════════════════════════════════════════════════════════════════════
# #217 — e-mail preview before sending, for the escalation mails too. Read-only by
# construction: no tracking call, no escalation bump, no SMTP, no write.
# ═════════════════════════════════════════════════════════════════════════════════
def test_posta_preview_requires_login(iso):
    anon = webapp.app.test_client()
    assert anon.post("/api/posta-uncollected/preview",
                     json={"package": "EF000000002SK"}).status_code == 401


def test_posta_preview_returns_the_next_escalation_mail_and_sends_nothing(iso, monkeypatch):
    webapp.run_posta_uncollected()                 # one uncollected shipment, mail #1 sent
    c = authed_client()
    iso["sent"].clear()
    before = (iso["tmp"] / "posta_uncollected.json").read_bytes()
    asked = []
    monkeypatch.setattr(webapp, "_fetch_tracking",
                        lambda pkg: asked.append(pkg) or TRACKING[pkg])

    r = c.post("/api/posta-uncollected/preview", json={"package": "EF000000002SK"})
    assert r.status_code == 200
    j = r.get_json()
    assert j["recipient"] == "jan@example.com"
    assert j["already_sent"] == 1 and j["count"] == 2 and j["max_reached"] is False
    # the SAME builder the run uses, fed the shipment's REAL office / retention values — not a
    # lookalike template that could drift away from what is actually sent
    (row,) = json.loads((iso["tmp"] / "posta_uncollected.json").read_text())["uncollected"]
    subject, html = posta_uncollected.build_email(
        2, row["name"], row["packageNumber"], row["office_name"], row["office_addr"],
        row["retained_till"])
    assert (j["subject"], j["html"]) == (subject, html)
    assert j["subject"].startswith("Pripomienka")   # mail #2 of the cadence

    assert iso["sent"] == []                        # nothing e-mailed
    assert asked == []                              # no Pošta SK round-trip
    assert (iso["tmp"] / "posta_uncollected.json").read_bytes() == before   # nothing written


def test_posta_preview_flags_an_exhausted_cadence(iso):
    """After the 4th mail the automation sends no more, so previewing a „5th" would be a lie —
    the endpoint shows the LAST one that went out and says the cadence is exhausted."""
    webapp.run_posta_uncollected()
    c = authed_client()
    st = json.loads((iso["tmp"] / "posta_uncollected.json").read_text())
    st["uncollected"][0]["count"] = 4
    (iso["tmp"] / "posta_uncollected.json").write_text(json.dumps(st, ensure_ascii=False),
                                                       encoding="utf-8")
    j = c.post("/api/posta-uncollected/preview",
               json={"package": "EF000000002SK"}).get_json()
    assert j["count"] == 4 and j["max_reached"] is True


def test_posta_preview_rejects_a_missing_or_unknown_package(iso):
    webapp.run_posta_uncollected()
    c = authed_client()
    iso["sent"].clear()
    assert c.post("/api/posta-uncollected/preview", json={}).status_code == 400
    assert c.post("/api/posta-uncollected/preview",
                  json={"package": "EF999999999SK"}).status_code == 404
    assert iso["sent"] == []


# ═════════════════════════════════════════════════════════════════════════════════
# PR #228 adversarial review — same class as the orders_reminder finding: the guard
# only validated the OUTER dict, so `{"escalation": null}` still restarted every
# escalation at count 0 and re-sent mail #1 to customers who already had it.
# ═════════════════════════════════════════════════════════════════════════════════
def test_a_non_dict_escalation_map_is_corruption_not_an_empty_store(iso):
    webapp.run_posta_uncollected()                     # run 1: escalation mail #1 goes out
    assert len(iso["sent"]) == 1
    p = iso["tmp"] / "posta_uncollected.json"
    st = json.loads(p.read_text())
    st["escalation"] = None                            # the dedup MAP itself is gone
    p.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    iso["sent"].clear()

    with pytest.raises(webapp.DedupStoreCorrupt):
        webapp.run_posta_uncollected()
    assert iso["sent"] == []                           # would have re-sent mail #1
    assert json.loads(p.read_text())["escalation"] is None      # not rewritten as empty


def test_a_corrupt_terminal_cache_does_not_block_the_run(iso, monkeypatch):
    """Deliberate scoping: `terminal` is a PERFORMANCE cache (it only ever saves an API call),
    so losing it cannot cause a duplicate mail — while blocking the run on it WOULD stop a
    genuine customer notification. Only `escalation`, the record of what was already sent, is
    fail-closed."""
    p = iso["tmp"] / "posta_uncollected.json"
    p.write_text(json.dumps({"escalation": {}, "terminal": "kaboom"}), encoding="utf-8")
    asked = []
    monkeypatch.setattr(webapp, "_fetch_tracking",
                        lambda pkg: asked.append(pkg) or TRACKING[pkg])
    stats = webapp.run_posta_uncollected()             # must NOT raise
    assert stats["emails_sent"] == 1                   # the customer IS notified
    assert "EF000000002SK" in asked


def test_the_posta_tab_says_the_store_is_corrupt(iso):
    c = authed_client()
    webapp.run_posta_uncollected()
    assert c.get("/api/posta-uncollected").get_json().get("store_corrupt") in (False, None)
    (iso["tmp"] / "posta_uncollected.json").write_text(_TRUNCATED, encoding="utf-8")
    j = c.get("/api/posta-uncollected").get_json()
    assert j["store_corrupt"] is True and j["uncollected"] == []


# ═════════════════════════════════════════════════════════════════════════════════
# PR #228 review, second pass — same class on the escalation store. `parse_notified`
# degrades ANY non-string to (0, None), so a garbage value under one order code read
# as „never notified" and re-sent escalation mail #1 to a customer who already had it.
# ═════════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("garbage", [None, 42, {"count": 2}, ["2|2026-07-18"]])
def test_an_unreadable_escalation_record_does_not_restart_the_cadence(iso, monkeypatch, garbage):
    webapp.run_posta_uncollected()                     # run 1: escalation mail #1 goes out
    assert len(iso["sent"]) == 1
    p = iso["tmp"] / "posta_uncollected.json"
    st = json.loads(p.read_text())
    st["escalation"]["2026100"] = garbage              # its record is now unreadable
    p.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    iso["sent"].clear()
    asked = []
    monkeypatch.setattr(webapp, "_fetch_tracking",
                        lambda pkg: asked.append(pkg) or TRACKING[pkg])

    stats = webapp.run_posta_uncollected()             # must not raise, must not re-mail
    assert iso["sent"] == []
    assert stats["emails_sent"] == 0
    assert "EF000000002SK" not in asked                # …and not even pay for the API round-trip
    # surfaced on the tab instead of vanishing silently
    (err,) = [e for e in json.loads(p.read_text())["errors"] if e["orderCode"] == "2026100"]
    assert "poškoden" in err["error"].lower()
