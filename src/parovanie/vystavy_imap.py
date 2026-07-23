"""IMAP inbox helper for the „Poľovnícke výstavy" reply detection (#111, chains B/D).

Pure, testable core (no network):
- `parse_inbox(messages)`  — email.message.Message[] → normalized dicts
- `trim_quote(body_text)`  — strip the quoted reply-chain, keep the new reply text
- `match_reply(msgs, vystavy, awaited_status, msgid_field)` — pair an inbox reply to
  the výstava whose sent Message-ID it is In-Reply-To (from + threading, both matched)

I/O edge (NOT exercised in tests):
- `fetch_inbox()` — IMAP4_SSL login + SEARCH SINCE 7 days + FETCH → parse_inbox.
  Any connection/login/parse failure degrades to [] (the automation just doesn't
  advance a state this run — it never crashes).

Credentials come from data/.mail_env (loaded into the environment by webreview/app.py):
IMAP_HOST (default mbox.myshoptet.com), IMAP_PORT (default 993), MAIL_USER, MAIL_PASS.
"""
from __future__ import annotations

import email
import imaplib
import logging
import os
import re
import ssl
from datetime import datetime, timedelta
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parseaddr

log = logging.getLogger("vystavy_imap")

# Newest-reply text ends where the quoted original begins. These markers cover the
# SK / CZ / EN clients the organizers actually use plus the generic forwarded header.
_QUOTE_MARKERS = [
    # SK „Dňa ... napísal:" / CZ „Dne ... napsal uživatel X:" — match to the line's
    # trailing colon (a name can sit between „napísal/napsal" and the „:").
    r"D[ňn][ae] .*nap[íi]?sal.*:",
    r"On .* wrote:",                            # EN „On Mon, ... wrote:"
    r"^\s*From:\s",                             # forwarded/quoted header block
    r"^\s*Od:\s",                               # SK/CZ „Od:" header block
    r"-{2,}\s*(Original|Origin[áa]ln[ay]|Forwarded|P[ôo]vodn[áa])",  # ---Original Message---
    r"_{5,}",                                    # Outlook divider line
]
_QUOTE_RE = re.compile("|".join(_QUOTE_MARKERS), re.IGNORECASE | re.MULTILINE)

_QUOTE_MAX = 500   # feed excerpt cap


def _decode(raw: str) -> str:
    """Decode an RFC 2047 encoded header (=?utf-8?...?=) to plain text."""
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw)))
    except Exception:  # noqa: BLE001 — a malformed header must never crash the run
        return raw


def _body_text(msg: Message) -> str:
    """The text/plain body (first non-attachment text part), best-effort decoded."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and \
                    "attachment" not in (part.get("Content-Disposition") or ""):
                payload = part.get_payload(decode=True)
                if payload is not None:
                    return payload.decode(part.get_content_charset() or "utf-8",
                                          errors="replace")
        return ""
    payload = msg.get_payload(decode=True)
    if payload is None:
        return str(msg.get_payload() or "")
    return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")


def trim_quote(body_text: str) -> str:
    """Return only the NEW reply text: everything before the first quoted-chain marker,
    stripped and capped to _QUOTE_MAX chars (for the in-app feed excerpt)."""
    text = (body_text or "").replace("\r\n", "\n")
    m = _QUOTE_RE.search(text)
    if m:
        text = text[:m.start()]
    text = text.strip()
    if len(text) > _QUOTE_MAX:
        text = text[:_QUOTE_MAX].rstrip() + "…"
    return text


def parse_inbox(messages: list[Message]) -> list[dict]:
    """email.message.Message[] → [{from, subject, in_reply_to, references, body_text, date}].
    `from` is the bare address, lowercased (for the case-insensitive organizer match)."""
    out = []
    for msg in messages:
        out.append({
            "from": parseaddr(msg.get("From", ""))[1].strip().lower(),
            "subject": _decode(msg.get("Subject", "")),
            "in_reply_to": (msg.get("In-Reply-To") or "").strip(),
            "references": (msg.get("References") or "").strip(),
            "body_text": _body_text(msg),
            "date": (msg.get("Date") or "").strip(),
        })
    return out


def _threads(m: dict) -> str:
    """The threading headers a reply carries the original Message-ID in."""
    return f"{m.get('in_reply_to', '')} {m.get('references', '')}"


def match_reply(messages: list[dict], vystavy: list[dict],
                awaited_status: str, msgid_field: str) -> list[tuple[str, str]]:
    """Pair inbox replies to výstavy waiting in `awaited_status`. A message matches a
    výstava when its sender equals the výstava's `email` (case-insensitive) AND the
    výstava's stored Message-ID (`msgid_field`) appears in the message's threading
    headers (In-Reply-To / References). The msgid disambiguates when one organizer
    runs several výstavy. Returns [(vystava_id, trimmed_reply_excerpt)]."""
    out = []
    for v in vystavy:
        if v.get("status") != awaited_status:
            continue
        want_msgid = (v.get(msgid_field) or "").strip()
        want_from = (v.get("email") or "").strip().lower()
        if not want_msgid or not want_from:
            continue
        for m in messages:
            if m.get("from") == want_from and want_msgid in _threads(m):
                out.append((v["id"], trim_quote(m.get("body_text", ""))))
                break   # one reply per výstava per run
    return out


def fetch_inbox(since_days: int = 7) -> list[dict]:
    """Fetch recent INBOX messages via IMAP4_SSL → parse_inbox. I/O edge — any
    failure (unconfigured, connection, login, search, parse) degrades to [] + a log
    line, so a reply-check automation never crashes; it just advances nothing this run."""
    host = os.environ.get("IMAP_HOST", "mbox.myshoptet.com")
    port = int(os.environ.get("IMAP_PORT", "993"))
    user = os.environ.get("MAIL_USER", "")
    pw = os.environ.get("MAIL_PASS", "")
    if not user or not pw:
        log.warning("vystavy_imap: IMAP not configured (MAIL_USER/MAIL_PASS) — 0 messages")
        return []
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE   # the Shoptet mbox host uses a self-signed cert
    imap = None
    try:
        imap = imaplib.IMAP4_SSL(host, port, ssl_context=ctx)
        imap.login(user, pw)
        imap.select("INBOX")
        since = (datetime.now() - timedelta(days=since_days)).strftime("%d-%b-%Y")
        typ, data = imap.search(None, "SINCE", since)
        if typ != "OK":
            log.warning("vystavy_imap: SEARCH failed (%s)", typ)
            return []
        ids = (data[0] or b"").split()
        messages = []
        for num in ids:
            typ, msg_data = imap.fetch(num, "(RFC822)")
            if typ != "OK" or not msg_data or not msg_data[0]:
                continue
            messages.append(email.message_from_bytes(msg_data[0][1]))
        parsed = parse_inbox(messages)
        log.info("vystavy_imap: fetched %d messages since %s", len(parsed), since)
        return parsed
    except Exception as e:  # noqa: BLE001 — degrade, never crash the automation
        log.error("vystavy_imap: fetch failed via %s:%s: %r", host, port, e)
        return []
    finally:
        if imap is not None:
            try:
                imap.logout()
            except Exception as e:  # noqa: BLE001 — logout best-effort
                log.debug("vystavy_imap: logout failed: %r", e)
