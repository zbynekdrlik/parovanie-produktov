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
    sent = []
    monkeypatch.setattr(webapp, "_send_mail_html",
                        lambda to, subject, body, bcc=None:
                        sent.append({"to": to, "subject": subject,
                                     "body": body, "bcc": bcc}) or True)
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
    assert stats == {"checked": 3, "uncollected": 1, "invalid": 1, "errors": 0,
                     "emails_sent": 1, "emails_failed": 0}
    # exactly ONE customer mail, template #1, bcc to Marek
    (m,) = iso["sent"]
    assert m["to"] == "jan@example.com"
    assert m["subject"] == "Vaša zásielka čaká na vyzdvihnutie | EF000000002SK"
    assert "Skalica 1" in m["body"]
    assert m["bcc"] == webapp.POSTA_BCC
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
                        lambda to, subject, body, bcc=None: False)
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
