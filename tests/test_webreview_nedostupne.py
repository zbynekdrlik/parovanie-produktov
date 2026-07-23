"""Webreview endpoint tests for „Nedostupné tovary" (#100).

Hermetic: the orders export is a fixture CSV, the alternatives resolver is monkeypatched (no
products.csv / marketing XML needed), SMTP is a capturing stub (a mail WOULD go out — nothing is
ever sent / no network), and every store path is redirected to tmp.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webreview"))
import app as webapp  # noqa: E402

from tests.conftest import authed_client  # noqa: E402

HEADER = "code;date;statusName;email;billFullName;itemName;itemAmount;itemCode;itemVariantName"
ORDERS_CSV = ("\r\n".join([
    HEADER,
    # two DIFFERENT customers ordered the unavailable variant 40237/3XL (both open)
    "5001;2026-07-10 10:00:00;Vybavuje sa;ada@example.com;Ada Nová;Nohavice FOREST;1;40237/3XL;3XL",
    "5002;2026-07-11 11:00:00;Vybavuje sa;bob@example.com;Bob Starý;Nohavice FOREST;2;40237/3XL;3XL",
    # a different (available) variant → excluded
    "5003;2026-07-12 09:00:00;Vybavuje sa;cyr@example.com;Cyril;Nohavice FOREST;1;40237/4XL;4XL",
]) + "\r\n").encode("cp1250")


def _resolve(code):
    if code == "40237/3XL":
        return ("Nohavice FOREST 1003",
                [{"code": "60116/90", "name": "Opasok FOREST",
                  "url": "https://www.forestshop.sk/opasok/"}])
    return ("", [])


@pytest.fixture
def iso(tmp_path, monkeypatch):
    """Isolate the stores + network/SMTP/catalog edges."""
    monkeypatch.setattr(webapp, "UNAVAIL", str(tmp_path / "unavailable_items.json"))
    monkeypatch.setattr(webapp, "NEDOSTUPNE", str(tmp_path / "nedostupne.json"))
    monkeypatch.setattr(webapp, "_orders_csv_cached", lambda: ORDERS_CSV)
    monkeypatch.setattr(webapp, "_resolve_alternatives", _resolve)
    sent = []
    monkeypatch.setattr(webapp, "_send_mail_html",
                        lambda to, subject, body, bcc=None:
                        sent.append({"to": to, "subject": subject, "body": body}) or True)
    # one product flagged unavailable (order-line key from the to-order tab)
    (tmp_path / "unavailable_items.json").write_text(
        json.dumps({"5001|40237/3XL": True}), encoding="utf-8")
    return {"tmp": tmp_path, "sent": sent}


def _nedostupne_store(iso):
    p = iso["tmp"] / "nedostupne.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def test_get_lists_flagged_product_with_orders_and_alternatives(iso):
    c = authed_client()
    j = c.get("/api/nedostupne").get_json()
    assert len(j["products"]) == 1
    p = j["products"][0]
    assert p["code"] == "40237/3XL"
    assert p["order_count"] == 2                       # only the two OPEN 3XL orders
    assert {o["email"] for o in p["orders"]} == {"ada@example.com", "bob@example.com"}
    assert p["alternatives"][0]["name"] == "Opasok FOREST"
    assert p["nedostupne"] is False and p["alternativa"] is False


def test_get_degrades_to_empty_on_orders_fetch_error(iso, monkeypatch):
    def boom():
        raise RuntimeError("no orders")
    monkeypatch.setattr(webapp, "_orders_csv_cached", boom)
    j = authed_client().get("/api/nedostupne").get_json()
    assert j["products"] == [] and "error" in j


def test_state_persists_checkbox_and_clears(iso):
    c = authed_client()
    assert c.post("/api/nedostupne/state",
                  json={"code": "40237/3XL", "field": "alternativa", "value": True}
                  ).get_json()["ok"] is True
    assert _nedostupne_store(iso)["40237/3XL"]["alternativa"] is True
    # clearing the only flag (and no sent history) drops the record
    c.post("/api/nedostupne/state",
           json={"code": "40237/3XL", "field": "alternativa", "value": False})
    assert "40237/3XL" not in _nedostupne_store(iso)


def test_state_rejects_bad_field(iso):
    r = authed_client().post("/api/nedostupne/state",
                             json={"code": "X", "field": "bogus", "value": True})
    assert r.status_code == 400


def test_preview_does_not_send_and_lists_recipients(iso):
    j = authed_client().post("/api/nedostupne/preview",
                             json={"code": "40237/3XL", "type": "nedostupne"}).get_json()
    assert j["ok"] is True
    assert {r["email"] for r in j["recipients"]} == {"ada@example.com", "bob@example.com"}
    assert "nedostupný" in j["html"]
    assert iso["sent"] == []                            # PREVIEW sends nothing


def test_preview_alternative_includes_links(iso):
    j = authed_client().post("/api/nedostupne/preview",
                             json={"code": "40237/3XL", "type": "alternativa"}).get_json()
    assert 'href="https://www.forestshop.sk/opasok/"' in j["html"]
    assert iso["sent"] == []


def test_send_emails_all_affected_and_dedups(iso):
    c = authed_client()
    r = c.post("/api/nedostupne/send", json={"code": "40237/3XL", "type": "nedostupne"}).get_json()
    assert r == {"ok": True, "sent": 2, "failed": 0, "skipped": 0}
    assert {m["to"] for m in iso["sent"]} == {"ada@example.com", "bob@example.com"}
    # dedup store recorded per order+type
    store = _nedostupne_store(iso)["40237/3XL"]["sent"]
    assert set(store) == {"5001|nedostupne", "5002|nedostupne"}
    # second send is a no-op (already sent → 0 new recipients)
    iso["sent"].clear()
    r2 = c.post("/api/nedostupne/send",
                json={"code": "40237/3XL", "type": "nedostupne"}).get_json()
    assert r2["sent"] == 0
    assert iso["sent"] == []


def test_send_reports_failure_when_smtp_down(iso, monkeypatch):
    monkeypatch.setattr(webapp, "_send_mail_html", lambda *a, **k: False)
    r = authed_client().post("/api/nedostupne/send",
                             json={"code": "40237/3XL", "type": "nedostupne"})
    assert r.status_code == 502
    body = r.get_json()
    assert body["sent"] == 0 and body["failed"] == 2
    # nothing recorded as sent → a later successful run can retry
    assert _nedostupne_store(iso).get("40237/3XL", {}).get("sent", {}) == {}


def test_send_rejects_bad_type(iso):
    r = authed_client().post("/api/nedostupne/send", json={"code": "X", "type": "bogus"})
    assert r.status_code == 400


def test_send_dedups_same_email_across_two_orders(iso, monkeypatch):
    """Regression (#100 review Finding 1): one customer with the SAME unavailable
    variant in TWO open orders must be e-mailed ONCE and never again. The dedup store
    must record BOTH order keys — not just the winning one — or the second send
    re-e-mails the sibling order (a real duplicate to a real customer)."""
    csv2 = ("\r\n".join([
        HEADER,
        "5001;2026-07-10 10:00:00;Vybavuje sa;ada@example.com;Ada Nová;Nohavice FOREST;1;40237/3XL;3XL",
        "5005;2026-07-13 12:00:00;Vybavuje sa;ada@example.com;Ada Nová;Nohavice FOREST;1;40237/3XL;3XL",
    ]) + "\r\n").encode("cp1250")
    monkeypatch.setattr(webapp, "_orders_csv_cached", lambda: csv2)
    c = authed_client()
    r = c.post("/api/nedostupne/send", json={"code": "40237/3XL", "type": "nedostupne"}).get_json()
    assert r["sent"] == 1                                   # ada e-mailed exactly ONCE
    assert [m["to"] for m in iso["sent"]] == ["ada@example.com"]
    # BOTH order keys recorded → the sibling order can never trigger a re-send
    store = _nedostupne_store(iso)["40237/3XL"]["sent"]
    assert set(store) == {"5001|nedostupne", "5005|nedostupne"}
    # second send is a genuine no-op — NO duplicate e-mail to ada
    iso["sent"].clear()
    r2 = c.post("/api/nedostupne/send", json={"code": "40237/3XL", "type": "nedostupne"}).get_json()
    assert r2["sent"] == 0
    assert iso["sent"] == []
