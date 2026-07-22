"""Webreview tests for the „Pripomienky objednávok" run wiring (#105).

Hermetic: the orders export is a fixture CSV, OpenAI classification is monkeypatched, SMTP is a
capturing stub (asserts a mail WOULD go out — nothing is ever sent / no network), and every store
path is redirected to tmp. Mirrors test_webreview_automations.py.
"""
import json
import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webreview"))
import app as webapp  # noqa: E402

from tests.conftest import authed_client  # noqa: E402

TODAY = date.today()
OLD = (TODAY - timedelta(days=10)).isoformat()      # >4d → in scope
FRESH = (TODAY - timedelta(days=1)).isoformat()     # <4d → out of scope

HEADER = ("code;date;statusName;shopRemark;email;phone;billFullName;"
          "itemName;itemAmount;totalPriceWithVat")
ORDERS_CSV = ("\r\n".join([
    HEADER,
    # >4d, NO note → red
    f"20261000;{OLD} 10:00:00;Vybavuje sa;;a@x.sk;+421900;Ján Bez;Bunda;1;99,90",
    # >4d, WITH note, will classify NOT contacted → e-mail
    f"20261001;{OLD} 09:00:00;Vybavuje sa;volať zákazníka;b@x.sk;;Eva Nová;Nohavice;2;50,00",
    # >4d, WITH note, will classify contacted → skipped
    f"20261002;{OLD} 08:00:00;Vybavuje sa;volané so zákazníkom, počká;c@x.sk;;Iva Stará;Čiapka;1;12,00",
    # fresh → excluded
    f"20261003;{FRESH} 08:00:00;Vybavuje sa;volať;d@x.sk;;Fero Mladý;Nôž;1;30,00",
]) + "\r\n").encode("cp1250")

# classification by note text — the monkeypatched OpenAI
_CLASSIFY = {
    "volať zákazníka": False,                     # not contacted → e-mail
    "volané so zákazníkom, počká": True,          # contacted → skip
}


@pytest.fixture
def iso(tmp_path, monkeypatch):
    """Isolate the automation stores + the network/SMTP/OpenAI edges."""
    monkeypatch.setattr(webapp.RUNNER, "state_path", str(tmp_path / "automations.json"))
    monkeypatch.setattr(webapp, "ORDERS_REMINDER_STATE", str(tmp_path / "orders_reminder.json"))
    monkeypatch.setattr(webapp, "_orders_csv_cached", lambda: ORDERS_CSV)
    monkeypatch.setattr(webapp, "_classify_contacted", lambda note: _CLASSIFY[note])
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")   # key present unless a test removes it
    sent = []
    monkeypatch.setattr(webapp, "_send_mail_html",
                        lambda to, subject, body, bcc=None:
                        sent.append({"to": to, "subject": subject,
                                     "body": body, "bcc": bcc}) or True)
    return {"tmp": tmp_path, "sent": sent}


def _store(iso):
    return json.loads((iso["tmp"] / "orders_reminder.json").read_text())


# ── auth gate ─────────────────────────────────────────────────────────────────
def test_orders_reminder_endpoint_requires_login(iso):
    anon = webapp.app.test_client()
    assert anon.get("/api/orders-reminder").status_code == 401
    assert anon.post("/api/automations/orders_reminder/run").status_code == 401


# ── default disabled (#93 contract) ────────────────────────────────────────────
def test_registered_and_default_disabled(iso):
    c = authed_client()
    j = c.get("/api/automations").get_json()
    (a,) = [x for x in j["automations"] if x["key"] == "orders_reminder"]
    assert a["enabled"] is False
    assert a["name"] == "Pripomienky objednávok"
    assert "denne o 08:00" in a["schedule"]


def test_disabled_tick_does_not_run(iso):
    # default state = disabled → a scheduler pass must NOT execute the run (which would fetch the
    # orders export + write the store). No store file after tick == the run never fired.
    webapp.RUNNER.tick_once()
    assert not (iso["tmp"] / "orders_reminder.json").exists()


# ── the run behaviour ──────────────────────────────────────────────────────────
def test_no_note_order_goes_red_no_email(iso):
    stats = webapp.run_orders_reminder()
    st = _store(iso)
    reds = {r["code"] for r in st["red"]}
    assert "20261000" in reds
    assert stats["no_note"] == 1
    # the no-note order never triggers a mail
    assert all(m["to"] != "a@x.sk" for m in iso["sent"])


def test_note_not_contacted_emails_once_and_dedups(iso):
    stats = webapp.run_orders_reminder()
    assert stats["emailed_now"] == 1
    (mail,) = [m for m in iso["sent"] if m["to"] == "b@x.sk"]
    assert mail["subject"] == "📦 Stav vašej objednávky z Forestshop.sk"
    assert mail["bcc"] is None                     # → _send_mail_html defaults to MAIL_BCC
    assert "20261001" in mail["body"] and "Eva Nová" in mail["body"]
    st = _store(iso)
    assert st["orders"]["20261001"]["status"] == "emailed"
    assert {r["code"] for r in st["orange"]} == {"20261001"}

    # second run the SAME day must NOT re-send (dedup via the store)
    iso["sent"].clear()
    stats2 = webapp.run_orders_reminder()
    assert stats2["emailed_now"] == 0
    assert iso["sent"] == []
    st2 = _store(iso)
    assert {r["code"] for r in st2["orange"]} == {"20261001"}   # still shown, from the store


def test_note_contacted_is_skipped_no_email(iso):
    webapp.run_orders_reminder()
    st = _store(iso)
    assert st["orders"]["20261002"]["status"] == "skipped_contacted"
    assert all(m["to"] != "c@x.sk" for m in iso["sent"])
    assert {r["code"] for r in st["skipped"]} == {"20261002"}


def test_openai_key_unset_does_not_email_blind(iso, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    stats = webapp.run_orders_reminder()
    assert iso["sent"] == []                        # NEVER e-mails without a classification
    assert stats["ai_unavailable"] == 2            # both with-note orders
    assert stats["emailed_now"] == 0
    st = _store(iso)
    assert st["orders"] == {}                       # nothing recorded → retried when key returns


def test_classification_error_recorded_not_emailed(iso, monkeypatch):
    def boom(note):
        raise RuntimeError("OpenAI 500")
    monkeypatch.setattr(webapp, "_classify_contacted", boom)
    stats = webapp.run_orders_reminder()
    assert stats["errors"] == 2
    assert iso["sent"] == []
    assert _store(iso)["orders"] == {}              # not recorded → retried next run


def test_smtp_failure_keeps_order_for_retry(iso, monkeypatch):
    monkeypatch.setattr(webapp, "_send_mail_html",
                        lambda to, subject, body, bcc=None: False)
    stats = webapp.run_orders_reminder()
    assert stats["emailed_now"] == 0
    assert stats["errors"] >= 1
    st = _store(iso)
    assert "20261001" not in st.get("orders", {})   # not recorded → retried next run


# ── incremental processing (#153) ────────────────────────────────────────────────
def test_second_run_skips_reclassification_of_unchanged_terminal_orders(iso, monkeypatch):
    webapp.run_orders_reminder()
    st = _store(iso)
    # fingerprints cover every currently-eligible code (incl. the red, never-terminal one) —
    # only DONE codes with a matching fingerprint get the fast path (checked below).
    assert set(st["fingerprints"]) == {"20261000", "20261001", "20261002"}

    # second run, SAME csv (nothing changed) — the AI classifier must NOT be called again for
    # the two already-terminal codes; a call would raise, proving the fast path was taken.
    def boom(note):
        raise AssertionError(f"re-classified an unchanged terminal order: {note!r}")
    monkeypatch.setattr(webapp, "_classify_contacted", boom)
    iso["sent"].clear()
    stats2 = webapp.run_orders_reminder()
    assert iso["sent"] == []                        # no re-send either
    st2 = _store(iso)
    assert {r["code"] for r in st2["orange"]} == {"20261001"}
    assert {r["code"] for r in st2["skipped"]} == {"20261002"}
    assert stats2["orders_4d"] == 3                  # full set still reported (red + 2 terminal)


def test_newly_eligible_order_is_still_caught_after_a_prior_run(iso, monkeypatch):
    # first run with only the base CSV (3 orders) — establishes fingerprints/done state.
    webapp.run_orders_reminder()
    # a 4th order, absent from the first run's CSV (simulating one that just aged past 4 days, or
    # a brand-new order) — has no prior fingerprint, so it must NEVER be treated as 'unchanged'.
    extra = ("20261099;" + OLD + " 07:00:00;Vybavuje sa;volať zákazníka;e@x.sk;;Nový Zákazník;"
             "Klobúk;1;20,00")
    csv2 = ORDERS_CSV.decode("cp1250").rstrip("\r\n") + "\r\n" + extra + "\r\n"
    monkeypatch.setattr(webapp, "_orders_csv_cached", lambda: csv2.encode("cp1250"))
    iso["sent"].clear()
    stats2 = webapp.run_orders_reminder()
    assert stats2["emailed_now"] == 1
    assert any(m["to"] == "e@x.sk" for m in iso["sent"])
    st2 = _store(iso)
    assert "20261099" in st2["orders"] and st2["orders"]["20261099"]["status"] == "emailed"


def test_run_never_touches_manager_stores(iso):
    # seed every manager store in tmp and assert the run writes none of them
    for name in ("decisions.json", "ordered_items.json", "order_pairings.json",
                 "waiting_items.json", "supplier_assignments.json"):
        p = iso["tmp"] / name
        p.write_text("{}")
    before = {p.name: p.read_text() for p in iso["tmp"].glob("*.json")
              if p.name != "orders_reminder.json"}
    webapp.run_orders_reminder()
    after = {p.name: p.read_text() for p in iso["tmp"].glob("*.json")
             if p.name != "orders_reminder.json"}
    assert before == after   # only orders_reminder.json changed


# ── the BCC „vždy" convention reaches the wire through the real _send_mail_html ──
class _FakeSMTP:
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


# ── manual per-row override (#153) ───────────────────────────────────────────────
def _seed(iso):
    """Run once so the state has one RED (no note), one ORANGE (emailed) and one SKIPPED
    (AI said contacted) row — the three cases the override endpoint acts on."""
    webapp.run_orders_reminder()
    return authed_client()


def test_override_requires_login(iso):
    anon = webapp.app.test_client()
    r = anon.post("/api/orders-reminder/override", json={"code": "20261000", "action": "contact"})
    assert r.status_code == 401


def test_override_rejects_bad_payload(iso):
    c = _seed(iso)
    assert c.post("/api/orders-reminder/override", json={"code": "", "action": "contact"}
                  ).status_code == 400
    assert c.post("/api/orders-reminder/override",
                  json={"code": "20261000", "action": "delete"}).status_code == 400


def test_override_unknown_code_404(iso):
    c = _seed(iso)
    r = c.post("/api/orders-reminder/override", json={"code": "nope", "action": "contact"})
    assert r.status_code == 404


def test_override_contact_marks_red_order_contacted_no_email(iso):
    c = _seed(iso)
    r = c.post("/api/orders-reminder/override", json={"code": "20261000", "action": "contact"})
    assert r.status_code == 200 and r.get_json() == {"ok": True, "status": "skipped_contacted"}
    st = _store(iso)
    assert st["orders"]["20261000"]["status"] == "skipped_contacted"
    assert st["orders"]["20261000"]["manual"] is True
    assert {r2["code"] for r2 in st["red"]} == set()          # moved out of red
    assert "20261000" in {r2["code"] for r2 in st["skipped"]}  # into skipped
    assert all(m["to"] != "a@x.sk" for m in iso["sent"])       # never e-mailed


def test_override_contact_rejects_already_resolved_order(iso):
    c = _seed(iso)
    # 20261001 is already 'emailed' (terminal) from the seeded run
    r = c.post("/api/orders-reminder/override", json={"code": "20261001", "action": "contact"})
    assert r.status_code == 409


def test_override_send_now_from_red_row(iso):
    c = _seed(iso)
    r = c.post("/api/orders-reminder/override", json={"code": "20261000", "action": "send"})
    assert r.status_code == 200 and r.get_json() == {"ok": True, "status": "emailed"}
    (mail,) = [m for m in iso["sent"] if m["to"] == "a@x.sk"]
    assert "20261000" in mail["body"] and "Ján Bez" in mail["body"]
    st = _store(iso)
    assert st["orders"]["20261000"]["status"] == "emailed"
    assert st["orders"]["20261000"]["manual"] is True
    assert {r2["code"] for r2 in st["red"]} == set()
    assert "20261000" in {r2["code"] for r2 in st["orange"]}


def test_override_send_now_overrides_wrong_ai_skip(iso):
    # 20261002 was AI-classified 'kontaktovany' (skipped, no mail) — the manager knows better.
    c = _seed(iso)
    r = c.post("/api/orders-reminder/override", json={"code": "20261002", "action": "send"})
    assert r.status_code == 200 and r.get_json() == {"ok": True, "status": "emailed"}
    assert any(m["to"] == "c@x.sk" for m in iso["sent"])
    st = _store(iso)
    assert st["orders"]["20261002"]["status"] == "emailed"
    assert {r2["code"] for r2 in st["skipped"]} == set()
    assert "20261002" in {r2["code"] for r2 in st["orange"]}


def test_override_send_rejects_already_emailed(iso):
    c = _seed(iso)
    r = c.post("/api/orders-reminder/override", json={"code": "20261001", "action": "send"})
    assert r.status_code == 409
    # unchanged — no duplicate send
    assert len([m for m in iso["sent"] if m["to"] == "b@x.sk"]) == 1


def test_override_send_without_email_rejected(iso, monkeypatch):
    # a red row whose customer has no e-mail on file
    csv_noemail = ORDERS_CSV.decode("cp1250").replace(
        "20261000;" + OLD + " 10:00:00;Vybavuje sa;;a@x.sk",
        "20261000;" + OLD + " 10:00:00;Vybavuje sa;;")
    monkeypatch.setattr(webapp, "_orders_csv_cached", lambda: csv_noemail.encode("cp1250"))
    c = _seed(iso)
    iso["sent"].clear()   # drop the seeded run's unrelated 20261001 mail
    r = c.post("/api/orders-reminder/override", json={"code": "20261000", "action": "send"})
    assert r.status_code == 400
    assert iso["sent"] == []


def test_override_send_smtp_failure_reports_error_and_keeps_row(iso, monkeypatch):
    c = _seed(iso)
    monkeypatch.setattr(webapp, "_send_mail_html", lambda *a, **kw: False)
    r = c.post("/api/orders-reminder/override", json={"code": "20261000", "action": "send"})
    assert r.status_code == 502
    st = _store(iso)
    assert "20261000" not in st["orders"]                      # not recorded — can retry
    assert "20261000" in {r2["code"] for r2 in st["red"]}       # still shown as red


def test_reminder_mail_bccs_mail_bcc_on_the_wire(iso, monkeypatch):
    # use the REAL _send_mail_html (not the capturing stub) to prove MAIL_BCC lands in the envelope
    monkeypatch.undo()   # drop the iso stubs, then re-seed only what this test needs
    monkeypatch.setattr(webapp, "ORDERS_REMINDER_STATE", str(iso["tmp"] / "orders_reminder.json"))
    monkeypatch.setattr(webapp, "_orders_csv_cached", lambda: ORDERS_CSV)
    monkeypatch.setattr(webapp, "_classify_contacted", lambda note: _CLASSIFY[note])
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("MAIL_HOST", "smtp.example.test")
    monkeypatch.setenv("MAIL_BCC", "owner@example.com")
    monkeypatch.setattr(webapp.smtplib, "SMTP", _FakeSMTP)
    webapp.run_orders_reminder()
    assert _FakeSMTP.last_rcpt == ["b@x.sk", "owner@example.com"]
