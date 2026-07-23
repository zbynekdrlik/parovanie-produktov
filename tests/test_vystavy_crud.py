"""Endpoint tests for „Poľovnícke výstavy" CRUD + send buttons (#111, Tasks 4+5).

Hermetic (vzor test_webreview_nedostupne): the store is redirected to tmp and the mail
helper is monkeypatched (no SMTP / no network). Covers list/add/edit/delete, manual
status reset, formula-injection reject, and the posli-otazku / ideme send flows incl.
the state machine + mail-failure 502 (state unchanged) + wrong-state 409.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webreview"))
import app as webapp  # noqa: E402

from tests.conftest import authed_client  # noqa: E402


@pytest.fixture
def iso(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "VYSTAVY", str(tmp_path / "vystavy.json"))
    sent = []
    monkeypatch.setattr(webapp, "_send_vystava_mail",
                        lambda to, subject, body:
                        (sent.append({"to": to, "subject": subject, "body": body})
                         or "<generated@forestshop.sk>"))
    return {"tmp": tmp_path, "sent": sent}


def _store(iso):
    p = iso["tmp"] / "vystavy.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def _add(c, **fields):
    return c.post("/api/vystavy", json=fields).get_json()


# ── auth gate ───────────────────────────────────────────────────────────────
def test_endpoints_require_login(iso):
    anon = webapp.app.test_client()
    assert anon.get("/api/vystavy").status_code == 401
    assert anon.post("/api/vystava/ideme", json={"id": "x"}).status_code == 401


# ── CRUD ────────────────────────────────────────────────────────────────────
def test_add_creates_new_vystava(iso):
    c = authed_client()
    j = _add(c, nazov="Deň Malacky", datum="15.8.2026", email="org@x.sk",
             velkost_stanku="9x3", kedy_riesit="Jún")
    assert j["ok"] is True
    v = j["vystava"]
    assert v["nazov"] == "Deň Malacky" and v["status"] == ""
    assert v["kedy_riesit"] == "Jún"          # stored verbatim (chain A casefolds when matching)
    assert v["feed"] == [] and len(v["id"]) == 32
    assert len(_store(iso)) == 1


def test_add_requires_nazov(iso):
    c = authed_client()
    r = c.post("/api/vystavy", json={"datum": "1.1.", "nazov": "  "})
    assert r.status_code == 400
    assert _store(iso) == []


def test_add_rejects_formula_injection(iso):
    c = authed_client()
    r = c.post("/api/vystavy", json={"nazov": "=SUM(A1)", "email": "x@y.sk"})
    assert r.status_code == 400
    r2 = c.post("/api/vystavy", json={"nazov": "OK", "email": "=cmd|/c calc"})
    assert r2.status_code == 400
    assert _store(iso) == []


def test_list_sorted_by_status(iso):
    c = authed_client()
    _add(c, nazov="Zebra", email="z@x.sk")               # New
    a = _add(c, nazov="Alfa", email="a@x.sk")["vystava"]
    # move Alfa to 'akcia bude' (sorts first)
    c.post("/api/vystava", json={"id": a["id"], "status": "akcia bude"})
    names = [v["nazov"] for v in c.get("/api/vystavy").get_json()["vystavy"]]
    assert names[0] == "Alfa"                              # akcia bude sorts above New


def test_edit_whitelisted_fields(iso):
    c = authed_client()
    v = _add(c, nazov="Pôvodný", email="a@x.sk")["vystava"]
    j = c.post("/api/vystava", json={"id": v["id"],
               "fields": {"nazov": "Nový", "miesto": "Malacky",
                          "status": "hacknuty"}}).get_json()
    assert j["ok"] is True
    stored = _store(iso)[0]
    assert stored["nazov"] == "Nový" and stored["miesto"] == "Malacky"
    assert stored["status"] == ""                          # 'status' not in the edit whitelist


def test_edit_rejects_formula(iso):
    c = authed_client()
    v = _add(c, nazov="A", email="a@x.sk")["vystava"]
    r = c.post("/api/vystava", json={"id": v["id"], "fields": {"miesto": "@evil"}})
    assert r.status_code == 400


def test_add_allows_phone_in_tel(iso):
    """A phone number like '+421 905 123 456' starts with '+' — it MUST be accepted:
    tel is exempt from the formula-lead guard (it never reaches a CSV/formula sink;
    the mails interpolate only nazov/datum/velkost_stanku). Regression for #198 FIX 2."""
    c = authed_client()
    j = _add(c, nazov="Deň X", email="org@x.sk", tel="+421 905 123 456")
    assert j["ok"] is True
    assert _store(iso)[0]["tel"] == "+421 905 123 456"


def test_edit_allows_phone_in_tel_and_kontakt(iso):
    """Edit form posts ALL fields, so a výstava carrying a '+'-leading phone must save.
    tel AND kontakt_osoba are exempt from the formula-lead guard (#198 FIX 2)."""
    c = authed_client()
    v = _add(c, nazov="A", email="a@x.sk")["vystava"]
    j = c.post("/api/vystava", json={"id": v["id"],
               "fields": {"tel": "+421 905 123 456",
                          "kontakt_osoba": "-Ing. Novák"}}).get_json()
    assert j["ok"] is True
    stored = _store(iso)[0]
    assert stored["tel"] == "+421 905 123 456"
    assert stored["kontakt_osoba"] == "-Ing. Novák"


def test_edit_still_rejects_formula_in_other_fields(iso):
    """The tel/kontakt exemption must NOT weaken the guard on the other editable fields
    (miesto/nazov/velkost_stanku/kedy_riesit/sposob still go through the mail sinks)."""
    c = authed_client()
    v = _add(c, nazov="A", email="a@x.sk")["vystava"]
    assert c.post("/api/vystava", json={"id": v["id"],
                  "fields": {"velkost_stanku": "=9x3"}}).status_code == 400
    assert c.post("/api/vystava", json={"id": v["id"],
                  "fields": {"miesto": "+evil"}}).status_code == 400


def test_delete(iso):
    c = authed_client()
    v = _add(c, nazov="Zmazať", email="a@x.sk")["vystava"]
    assert c.post("/api/vystava", json={"id": v["id"], "delete": True}).get_json()["ok"]
    assert _store(iso) == []


def test_edit_unknown_id_404(iso):
    c = authed_client()
    assert c.post("/api/vystava", json={"id": "nope"}).status_code == 404


def test_manual_status_reset_clears_msgids(iso):
    c = authed_client()
    v = _add(c, nazov="A", email="a@x.sk")["vystava"]
    # push it through a send so it carries a msgid, then manually reset to New
    c.post("/api/vystava/posli-otazku", json={"id": v["id"]})
    assert _store(iso)[0]["email_otazka_msgid"] == "<generated@forestshop.sk>"
    c.post("/api/vystava", json={"id": v["id"], "status": ""})
    stored = _store(iso)[0]
    assert stored["status"] == "" and stored["email_otazka_msgid"] == ""


def test_manual_status_reject_invalid(iso):
    c = authed_client()
    v = _add(c, nazov="A", email="a@x.sk")["vystava"]
    assert c.post("/api/vystava", json={"id": v["id"], "status": "bogus"}).status_code == 400


# ── send: posli-otazku (chain A manual) ─────────────────────────────────────
def test_posli_otazku_sends_and_advances(iso):
    c = authed_client()
    v = _add(c, nazov="Deň X", datum="1.9.", email="org@x.sk")["vystava"]
    j = c.post("/api/vystava/posli-otazku", json={"id": v["id"]}).get_json()
    assert j["ok"] is True and j["vystava"]["status"] == "otazka"
    assert j["vystava"]["email_otazka_msgid"] == "<generated@forestshop.sk>"
    assert len(iso["sent"]) == 1
    assert iso["sent"][0]["to"] == "org@x.sk"
    assert iso["sent"][0]["subject"] == "Otázka ohľadom: Deň X dňa 1.9."
    # feed records it (newest-first)
    assert _store(iso)[0]["feed"][0]["typ"] == "otazka_poslana"


def test_posli_otazku_only_from_nova(iso):
    """posli-otazku must be allowed ONLY from the Nová (empty) state — sending it again
    on an in-flight výstava (poziadane/otazka/…) would reset status→otazka and re-mail
    the organizer. Wrong state → 409, no mail sent, state unchanged. #198 FIX 3."""
    c = authed_client()
    v = _add(c, nazov="Deň X", email="org@x.sk")["vystava"]
    c.post("/api/vystava", json={"id": v["id"], "status": "poziadane"})
    r = c.post("/api/vystava/posli-otazku", json={"id": v["id"]})
    assert r.status_code == 409
    assert iso["sent"] == []                         # no mail
    assert _store(iso)[0]["status"] == "poziadane"   # state unchanged


def test_posli_otazku_no_email_400(iso):
    c = authed_client()
    v = _add(c, nazov="Bez mailu")["vystava"]
    assert c.post("/api/vystava/posli-otazku", json={"id": v["id"]}).status_code == 400
    assert iso["sent"] == []


def test_posli_otazku_mail_failure_502_state_unchanged(iso, monkeypatch):
    monkeypatch.setattr(webapp, "_send_vystava_mail", lambda *a, **k: None)
    c = authed_client()
    v = _add(c, nazov="Deň X", email="org@x.sk")["vystava"]
    r = c.post("/api/vystava/posli-otazku", json={"id": v["id"]})
    assert r.status_code == 502
    assert _store(iso)[0]["status"] == ""      # unchanged → retryable


# ── send: ideme (chain C, in-app approval) ──────────────────────────────────
def test_ideme_requires_akcia_bude_state(iso):
    c = authed_client()
    v = _add(c, nazov="Deň X", email="org@x.sk")["vystava"]   # status New
    r = c.post("/api/vystava/ideme", json={"id": v["id"]})
    assert r.status_code == 409                # wrong state
    assert iso["sent"] == []


def test_ideme_sends_prihlaska_and_advances(iso):
    c = authed_client()
    v = _add(c, nazov="Deň X", datum="1.9.", email="org@x.sk", velkost_stanku="9x3")["vystava"]
    c.post("/api/vystava", json={"id": v["id"], "status": "akcia bude"})
    j = c.post("/api/vystava/ideme", json={"id": v["id"]}).get_json()
    assert j["ok"] is True and j["vystava"]["status"] == "poziadane"
    assert j["vystava"]["email_ziadost_msgid"] == "<generated@forestshop.sk>"
    assert iso["sent"][0]["subject"] == "Žiadosť o účasť: Deň X dňa 1.9."
    assert "stánok veľkosti 9x3" in iso["sent"][0]["body"]
    assert _store(iso)[0]["feed"][0]["typ"] == "prihlaska_poslana"


def test_ideme_mail_failure_502_state_unchanged(iso, monkeypatch):
    c = authed_client()
    v = _add(c, nazov="Deň X", email="org@x.sk")["vystava"]
    c.post("/api/vystava", json={"id": v["id"], "status": "akcia bude"})
    monkeypatch.setattr(webapp, "_send_vystava_mail", lambda *a, **k: None)
    r = c.post("/api/vystava/ideme", json={"id": v["id"]})
    assert r.status_code == 502
    assert _store(iso)[0]["status"] == "akcia bude"   # unchanged
