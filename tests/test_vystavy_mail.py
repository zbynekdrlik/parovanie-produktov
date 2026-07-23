"""Tests for the „Poľovnícke výstavy" mail helper + templates (#111, Task 2).

Hermetic: SMTP is a capturing stub (no network). Asserts the explicit Message-ID header
is set + returned, the templated fields are filled, and failures return None.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webreview"))
import app as webapp  # noqa: E402


class _FakeSMTP:
    """Captures sendmail() args (recipients + the raw message string); no real network."""
    def __init__(self, *a, **kw):
        pass

    def starttls(self):
        pass

    def login(self, user, pw):
        pass

    def sendmail(self, frm, rcpt, msg):
        _FakeSMTP.last_rcpt = rcpt
        _FakeSMTP.last_msg = msg

    def quit(self):
        pass


def test_send_vystava_mail_sets_and_returns_message_id(monkeypatch):
    monkeypatch.setenv("MAIL_HOST", "smtp.example.test")
    monkeypatch.delenv("MAIL_BCC", raising=False)
    monkeypatch.setattr(webapp.smtplib, "SMTP", _FakeSMTP)
    msgid = webapp._send_vystava_mail("org@vystava.sk", "Predmet", "Telo správy.")
    assert msgid and msgid.startswith("<") and msgid.endswith(">")
    assert msgid.endswith("@forestshop.sk>")                 # domain per spec
    # the SAME id is written to the Message-ID header (IMAP threading needs this)
    assert msgid in _FakeSMTP.last_msg
    assert "Message-ID:" in _FakeSMTP.last_msg
    assert _FakeSMTP.last_rcpt == ["org@vystava.sk"]


def test_send_vystava_mail_bcc_default(monkeypatch):
    monkeypatch.setenv("MAIL_HOST", "smtp.example.test")
    monkeypatch.setenv("MAIL_BCC", "owner@example.com")
    monkeypatch.setattr(webapp.smtplib, "SMTP", _FakeSMTP)
    webapp._send_vystava_mail("org@vystava.sk", "Predmet", "Telo")
    assert _FakeSMTP.last_rcpt == ["org@vystava.sk", "owner@example.com"]


def test_send_vystava_mail_unconfigured_returns_none(monkeypatch):
    monkeypatch.delenv("MAIL_HOST", raising=False)
    assert webapp._send_vystava_mail("org@vystava.sk", "P", "T") is None


def test_send_vystava_mail_failure_returns_none(monkeypatch):
    monkeypatch.setenv("MAIL_HOST", "smtp.example.test")

    class _Boom:
        def __init__(self, *a, **kw):
            raise OSError("smtp down")
    monkeypatch.setattr(webapp.smtplib, "SMTP", _Boom)
    assert webapp._send_vystava_mail("org@vystava.sk", "P", "T") is None


# ── templates fill the placeholders ─────────────────────────────────────────────
def test_otazka_template_fills_fields():
    subj, body = webapp._vy_otazka_mail(
        {"nazov": "Deň Malacky", "datum": "15.8.2026"})
    assert subj == "Otázka ohľadom: Deň Malacky dňa 15.8.2026"
    assert "podujatie Deň Malacky v termíne 15.8.2026" in body
    assert "Štepán Drlík" in body


def test_prihlaska_template_fills_fields():
    subj, body = webapp._vy_prihlaska_mail(
        {"nazov": "Deň Malacky", "datum": "15.8.2026", "velkost_stanku": "9x3"})
    assert subj == "Žiadosť o účasť: Deň Malacky dňa 15.8.2026"
    assert "podujatie Deň Malacky sa bude konať aj tento rok dňa 15.8.2026" in body
    assert "stánok veľkosti 9x3" in body
