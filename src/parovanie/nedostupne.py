"""Nedostupné tovary (#100) — pure logic (no network / no SMTP / no file I/O).

The manager flags a product „nedostupné u dodávateľa" on the „Na objednanie" tab (the per-line
``unavailable_items.json`` store, #84). This tab COLLECTS every such flagged product in one place,
joins it to the currently-open orders (customers who ordered it) and lets the manager send — behind
an explicit preview + „Odoslať" (never auto-fire) — one of two customer e-mails:

  1. „nedostupné"  — the ordered product is currently unavailable,
  2. „alternatíva" — the same, plus the alternative products Shoptet has assigned to it
     (``relatedProduct*`` in the catalog export).

This module holds the NETWORK-FREE core so it is unit-testable with a fixture orders CSV, an
in-memory store and a mocked SMTP send (no real e-mail): the unavailable-code extraction, the
open-order → customer join, the send-plan dedup, and the two customer e-mail templates (verbatim
in the same HTML style as ``orders_reminder.build_reminder_email``, free text HTML-escaped).

The Flask app (webreview/app.py) wires this to the cached orders export, the catalog
(name + relatedProduct + URL resolution), SMTP and the data/out/nedostupne.json state store.
"""
from __future__ import annotations

import csv
import io
from html import escape

ORDER_STATUS = "Vybavuje sa"          # only currently-processing orders have customers to notify

# The two e-mail types. Kept as constants so the store dedup key (``<orderCode>|<type>``) and the
# API ``type`` argument are the SAME string everywhere.
TYPE_UNAVAILABLE = "nedostupne"
TYPE_ALTERNATIVE = "alternativa"
EMAIL_TYPES = (TYPE_UNAVAILABLE, TYPE_ALTERNATIVE)

UNAVAILABLE_SUBJECT = "Informácia o dostupnosti vašej objednávky — Forestshop.sk"
ALTERNATIVE_SUBJECT = "Alternatívy k vášmu tovaru — Forestshop.sk"

MAX_ALTERNATIVES = 8                  # boss: relatedProduct1..8 (the assigned alternatives)


def unavailable_item_codes(unavail_store) -> set:
    """Distinct itemCodes flagged unavailable, from the per-line to-order store keyed
    ``'<orderCode>|<itemCode>'`` (#84). A falsy value = not flagged. Forestshop item codes never
    contain '|' (they look like ``40237/3XL``), so split on the FIRST '|' isolates the itemCode."""
    out = set()
    for k, v in (unavail_store or {}).items():
        if not v:
            continue
        parts = str(k).split("|", 1)
        if len(parts) == 2 and parts[1].strip():
            out.add(parts[1].strip())
    return out


def affected_orders(orders_csv, item_codes, order_status: str = ORDER_STATUS) -> dict:
    """itemCode -> [ {orderCode, date, email, billFullName, qty, size, itemName, key} ] for every
    OPEN order line whose itemCode is in ``item_codes``.

    Matched by EXACT variant itemCode (a customer who ordered size L is not notified when only size
    M was flagged unavailable — the flag is per variant). One entry per order LINE; the send-plan
    dedups per order. Pure (cp1250 bytes or str) → unit-testable."""
    text = (orders_csv.decode("cp1250", errors="replace")
            if isinstance(orders_csv, bytes) else orders_csv)
    want = {str(c).strip() for c in item_codes if str(c).strip()}
    out: dict = {}
    if not want:
        return out
    for r in csv.DictReader(io.StringIO(text), delimiter=";"):
        if (r.get("statusName") or "").strip() != order_status:
            continue
        code = (r.get("itemCode") or "").strip()
        if not code or code not in want:
            continue
        order = (r.get("code") or "").strip()
        out.setdefault(code, []).append({
            "orderCode": order,
            "date": (r.get("date") or "").strip()[:10],
            "email": (r.get("email") or "").strip(),
            "billFullName": (r.get("billFullName") or "").strip(),
            "qty": (r.get("itemAmount") or "").strip(),
            "size": (r.get("itemVariantName") or "").strip(),
            "itemName": (r.get("itemName") or "").strip(),
            "key": f"{order}|{code}",
        })
    return out


def build_view(orders_csv, unavail_store, state_store, resolve,
               order_status: str = ORDER_STATUS) -> list:
    """The „Nedostupné tovary" tab data: one entry per flagged (unavailable) product.

    ``resolve(code)`` -> (product_name, alternatives) where alternatives is a list of
    ``{"code","name","url"}`` (the Shoptet relatedProduct* alternatives resolved to name + link);
    it lets the app inject catalog access without this module reading any file. Each entry carries
    the two persisted checkbox states, the affected orders (with a per-order sent-flag for each
    e-mail type) and the alternatives. Products are returned name-sorted for a stable UI."""
    codes = unavailable_item_codes(unavail_store)
    by_code = affected_orders(orders_csv, codes, order_status)
    state_store = state_store or {}
    out = []
    for code in codes:
        orders = by_code.get(code, [])
        st = state_store.get(code) or {}
        sent = st.get("sent") or {}
        cat_name, alternatives = resolve(code)
        name = (orders[0]["itemName"] if orders else "") or (cat_name or "")
        order_rows = []
        for o in orders:
            oc = o["orderCode"]
            order_rows.append({
                **o,
                "unavailable_sent": bool(sent.get(f"{oc}|{TYPE_UNAVAILABLE}")),
                "alternative_sent": bool(sent.get(f"{oc}|{TYPE_ALTERNATIVE}")),
            })
        out.append({
            "code": code,
            "itemName": name,
            "nedostupne": bool(st.get(TYPE_UNAVAILABLE)),
            "alternativa": bool(st.get(TYPE_ALTERNATIVE)),
            "orders": order_rows,
            "alternatives": alternatives or [],
            "order_count": len(order_rows),
            "unavailable_sent_count": sum(1 for o in order_rows if o["unavailable_sent"]),
            "alternative_sent_count": sum(1 for o in order_rows if o["alternative_sent"]),
        })
    out.sort(key=lambda p: (p["itemName"].lower(), p["code"]))
    return out


def plan_sends(order_rows, sent_map, type_key: str) -> list:
    """Which order lines still need this e-mail type: one recipient per order, skipping any already
    sent (persistent dedup via ``sent_map['<orderCode>|<type>']``), any line without an e-mail, and
    a repeat of an e-mail address ALREADY queued in this batch (a customer with the same product in
    two open orders is e-mailed once). Returns [ {orderCode, email, billFullName, key} ]."""
    sent_map = sent_map or {}
    out, seen_emails = [], set()
    for o in order_rows:
        oc = (o.get("orderCode") or "").strip()
        email = (o.get("email") or "").strip()
        if not email:
            continue
        if sent_map.get(f"{oc}|{type_key}"):
            continue
        low = email.lower()
        if low in seen_emails:
            continue
        seen_emails.add(low)
        out.append({"orderCode": oc, "email": email,
                    "billFullName": (o.get("billFullName") or "").strip(),
                    "key": o.get("key") or f"{oc}|"})
    return out


# --------------------------------------------------------------------------- #
# Customer e-mail templates — the same HTML style as orders_reminder.build_reminder_email
# (boss: „pekný, v ROVNAKOM formáte ako ostatné automatizácie"). Free text is HTML-escaped.
# --------------------------------------------------------------------------- #
def _shell(name_h: str, inner: str) -> str:
    return (
        '<!DOCTYPE html>\n<html>\n'
        '  <body style="font-family: Arial, sans-serif; font-size: 16px; color: #333;">\n'
        f'    <p>Dobrý deň, <strong>{name_h}</strong>,</p>\n\n'
        f'{inner}'
        '    <p style="margin-top: 30px;">S pozdravom,<br>\n'
        '      <strong>Tím Forestshop.sk</strong><br>\n'
        '      <a href="https://www.forestshop.sk" target="_blank">www.forestshop.sk</a>\n'
        '    </p>\n'
        '  </body>\n</html>'
    )


def _contact_line() -> str:
    return (
        '    <p>Ak máte akékoľvek otázky, pokojne nás kontaktujte na '
        '<a href="mailto:eshop@forestshop.sk">eshop@forestshop.sk</a> alebo na telefónnom čísle '
        '<a href="tel:+421903670766">+421 903 670 766</a>.</p>\n\n'
    )


def build_unavailable_email(bill_full_name: str, item_name: str) -> tuple:
    """(subject, html_body) — the ordered product is currently unavailable. Free text escaped."""
    name_h = escape((bill_full_name or "").strip() or "zákazník")
    prod_h = escape((item_name or "").strip() or "objednaný tovar")
    inner = (
        f'    <p>Radi by sme vás informovali, že produkt <strong>{prod_h}</strong> z vašej '
        'objednávky je momentálne <strong>nedostupný</strong>. Mrzí nás to a rozumieme, '
        'že ste sa na svoju výbavu tešili.</p>\n\n'
        '    <p>Situáciu aktívne riešime a v prípade potreby vás budeme osobne kontaktovať '
        'ohľadom ďalšieho postupu (náhradný termín dodania alebo iné riešenie).</p>\n\n'
        + _contact_line()
        + '    <p>Ďakujeme vám za trpezlivosť a dôveru.</p>\n\n'
    )
    return UNAVAILABLE_SUBJECT, _shell(name_h, inner)


def build_alternative_email(bill_full_name: str, item_name: str, alternatives) -> tuple:
    """(subject, html_body) — the product is unavailable + the assigned alternative products
    (relatedProduct*) as name + link. Alternative names/URLs are HTML-escaped."""
    name_h = escape((bill_full_name or "").strip() or "zákazník")
    prod_h = escape((item_name or "").strip() or "objednaný tovar")
    items = ""
    for a in (alternatives or []):
        alt_name = escape((a.get("name") or a.get("code") or "").strip() or "alternatíva")
        url = (a.get("url") or "").strip()
        if url.startswith("http"):
            href = escape(url, quote=True)
            items += (f'      <li><a href="{href}" target="_blank">{alt_name}</a></li>\n')
        else:
            items += (f'      <li>{alt_name}</li>\n')
    alt_block = (
        '    <p>Radi by sme vám preto ponúkli tieto <strong>alternatívne produkty</strong>:</p>\n'
        f'    <ul>\n{items}    </ul>\n\n'
    ) if items else (
        '    <p>Radi vám pomôžeme nájsť vhodnú alternatívu — stačí nás kontaktovať.</p>\n\n'
    )
    inner = (
        f'    <p>Radi by sme vás informovali, že produkt <strong>{prod_h}</strong> z vašej '
        'objednávky je momentálne <strong>nedostupný</strong>.</p>\n\n'
        + alt_block
        + _contact_line()
        + '    <p>Ďakujeme vám za dôveru.</p>\n\n'
    )
    return ALTERNATIVE_SUBJECT, _shell(name_h, inner)
