"""Unit tests for the Pošta SK uncollected-shipments pure logic (#93).

Hermetic: tracking responses are saved fixtures (shapes verified against the
LIVE api.posta.sk on 2026-07-22 — the delivered + invalid_format ones are real
responses with anonymized numbers), no network, no SMTP.
"""
import json
import os
from datetime import date, timedelta

from parovanie import posta_uncollected as pu

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "posta")
TODAY = date(2026, 7, 22)


def _fix(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return json.load(f)


ORDERS_CSV = (
    "code;date;statusName;email;phone;billFullName;packageNumber;itemCode\r\n"
    "2026100;2026-07-10 10:00:00;Vybavená;jan@example.com;+421900111222;Ján Vzor;EF000000002SK;1/M\r\n"
    # second item line of the SAME order — must dedupe to one shipment
    "2026100;2026-07-10 10:00:00;Vybavená;jan@example.com;+421900111222;Ján Vzor;EF000000002SK;2/L\r\n"
    # the numeric-label class that broke n8n (still a shipment — checked, then flagged invalid)
    "2026101;2026-07-12 09:00:00;Vybavená;eva@example.com;;Eva Testová;06565700348274;3/S\r\n"
    # cancelled order — never nag the customer
    "2026102;2026-07-15 09:00:00;Stornovaná;x@example.com;;Storno Osoba;EF000000003SK;4/S\r\n"
    # older than the 30-day source window
    "2026103;2026-05-01 09:00:00;Vybavená;old@example.com;;Stará Objednávka;EF000000004SK;5/S\r\n"
    # no package number → not shipped via tracked carrier
    "2026104;2026-07-18 09:00:00;Vybavuje sa;nopkg@example.com;;Bez Balíka;;6/S\r\n"
)


# ── shipments_from_orders_csv ──────────────────────────────────────────────────
def test_shipments_filter_dedupe_and_window():
    s = pu.shipments_from_orders_csv(ORDERS_CSV, today=TODAY)
    assert [x["code"] for x in s] == ["2026100", "2026101"]
    first = s[0]
    assert first["packageNumber"] == "EF000000002SK"
    assert first["email"] == "jan@example.com"
    assert first["phone"] == "+421900111222"
    assert first["billFullName"] == "Ján Vzor"
    assert first["date"] == "2026-07-10"


def test_shipments_cp1250_bytes_roundtrip():
    s = pu.shipments_from_orders_csv(ORDERS_CSV.encode("cp1250"), today=TODAY)
    assert s[0]["billFullName"] == "Ján Vzor"          # diacritics survive
    assert s[1]["billFullName"] == "Eva Testová"


def test_shipments_bad_date_skipped():
    csv_txt = ("code;date;statusName;email;phone;billFullName;packageNumber\r\n"
               "X1;garbage;Vybavená;a@b.c;;Meno;EF1SK\r\n")
    assert pu.shipments_from_orders_csv(csv_txt, today=TODAY) == []


# ── classify_tracking ──────────────────────────────────────────────────────────
def test_classify_notified_znp_is_uncollected():
    c = pu.classify_tracking(_fix("tracking_notified_znp.json"), today=TODAY)
    assert c["uncollected"] is True
    assert c["status"] == "ok"
    assert c["office_name"] == "Skalica 1"
    assert c["office_addr"] == "Potočná 24, 90901 Skalica"
    assert c["retained_till"] == "2026-08-03"
    assert c["notified_since"] == "2026-07-16"
    assert c["days_at_post"] == 6                       # 22.7. - 16.7.


def test_classify_delivered_not_uncollected():
    c = pu.classify_tracking(_fix("tracking_delivered.json"), today=TODAY)
    assert c["uncollected"] is False
    assert c["status"] == "ok"


def test_classify_invalid_format_surfaced():
    """The exact per-result status that silently broke the n8n workflow."""
    c = pu.classify_tracking(_fix("tracking_invalid_format.json"), today=TODAY)
    assert c["status"] == "invalid_format"
    assert c["uncollected"] is False


def test_classify_notified_without_znp_detail_is_not_uncollected():
    j = {"results": [{"status": "ok", "events": [
        {"stateCode": "notified", "detailCode": "XYZ", "localDate": "2026-07-16T08:00:00"}]}]}
    assert pu.classify_tracking(j, today=TODAY)["uncollected"] is False


def test_classify_znp_must_be_last_event():
    """A shipment notified earlier but since delivered must NOT alert."""
    j = {"results": [{"status": "ok", "events": [
        {"stateCode": "notified", "detailCode": "ZNPOK", "localDate": "2026-07-10T08:00:00"},
        {"stateCode": "delivered", "detailCode": "OK", "localDate": "2026-07-12T10:00:00"}]}]}
    assert pu.classify_tracking(j, today=TODAY)["uncollected"] is False


def test_classify_empty_and_missing_shapes():
    assert pu.classify_tracking({}, today=TODAY)["status"] == "no_results"
    assert pu.classify_tracking(None, today=TODAY)["status"] == "no_results"
    assert pu.classify_tracking({"results": [{"status": "ok", "events": []}]},
                                today=TODAY)["status"] == "no_events"


def test_classify_days_at_post_minimum_one():
    j = {"results": [{"status": "ok", "events": [
        {"stateCode": "notified", "detailCode": "ZNPOK",
         "localDate": TODAY.isoformat() + "T08:00:00"}]}]}
    assert pu.classify_tracking(j, today=TODAY)["days_at_post"] == 1


# ── escalation state parsing ───────────────────────────────────────────────────
def test_parse_notified_variants():
    assert pu.parse_notified("") == (0, None)
    assert pu.parse_notified(None) == (0, None)
    assert pu.parse_notified("2|2026-07-01") == (2, date(2026, 7, 1))
    # legacy n8n value: bare date = one notification already sent
    assert pu.parse_notified("2026-07-01") == (1, date(2026, 7, 1))
    assert pu.parse_notified("junk") == (0, None)
    assert pu.parse_notified("x|2026-07-01") == (0, date(2026, 7, 1))


# ── cadence: day 0 → +3 → +3 → +7, max 4 ──────────────────────────────────────
def test_should_send_cadence():
    d = date(2026, 7, 22)
    assert pu.should_send(0, None, d) is True                       # first mail immediately
    assert pu.should_send(1, d - timedelta(days=2), d) is False
    assert pu.should_send(1, d - timedelta(days=3), d) is True   # +3
    assert pu.should_send(2, d - timedelta(days=2), d) is False
    assert pu.should_send(2, d - timedelta(days=3), d) is True   # +3
    assert pu.should_send(3, d - timedelta(days=6), d) is False
    assert pu.should_send(3, d - timedelta(days=7), d) is True   # +7
    assert pu.should_send(4, d - timedelta(days=30), d) is False  # hard cap
    assert pu.should_send(1, None, d) is False                       # count>0 with no date


# ── e-mail templates (verbatim n8n port) ──────────────────────────────────────
def test_build_email_subjects_per_count():
    subs = [pu.build_email(n, "Ján Vzor", "EF000000002SK", "Skalica 1",
                           "Potočná 24, 90901 Skalica", "2026-08-03")[0]
            for n in (1, 2, 3, 4)]
    assert subs[0] == "Vaša zásielka čaká na vyzdvihnutie | EF000000002SK"
    assert subs[1] == "Pripomienka: zásielka stále čaká | EF000000002SK"
    assert subs[2] == "Posledné upozornenie: zásielka bude vrátená | EF000000002SK"
    assert subs[3] == "Posledná výzva: zásielka bude vrátená | EF000000002SK"


def test_build_email_body_contents():
    _, body = pu.build_email(1, "Ján Vzor", "EF000000002SK", "Skalica 1",
                             "Potočná 24, 90901 Skalica", "2026-08-03")
    assert "Dobrý deň, <strong>Ján Vzor</strong>" in body
    assert "EF000000002SK" in body
    assert "Skalica 1" in body
    assert "2026-08-03" in body
    assert "https://www.posta.sk/sledovanie-zasielok#parcel=EF000000002SK" in body
    assert "eshop@forestshop.sk" in body


def test_build_email_no_retained_till_fallback():
    _, body = pu.build_email(1, "X", "EF1SK", "Pošta", "", "")
    assert "čo najskôr" in body


def test_build_email_escapes_customer_name():
    _, body = pu.build_email(1, '<img src=x onerror=alert(1)>', "EF1SK", "P", "", "")
    assert "<img" not in body
    assert "&lt;img" in body


# ── evaluate_shipment (full verdict) ──────────────────────────────────────────
SHIP = {"code": "2026100", "date": "2026-07-10", "packageNumber": "EF000000002SK",
        "email": "jan@example.com", "phone": "+421900111222", "billFullName": "Ján Vzor"}


def test_evaluate_first_notification():
    r = pu.evaluate_shipment(SHIP, _fix("tracking_notified_znp.json"), "", today=TODAY)
    assert r["uncollected"] and r["send"]
    assert r["count"] == 1
    assert r["new_state_value"] == "1|2026-07-22"
    assert r["email_subject"].startswith("Vaša zásielka čaká")
    assert r["call_needed"] is False
    assert r["admin_link"].endswith("vyhladavanie/?string=2026100&src=orders")
    assert r["days_at_post"] == 6


def test_evaluate_recent_notification_waits():
    r = pu.evaluate_shipment(SHIP, _fix("tracking_notified_znp.json"),
                             "1|2026-07-21", today=TODAY)
    assert r["uncollected"] and not r["send"]
    assert r["count"] == 1
    assert r["new_state_value"] == "1|2026-07-21"      # unchanged
    assert r["email_body"] == ""


def test_evaluate_fourth_mail_flags_call_needed():
    r = pu.evaluate_shipment(SHIP, _fix("tracking_notified_znp.json"),
                             "3|2026-07-10", today=TODAY)
    assert r["send"] and r["count"] == 4
    assert r["call_needed"] is True
    assert r["email_subject"].startswith("Posledná výzva")


def test_evaluate_cap_after_four():
    r = pu.evaluate_shipment(SHIP, _fix("tracking_notified_znp.json"),
                             "4|2026-06-01", today=TODAY)
    assert r["uncollected"] and not r["send"]
    assert r["call_needed"] is True                    # still needs the phone call


def test_evaluate_delivered_resets_nothing_sends_nothing():
    r = pu.evaluate_shipment(SHIP, _fix("tracking_delivered.json"), "2|2026-07-15",
                             today=TODAY)
    assert not r["uncollected"] and not r["send"] and not r["invalid"]


def test_evaluate_invalid_format():
    ship = dict(SHIP, packageNumber="06565700348274")
    r = pu.evaluate_shipment(ship, _fix("tracking_invalid_format.json"), "", today=TODAY)
    assert r["invalid"] is True
    assert not r["uncollected"] and not r["send"]
