"""NEGATIVE CONTROLS — PR #228. Version-agnostic: runs on main (22e8442), the PR head
(df88c43) and the working tree, and PRINTS what each does. Temporary; delete after review.
"""
import json
import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webreview"))
import app as webapp  # noqa: E402

TODAY = date.today()
OLD = (TODAY - timedelta(days=10)).isoformat()
FRESH = (TODAY - timedelta(days=1)).isoformat()

HEADER = ("code;date;statusName;shopRemark;email;phone;billFullName;"
          "itemName;itemAmount;totalPriceWithVat")
ORDERS_CSV = ("\r\n".join([
    HEADER,
    f"20261000;{OLD} 10:00:00;Vybavuje sa;;a@x.sk;+421900;Ján Bez;Bunda;1;99,90",
    f"20261001;{OLD} 09:00:00;Vybavuje sa;volať zákazníka;b@x.sk;;Eva Nová;Nohavice;2;50,00",
    f"20261002;{OLD} 08:00:00;Vybavuje sa;volané so zákazníkom, počká;c@x.sk;;Iva Stará;Čiapka;1;12,00",
    f"20261003;{FRESH} 08:00:00;Vybavuje sa;volať;d@x.sk;;Fero Mladý;Nôž;1;30,00",
]) + "\r\n").encode("cp1250")

_CLASSIFY = {"volať zákazníka": False, "volané so zákazníkom, počká": True}


def _csv_plus_one():
    return (ORDERS_CSV.decode("cp1250").rstrip("\r\n") + "\r\n"
            + f"20261005;{OLD} 07:00:00;Vybavuje sa;volať zákazníka;e@x.sk;;Nový;Batoh;1;70,00\r\n"
            ).encode("cp1250")


@pytest.fixture
def iso(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp.RUNNER, "state_path", str(tmp_path / "automations.json"))
    monkeypatch.setattr(webapp, "ORDERS_REMINDER_STATE", str(tmp_path / "orders_reminder.json"))
    monkeypatch.setattr(webapp, "_orders_csv_cached", lambda: ORDERS_CSV)
    monkeypatch.setattr(webapp, "_classify_contacted", lambda note: _CLASSIFY[note])
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("MAIL_BCC", "owner@example.com")
    sent = []
    monkeypatch.setattr(webapp, "_send_mail_html",
                        lambda to, subject, body, bcc=None, **kw:
                        sent.append({"to": to, "subject": subject, "body": body}) or True)
    if hasattr(webapp, "_quarantined"):
        webapp._quarantined.clear()
    yield {"tmp": tmp_path, "sent": sent}
    if hasattr(webapp, "_quarantined"):
        webapp._quarantined.clear()


def _p(iso):
    return iso["tmp"] / "orders_reminder.json"


def _run(iso):
    """Run, tolerating the fail-closed exception; report whether it raised."""
    corrupt_exc = getattr(webapp, "DedupStoreCorrupt", None)
    try:
        webapp.run_orders_reminder()
        return "ran"
    except Exception as e:                                    # noqa: BLE001
        if corrupt_exc and isinstance(e, corrupt_exc):
            return "blocked"
        return f"raised:{type(e).__name__}"


def _orders(iso):
    try:
        return sorted(json.loads(_p(iso).read_text()).get("orders") or {})
    except Exception as e:                                    # noqa: BLE001
        return f"unreadable:{type(e).__name__}"


VER = os.environ.get("PROBE_VER", "?")


# ── NC1 — the ORIGINAL #225 bug: an unparseable store is silently wiped ────────
def test_NC1_truncated_store(iso):
    webapp.run_orders_reminder()
    iso["sent"].clear()
    _p(iso).write_text('{"orders": {"20261001": {"status": "emai', encoding="utf-8")
    out = _run(iso)
    print(f"\n[{VER}] NC1 truncated store  -> {out:8} | orders now: {_orders(iso)} "
          f"| mails: {[m['to'] for m in iso['sent']]}")


# ── NC2 — the INNER map wipe found in this review round ───────────────────────
def test_NC2_inner_map_null(iso):
    webapp.run_orders_reminder()
    iso["sent"].clear()
    st = json.loads(_p(iso).read_text())
    st["orders"] = None
    _p(iso).write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    out = _run(iso)
    print(f"\n[{VER}] NC2 {{'orders': null}}   -> {out:8} | orders now: {_orders(iso)} "
          f"| mails run2: {[m['to'] for m in iso['sent']]}")
    iso["sent"].clear()
    out2 = _run(iso)
    print(f"[{VER}] NC2 next run          -> {out2:8} | mails run3: "
          f"{[m['to'] for m in iso['sent']]}")


# ── NC3 — a write cut mid-multibyte (UnicodeDecodeError, not JSONDecodeError) ──
def test_NC3_truncated_multibyte(iso):
    webapp.run_orders_reminder()
    iso["sent"].clear()
    _p(iso).write_bytes(b'{"orders": {"20261001": {"name": "Ja\xc3')
    out = _run(iso)
    bk = len(list(iso["tmp"].glob("orders_reminder.json.corrupt-*")))
    print(f"\n[{VER}] NC3 multibyte cut     -> {out:8} | backups: {bk} "
          f"| mails: {[m['to'] for m in iso['sent']]}")


# ── NC4 — the file DISAPPEARS mid-run (the regression this round must not add) ─
def test_NC4_store_deleted_mid_run(iso, monkeypatch):
    webapp.run_orders_reminder()
    before = _orders(iso)
    iso["sent"].clear()
    real = _CLASSIFY

    def kill_then_classify(note):
        _p(iso).unlink(missing_ok=True)
        return real[note]
    monkeypatch.setattr(webapp, "_classify_contacted", kill_then_classify)
    monkeypatch.setattr(webapp, "_orders_csv_cached", _csv_plus_one)
    out = _run(iso)
    after = _orders(iso)
    # …and what the NEXT normal run then does
    monkeypatch.setattr(webapp, "_classify_contacted", lambda n: real[n])
    monkeypatch.setattr(webapp, "_orders_csv_cached", lambda: ORDERS_CSV)
    iso["sent"].clear()
    _run(iso)
    print(f"\n[{VER}] NC4 deleted mid-run   -> {out:8} | before: {before} -> after: {after} "
          f"| NEXT RUN re-mails: {[m['to'] for m in iso['sent']]}")


# ── NC5 — the corrupt-store MESSAGE the manager is told to act on ─────────────
def test_NC5_recovery_message(iso):
    _p(iso).write_text('{"orders": {"20261001": {"status": "emai', encoding="utf-8")
    corrupt_exc = getattr(webapp, "DedupStoreCorrupt", None)
    msg = "(no fail-closed guard in this version)"
    if corrupt_exc:
        try:
            webapp.run_orders_reminder()
        except corrupt_exc as e:
            msg = str(e)
    print(f"\n[{VER}] NC5 message -> {msg}")
