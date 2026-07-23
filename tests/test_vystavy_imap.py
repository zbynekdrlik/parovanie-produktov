"""Pure IMAP-helper tests for „Poľovnícke výstavy" (#111, Task 3).

No network: builds email.message.Message objects in-memory and drives parse_inbox /
trim_quote / match_reply. fetch_inbox (I/O) is only checked for its degrade-to-[]
behaviour when unconfigured.
"""
import os
import sys
from email.message import EmailMessage

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from parovanie import vystavy_imap as vi  # noqa: E402


def _msg(frm, subject, body, in_reply_to=None, references=None):
    m = EmailMessage()
    m["From"] = frm
    m["Subject"] = subject
    if in_reply_to:
        m["In-Reply-To"] = in_reply_to
    if references:
        m["References"] = references
    m.set_content(body)
    return m


# ── trim_quote ────────────────────────────────────────────────────────────────
def test_trim_quote_sk_napisal():
    body = ("Áno, výstava bude aj tento rok, tešíme sa!\n\n"
            "Dňa 3. 6. 2026 o 9:00 Štepán Drlík <info@forestshop.sk> napísal:\n"
            "> obraciam sa na Vás s otázkou...")
    assert vi.trim_quote(body) == "Áno, výstava bude aj tento rok, tešíme sa!"


def test_trim_quote_cz_napsal():
    body = "Dobrý den, ano bude.\n\nDne 3.6.2026 napsal uživatel X:\n> puvodni"
    assert vi.trim_quote(body) == "Dobrý den, ano bude."


def test_trim_quote_en_wrote_and_from_header():
    body_en = "Yes, confirmed.\n\nOn Mon, Jun 3, 2026 at 9 AM John wrote:\n> original"
    assert vi.trim_quote(body_en) == "Yes, confirmed."
    body_fwd = "Potvrdzujem.\n\nFrom: info@forestshop.sk\nSent: ...\n> original"
    assert vi.trim_quote(body_fwd) == "Potvrdzujem."


def test_trim_quote_no_marker_returns_whole_capped():
    assert vi.trim_quote("Krátka odpoveď bez citátu.") == "Krátka odpoveď bez citátu."
    long = "x" * 900
    trimmed = vi.trim_quote(long)
    assert len(trimmed) <= vi._QUOTE_MAX + 1 and trimmed.endswith("…")


def test_trim_quote_empty():
    assert vi.trim_quote("") == ""
    assert vi.trim_quote(None) == ""


# ── parse_inbox ───────────────────────────────────────────────────────────────
def test_parse_inbox_extracts_fields():
    m = _msg('"Organizátor" <org@vystava.sk>', "Re: Otázka", "Telo odpovede.",
             in_reply_to="<abc@forestshop.sk>")
    (p,) = vi.parse_inbox([m])
    assert p["from"] == "org@vystava.sk"          # bare address, lowercased
    assert p["subject"] == "Re: Otázka"
    assert p["in_reply_to"] == "<abc@forestshop.sk>"
    assert "Telo odpovede." in p["body_text"]


# ── match_reply ───────────────────────────────────────────────────────────────
VYSTAVY = [
    {"id": "v1", "email": "Org@Vystava.sk", "status": "otazka",
     "email_otazka_msgid": "<q1@forestshop.sk>"},
    {"id": "v2", "email": "org@vystava.sk", "status": "otazka",      # same organizer!
     "email_otazka_msgid": "<q2@forestshop.sk>"},
    {"id": "v3", "email": "iny@ine.sk", "status": "poziadane",
     "email_ziadost_msgid": "<z3@forestshop.sk>"},
]


def test_match_reply_matches_from_and_msgid():
    msgs = vi.parse_inbox([
        _msg("org@vystava.sk", "Re: Otázka", "Áno, bude to.",
             in_reply_to="<q1@forestshop.sk>"),
    ])
    matches = vi.match_reply(msgs, VYSTAVY, "otazka", "email_otazka_msgid")
    assert matches == [("v1", "Áno, bude to.")]


def test_match_reply_disambiguates_same_organizer_by_msgid():
    """One organizer runs two výstavy (v1, v2) — the In-Reply-To msgid picks v2, not v1."""
    msgs = vi.parse_inbox([
        _msg("org@vystava.sk", "Re: Otázka", "Druhá výstava potvrdená.",
             in_reply_to="<q2@forestshop.sk>"),
    ])
    matches = vi.match_reply(msgs, VYSTAVY, "otazka", "email_otazka_msgid")
    assert matches == [("v2", "Druhá výstava potvrdená.")]


def test_match_reply_uses_references_when_no_in_reply_to():
    msgs = vi.parse_inbox([
        _msg("org@vystava.sk", "Re: Otázka", "Cez References.",
             references="<other@x> <q1@forestshop.sk>"),
    ])
    matches = vi.match_reply(msgs, VYSTAVY, "otazka", "email_otazka_msgid")
    assert matches == [("v1", "Cez References.")]


def test_match_reply_ignores_unrelated_and_wrong_status():
    msgs = vi.parse_inbox([
        # right sender, wrong (unknown) msgid → no match
        _msg("org@vystava.sk", "Re", "neviazané", in_reply_to="<nope@forestshop.sk>"),
        # matches v3's msgid but v3 is awaited in a DIFFERENT status this call
        _msg("iny@ine.sk", "Re", "prihláška ok", in_reply_to="<z3@forestshop.sk>"),
    ])
    assert vi.match_reply(msgs, VYSTAVY, "otazka", "email_otazka_msgid") == []


def test_match_reply_prihlaska_status():
    msgs = vi.parse_inbox([
        _msg("iny@ine.sk", "Re: Žiadosť", "Prijaté, ďakujeme.",
             in_reply_to="<z3@forestshop.sk>"),
    ])
    assert vi.match_reply(msgs, VYSTAVY, "poziadane", "email_ziadost_msgid") \
        == [("v3", "Prijaté, ďakujeme.")]


def test_match_reply_skips_vystava_without_msgid():
    vystavy = [{"id": "x", "email": "a@b.sk", "status": "otazka", "email_otazka_msgid": ""}]
    msgs = vi.parse_inbox([_msg("a@b.sk", "Re", "telo", in_reply_to="<any@forestshop.sk>")])
    assert vi.match_reply(msgs, vystavy, "otazka", "email_otazka_msgid") == []


# ── fetch_inbox degrade ───────────────────────────────────────────────────────
def test_fetch_inbox_unconfigured_returns_empty(monkeypatch):
    monkeypatch.delenv("MAIL_USER", raising=False)
    monkeypatch.delenv("MAIL_PASS", raising=False)
    assert vi.fetch_inbox() == []


def test_fetch_inbox_connection_error_degrades(monkeypatch):
    monkeypatch.setenv("MAIL_USER", "u@x.sk")
    monkeypatch.setenv("MAIL_PASS", "pw")
    monkeypatch.setenv("IMAP_HOST", "127.0.0.1")
    monkeypatch.setenv("IMAP_PORT", "1")            # nothing listening → connection refused

    def boom(*a, **k):
        raise OSError("connection refused")
    monkeypatch.setattr(vi.imaplib, "IMAP4_SSL", boom)
    assert vi.fetch_inbox() == []
