"""Automation-run tests for „Poľovnícke výstavy" chains A/B/D (#111, Task 6).

Hermetic: the store is redirected to tmp, the SMTP send + IMAP fetch are monkeypatched
(no network). Covers the chain-A filter (month + state + sposob + e-mail), the IMAP
state advances (B/D), and that all 3 automations default DISABLED (#93 contract).
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webreview"))
import app as webapp  # noqa: E402

from tests.conftest import authed_client  # noqa: E402

MONTH = webapp._SK_MONTHS[6]   # 'jún' — the month chain A will match against (pinned below)


@pytest.fixture
def iso(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "VYSTAVY", str(tmp_path / "vystavy.json"))
    monkeypatch.setattr(webapp.RUNNER, "state_path", str(tmp_path / "automations.json"))
    monkeypatch.setattr(webapp, "_sk_month_now", lambda: MONTH)   # deterministic "current month"
    sent = []
    monkeypatch.setattr(webapp, "_send_vystava_mail",
                        lambda to, subject, body:
                        (sent.append({"to": to, "subject": subject})
                         or "<sent@forestshop.sk>"))
    return {"tmp": tmp_path, "sent": sent}


def _seed(iso, vystavy):
    (iso["tmp"] / "vystavy.json").write_text(
        json.dumps(vystavy, ensure_ascii=False), encoding="utf-8")


def _store(iso):
    return json.loads((iso["tmp"] / "vystavy.json").read_text(encoding="utf-8"))


def _v(**kw):
    base = {"id": kw.get("id", "v"), "nazov": "V", "datum": "1.6.", "email": "org@x.sk",
            "velkost_stanku": "9x3", "kedy_riesit": MONTH, "sposob": "email",
            "status": "", "email_datum": "", "email_otazka_msgid": "",
            "email_ziadost_msgid": "", "feed": []}
    base.update(kw)
    return base


# ── chain A: rozposlať otázky ──────────────────────────────────────────────
def test_otazka_sends_only_matching(iso):
    _seed(iso, [
        _v(id="match", kedy_riesit="Jún"),                       # matches (case-insensitive)
        _v(id="wrong_month", kedy_riesit="december"),            # wrong month → skip
        _v(id="not_new", status="otazka"),                        # already sent → skip
        _v(id="pdf", sposob="pdf"),                               # pdf → skip (manual)
        _v(id="no_email", email=""),                              # no e-mail → skip
    ])
    result = webapp.run_vystavy_otazka()
    assert result["poslane"] == 1
    assert [m["to"] for m in iso["sent"]] == ["org@x.sk"]
    # spec (design.md:145) summary shape is {poslane, preskocene}; preskocene counts the
    # výstavy skipped (wrong month / not-new / pdf / no e-mail) — 4 of the 5 seeded (#198 FIX 4).
    assert result["preskocene"] == 4
    store = {v["id"]: v for v in _store(iso)}
    assert store["match"]["status"] == "otazka"
    assert store["match"]["email_otazka_msgid"] == "<sent@forestshop.sk>"
    assert store["match"]["feed"][0]["typ"] == "otazka_poslana"
    assert store["wrong_month"]["status"] == ""                  # untouched


def test_otazka_mail_failure_leaves_state(iso, monkeypatch):
    monkeypatch.setattr(webapp, "_send_vystava_mail", lambda *a, **k: None)
    _seed(iso, [_v(id="m")])
    result = webapp.run_vystavy_otazka()
    assert result["poslane"] == 0 and result["zlyhane"] == 1
    assert _store(iso)[0]["status"] == ""                        # not advanced → retried next run


# ── chain B: odpoveď na otázku (IMAP) ───────────────────────────────────────
def test_odpoved_otazka_advances_on_match(iso, monkeypatch):
    _seed(iso, [_v(id="v1", status="otazka",
                   email_otazka_msgid="<q1@forestshop.sk>", email="org@x.sk")])
    inbox = [{"from": "org@x.sk", "subject": "Re", "in_reply_to": "<q1@forestshop.sk>",
              "references": "", "body_text": "Áno, bude to.", "date": ""}]
    monkeypatch.setattr(webapp.vystavy_imap, "fetch_inbox", lambda: inbox)
    result = webapp.run_vystavy_odpoved_otazka()
    assert result["najdene"] == 1
    v = _store(iso)[0]
    assert v["status"] == "akcia bude"
    assert v["feed"][0]["typ"] == "odpoved_otazka"
    assert "Áno, bude to." in v["feed"][0]["text"]


def test_odpoved_otazka_no_match_no_change(iso, monkeypatch):
    _seed(iso, [_v(id="v1", status="otazka",
                   email_otazka_msgid="<q1@forestshop.sk>", email="org@x.sk")])
    monkeypatch.setattr(webapp.vystavy_imap, "fetch_inbox", lambda: [])   # empty inbox
    assert webapp.run_vystavy_odpoved_otazka()["najdene"] == 0
    assert _store(iso)[0]["status"] == "otazka"


# ── chain D: odpoveď na prihlášku (IMAP) ────────────────────────────────────
def test_odpoved_prihlaska_advances_on_match(iso, monkeypatch):
    _seed(iso, [_v(id="v1", status="poziadane",
                   email_ziadost_msgid="<z1@forestshop.sk>", email="org@x.sk")])
    inbox = [{"from": "org@x.sk", "subject": "Re", "in_reply_to": "<z1@forestshop.sk>",
              "references": "", "body_text": "Prijaté.", "date": ""}]
    monkeypatch.setattr(webapp.vystavy_imap, "fetch_inbox", lambda: inbox)
    result = webapp.run_vystavy_odpoved_prihlaska()
    assert result["najdene"] == 1
    assert _store(iso)[0]["status"] == "odpovedane od organizatora"


# ── #93: all three default DISABLED ─────────────────────────────────────────
def test_vystavy_automations_default_disabled(iso):
    c = authed_client()
    autos = {a["key"]: a for a in c.get("/api/automations").get_json()["automations"]}
    for key in ("vystavy_otazka", "vystavy_odpoved_otazka", "vystavy_odpoved_prihlaska"):
        assert key in autos, key
        assert autos[key]["enabled"] is False           # SAFETY: deploy never auto-enables
        assert autos[key]["description"]                # #173: description present
    assert autos["vystavy_otazka"]["schedule"] == "denne o 06:00"
    assert autos["vystavy_odpoved_otazka"]["schedule"] == "denne o 09:00"
    assert autos["vystavy_odpoved_prihlaska"]["schedule"] == "denne o 09:30"
