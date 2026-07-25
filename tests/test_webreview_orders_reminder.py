"""Webreview tests for the „Pripomienky objednávok" run wiring (#105).

Hermetic: the orders export is a fixture CSV, OpenAI classification is monkeypatched, SMTP is a
capturing stub (asserts a mail WOULD go out — nothing is ever sent / no network), and every store
path is redirected to tmp. Mirrors test_webreview_automations.py.
"""
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webreview"))
import app as webapp  # noqa: E402

from tests.conftest import authed_client  # noqa: E402

# the UNPATCHED helper — the „BCC vždy" wiring tests need the real SMTP path (behind a fake
# smtplib), not the capturing stub the `iso` fixture installs.
_REAL_SEND_MAIL_HTML = webapp._send_mail_html

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
    # MAIL_BCC must be PINNED, never inherited: app.py loads the repo's data/.mail_env into the
    # environment, so a dev box (which has one) and CI (which does not) would otherwise take
    # different „BCC vždy" branches — green here, red there. Tests that need the missing-BCC
    # behaviour delenv it explicitly.
    monkeypatch.setenv("MAIL_BCC", "owner@example.com")
    sent = []
    monkeypatch.setattr(webapp, "_send_mail_html",
                        lambda to, subject, body, bcc=None, **kw:
                        sent.append({"to": to, "subject": subject, "body": body,
                                     "bcc": bcc, **kw}) or True)
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
                        lambda to, subject, body, bcc=None, **kw: False)
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


def test_override_send_does_not_hold_the_store_lock_during_smtp(iso, monkeypatch):
    # The SMTP call must run OUTSIDE `_lock` (the app's single global lock guards every store) —
    # simulate a concurrent request resolving the SAME code WHILE our _send_mail_html call is
    # "in flight" (its mocked body mutates the store directly, exactly what another thread could
    # do if the lock were free during the network call). The endpoint's post-send re-check must
    # notice the race and NOT append a second orange row / overwrite the concurrent result.
    c = _seed(iso)

    def concurrent_send(to, subject, body, bcc=None, **kw):
        st = _store(iso)
        st["orders"]["20261000"] = {"status": "emailed", "email": to, "manual": True,
                                     "date": "concurrent"}
        st["red"] = [r for r in st["red"] if r["code"] != "20261000"]
        st.setdefault("orange", []).append({"code": "20261000", "billFullName": "Ján Bez",
                                            "email": to, "sent_date": "concurrent"})
        webapp._save_orders_reminder(st)
        return True

    monkeypatch.setattr(webapp, "_send_mail_html", concurrent_send)
    r = c.post("/api/orders-reminder/override", json={"code": "20261000", "action": "send"})
    assert r.status_code == 200 and r.get_json() == {"ok": True, "status": "emailed"}
    st = _store(iso)
    # exactly ONE orange row for 20261000 — the endpoint did not append a duplicate
    assert len([o for o in st["orange"] if o["code"] == "20261000"]) == 1
    assert st["orders"]["20261000"]["date"] == "concurrent"   # the concurrent write won, untouched


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


# ═════════════════════════════════════════════════════════════════════════════════
# DÁVKA A — e-mail safety (BUG 2/3/4 + VYLEPŠENIE 3/4).
# Every mail here goes to a REAL customer, so the whole class of failures locked
# below has exactly one symptom: the same customer gets the same mail twice.
# ═════════════════════════════════════════════════════════════════════════════════

# ── BUG 2 — a double-clicked manual „send" must e-mail exactly ONCE ────────────────
def test_override_send_double_click_sends_only_one_email(iso, monkeypatch):
    """The pre-check runs under `_lock`, the SMTP round-trip (~20s) does NOT — so without an
    in-flight claim TWO concurrent 'send' requests both pass the check and both e-mail the
    customer. The second one must be rejected while the first is still talking to SMTP."""
    c = _seed(iso)
    calls, second = [], {}

    def send_with_a_second_click(to, subject, body, bcc=None, **kw):
        calls.append(to)
        if len(calls) == 1:                       # the double-click lands mid-SMTP
            second["r"] = authed_client().post(
                "/api/orders-reminder/override", json={"code": "20261000", "action": "send"})
        return True

    monkeypatch.setattr(webapp, "_send_mail_html", send_with_a_second_click)
    r = c.post("/api/orders-reminder/override", json={"code": "20261000", "action": "send"})
    assert r.status_code == 200
    assert second["r"].status_code == 409          # blocked by the in-flight claim
    assert calls == ["a@x.sk"]                     # exactly ONE mail left the app
    st = _store(iso)
    assert st["orders"]["20261000"]["status"] == "emailed"
    assert len([o for o in st["orange"] if o["code"] == "20261000"]) == 1


def test_override_send_failure_releases_the_claim_for_a_retry(iso, monkeypatch):
    """The transient claim must never become a permanent lock: a failed send restores the
    previous state so the manager can simply click again."""
    c = _seed(iso)
    monkeypatch.setattr(webapp, "_send_mail_html", lambda *a, **kw: False)
    assert c.post("/api/orders-reminder/override",
                  json={"code": "20261000", "action": "send"}).status_code == 502
    st = _store(iso)
    assert "20261000" not in st.get("orders", {})          # claim released
    assert "20261000" in {r["code"] for r in st["red"]}    # still actionable

    monkeypatch.setattr(webapp, "_send_mail_html", lambda to, s, b, bcc=None, **kw: True)
    r2 = c.post("/api/orders-reminder/override", json={"code": "20261000", "action": "send"})
    assert r2.status_code == 200 and r2.get_json()["status"] == "emailed"


def test_stale_sending_claim_does_not_block_a_new_attempt(iso):
    """A crash between claim and send leaves a 'sending' record behind — after the TTL a new
    attempt must be allowed (otherwise the order is stuck forever)."""
    c = _seed(iso)
    st = _store(iso)
    stale = (datetime.now(timezone.utc).astimezone() - timedelta(hours=2)).isoformat()
    st.setdefault("orders", {})["20261000"] = {"status": "sending", "claimed_at": stale}
    webapp._save_orders_reminder(st)
    iso["sent"].clear()
    r = c.post("/api/orders-reminder/override", json={"code": "20261000", "action": "send"})
    assert r.status_code == 200
    assert [m["to"] for m in iso["sent"]] == ["a@x.sk"]
    assert _store(iso)["orders"]["20261000"]["status"] == "emailed"


# ── BUG 3 — the final wholesale save must not clobber an override written mid-run ──
def test_manager_override_during_a_run_survives_the_final_save(iso, monkeypatch):
    """`done` is snapshotted at the START of the run; the run then spends minutes in OpenAI +
    SMTP. An override written meanwhile must NOT be lost by the final save — losing its
    terminal record means the next run e-mails that customer again."""
    def classify_and_override(note):
        # the manager resolves 20261000 from the tab while the run is mid-OpenAI/SMTP
        # (the app loader, not _store — the store file may not exist yet this early in the run)
        st = webapp._load_orders_reminder()
        st.setdefault("orders", {})["20261000"] = {
            "status": "skipped_contacted", "manual": True, "date": "during-run"}
        webapp._save_orders_reminder(st)
        return _CLASSIFY[note]

    monkeypatch.setattr(webapp, "_classify_contacted", classify_and_override)
    webapp.run_orders_reminder()
    st = _store(iso)
    assert st["orders"].get("20261000", {}).get("status") == "skipped_contacted"
    assert st["orders"]["20261000"]["date"] == "during-run"    # untouched by the run
    assert st["orders"]["20261001"]["status"] == "emailed"     # the run's own record survives too


# ── BUG 4 — an order with no e-mail must never burn a paid OpenAI call ─────────────
NOEMAIL_ROW = ("20261098;" + OLD + " 06:00:00;Vybavuje sa;treba doriešiť;;;"
               "Bez Mailu;Rukavice;1;10,00")


def _csv_with_noemail_order():
    return (ORDERS_CSV.decode("cp1250").rstrip("\r\n") + "\r\n" + NOEMAIL_ROW + "\r\n"
            ).encode("cp1250")


def test_order_without_email_is_not_classified_and_is_surfaced(iso, monkeypatch):
    """No e-mail → the reminder can never be sent → the order never becomes terminal → today it
    is re-classified (paid OpenAI call) on EVERY run, forever. Skip the call and show the order
    so the manager can fill the address in."""
    classified = []
    monkeypatch.setattr(webapp, "_classify_contacted",
                        lambda note: classified.append(note) or _CLASSIFY[note])
    monkeypatch.setattr(webapp, "_orders_csv_cached", _csv_with_noemail_order)
    stats = webapp.run_orders_reminder()
    assert "treba doriešiť" not in classified          # 0 OpenAI calls for the no-e-mail order
    assert stats["no_email"] == 1
    st = _store(iso)
    assert {r["code"] for r in st["no_email"]} == {"20261098"}
    assert "20261098" not in st["orders"]              # not terminal — resolved by adding the mail
    assert all(m["to"] for m in iso["sent"])           # nothing addressed to nobody


def test_order_without_email_stays_free_of_openai_on_every_run(iso, monkeypatch):
    monkeypatch.setattr(webapp, "_orders_csv_cached", _csv_with_noemail_order)
    webapp.run_orders_reminder()
    classified = []
    monkeypatch.setattr(webapp, "_classify_contacted",
                        lambda note: classified.append(note) or _CLASSIFY[note])
    webapp.run_orders_reminder()
    assert "treba doriešiť" not in classified


# ── VYLEPŠENIE 3 — a failing store write must not crash the run ────────────────────
def _drop_first_emailed_write(monkeypatch):
    """Make the IMMEDIATE dedup write for 20261001 fail exactly once (targeted by CONTENT, not by
    call index — the run also writes its in-flight claims, so counting calls is brittle)."""
    real_save = webapp._save_orders_reminder
    dropped = {"once": False}

    def flaky_save(data):
        entry = (data.get("orders") or {}).get("20261001") or {}
        if not dropped["once"] and isinstance(entry, dict) and entry.get("status") == "emailed":
            dropped["once"] = True
            raise OSError("[Errno 28] No space left on device")
        return real_save(data)

    monkeypatch.setattr(webapp, "_save_orders_reminder", flaky_save)
    return real_save


def test_persist_failure_is_logged_and_the_run_continues(iso, monkeypatch, caplog):
    _drop_first_emailed_write(monkeypatch)
    with caplog.at_level("ERROR"):
        stats = webapp.run_orders_reminder()       # must NOT propagate
    assert stats["emailed_now"] == 1
    assert any("20261001" in r.getMessage() for r in caplog.records if r.levelname == "ERROR")
    # PR #223 review (IMPORTANT 2): the final save re-reads `orders` from disk, so a dropped
    # immediate write is not merely logged — it is DISCARDED, and the record of a mail that
    # really did go out disappears. The run must re-apply it on top of the fresh disk map.
    assert _store(iso)["orders"]["20261001"]["status"] == "emailed"


def test_a_dropped_immediate_write_does_not_remail_on_the_next_run(iso, monkeypatch):
    """The whole point of the healing net: after a failed immediate write the customer must not
    be e-mailed again by tomorrow's run."""
    real_save = _drop_first_emailed_write(monkeypatch)
    webapp.run_orders_reminder()
    # restore ONLY the save (monkeypatch.undo() would drop the whole `iso` isolation too)
    monkeypatch.setattr(webapp, "_save_orders_reminder", real_save)
    iso["sent"].clear()
    stats2 = webapp.run_orders_reminder()
    assert stats2["emailed_now"] == 0
    assert all(m["to"] != "b@x.sk" for m in iso["sent"])


# ── VYLEPŠENIE 4 — „BCC vždy": no MAIL_BCC → the customer mail does not go out ─────
def _real_smtp_path(monkeypatch, iso):
    """Swap the capturing stub for the REAL _send_mail_html behind a fake smtplib."""
    monkeypatch.delenv("MAIL_BCC", raising=False)
    monkeypatch.setenv("MAIL_HOST", "smtp.example.test")
    monkeypatch.setenv("MAIL_PORT", "587")
    monkeypatch.setattr(webapp.smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setattr(webapp.smtplib, "SMTP_SSL", _FakeSMTP)
    monkeypatch.setattr(webapp, "_send_mail_html", _REAL_SEND_MAIL_HTML)
    _FakeSMTP.last_rcpt = None


def test_run_does_not_email_the_customer_without_mail_bcc(iso, monkeypatch):
    _real_smtp_path(monkeypatch, iso)
    stats = webapp.run_orders_reminder()
    assert _FakeSMTP.last_rcpt is None             # nothing reached the wire
    assert stats["emailed_now"] == 0
    assert "20261001" not in _store(iso).get("orders", {})   # retried once BCC is configured


def test_override_send_does_not_email_the_customer_without_mail_bcc(iso, monkeypatch):
    c = _seed(iso)
    _real_smtp_path(monkeypatch, iso)
    r = c.post("/api/orders-reminder/override", json={"code": "20261000", "action": "send"})
    # 503, not 502 (PR #223 review MINOR 6): the endpoint pre-flights the „BCC vždy" requirement
    # so the manager is told it is a CONFIGURATION gap, not a transient send failure.
    assert r.status_code == 503
    assert _FakeSMTP.last_rcpt is None
    assert "20261000" not in _store(iso).get("orders", {})   # no claim taken, retry possible


# ═════════════════════════════════════════════════════════════════════════════════
# PR #223 review — the READ side of the lost update (BUG 3 fixed only the WRITE side)
# plus two claim-lifecycle holes. Same symptom every time: a duplicate customer mail.
# ═════════════════════════════════════════════════════════════════════════════════
MIDRUN_ROW = ("20261005;" + OLD + " 05:00:00;Vybavuje sa;volať zákazníka;mid@x.sk;;"
              "Stred Behu;Čelovka;1;15,00")


def _csv_with_midrun_order():
    return (ORDERS_CSV.decode("cp1250").rstrip("\r\n") + "\r\n" + MIDRUN_ROW + "\r\n"
            ).encode("cp1250")


def _override_store_on_first_classify(monkeypatch, entry):
    """Write `entry` for 20261005 into the store during the FIRST classification of the run —
    i.e. exactly while the run is busy in OpenAI/SMTP, the window an override lands in."""
    seen = []

    def classify(note):
        seen.append(note)
        if len(seen) == 1:
            st = webapp._load_orders_reminder()
            st.setdefault("orders", {})["20261005"] = entry
            webapp._save_orders_reminder(st)
        return _CLASSIFY[note]

    monkeypatch.setattr(webapp, "_orders_csv_cached", _csv_with_midrun_order)
    monkeypatch.setattr(webapp, "_classify_contacted", classify)
    return seen


def test_override_resolved_during_a_run_is_not_remailed_by_that_run(iso, monkeypatch):
    """The run decided from `done` — the START-of-run snapshot — so an order the manager
    resolved minutes ago (while the run sat in OpenAI/SMTP) was still classified and mailed."""
    _override_store_on_first_classify(
        monkeypatch, {"status": "emailed", "manual": True, "date": "during-run"})
    webapp.run_orders_reminder()
    assert all(m["to"] != "mid@x.sk" for m in iso["sent"])        # no duplicate mail
    assert _store(iso)["orders"]["20261005"]["date"] == "during-run"   # record untouched


def test_run_does_not_race_a_manual_send_claimed_during_the_run(iso, monkeypatch):
    """Same read-side hole for the in-flight claim: a manual send claimed WHILE the run is
    working was invisible to it, so both mailed the same customer."""
    fresh = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    _override_store_on_first_classify(
        monkeypatch, {"status": "sending", "claimed_at": fresh, "claim": "abc123"})
    webapp.run_orders_reminder()
    assert all(m["to"] != "mid@x.sk" for m in iso["sent"])        # left to the manual send
    assert _store(iso)["orders"]["20261005"]["status"] == "sending"   # claim not clobbered


def test_abandoned_claim_does_not_block_marking_the_order_contacted(iso):
    """A claim orphaned by a restart/500 between claim and send must not permanently 409 the
    'contact' action — the manager would have no way to resolve that order, ever."""
    c = _seed(iso)
    st = _store(iso)
    stale = (datetime.now(timezone.utc).astimezone() - timedelta(hours=2)).isoformat()
    st.setdefault("orders", {})["20261000"] = {"status": "sending", "claimed_at": stale}
    webapp._save_orders_reminder(st)
    r = c.post("/api/orders-reminder/override", json={"code": "20261000", "action": "contact"})
    assert r.status_code == 200 and r.get_json()["status"] == "skipped_contacted"
    assert _store(iso)["orders"]["20261000"]["status"] == "skipped_contacted"


def test_override_send_reports_honestly_when_the_mail_left_but_the_write_failed(iso, monkeypatch,
                                                                                caplog):
    """The mail ALREADY went out — reporting a plain failure would invite the manager to click
    again (duplicate mail). The response must say so explicitly and the order code must be
    logged for manual follow-up."""
    c = _seed(iso)
    real_save = webapp._save_orders_reminder
    n = {"calls": 0}

    def flaky_save(data):
        n["calls"] += 1
        if n["calls"] == 2:            # 1 = the claim, 2 = the post-send record
            raise OSError("[Errno 28] No space left on device")
        return real_save(data)

    monkeypatch.setattr(webapp, "_save_orders_reminder", flaky_save)
    iso["sent"].clear()
    with caplog.at_level("ERROR"):
        r = c.post("/api/orders-reminder/override", json={"code": "20261000", "action": "send"})
    assert r.get_json()["error"]                       # a handled JSON error, not a bare 500 page
    assert "odišiel" in r.get_json()["error"].lower()  # tells the manager NOT to click again
    assert len([m for m in iso["sent"] if m["to"] == "a@x.sk"]) == 1     # sent exactly once
    assert any("20261000" in rec.getMessage() for rec in caplog.records
               if rec.levelname == "ERROR")


def test_override_survives_a_corrupt_order_record(iso):
    """A partial write can leave a non-dict under a code. The override endpoint must still
    answer (the store guard pattern), not 500 — a 500 here tells the manager nothing and the
    order stays unresolvable."""
    c = _seed(iso)
    st = _store(iso)
    st.setdefault("orders", {})["20261000"] = "kaboom"      # garbage from a partial write
    webapp._save_orders_reminder(st)
    r = c.post("/api/orders-reminder/override", json={"code": "20261000", "action": "send"})
    assert r.status_code == 200 and r.get_json()["status"] == "emailed"
    assert [m["to"] for m in iso["sent"] if m["to"] == "a@x.sk"] == ["a@x.sk"]

    st2 = _store(iso)
    st2["orders"]["20261002"] = ["also", "wrong"]
    webapp._save_orders_reminder(st2)
    r2 = c.post("/api/orders-reminder/override", json={"code": "20261002", "action": "contact"})
    assert r2.status_code == 200 and r2.get_json()["status"] == "skipped_contacted"


def test_run_survives_a_corrupt_order_record(iso):
    webapp.run_orders_reminder()
    st = _store(iso)
    st["orders"]["20261001"] = "kaboom"
    webapp._save_orders_reminder(st)
    iso["sent"].clear()
    stats = webapp.run_orders_reminder()                    # must not raise
    assert stats["emailed_now"] == 1                        # re-processed, record repaired
    assert _store(iso)["orders"]["20261001"]["status"] == "emailed"


# ═════════════════════════════════════════════════════════════════════════════════
# PR #223 adversarial review — the RUN's own send window was still unclaimed, a dropped
# write was silently discarded, and a failed post-send write only bought 10 minutes.
# Symptom of all three: the SAME customer gets the SAME reminder twice.
# ═════════════════════════════════════════════════════════════════════════════════
def _csv_with_a_note_on_the_red_order():
    """The RED order (nobody had touched it) picked up an internal note, so the NEXT run
    classifies + e-mails it — while its RED row is still on the tab, i.e. the manager can click
    „▶ Poslať pripomienku" on it at exactly that moment."""
    return ORDERS_CSV.decode("cp1250").replace(
        f"20261000;{OLD} 10:00:00;Vybavuje sa;;a@x.sk",
        f"20261000;{OLD} 10:00:00;Vybavuje sa;volať zákazníka;a@x.sk").encode("cp1250")


# ── IMPORTANT 1 — the run must CLAIM an order before its own OpenAI+SMTP window ────
def test_manual_send_during_the_runs_own_send_window_is_blocked(iso, monkeypatch):
    """The claim mechanism was one-sided: only the manual override claimed before SMTP. The run
    did a fresh per-order read, RELEASED the lock, and then spent ~20 s in OpenAI + SMTP with no
    in-flight marker on disk at all — so a click landing in that window passed the 409 gate,
    claimed, and mailed, and the run mailed too."""
    _seed(iso)                              # run 1: 20261000 sits in RED (no note yet)
    monkeypatch.setattr(webapp, "_orders_csv_cached", _csv_with_a_note_on_the_red_order)
    iso["sent"].clear()
    clicked = {}

    def classify_and_click(note):
        if "r" not in clicked:              # the manager clicks while the run is in OpenAI
            clicked["r"] = authed_client().post(
                "/api/orders-reminder/override", json={"code": "20261000", "action": "send"})
        return _CLASSIFY[note]

    monkeypatch.setattr(webapp, "_classify_contacted", classify_and_click)
    webapp.run_orders_reminder()
    assert clicked["r"].status_code == 409                        # the run had claimed it
    assert [m["to"] for m in iso["sent"] if m["to"] == "a@x.sk"] == ["a@x.sk"]   # ONE mail
    assert _store(iso)["orders"]["20261000"]["status"] == "emailed"


def test_the_runs_claim_is_released_when_its_own_send_fails(iso, monkeypatch):
    """The claim has to be live ON DISK for the whole SMTP round-trip (that is what makes a
    concurrent click 409) — and must never outlive the attempt, or a failed send would lock the
    order until the TTL lapses."""
    _seed(iso)
    monkeypatch.setattr(webapp, "_orders_csv_cached", _csv_with_a_note_on_the_red_order)
    seen = {}

    def send_fails(to, subject, body, bcc=None, **kw):
        seen["claim"] = (webapp._load_orders_reminder().get("orders") or {}).get("20261000")
        return False

    monkeypatch.setattr(webapp, "_send_mail_html", send_fails)
    webapp.run_orders_reminder()
    assert (seen.get("claim") or {}).get("status") == "sending"    # claimed before SMTP
    assert "20261000" not in _store(iso).get("orders", {})         # …and released after


def test_a_claim_that_cannot_be_written_skips_the_order_instead_of_mailing_it(iso, monkeypatch):
    """A claim that never reached disk protects nothing, so the run must NOT proceed to send
    unclaimed — and it must not crash either (VYLEPŠENIE 3): the order is simply retried."""
    real_save = webapp._save_orders_reminder

    def no_claims(data):
        entry = (data.get("orders") or {}).get("20261001") or {}
        if isinstance(entry, dict) and entry.get("status") == "sending":
            raise OSError("[Errno 28] No space left on device")
        return real_save(data)

    monkeypatch.setattr(webapp, "_save_orders_reminder", no_claims)
    stats = webapp.run_orders_reminder()                     # must not raise
    assert stats["emailed_now"] == 0
    assert all(m["to"] != "b@x.sk" for m in iso["sent"])     # nothing sent unclaimed
    assert "20261001" not in _store(iso).get("orders", {})   # not recorded → retried next run


def test_the_runs_claim_is_released_when_classification_fails(iso, monkeypatch):
    _seed(iso)
    monkeypatch.setattr(webapp, "_orders_csv_cached", _csv_with_a_note_on_the_red_order)
    seen = {}

    def boom(note):
        seen["claim"] = (webapp._load_orders_reminder().get("orders") or {}).get("20261000")
        raise RuntimeError("OpenAI 500")

    monkeypatch.setattr(webapp, "_classify_contacted", boom)
    webapp.run_orders_reminder()
    assert (seen.get("claim") or {}).get("status") == "sending"    # claimed before the AI call
    assert "20261000" not in _store(iso).get("orders", {})         # …and released after


# ── IMPORTANT 3 — a failed post-send write must leave a NON-EXPIRING marker ────────
def test_a_manual_send_whose_write_failed_is_not_remailed_by_the_next_run(iso, monkeypatch):
    """When the post-send write fails the endpoint deliberately keeps the claim so a re-click is
    blocked — but `sending` is TRANSIENT: after SENDING_CLAIM_TTL_S it is neither an active claim
    nor terminal, so the next daily run treats the order as unprocessed and mails the customer a
    second time. The mitigation may not depend on a human noticing within 10 minutes."""
    c = _seed(iso)
    real_save = webapp._save_orders_reminder
    n = {"calls": 0}

    def flaky_save(data):
        n["calls"] += 1
        if n["calls"] == 2:              # 1 = the claim, 2 = the post-send record
            raise OSError("[Errno 28] No space left on device")
        return real_save(data)

    monkeypatch.setattr(webapp, "_save_orders_reminder", flaky_save)
    iso["sent"].clear()
    r = c.post("/api/orders-reminder/override", json={"code": "20261000", "action": "send"})
    assert r.status_code == 500
    assert [m["to"] for m in iso["sent"]] == ["a@x.sk"]           # the mail DID go out
    monkeypatch.setattr(webapp, "_save_orders_reminder", real_save)

    # …10 minutes later the claim has lapsed and the order has picked up a note
    monkeypatch.setattr(webapp, "SENDING_CLAIM_TTL_S", 0)
    monkeypatch.setattr(webapp, "_orders_csv_cached", _csv_with_a_note_on_the_red_order)
    iso["sent"].clear()
    webapp.run_orders_reminder()
    assert all(m["to"] != "a@x.sk" for m in iso["sent"])          # NO second mail
    assert _reminder_terminal_on_disk(iso, "20261000")


def _reminder_terminal_on_disk(iso, code) -> bool:
    return webapp._reminder_is_terminal((_store(iso).get("orders") or {}).get(code))


# ── MINOR 4 — a row resolved DURING the run must not be written back onto the tab ──
def test_red_row_resolved_during_a_run_is_not_written_back_as_unhandled(iso, monkeypatch):
    """`red` is built from the START-of-run snapshot and saved wholesale at the end, so an order
    the manager resolved (✓ Kontaktované) mid-run reappears as red — and their next click on it
    gets a 409 „už vybavená" they cannot explain."""
    _seed(iso)
    monkeypatch.setattr(webapp, "_orders_csv_cached", _csv_with_midrun_order)
    iso["sent"].clear()
    done_click = {}

    def classify_and_resolve(note):
        if "r" not in done_click:        # resolved while the run sits in OpenAI on ANOTHER order
            done_click["r"] = authed_client().post(
                "/api/orders-reminder/override", json={"code": "20261000", "action": "contact"})
        return _CLASSIFY[note]

    monkeypatch.setattr(webapp, "_classify_contacted", classify_and_resolve)
    webapp.run_orders_reminder()
    assert done_click["r"].status_code == 200
    st = _store(iso)
    assert "20261000" not in {r["code"] for r in st["red"]}        # not resurrected
    assert "20261000" in {r["code"] for r in st["skipped"]}        # shown where it belongs
    assert st["orders"]["20261000"]["status"] == "skipped_contacted"


# ── MINOR 6 — a missing MAIL_BCC is a CONFIG error, not a transient send failure ───
def test_override_send_without_mail_bcc_reports_a_configuration_error(iso, monkeypatch):
    """A generic 502 „odoslanie zlyhalo" reads like a transient glitch, so the manager keeps
    clicking forever. Missing MAIL_BCC needs its own, actionable answer."""
    c = _seed(iso)
    monkeypatch.delenv("MAIL_BCC", raising=False)
    iso["sent"].clear()
    r = c.post("/api/orders-reminder/override", json={"code": "20261000", "action": "send"})
    assert r.status_code == 503
    assert "MAIL_BCC" in r.get_json()["error"]
    assert iso["sent"] == []                                       # no send even attempted
    assert "20261000" not in _store(iso).get("orders", {})         # and no claim left behind
    assert "20261000" in {r2["code"] for r2 in _store(iso)["red"]}  # row still actionable


# ── MINOR 7 — a dead automation must be visible in the UI, not only in the log ─────
def test_run_surfaces_missing_mail_bcc_in_its_stats(iso, monkeypatch):
    """Without MAIL_BCC the customer automations refuse to send (VYLEPŠENIE 4) — but that is an
    ERROR line in the log only, so the tab shows a healthy run that quietly mailed nobody."""
    monkeypatch.delenv("MAIL_BCC", raising=False)
    stats = webapp.run_orders_reminder()
    assert stats["bcc_missing"] is True
    assert _store(iso)["stats"]["bcc_missing"] is True


def test_run_stats_do_not_flag_bcc_when_it_is_configured(iso):
    assert webapp.run_orders_reminder()["bcc_missing"] is False


# ═════════════════════════════════════════════════════════════════════════════════
# DÁVKA B1 — three holes the PR #223 verification found in the run's claim path.
# All three share one symptom: the tab shows a healthy-looking run while an order
# was silently dropped — the „ticho mŕtva automatizácia" MINOR 7 exists to prevent.
# ═════════════════════════════════════════════════════════════════════════════════

def _claim_writes_fail(monkeypatch, code="20261001"):
    """Every attempt to persist the transient 'sending' claim for `code` fails (full disk)."""
    real_save = webapp._save_orders_reminder

    def no_claims(data):
        entry = (data.get("orders") or {}).get(code) or {}
        if isinstance(entry, dict) and entry.get("status") == "sending":
            raise OSError("[Errno 28] No space left on device")
        return real_save(data)

    monkeypatch.setattr(webapp, "_save_orders_reminder", no_claims)


def _display_codes(iso):
    """Every code the tab can act on — the three lists the override endpoint searches."""
    st = _store(iso)
    return {r["code"] for section in ("red", "orange", "skipped")
            for r in st.get(section) or []}


# ── M1 — a claim that cannot be WRITTEN must be counted as an error ───────────────
def test_a_claim_that_cannot_be_written_is_counted_as_an_error(iso, monkeypatch):
    """A full disk makes every claim write fail, so the run mails nobody — and reported
    `errors: 0`, i.e. the tab rendered „odoslané pripomienky teraz: 0" with no error count at
    all. Indistinguishable from a quiet day with nothing to do."""
    _claim_writes_fail(monkeypatch)
    stats = webapp.run_orders_reminder()
    assert stats["emailed_now"] == 0
    assert all(m["to"] != "b@x.sk" for m in iso["sent"])     # nothing sent unclaimed
    assert stats["errors"] >= 1                              # …and the run says so


# ── M2 — an order the run gave up on mid-flight must stay ON the tab ──────────────
def test_an_order_whose_send_failed_stays_actionable_on_the_tab(iso, monkeypatch):
    """The run claims, SMTP fails, the claim is released — and the order appears in NO display
    list, because those are rebuilt from scratch every run and this branch just `continue`s. The
    manager cannot even see the failed order, and „▶ Poslať pripomienku" on the row they saw
    before the run answers 404 „objednávka sa v aktuálnom zozname nenašla"."""
    monkeypatch.setattr(webapp, "_send_mail_html",
                        lambda to, subject, body, bcc=None, **kw: False)
    webapp.run_orders_reminder()
    assert "20261001" in _display_codes(iso)
    # …and the row is genuinely actionable, not just visible
    r = authed_client().post("/api/orders-reminder/override",
                             json={"code": "20261001", "action": "contact"})
    assert r.status_code == 200


def test_an_order_whose_claim_could_not_be_written_stays_actionable_on_the_tab(iso, monkeypatch):
    _claim_writes_fail(monkeypatch)
    webapp.run_orders_reminder()
    assert "20261001" in _display_codes(iso)
    r = authed_client().post("/api/orders-reminder/override",
                             json={"code": "20261001", "action": "contact"})
    assert r.status_code == 200


def test_an_order_whose_classification_failed_stays_actionable_on_the_tab(iso, monkeypatch):
    def boom(note):
        raise RuntimeError("OpenAI 500")
    monkeypatch.setattr(webapp, "_classify_contacted", boom)
    webapp.run_orders_reminder()
    assert {"20261001", "20261002"} <= _display_codes(iso)
    r = authed_client().post("/api/orders-reminder/override",
                             json={"code": "20261001", "action": "send"})
    assert r.status_code in (200, 409)                       # anything but 404


def test_an_order_lost_to_a_concurrent_manual_send_stays_on_the_tab(iso, monkeypatch):
    """The manager claims the order a heartbeat before the run reaches it: the run skips it (
    correctly — it must not race the manual send) but then leaves it off the tab entirely."""
    _seed(iso)
    monkeypatch.setattr(webapp, "_orders_csv_cached", _csv_with_a_note_on_the_red_order)
    st = webapp._load_orders_reminder()
    st.setdefault("orders", {})["20261000"] = {
        "status": "sending", "claim": "someone-else", "email": "a@x.sk",
        "claimed_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")}
    webapp._save_orders_reminder(st)
    iso["sent"].clear()
    webapp.run_orders_reminder()
    assert all(m["to"] != "a@x.sk" for m in iso["sent"])      # the run did not race the click
    assert "20261000" in _display_codes(iso)                  # …and did not hide the order


# ── M3 — no MAIL_BCC disqualifies an order BEFORE the paid OpenAI call ────────────
def test_without_mail_bcc_the_run_pays_for_no_classification(iso, monkeypatch):
    """`bcc_missing` was computed and reported but never USED as a disqualifier: the run claimed
    the order, paid OpenAI, and only then had the send refused by require_bcc — and since the
    order never becomes terminal, it did that again on every single run, forever. The repo rule
    is „expensive call only after the cheap disqualifiers"."""
    monkeypatch.delenv("MAIL_BCC", raising=False)
    classified = []
    monkeypatch.setattr(webapp, "_classify_contacted",
                        lambda note: classified.append(note) or False)
    writes = {"n": 0}
    real_save = webapp._save_orders_reminder

    def counting_save(data):
        writes["n"] += 1
        return real_save(data)

    monkeypatch.setattr(webapp, "_save_orders_reminder", counting_save)
    stats = webapp.run_orders_reminder()
    assert classified == []                     # not one paid classification
    assert iso["sent"] == []
    assert stats["bcc_missing"] is True
    assert writes["n"] == 1                     # only the final save — no claim ever written
    assert _store(iso).get("orders") == {}      # nothing recorded → retried once BCC is set
    # the orders are still visible so the manager sees WHAT is stuck, next to the BCC warning
    assert {"20261001", "20261002"} <= _display_codes(iso)
