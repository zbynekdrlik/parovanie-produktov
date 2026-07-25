"""Webreview tests for the automations API + the Pošta SK run wiring (#93).

Hermetic: the Pošta API is monkeypatched with saved fixtures, SMTP is
monkeypatched to a capturing stub (asserts a mail WOULD go out — nothing is
ever sent), the orders export is a fixture CSV, and every store path is
redirected to tmp. Mirrors the test_webreview.py import pattern.
"""
import json
import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webreview"))
import app as webapp  # noqa: E402

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
    f"2026101;{D} 09:00:00;Vybavená;eva@example.com;;Eva Testová;06565700348274;3/S\r\n"
    f"2026105;{D} 08:00:00;Vybavená;peter@example.com;;Peter Prevzatý;EF000000001SK;7/A\r\n"
).encode("cp1250")

TRACKING = {
    "EF000000002SK": _fix("tracking_notified_znp.json"),     # uncollected → mail
    "06565700348274": _fix("tracking_invalid_format.json"),  # the n8n-breaking class
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
                     "api_skipped": 0}
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
    assert i["packageNumber"] == "06565700348274"    # surfaced, never silent
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
    assert "06565700348274" not in (st.get("terminal") or {})   # invalid_format is not final


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
