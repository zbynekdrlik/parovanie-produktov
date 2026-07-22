"""Nevyzdvihnuté zásielky — Pošta SK (#93): pure logic, no network / no SMTP.

Faithful port of the n8n workflow „Notifikácia nevyzdvihnutých zásielok - Pošta SK"
(2mhrdy0ouHe4VPeH, broken in n8n for ~a month), read node-by-node via MCP:

- source rows: orders with a non-empty packageNumber, newer than 30 days,
  deduped per order code (the app's own Shoptet orders export replaces the
  Google Sheet — it already carries packageNumber/email/phone/billFullName);
- tracking:   GET https://api.posta.sk/tracking?q=<pkg>&l=sk&p=1 per shipment;
- uncollected = LAST event stateCode=='notified' AND detailCode starts 'ZNP';
  post office + retainedTill + days-on-post extracted from the events;
- escalation: max 4 customer e-mails, cadence day 0 → +3 → +3 → +7, state
  'count|YYYY-MM-DD' per order (data/out/posta_uncollected.json);
- ALL 4 e-mails go to the CUSTOMER (verbatim n8n HTML templates below);
  the 4th also flags '⚠️ TREBA ZAVOLAŤ' for the team (in-app badge — the
  n8n Discord channel is replaced by the app tab).

Why n8n silently broke: ~1/5 of shipments have 13-14 digit NUMERIC package
numbers (a different carrier label) → the API answers per-result
status:'invalid_format' with no events; continueOnFail hid that as success.
Here those are surfaced explicitly (invalid=True) so the tab can show them.

Carrier filter (#126): the automation covers Pošta SK only — DPD (and other
non-Pošta couriers) are excluded from the shipment source entirely, not shown
as "nesledovateľné". Live export check (2026-07-22, 523 orders): the SHIPPING
pseudo-item's itemName is NEVER literally "Slovenská pošta" — Pošta SK home
delivery is labelled "Kuriér" (~98% of volume, EF...SK tracking numbers), so
the filter is a BLOCKLIST of recognised non-Pošta carrier names (DPD + common
SK couriers), not an allowlist matching "pošta" (that would have excluded
almost every real Pošta shipment). An order with no SHIPPING row at all
(older/partial export) fails OPEN (kept) — never silently drops a shipment.

The Flask app (webreview/app.py) wires this to the network, SMTP and stores.
"""
import csv
import io
from datetime import date, timedelta
from html import escape

# Non-Pošta courier names recognised in the SHIPPING pseudo-item's itemName
# (case-insensitive substring match). DPD is the one confirmed live (#126);
# the rest are common SK couriers kept out defensively per the issue's "DPD +
# other couriers" wording.
NON_POSTA_CARRIER_KEYWORDS = (
    "dpd", "gls", "packeta", "zásielkov", "zasielkov",
    "in time", "intime", "wedo", "spservis",
)

TRACKING_API = "https://api.posta.sk/tracking?q={q}&l=sk&p=1"
TRACKING_LINK = "https://www.posta.sk/sledovanie-zasielok#parcel={q}"
# Orders export has no internal admin order id (the n8n Sheet had one). The one
# GET deep-link the Shoptet admin supports is the global search — verified live
# 2026-07-22: /admin/vyhladavanie/?string=<code>&src=orders returns exactly that
# order with its detail link (the overview's filter is POST-only; ?query=/?code=
# are silently ignored).
ADMIN_ORDER_LINK = ("https://www.forestshop.sk/admin/vyhladavanie/"
                    "?string={code}&src=orders")
SOURCE_WINDOW_DAYS = 30
MAX_EMAILS = 4


def _parse_date(s) -> date | None:
    """Lenient 'YYYY-MM-DD...' prefix → date; None (never raises) on junk."""
    try:
        return date.fromisoformat(str(s or "").strip()[:10])
    except ValueError:
        return None


def _order_carriers(rows: list[dict]) -> dict[str, str]:
    """order code -> SHIPPING pseudo-item name (itemCode starting 'SHIPPING'),
    scanning ALL rows of the export — the SHIPPING line need not be the first
    row per order. An order with no such row is simply absent (caller treats
    that as 'unknown carrier' -> fail-open, kept)."""
    out: dict[str, str] = {}
    for r in rows:
        code = (r.get("code") or "").strip()
        item_code = (r.get("itemCode") or "").strip()
        if code and item_code.upper().startswith("SHIPPING"):
            out[code] = (r.get("itemName") or "").strip()
    return out


def _is_non_posta_carrier(name: str) -> bool:
    """True when the SHIPPING itemName names a recognised non-Pošta courier
    (see NON_POSTA_CARRIER_KEYWORDS). Empty/unknown name -> False (fail-open)."""
    n = (name or "").lower()
    return any(k in n for k in NON_POSTA_CARRIER_KEYWORDS)


def shipments_from_orders_csv(orders_csv, today: date | None = None) -> list[dict]:
    """Shoptet orders.csv (cp1250 bytes or str) → one shipment per ORDER.

    Port of the n8n 'Filter' + 'Remove Duplicates' nodes: packageNumber
    non-empty AND order date within the last 30 days, first row per order code
    wins (the export repeats order fields on every item line). One deliberate
    deviation from n8n: a cancelled order (Stornovaná) is skipped — nagging a
    customer who cancelled would be wrong (documented on #93). Second
    deviation (#126): orders shipped via a non-Pošta courier (DPD etc., see
    _is_non_posta_carrier) are excluded entirely — this automation is Pošta
    SK only."""
    text = (orders_csv.decode("cp1250", errors="replace")
            if isinstance(orders_csv, bytes) else orders_csv)
    today = today or date.today()
    cutoff = today - timedelta(days=SOURCE_WINDOW_DAYS)
    rows = list(csv.DictReader(io.StringIO(text), delimiter=";"))
    carriers = _order_carriers(rows)
    out, seen = [], set()
    for r in rows:
        code = (r.get("code") or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        pkg = (r.get("packageNumber") or "").strip()
        if not pkg:
            continue
        if (r.get("statusName") or "").strip().lower() == "stornovaná":
            continue
        if _is_non_posta_carrier(carriers.get(code, "")):
            continue
        od = _parse_date(r.get("date"))
        if od is None or od < cutoff:
            continue
        out.append({
            "code": code,
            "date": od.isoformat(),
            "packageNumber": pkg,
            "email": (r.get("email") or "").strip(),
            "phone": (r.get("phone") or "").strip(),
            "billFullName": (r.get("billFullName") or "").strip(),
        })
    return out


def classify_tracking(api_json, today: date | None = None) -> dict:
    """Pošta SK tracking response → classification (port of the n8n Code node's
    detection half). status: 'ok' | 'invalid_format' | 'no_results' |
    'no_events' | whatever the API said per-result."""
    today = today or date.today()
    out = {"status": "no_results", "uncollected": False, "office_name": "",
           "office_addr": "", "retained_till": "", "notified_since": "",
           "days_at_post": 1}
    results = (api_json or {}).get("results") or []
    if not results:
        return out
    p = results[0] or {}
    out["status"] = p.get("status") or "no_results"
    if out["status"] != "ok":
        return out                      # invalid_format lands here (no events)
    events = p.get("events") or []
    if not events:
        out["status"] = "no_events"
        return out
    last = events[-1]
    state = (last.get("stateCode") or "").lower()
    detail = (last.get("detailCode") or "").upper()
    if state == "notified" and detail.startswith("ZNP"):
        out["uncollected"] = True
        po = last.get("postOffice") or {}
        out["office_name"] = po.get("name") or ""
        if po.get("street"):
            out["office_addr"] = (f"{po['street']}, {po.get('postcode', '')} "
                                  f"{po.get('city', '')}").strip()
        ld = (last.get("localDate") or "")[:10]
        out["notified_since"] = ld
        nd = _parse_date(ld)
        if nd is not None:              # unparsable date keeps the n8n default of 1
            out["days_at_post"] = max(1, (today - nd).days)
        for e in events:                # n8n: first event carrying retainedTill
            if e.get("retainedTill"):
                out["retained_till"] = str(e["retainedTill"])
                break
    return out


def parse_notified(value) -> tuple[int, date | None]:
    """Escalation state 'count|YYYY-MM-DD' → (count, last_sent). Legacy n8n
    value (bare date) counts as one notification; junk counts as none."""
    v = str(value or "").strip()
    if not v:
        return 0, None
    if "|" in v:
        c, _, d = v.partition("|")
        try:
            count = int(c)
        except ValueError:
            count = 0
        return count, _parse_date(d)
    d = _parse_date(v)
    return (1, d) if d is not None else (0, None)


def should_send(count: int, last_sent: date | None, today: date | None = None) -> bool:
    """n8n cadence: 4 mails max, day 0 → +3 → +3 → +7."""
    today = today or date.today()
    if count >= MAX_EMAILS:
        return False
    if count == 0:
        return True
    if last_sent is None:
        return False
    needed = 3 if count < 3 else 7
    return (today - last_sent).days >= needed


def build_email(count: int, name: str, track_num: str, office_name: str,
                office_addr: str, retained_till: str) -> tuple[str, str]:
    """(subject, html_body) for customer e-mail #count — the verbatim n8n
    templates. Free-text fields (customer name, office) are HTML-escaped here
    (the one hardening added over n8n)."""
    link = TRACKING_LINK.format(q=track_num)
    name_h = escape(name)
    num_h = f"<strong>{escape(track_num)}</strong>"
    office_h = f"<strong>{escape(office_name)}</strong>" + (
        f" ({escape(office_addr)})" if office_addr else "")
    till_h = f"<strong>{escape(retained_till)}</strong>"
    if count == 1:
        subject = f"Vaša zásielka čaká na vyzdvihnutie | {track_num}"
        intro = (f"vaša zásielka č. {num_h} je uložená na pošte {office_h} "
                 "a čaká na vyzdvihnutie.")
        urgency = (f"Prosím vyzdvihnite si ju do {till_h}, aby nebola vrátená späť odosielateľovi."
                   if retained_till else
                   "Prosím vyzdvihnite si ju čo najskôr, aby nebola vrátená späť odosielateľovi.")
    elif count == 2:
        subject = f"Pripomienka: zásielka stále čaká | {track_num}"
        intro = (f"opätovne vás upozorňujeme, že vaša zásielka č. {num_h} je stále "
                 f"uložená na pošte {office_h} a zatiaľ nebola vyzdvihnutá.")
        urgency = (f"Termín na vyzdvihnutie sa blíži: {till_h}. Po tomto dátume bude zásielka vrátená."
                   if retained_till else
                   "Prosím vyzdvihnite si ju čo najskôr. Zásielka bude čoskoro vrátená odosielateľovi.")
    elif count == 3:
        subject = f"Posledné upozornenie: zásielka bude vrátená | {track_num}"
        intro = (f"toto je naše posledné upozornenie — vaša zásielka č. {num_h} "
                 f"je stále na pošte {office_h}.")
        urgency = (f"Ak si ju nevyzdvihnete do {till_h}, bude vrátená späť odosielateľovi."
                   if retained_till else
                   "Ak si ju čoskoro nevyzdvihnete, bude vrátená späť odosielateľovi.")
    else:
        subject = f"Posledná výzva: zásielka bude vrátená | {track_num}"
        intro = (f"napriek opakovaným upozorneniam vaša zásielka č. {num_h} "
                 f"je stále nevyzdvihnutá na pošte {office_h}.")
        urgency = ("Zásielka bude v najbližších dňoch vrátená späť. Ak ju stále chcete, "
                   'prosím kontaktujte nás čo najskôr na '
                   '<a href="mailto:eshop@forestshop.sk">eshop@forestshop.sk</a> '
                   "alebo telefonicky.")
    body = (
        '<!DOCTYPE html>\n<html>\n'
        '  <body style="font-family: Arial, sans-serif; font-size: 16px; color: #333;">\n'
        f'    <p>Dobrý deň, <strong>{name_h}</strong>,</p>\n\n'
        f'    <p>{intro}</p>\n\n'
        f'    <p>{urgency}</p>\n\n'
        f'    <p>👉 Stav zásielky môžete sledovať tu: <a href="{link}">{link}</a></p>\n\n'
        '    <p>\n'
        '      Ak máte akékoľvek otázky, pokojne nás kontaktujte na\n'
        '      <a href="mailto:eshop@forestshop.sk">eshop@forestshop.sk</a>.\n'
        '    </p>\n\n'
        '    <p style="margin-top: 30px;">\n'
        '      S pozdravom,<br>\n'
        '      <strong>Tím Forestshop.sk</strong><br>\n'
        '      <a href="https://www.forestshop.sk" target="_blank">www.forestshop.sk</a>\n'
        '    </p>\n'
        '  </body>\n</html>'
    )
    return subject, body


def evaluate_shipment(shipment: dict, tracking_json, state_value,
                      today: date | None = None) -> dict:
    """One shipment + its tracking response + its escalation state → the full
    verdict: display row for the tab, whether to e-mail now (with the prepared
    subject/body), and the new escalation state value."""
    today = today or date.today()
    cls = classify_tracking(tracking_json, today)
    count, last_sent = parse_notified(state_value)
    send = cls["uncollected"] and should_send(count, last_sent, today)
    new_count = count + 1 if send else count
    r = {
        "orderCode": shipment["code"],
        "packageNumber": shipment["packageNumber"],
        "email": shipment.get("email", ""),
        "phone": shipment.get("phone", ""),
        "name": shipment.get("billFullName", ""),
        "status": cls["status"],
        "uncollected": cls["uncollected"],
        "invalid": cls["status"] == "invalid_format",
        "office_name": cls["office_name"],
        "office_addr": cls["office_addr"],
        "retained_till": cls["retained_till"],
        "notified_since": cls["notified_since"],
        "days_at_post": cls["days_at_post"],
        "send": send,
        "count": new_count,
        "last_sent": today.isoformat() if send else
                     (last_sent.isoformat() if last_sent else ""),
        "new_state_value": (f"{new_count}|{today.isoformat()}" if send
                            else str(state_value or "")),
        "call_needed": cls["uncollected"] and new_count >= MAX_EMAILS,
        "tracking_link": TRACKING_LINK.format(q=shipment["packageNumber"]),
        "admin_link": ADMIN_ORDER_LINK.format(code=shipment["code"]),
        "email_subject": "",
        "email_body": "",
    }
    if send:
        r["email_subject"], r["email_body"] = build_email(
            new_count, r["name"], r["packageNumber"],
            cls["office_name"], cls["office_addr"], cls["retained_till"])
    return r
