"""ADVERSARIAL PROBE — PR #228. Temporary; delete after the review.

Hermetic: reuses the same isolation the shipped suites use (tmp stores, stubbed SMTP/OpenAI,
no network, no Posta API).
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
                        sent.append({"to": to, "subject": subject, "body": body,
                                     "bcc": bcc, **kw}) or True)
    # the process-wide quarantine memo must not leak between probes
    webapp._quarantined.clear()
    yield {"tmp": tmp_path, "sent": sent}
    webapp._quarantined.clear()


def _p(iso):
    return iso["tmp"] / "orders_reminder.json"


def _store(iso):
    return json.loads(_p(iso).read_text())


# ══════════════════════════════════════════════════════════════════════════════
# P1 — corrupt store: 0 mails, backup exists, ORIGINAL content not lost
# ══════════════════════════════════════════════════════════════════════════════
_CORRUPT = '{"orders": {"20261001": {"status": "emai'


def test_P1_corrupt_store_mails_nobody_and_keeps_the_original(iso):
    webapp.run_orders_reminder()
    assert len(iso["sent"]) == 1
    good = _p(iso).read_bytes()
    assert b"20261001" in good
    _p(iso).write_text(_CORRUPT, encoding="utf-8")
    iso["sent"].clear()

    with pytest.raises(webapp.DedupStoreCorrupt):
        webapp.run_orders_reminder()

    assert iso["sent"] == []                                   # nothing mailed
    assert _p(iso).read_text(encoding="utf-8") == _CORRUPT      # original untouched
    (bk,) = list(iso["tmp"].glob("orders_reminder.json.corrupt-*"))
    assert bk.read_text(encoding="utf-8") == _CORRUPT           # backup == the corrupt bytes
    # …and repeated runs never mail either
    for _ in range(3):
        with pytest.raises(webapp.DedupStoreCorrupt):
            webapp.run_orders_reminder()
    assert iso["sent"] == []
    assert len(list(iso["tmp"].glob("orders_reminder.json.corrupt-*"))) == 1


# ══════════════════════════════════════════════════════════════════════════════
# P2 — MISSING store is a legitimate first run (no false silencing)
# ══════════════════════════════════════════════════════════════════════════════
def test_P2_missing_store_runs_normally_and_mails(iso):
    assert not _p(iso).exists()
    stats = webapp.run_orders_reminder()
    assert stats["emailed_now"] == 1
    assert [m["to"] for m in iso["sent"]] == ["b@x.sk"]
    assert set(_store(iso)["orders"]) == {"20261001", "20261002"}


# ══════════════════════════════════════════════════════════════════════════════
# P3 — the whole shape matrix: which shapes BLOCK, which pass
# ══════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("raw,expect", [
    ('{"orders": null}', "block"),
    ('{"orders": []}', "block"),
    ('{"orders": "x"}', "block"),
    ('{"orders": 0}', "block"),
    ('[]', "block"),
    ('null', "block"),
    ('"a string"', "block"),
    ('', "block"),                       # 0-byte file (truncated write)
    ('   ', "block"),                    # whitespace only
    ('{"orders": {}}', "run"),           # empty map = nothing sent yet
    ('{}', "run"),                       # no `orders` key at all = first run
    ('{"last_check": "x"}', "run"),      # key simply absent
    ('{"orders": {"20261001": null}}', "run"),   # garbage UNDER one code, tolerated
])
def test_P3_shape_matrix(iso, raw, expect):
    _p(iso).write_text(raw, encoding="utf-8")
    if expect == "block":
        with pytest.raises(webapp.DedupStoreCorrupt):
            webapp.run_orders_reminder()
        assert iso["sent"] == []
        assert _p(iso).read_text(encoding="utf-8") == raw    # never rewritten
    else:
        webapp.run_orders_reminder()                         # must NOT raise
        assert [m["to"] for m in iso["sent"]] == ["b@x.sk"]


def test_P3b_truncated_multibyte_blocks(iso):
    _p(iso).write_bytes(b'{"orders": {"20261001": {"name": "Ja\xc3')
    with pytest.raises(webapp.DedupStoreCorrupt):
        webapp.run_orders_reminder()
    assert iso["sent"] == []
    assert len(list(iso["tmp"].glob("orders_reminder.json.corrupt-*"))) == 1


def test_P3c_a_zero_byte_backup_is_useless_for_repair(iso):
    """0-byte store IS blocked (right call) — but the backup it points the human at is 0 bytes
    too, so the message „oprav podľa zálohy" gives them nothing to repair FROM."""
    _p(iso).write_text('', encoding="utf-8")
    with pytest.raises(webapp.DedupStoreCorrupt) as e:
        webapp.run_orders_reminder()
    (bk,) = list(iso["tmp"].glob("orders_reminder.json.corrupt-*"))
    assert bk.read_bytes() == b""
    assert "zálohy" in str(e.value)


# ══════════════════════════════════════════════════════════════════════════════
# P5 — the preview endpoints are provably inert
# ══════════════════════════════════════════════════════════════════════════════
def _seed(iso):
    webapp.run_orders_reminder()
    return authed_client()


def test_P5_reminder_preview_writes_nothing_and_sends_nothing(iso, monkeypatch):
    c = _seed(iso)
    iso["sent"].clear()
    before = _p(iso).read_bytes()
    mtime = os.path.getmtime(_p(iso))
    # any SMTP or OpenAI touch is an outright failure
    monkeypatch.setattr(webapp, "_send_mail_html",
                        lambda *a, **k: pytest.fail("preview called SMTP"))
    monkeypatch.setattr(webapp, "_classify_contacted",
                        lambda *a, **k: pytest.fail("preview called OpenAI"))
    monkeypatch.setattr(webapp, "_save_orders_reminder",
                        lambda *a, **k: pytest.fail("preview wrote the store"))

    for _ in range(3):
        r = c.post("/api/orders-reminder/preview", json={"code": "20261000"})
        assert r.status_code == 200 and r.get_json()["ok"] is True
        r = c.get("/api/orders-reminder/preview?code=20261002")
        assert r.status_code == 200

    assert _p(iso).read_bytes() == before          # BYTE-identical
    assert os.path.getmtime(_p(iso)) == mtime      # not even rewritten with the same bytes
    assert iso["sent"] == []
    assert list(_store(iso)["orders"]) == ["20261001", "20261002"]   # no new claim/record
    assert not list(iso["tmp"].glob("*.tmp"))


def test_P5b_preview_is_behind_the_login_gate(iso):
    _seed(iso)
    anon = webapp.app.test_client()
    for m, u in (("post", "/api/orders-reminder/preview"),
                 ("get", "/api/orders-reminder/preview?code=20261000"),
                 ("post", "/api/posta-uncollected/preview"),
                 ("get", "/api/posta-uncollected/preview?package=EF1")):
        r = getattr(anon, m)(u, json={"code": "20261000", "package": "EF1"})
        assert r.status_code == 401, (m, u, r.status_code)
        assert "recipient" not in (r.get_json() or {})       # no PII leaked


def test_P5c_preview_html_is_escaped(iso):
    """The name lands in the mail body; the modal renders it in a sandbox="" iframe, but the
    builder must escape it anyway — the SAME html is what the customer's mail client gets."""
    c = _seed(iso)
    st = _store(iso)
    st["red"][0]["billFullName"] = '<img src=x onerror=alert(1)>'
    _p(iso).write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    j = c.post("/api/orders-reminder/preview", json={"code": "20261000"}).get_json()
    assert "&lt;img" in j["html"] and "<img src=x" not in j["html"]


# ══════════════════════════════════════════════════════════════════════════════
# P6 / P7 — adversarial: the store changes UNDER a run that already started
# ══════════════════════════════════════════════════════════════════════════════
def test_P6_store_DELETED_mid_run_wipes_the_dedup_history(iso, monkeypatch):
    """The final save re-reads the store; a MISSING file reads as {} (first run), so the whole
    map is replaced by this run's own records. Everything sent before is forgotten."""
    webapp.run_orders_reminder()                                # 20261001 emailed, 20261002 skipped
    assert set(_store(iso)["orders"]) == {"20261001", "20261002"}
    iso["sent"].clear()

    real = webapp._classify_contacted

    def delete_then_classify(note):
        _p(iso).unlink(missing_ok=True)                         # a human „zmaž súbor" mid-run
        return real(note)
    monkeypatch.setattr(webapp, "_classify_contacted", delete_then_classify)
    monkeypatch.setattr(webapp, "_orders_csv_cached", _csv_with_a_new_note_order)
    webapp.run_orders_reminder()

    survivors = set(_store(iso)["orders"])
    print("\nP6 survivors after mid-run delete:", sorted(survivors))


def _csv_with_a_new_note_order():
    return (ORDERS_CSV.decode("cp1250").rstrip("\r\n") + "\r\n"
            + f"20261005;{OLD} 07:00:00;Vybavuje sa;volať zákazníka;e@x.sk;;Nový;Batoh;1;70,00\r\n"
            ).encode("cp1250")


def test_P6b_store_deleted_mid_run_then_next_run_remails(iso, monkeypatch):
    webapp.run_orders_reminder()
    assert [m["to"] for m in iso["sent"]] == ["b@x.sk"]
    iso["sent"].clear()
    real = webapp._classify_contacted

    def delete_then_classify(note):
        _p(iso).unlink(missing_ok=True)
        return real(note)
    monkeypatch.setattr(webapp, "_classify_contacted", delete_then_classify)
    monkeypatch.setattr(webapp, "_orders_csv_cached", _csv_with_a_new_note_order)
    webapp.run_orders_reminder()
    lost = "20261001" not in _store(iso)["orders"]

    iso["sent"].clear()
    monkeypatch.setattr(webapp, "_classify_contacted", real)
    monkeypatch.setattr(webapp, "_orders_csv_cached", lambda: ORDERS_CSV)
    webapp.run_orders_reminder()
    print("\nP6b record lost:", lost, "| re-mailed:", [m["to"] for m in iso["sent"]])


def test_P7_store_CORRUPTED_mid_run_aborts_without_writing(iso, monkeypatch):
    webapp.run_orders_reminder()
    before = set(_store(iso)["orders"])
    iso["sent"].clear()
    real = webapp._classify_contacted

    def corrupt_then_classify(note):
        _p(iso).write_text(_CORRUPT, encoding="utf-8")
        return real(note)
    monkeypatch.setattr(webapp, "_classify_contacted", corrupt_then_classify)
    monkeypatch.setattr(webapp, "_orders_csv_cached", _csv_with_a_new_note_order)
    with pytest.raises(webapp.DedupStoreCorrupt):
        webapp.run_orders_reminder()
    assert _p(iso).read_text(encoding="utf-8") == _CORRUPT      # not overwritten
    print("\nP7 before:", sorted(before), "| mails during aborted run:",
          [m["to"] for m in iso["sent"]])


# ══════════════════════════════════════════════════════════════════════════════
# P9 — the corruption is VISIBLE to the manager (not a clean empty tab)
# ══════════════════════════════════════════════════════════════════════════════
def test_P9_tab_flags_the_corruption(iso):
    c = _seed(iso)
    j = c.get("/api/orders-reminder").get_json()
    assert j.get("store_corrupt") in (False, None)
    _p(iso).write_text(_CORRUPT, encoding="utf-8")
    r = c.get("/api/orders-reminder")
    assert r.status_code == 200
    assert r.get_json()["store_corrupt"] is True


def test_P9b_override_and_preview_fail_closed_too(iso):
    c = _seed(iso)
    iso["sent"].clear()
    _p(iso).write_text(_CORRUPT, encoding="utf-8")
    for payload in ({"code": "20261000", "action": "send"},
                    {"code": "20261000", "action": "contact"}):
        r = c.post("/api/orders-reminder/override", json=payload)
        assert r.status_code == 503, payload
    assert c.post("/api/orders-reminder/preview",
                  json={"code": "20261000"}).status_code == 503
    assert iso["sent"] == []
    assert _p(iso).read_text(encoding="utf-8") == _CORRUPT


# ══════════════════════════════════════════════════════════════════════════════
# P10 — the automation runner surfaces it, and it stays blocked until repaired
# ══════════════════════════════════════════════════════════════════════════════
def test_P10_blocked_until_a_human_repairs_it(iso):
    c = authed_client()
    webapp.run_orders_reminder()
    good = _p(iso).read_bytes()
    iso["sent"].clear()
    _p(iso).write_text(_CORRUPT, encoding="utf-8")

    for _ in range(2):
        assert c.post("/api/automations/orders_reminder/run").get_json()["started"] is True
        webapp.RUNNER._threads["orders_reminder"].join(timeout=15)
        (a,) = [x for x in c.get("/api/automations").get_json()["automations"]
                if x["key"] == "orders_reminder"]
        assert a["last_status"] == "error"
    assert iso["sent"] == []
    print("\nP10 last_error:", a["last_error"])

    _p(iso).write_bytes(good)                       # human restores the file
    assert c.post("/api/automations/orders_reminder/run").get_json()["started"] is True
    webapp.RUNNER._threads["orders_reminder"].join(timeout=15)
    (a,) = [x for x in c.get("/api/automations").get_json()["automations"]
            if x["key"] == "orders_reminder"]
    assert a["last_status"] == "ok"
    assert iso["sent"] == []                        # …and still no duplicate mail
