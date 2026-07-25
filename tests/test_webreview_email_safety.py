"""E-mail safety of the SMTP helpers — the duplicate-mail / silent-loss class (DÁVKA A).

Every automation e-mail goes to a REAL customer, so a helper that reports the wrong outcome
costs a duplicate mail (reported success → dedup state bumped → …) or a silently lost one.
The three failures locked here:

* BUG 1 — `quit()` raising AFTER a successful `sendmail()` must NOT be reported as a failure
  (the mail is already handed over; a False return makes the caller skip its dedup write, so
  the next run e-mails the same customer AGAIN).
* BUG 5 — `smtplib.sendmail` only RAISES when EVERY recipient is refused; a partial refusal
  comes back as a plain dict. A refused CUSTOMER address must be a failure (retry), a refused
  BCC-only one must not be.
* VYLEPŠENIE 4 — the „BCC vždy" contract: an automation customer mail (`require_bcc=True`)
  must not go out at all when MAIL_BCC is missing; other paths only warn.

Hermetic: `smtplib.SMTP` / `SMTP_SSL` are replaced by a stub, no network, no real mail.
"""
import os
import smtplib
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webreview"))
import app as webapp  # noqa: E402

CUSTOMER = "zakaznik@example.com"
OWNER = "owner@example.com"


class _StubSMTP:
    """Configurable fake SMTP server (class attrs drive the per-test behaviour)."""
    refused: dict = {}          # what sendmail() returns (partially refused recipients)
    quit_error = None           # exception quit() raises, if any
    calls: list = []            # every sendmail(sender, rcpt, msg)

    def __init__(self, host, port, timeout=None):
        pass

    def starttls(self):
        pass

    def login(self, user, pw):
        pass

    def sendmail(self, sender, rcpt, msg):
        _StubSMTP.calls.append({"sender": sender, "rcpt": list(rcpt), "msg": msg})
        return dict(_StubSMTP.refused)

    def quit(self):
        if _StubSMTP.quit_error is not None:
            raise _StubSMTP.quit_error


@pytest.fixture
def smtp(monkeypatch):
    """Deterministic SMTP config + a clean stub (the dev box has a real data/.mail_env)."""
    _StubSMTP.refused = {}
    _StubSMTP.quit_error = None
    _StubSMTP.calls = []
    monkeypatch.setenv("MAIL_HOST", "smtp.example.test")
    monkeypatch.setenv("MAIL_PORT", "587")
    monkeypatch.setenv("MAIL_USER", "robot@example.test")
    monkeypatch.setenv("MAIL_PASS", "x")
    monkeypatch.setenv("MAIL_FROM", "eshop@example.test")
    monkeypatch.setenv("MAIL_BCC", OWNER)
    monkeypatch.setattr(webapp.smtplib, "SMTP", _StubSMTP)
    monkeypatch.setattr(webapp.smtplib, "SMTP_SSL", _StubSMTP)
    monkeypatch.setattr(webapp, "_BCC_WARNED", False, raising=False)
    return _StubSMTP


# ── BUG 1 — a failing quit() AFTER a successful sendmail() is NOT a send failure ──────────
def test_html_mail_is_success_when_quit_disconnects(smtp):
    smtp.quit_error = smtplib.SMTPServerDisconnected("server closed the connection")
    assert webapp._send_mail_html(CUSTOMER, "predmet", "<p>telo</p>") is True
    assert len(smtp.calls) == 1                     # the mail really was handed over


def test_plain_mail_is_success_when_quit_disconnects(smtp):
    smtp.quit_error = smtplib.SMTPServerDisconnected("server closed the connection")
    assert webapp._send_mail(CUSTOMER, "predmet", "telo") is True
    assert len(smtp.calls) == 1


def test_vystava_mail_returns_msgid_when_quit_disconnects(smtp):
    smtp.quit_error = smtplib.SMTPServerDisconnected("server closed the connection")
    assert webapp._send_vystava_mail("org@vystava.sk", "predmet", "telo")
    assert len(smtp.calls) == 1


def test_disconnect_during_send_is_still_a_failure(smtp, monkeypatch):
    """Sanity guard for the BUG 1 fix: a drop DURING sendmail() must stay a failure."""
    class _Boom(_StubSMTP):
        def sendmail(self, sender, rcpt, msg):
            raise smtplib.SMTPServerDisconnected("dropped mid-DATA")

    monkeypatch.setattr(webapp.smtplib, "SMTP", _Boom)
    assert webapp._send_mail_html(CUSTOMER, "predmet", "<p>telo</p>") is False
    assert smtp.calls == []


# ── BUG 5 — a partially refused recipient list comes back as a dict, not an exception ─────
def test_refused_customer_recipient_is_a_failure(smtp):
    smtp.refused = {CUSTOMER: (550, b"mailbox unavailable")}
    assert webapp._send_mail_html(CUSTOMER, "predmet", "<p>telo</p>") is False


def test_refused_bcc_only_still_counts_as_delivered(smtp):
    smtp.refused = {OWNER: (550, b"mailbox unavailable")}
    assert webapp._send_mail_html(CUSTOMER, "predmet", "<p>telo</p>") is True


def test_refused_customer_recipient_matches_case_insensitively(smtp):
    smtp.refused = {CUSTOMER.upper(): (450, b"greylisted")}
    assert webapp._send_mail_html(CUSTOMER, "predmet", "<p>telo</p>") is False


def test_plain_mail_refused_customer_recipient_is_a_failure(smtp):
    smtp.refused = {CUSTOMER: (550, b"mailbox unavailable")}
    assert webapp._send_mail(CUSTOMER, "predmet", "telo") is False


def test_vystava_mail_refused_recipient_returns_none(smtp):
    smtp.refused = {"org@vystava.sk": (550, b"mailbox unavailable")}
    assert webapp._send_vystava_mail("org@vystava.sk", "predmet", "telo") is None


# ── VYLEPŠENIE 4 — „BCC vždy": automation customer mail refuses to go out without MAIL_BCC ─
def test_require_bcc_refuses_send_when_mail_bcc_missing(smtp, monkeypatch):
    monkeypatch.delenv("MAIL_BCC", raising=False)
    assert webapp._send_mail_html(CUSTOMER, "predmet", "<p>telo</p>", require_bcc=True) is False
    assert smtp.calls == []                          # nothing left the app


def test_require_bcc_sends_normally_when_mail_bcc_present(smtp):
    assert webapp._send_mail_html(CUSTOMER, "predmet", "<p>telo</p>", require_bcc=True) is True
    assert smtp.calls[0]["rcpt"] == [CUSTOMER, OWNER]


def test_missing_mail_bcc_only_warns_on_non_automation_paths(smtp, monkeypatch, caplog):
    """Reset-password & co. keep working without MAIL_BCC — they only get a one-shot warning."""
    monkeypatch.delenv("MAIL_BCC", raising=False)
    with caplog.at_level("WARNING"):
        assert webapp._send_mail_html(CUSTOMER, "predmet", "<p>telo</p>") is True
        assert webapp._send_mail_html(CUSTOMER, "predmet2", "<p>telo</p>") is True
    assert len(smtp.calls) == 2
    warns = [r for r in caplog.records if "MAIL_BCC" in r.getMessage() and r.levelname == "WARNING"]
    assert len(warns) == 1                           # „jednorazovo" — not once per mail


def test_explicit_empty_bcc_opts_out_without_warning(smtp, monkeypatch, caplog):
    monkeypatch.delenv("MAIL_BCC", raising=False)
    with caplog.at_level("WARNING"):
        assert webapp._send_mail_html(CUSTOMER, "predmet", "<p>telo</p>", bcc="") is True
    assert smtp.calls[0]["rcpt"] == [CUSTOMER]
    assert not [r for r in caplog.records if "MAIL_BCC" in r.getMessage()]


# ── PR #223 review, MINOR 5 — a raise before quit() must not leak the SMTP socket ──────────
# `quit()` is only reached on the success path, so an exception out of starttls() / login() /
# sendmail() used to leave the connection open until the garbage collector happened to run.
# On a server that mails all day (two customer automations + resets) that is a slow file-
# descriptor leak, and the sockets stay half-open on the SMTP relay too.
class _ClosingSMTP(_StubSMTP):
    closed = False

    def close(self):
        type(self).closed = True


def _closing_stub(monkeypatch, **overrides):
    cls = type("_Stub", (_ClosingSMTP,), overrides)
    cls.closed = False
    monkeypatch.setattr(webapp.smtplib, "SMTP", cls)
    monkeypatch.setattr(webapp.smtplib, "SMTP_SSL", cls)
    return cls


def test_smtp_socket_is_closed_when_login_raises(smtp, monkeypatch):
    def boom_login(self, user, pw):
        raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    cls = _closing_stub(monkeypatch, login=boom_login)
    assert webapp._send_mail_html(CUSTOMER, "predmet", "<p>telo</p>") is False
    assert cls.closed is True


def test_smtp_socket_is_closed_when_sendmail_raises(smtp, monkeypatch):
    def boom_send(self, sender, rcpt, msg):
        raise smtplib.SMTPServerDisconnected("dropped mid-DATA")

    cls = _closing_stub(monkeypatch, sendmail=boom_send)
    assert webapp._send_mail_html(CUSTOMER, "predmet", "<p>telo</p>") is False
    assert cls.closed is True


def test_smtp_socket_is_closed_when_starttls_raises(smtp, monkeypatch):
    def boom_tls(self):
        raise smtplib.SMTPException("STARTTLS refused")

    cls = _closing_stub(monkeypatch, starttls=boom_tls)
    assert webapp._send_mail(CUSTOMER, "predmet", "telo") is False
    assert cls.closed is True


def test_smtp_socket_is_closed_when_quit_raises(smtp, monkeypatch):
    """quit() itself failing still leaves the socket behind — the mail is already delivered
    (BUG 1: still a success), but the connection must not be left to the GC either."""
    def boom_quit(self):
        raise smtplib.SMTPServerDisconnected("server closed the connection")

    cls = _closing_stub(monkeypatch, quit=boom_quit)
    assert webapp._send_mail_html(CUSTOMER, "predmet", "<p>telo</p>") is True
    assert cls.closed is True


def test_successful_quit_does_not_also_close(smtp, monkeypatch):
    """The normal path stays exactly as it was: quit() alone, no extra close() on the wire."""
    cls = _closing_stub(monkeypatch)
    assert webapp._send_mail_html(CUSTOMER, "predmet", "<p>telo</p>") is True
    assert cls.closed is False
