"""Unit tests for the Pošta SK uncollected-shipments pure logic (#93).

Hermetic: tracking responses are saved fixtures (shapes verified against the
LIVE api.posta.sk on 2026-07-22 — the delivered + invalid_format ones are real
responses with anonymized numbers), no network, no SMTP.

`tracking_notified_znp.json` was rebuilt for #283: the original was invented
alongside the feature and put `retainedTill` on the notified EVENT, a shape the
live API does not produce — it carries the field at RESULT level. That invented
shape is exactly why the missing pickup deadline in the escalation mails went
unnoticed, so the fixture now mirrors the real response (result-level
`retainedTill`, real `detailCode` ZNP1AN as seen in the anonymized
`tracking_collected_at_office.json`).
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


# ── carrier filter (#126): only Pošta SK, DPD/other couriers excluded ──────────
# Real live export (2026-07-22, data/out/orders_cache.csv, 523 orders): the
# SHIPPING pseudo-item's itemName is NEVER literally "Slovenská pošta" — Pošta
# SK home delivery is labelled "Kuriér" (SHIPPING11, ~98% of volume, carries
# EF...SK tracking numbers); "DPD doručenie na adresu" (SHIPPING23) and "DPD
# kuriér" (SHIPPING26) carry 14-digit numeric DPD labels. So the filter is a
# BLOCKLIST (exclude recognised non-Pošta couriers), not an allowlist matching
# "pošta" — an allowlist would have excluded 223/228 real Pošta shipments.
CARRIER_CSV = (
    "code;date;statusName;email;phone;billFullName;packageNumber;itemCode;itemName\r\n"
    # Pošta SK home delivery, labelled "Kuriér" in the export — INCLUDED
    "3001;2026-07-10 10:00:00;Vybavená;posta@example.com;;Pošta Zákazník;"
    "EF000000009SK;SHIPPING11;Kuriér\r\n"
    "3001;2026-07-10 10:00:00;Vybavená;posta@example.com;;Pošta Zákazník;"
    "EF000000009SK;1/M;Obuv\r\n"
    # DPD home delivery — EXCLUDED even though it has a non-empty packageNumber
    "3002;2026-07-11 10:00:00;Vybavená;dpd@example.com;;Dpd Zákazník;"
    "00000000000001;2/M;Obuv\r\n"
    "3002;2026-07-11 10:00:00;Vybavená;dpd@example.com;;Dpd Zákazník;"
    "00000000000001;SHIPPING23;DPD doručenie na adresu\r\n"
    # second DPD shipping method label, lower-case check — EXCLUDED
    "3003;2026-07-12 10:00:00;Vybavená;dpd2@example.com;;Dpd2 Zákazník;"
    "00000000000002;SHIPPING26;dpd kuriér\r\n"
    # no SHIPPING row present at all (older/partial export) — fail-open, INCLUDED
    "3004;2026-07-13 10:00:00;Vybavená;nokur@example.com;;Bez Info;"
    "EF000000010SK;9/L;Obuv\r\n"
)


def test_shipments_dpd_carrier_excluded_posta_carrier_included():
    s = pu.shipments_from_orders_csv(CARRIER_CSV, today=TODAY)
    codes = [x["code"] for x in s]
    assert "3001" in codes                       # "Kuriér" == Pošta SK -> included
    assert "3002" not in codes                    # DPD doručenie na adresu -> excluded
    assert "3003" not in codes                    # DPD kuriér (any case) -> excluded
    assert "3004" in codes                        # no SHIPPING row -> fail-open, included
    assert len(s) == 2


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


def test_classify_reads_retained_till_from_result_level():
    """#283 — the live API returns `retainedTill` on the RESULT, never on an event. Reading it
    only from the events left `retained_till` empty, so the escalation mail dropped its „vyzdvihnite
    si ju do <dátum>" line and fell back to a vague „čo najskôr" — on the shipment that started
    this issue the missing date was the actual deadline."""
    j = {"results": [{"status": "ok", "retainedTill": "2026-07-27", "events": [
        {"stateCode": "notified", "detailCode": "ZNP1AN", "localDate": "2026-07-16T08:10:00"}]}]}
    assert pu.classify_tracking(j, today=TODAY)["retained_till"] == "2026-07-27"


def test_classify_retained_till_event_fallback_kept():
    """The event-level shape is what the ported n8n workflow observed. We have exactly one live
    sample of the result-level shape, so the old reading stays as a fallback rather than being
    swapped for it — the field is display-only and never gates a send, so honouring both costs
    nothing and cannot be wrong-footed by whichever shape the API answers with."""
    j = {"results": [{"status": "ok", "events": [
        {"stateCode": "notified", "detailCode": "ZNP1AN", "localDate": "2026-07-16T08:10:00",
         "retainedTill": "2026-08-03"}]}]}
    assert pu.classify_tracking(j, today=TODAY)["retained_till"] == "2026-08-03"


def test_classify_result_level_retained_till_wins_over_event():
    """Both shapes present: the result-level value is the shipment's current deadline, an event's
    is whatever was true when that event was written — so the result wins."""
    j = {"results": [{"status": "ok", "retainedTill": "2026-07-27", "events": [
        {"stateCode": "notified", "detailCode": "ZNP1AN", "localDate": "2026-07-16T08:10:00",
         "retainedTill": "2026-08-03"}]}]}
    assert pu.classify_tracking(j, today=TODAY)["retained_till"] == "2026-07-27"


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


# ── #222 — a shipment in a FINAL state never has to be tracked again ───────────────
def test_terminal_state_of_a_delivered_shipment():
    assert pu.terminal_state(_fix("tracking_delivered.json")) == "delivered"


def test_notified_shipment_is_not_terminal():
    """The whole point: 'notified' is the state this automation exists to chase — it changes
    (collected, or returned) and must be re-checked on every run."""
    assert pu.terminal_state(_fix("tracking_notified_znp.json")) == ""


def test_invalid_format_is_not_terminal():
    """Pošta SK cannot track it at all, so there is no final state to cache — it stays on the
    „nesledovateľné" list and gets re-checked (the label may be re-issued)."""
    assert pu.terminal_state(_fix("tracking_invalid_format.json")) == ""


def test_collected_at_the_post_office_is_terminal():
    """The case that matters most for the escalation: a parcel that WAS „notified" (ZNP1AN) and
    the customer finally collected it at the office. A live probe of api.posta.sk (2026-07-25)
    showed Pošta SK reports that as stateCode 'delivered' / detailCode 'OKP', not a separate
    code — this fixture is that real (anonymized) response."""
    fx = _fix("tracking_collected_at_office.json")
    states = [e["stateCode"] for e in fx["results"][0]["events"]]
    assert "notified" in states                      # it really was an uncollected parcel…
    assert pu.terminal_state(fx) == "delivered"      # …and collecting it ends the chase


def test_an_unverified_return_state_is_not_trusted():
    """'returned' was never observed in the live probe, so it is NOT in TERMINAL_STATE_CODES
    (#226). If Pošta SK used it for „vrátená na dodaciu poštu" — back at the office and still
    collectible — trusting it would silently freeze a genuinely uncollected parcel out of the
    escalation. An unverified code must cost an API call, never a missed customer notice."""
    api = {"status": "ok", "results": [{"number": "EF1SK", "status": "ok", "events": [
        {"stateCode": "received", "detailCode": "PODOD", "localDate": "2026-07-01T08:00:00"},
        {"stateCode": "returned", "detailCode": "VRAT", "localDate": "2026-07-20T08:00:00"}]}]}
    assert pu.terminal_state(api) == ""


def test_unknown_state_is_never_treated_as_terminal():
    """Fail-safe: an unrecognised stateCode keeps the shipment in the daily check rather than
    silently freezing it out of the automation forever."""
    api = {"status": "ok", "results": [{"number": "EF1SK", "status": "ok", "events": [
        {"stateCode": "somethingNew", "detailCode": "?", "localDate": "2026-07-20T08:00:00"}]}]}
    assert pu.terminal_state(api) == ""
    assert pu.terminal_state({}) == ""
    assert pu.terminal_state(None) == ""
    assert pu.terminal_state([]) == ""


# ── source_coverage: the „zdroj prestal dávať zásielky" alarm (#282 part 1) ────
# The automation's only source of shipments is the `packageNumber` column of the orders export.
# When that column stopped being filled (2026-07-02) the run kept reporting a healthy `ok` with a
# quietly shrinking `checked` — 21 → 13 → 9 → 6 → 4 over five days — and the tab read „0
# nevyzdvihnutých" while a real parcel sat at the post office until its deadline. These tests pin
# the alarm that makes that state impossible to miss, and — just as importantly — pin that it
# stays QUIET on the healthy history it was calibrated against.
def _orders(rows, base=date(2026, 7, 27)):
    """rows = (days_ago, statusName, packageNumber, carrier) → orders export CSV (str)."""
    out = ["code;date;statusName;email;phone;billFullName;packageNumber;itemCode;itemName"]
    for i, (days_ago, status, pkg, carrier) in enumerate(rows):
        d = (base - timedelta(days=days_ago)).isoformat()
        out.append(f"{9000 + i};{d} 10:00:00;{status};k{i}@example.com;;Zákazník {i};"
                   f"{pkg};1/M;Topánky")
        if carrier:
            out.append(f"{9000 + i};{d} 10:00:00;{status};k{i}@example.com;;Zákazník {i};"
                       f"{pkg};SHIPPING1;{carrier}")
    return "\r\n".join(out) + "\r\n"


def test_source_coverage_fires_on_the_live_failure_shape():
    """Tonight's real numbers, measured read-only off the live orders export (27.7. 21:02): in the
    30-day window 4 Pošta-eligible orders carry a package number and 87 dispatched ones do not,
    and the newest order carrying one is 26 days old. Both rules trip; the run must be degraded."""
    rows = ([(26, "Vybavená", f"EF00000{i:04d}SK", "Kuriér") for i in range(4)]
            + [(d % 30, "Vybavená", "", "Kuriér") for d in range(87)]
            + [(d % 20, "Vybavuje sa", "", "Kuriér") for d in range(48)])
    c = pu.source_coverage(_orders(rows), today=date(2026, 7, 27))
    assert c["dispatched_orders"] == 91
    assert c["dispatched_without_package"] == 87
    assert c["missing_package"] == 135          # incl. the 48 not-yet-dispatched ones
    assert c["days_since_last_package"] == 26
    assert c["degraded"] is True


def test_source_coverage_quiet_on_a_healthy_window():
    """The healthy baseline the thresholds were calibrated on: a rolling 30-day window in May/June
    never dropped below 73.2 % coverage of dispatched orders and never went more than 3 days
    without a new package number. That must NOT raise an alarm — an alarm that cries on normal
    trading is one the manager learns to ignore."""
    rows = ([(d % 30, "Vybavená", f"EF10000{i:03d}SK", "Kuriér")
             for i, d in enumerate(range(115))]                     # 115 dispatched WITH a number
            + [(d % 30, "Vybavená", "", "Kuriér") for d in range(42)]  # 42 without → 73.2 %
            + [(d % 10, "Vybavuje sa", "", "Kuriér") for d in range(30)])
    c = pu.source_coverage(_orders(rows), today=date(2026, 7, 27))
    assert c["dispatched_orders"] == 157
    assert c["dispatched_without_package"] == 42
    assert c["days_since_last_package"] == 0
    assert c["degraded"] is False


def test_source_coverage_staleness_alone_degrades():
    """The sharper of the two rules, and the one that would have caught the real outage on 8.7.
    Coverage is still over the floor (6 of 11 dispatched orders carry a number), so only staleness
    can be firing: parcels kept going out for the last two days while the newest number is 10 days
    old. In the healthy two months the longest such gap was 3 days."""
    rows = ([(10, "Vybavená", f"EF20000{d:03d}SK", "Kuriér") for d in range(6)]   # 54.5 % coverage
            + [(d % 3, "Vybavená", "", "Kuriér") for d in range(5)])              # still shipping
    c = pu.source_coverage(_orders(rows), today=date(2026, 7, 27))
    assert c["dispatched_with_package"] == 6 and c["dispatched_orders"] == 11     # coverage OK…
    assert c["days_since_last_package"] == 10
    assert c["degraded"] is True                    # …and it is still degraded


def test_source_coverage_quiet_stretch_is_not_a_dead_source():
    """The false positive this rule must not have. Measured against TODAY, any drought — a shop
    holiday, a slow week — trips staleness: orders dispatched earlier stay in the 30-day window,
    nothing new ships, and a perfectly healthy source reads as dead. Here nothing has shipped for
    12 days and every parcel that DID ship carries its number, so there is no evidence of anything
    wrong and the run must stay quiet."""
    rows = [(12 + d, "Vybavená", f"EF21000{d:03d}SK", "Kuriér") for d in range(10)]
    c = pu.source_coverage(_orders(rows), today=date(2026, 7, 27))
    assert c["dispatched_without_package"] == 0
    assert c["days_since_last_package"] == 12       # stale by the naive measure…
    assert c["degraded"] is False                   # …but nothing shipped without a number


def test_source_coverage_surfaces_an_unrecognised_order_status():
    """The alarm's own blind spot. Every count hangs off one hard-coded Shoptet status string; if
    that vocabulary ever changes, `dispatched_orders` falls to 0 and this alarm — the one built
    against silent death — would itself go quietly green forever. Eligible orders of which not one
    is dispatched is the signature, and it must be visible."""
    rows = [(d, "Odoslaná kuriérom", "", "Kuriér") for d in range(8)]   # status we do not know
    c = pu.source_coverage(_orders(rows), today=date(2026, 7, 27))
    assert c["dispatched_orders"] == 0
    assert c["dispatched_status_unknown"] is True
    # …and a window we DO understand never raises it
    healthy = [(d, "Vybavená", f"EF22000{d:03d}SK", "Kuriér") for d in range(8)]
    assert pu.source_coverage(_orders(healthy),
                              today=date(2026, 7, 27))["dispatched_status_unknown"] is False


def test_source_coverage_low_coverage_alone_degrades():
    """The other direction: numbers are still arriving (gap 0), but only a third of the dispatched
    orders get one — a half-broken source, which the staleness rule alone would never see."""
    rows = ([(d, "Vybavená", f"EF30000{d:03d}SK", "Kuriér") for d in range(5)]
            + [(d % 20, "Vybavená", "", "Kuriér") for d in range(15)])
    c = pu.source_coverage(_orders(rows), today=date(2026, 7, 27))
    assert c["days_since_last_package"] == 0        # fresh numbers still coming in…
    assert c["degraded"] is True                    # …but 5/20 = 25 % coverage


def test_source_coverage_pending_orders_never_degrade():
    """An order still being picked („Vybavuje sa") has no package number yet and that is entirely
    normal — counting those into the rule would light the alarm up every single day."""
    rows = [(d % 25, "Vybavuje sa", "", "Kuriér") for d in range(40)]
    c = pu.source_coverage(_orders(rows), today=date(2026, 7, 27))
    assert c["dispatched_orders"] == 0
    assert c["missing_package"] == 40
    assert c["degraded"] is False


def test_source_coverage_ignores_a_future_dated_package_number():
    """A single mistyped/clock-skewed order date must not be able to switch the staleness rule
    off. Counted naively it would give a NEGATIVE gap — which reads as fresher than any threshold
    and would silence the alarm for as long as that row sits in the window."""
    rows = ([(-5, "Vybavená", "EF40000001SK", "Kuriér")]          # dated 5 days in the FUTURE
            + [(20, "Vybavená", "", "Kuriér") for _ in range(10)])
    c = pu.source_coverage(_orders(rows), today=date(2026, 7, 27))
    assert c["days_since_last_package"] is None       # not -5, and not treated as fresh
    assert c["degraded"] is True


def test_source_coverage_needs_enough_evidence_to_accuse_the_source():
    """~27 % of dispatched orders legitimately carry no number even in a healthy month, so on a
    near-empty window „no numbers at all" is a coincidence (odds 0.27 at one order, 0.02 at three),
    not proof of a dead source. Four dispatched orders without one stay quiet; the real failure
    runs 91 orders deep, so this floor costs nothing against it."""
    rows = [(20, "Vybavená", "", "Kuriér") for _ in range(4)]
    assert pu.source_coverage(_orders(rows), today=date(2026, 7, 27))["degraded"] is False
    # …one more order and the same evidence is worth acting on
    rows.append((20, "Vybavená", "", "Kuriér"))
    c = pu.source_coverage(_orders(rows), today=date(2026, 7, 27))
    assert c["dispatched_orders"] == 5 and c["degraded"] is True


def test_source_coverage_empty_window_never_degrades():
    """No eligible orders at all (shop closed, fresh install, export not pulled yet) is not
    evidence of a broken source — no orders can prove nothing."""
    c = pu.source_coverage(_orders([]), today=date(2026, 7, 27))
    assert c == {"dispatched_orders": 0, "dispatched_with_package": 0, "missing_package": 0,
                 "dispatched_without_package": 0, "days_since_last_package": None,
                 "dispatched_status_unknown": False, "degraded": False}


def test_source_coverage_uses_the_same_eligibility_as_the_shipment_source():
    """The alarm must count exactly the orders the automation would have chased — otherwise it
    reports a gap that was never its job. Cancelled orders, non-Pošta couriers and orders outside
    the 30-day window are excluded here exactly as they are in shipments_from_orders_csv."""
    # The three excluded rows carry a NON-EMPTY package number on purpose: with empty ones the
    # `shipments_from_orders_csv` assertion below would hold no matter what the eligibility
    # filters did (it drops numberless orders anyway) and would prove nothing. The numbers are
    # obviously fictitious — a fixture never needs a real one to be realistic (automation-health.md).
    rows = [(2, "Stornovaná", "EF50000001SK", "Kuriér"),      # cancelled → not our shipment
            (2, "Vybavená", "00000000000001", "DPD kuriér"),  # DPD → different carrier entirely
            (45, "Vybavená", "EF50000003SK", "Kuriér"),       # older than the 30-day window
            (2, "Vybavená", "", "Kuriér")]                    # the only one that counts
    csv_text = _orders(rows)
    c = pu.source_coverage(csv_text, today=date(2026, 7, 27))
    assert c["dispatched_orders"] == 1 and c["missing_package"] == 1
    # and the two functions really do agree on what is eligible: every row the alarm excluded is
    # also a row the automation never chases, even though three of them DO carry a number
    assert pu.shipments_from_orders_csv(csv_text, today=date(2026, 7, 27)) == []


def test_build_email_ignores_a_deadline_that_already_passed():
    """#283 side effect. Until the deadline was read correctly the field was ALWAYS empty, so
    every mail used the dateless wording. Now that it is populated, a parcel first seen late — or
    chased through the day 0 → +3 → +3 → +7 cadence — could be told „ak si ju nevyzdvihnete do
    <minulý týždeň>". Past the deadline the honest wording is the dateless one."""
    past = (TODAY - timedelta(days=4)).isoformat()
    future = (TODAY + timedelta(days=4)).isoformat()
    for count, dateless in ((1, "čo najskôr"), (2, "čo najskôr"), (3, "čoskoro")):
        _, body = pu.build_email(count, "Ján Vzor", "EF1SK", "Skalica 1", "", past, today=TODAY)
        assert past not in body, f"mail #{count} quoted a deadline that already passed"
        assert dateless in body, f"mail #{count} did not fall back to the dateless wording"
        # …and a deadline still ahead of us is named, as it must be
        _, ahead = pu.build_email(count, "Ján Vzor", "EF1SK", "Skalica 1", "", future, today=TODAY)
        assert future in ahead


def test_classify_normalises_a_timestamped_retained_till():
    """One live sample of the result-level shape is not enough to assume it is always a bare date;
    a timestamp would otherwise be quoted verbatim at the customer."""
    j = {"results": [{"status": "ok", "retainedTill": "2026-08-03T00:00:00", "events": [
        {"stateCode": "notified", "detailCode": "ZNP1AN", "localDate": "2026-07-16T08:10:00"}]}]}
    assert pu.classify_tracking(j, today=TODAY)["retained_till"] == "2026-08-03"


def test_classify_drops_an_unparsable_retained_till():
    """Retires the defect this test used to PIN („a shape we do not recognise is still better
    shown than silently dropped"). It is not: `retained_till` is a DATE field, and the mail
    template hard-codes the „…vyzdvihnite si ju do <hodnota>" prefix around it, so anything that
    is not a date reads as nonsense at a real customer („do do odvolania"). Worse, the expiry
    guard in build_email is the SAME parser — a value it cannot read is silently exempt from the
    „deadline already passed" check, so a garbled AND long-expired value would be presented as if
    it were still ahead. Fail-soft for a date field is to DROP it and use the dateless wording."""
    j = {"results": [{"status": "ok", "retainedTill": "do odvolania", "events": [
        {"stateCode": "notified", "detailCode": "ZNP1AN", "localDate": "2026-07-16T08:10:00"}]}]}
    assert pu.classify_tracking(j, today=TODAY)["retained_till"] == ""
