"""Pripomienky objednávok — Forestshop orders (#105): pure logic, no network / no SMTP / no OpenAI.

In-app migration of the n8n workflow „Forestshop orders" (MnskuiOdu3i5GKlF, LIVE, daily 08:00),
read node-by-node via MCP. This module holds the NETWORK-FREE core so it is unit-testable with a
fixture CSV, a mocked OpenAI reply and a mocked SMTP send (no real network / no real e-mail):

- ``select_orders`` — the source rows: „Vybavuje sa" orders, deduped per order ``code`` (the
  export repeats order fields on every item line — first row per code wins, like the n8n
  „Remove Duplicates"), older than ``min_days`` days (n8n: date before now-4d). Each carries a
  ``has_note`` flag (shopRemark non-empty) and the display fields the tab needs.
- ``build_reminder_email`` — the verbatim n8n „Edit Fields2" HTML customer e-mail (a SINGLE
  reminder, not the posta 4-step escalation), free-text fields HTML-escaped (the hardening over n8n).
- ``build_classifier_messages`` / ``parse_classification`` — the OpenAI prompt + tolerant parse
  mirroring the n8n „Kontaktovany?" Text Classifier (gpt classifier of the internal shop note:
  past-tense „volané" = contacted vs future „volať/budeme volať" = not yet).

The CLEAN state machine (the issue's optimisation #3, deliberately diverging from n8n's two
parallel branches): a >4d order with an EMPTY note surfaces ONLY as the RED „nikto sa jej
nedotkol" alert (no e-mail); a >4d order WITH a note is AI-classified — contacted → skipped,
not-contacted → one reminder e-mail (max once per order via the data/out state store). n8n
additionally e-mailed empty-note orders via „BEZ POZNAMKY" → nekontaktovany; that double-path is
dropped here so an empty note never both alerts AND e-mails.

The Flask app (webreview/app.py) wires this to the cached orders export, OpenAI, SMTP and the
data/out/orders_reminder.json store.
"""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timedelta
from html import escape

# The orders export has no internal admin order id (n8n used /objednavky-detail/?id=<id>, which
# came from a Google Sheet). The one GET deep-link the Shoptet admin supports is the global search
# — verified live 2026-07-22 (see posta_uncollected.ADMIN_ORDER_LINK). Reused verbatim so both
# order automations link to the admin the same, verified way.
from parovanie.posta_uncollected import ADMIN_ORDER_LINK

ORDER_STATUS = "Vybavuje sa"          # n8n „Filter": statusName == 'Vybavuje sa'
MIN_DAYS = 4                          # n8n: date before $now.minus(4,'days')
EMAIL_SUBJECT = "📦 Stav vašej objednávky z Forestshop.sk"   # n8n typo „Foresthop" fixed

# The classifier's two categories (verbatim from the n8n „Kontaktovany?" node), used both in the
# system prompt and — crucially — as the ALLOWED output labels parse_classification accepts.
CATEGORY_CONTACTED = "kontaktovany"
CATEGORY_NOT_CONTACTED = "nekontaktovany"


def _parse_dt(s) -> datetime | None:
    """Lenient Shoptet order date → naive datetime. Accepts 'YYYY-MM-DD HH:MM:SS' and a bare
    'YYYY-MM-DD'. Returns None (never raises) on junk — the caller then skips the order rather
    than risk e-mailing on an unknown age."""
    v = str(s or "").strip()
    if not v:
        return None
    for width, fmt in ((19, "%Y-%m-%d %H:%M:%S"), (16, "%Y-%m-%d %H:%M"), (10, "%Y-%m-%d")):
        try:
            return datetime.strptime(v[:width], fmt)
        except ValueError:
            continue
    return None


def select_orders(orders_csv, now: datetime | None = None,
                  min_days: int = MIN_DAYS) -> list[dict]:
    """Shoptet orders.csv (cp1250 bytes or str) → one entry per ORDER worth acting on.

    Port of the n8n „Filter" (statusName == 'Vybavuje sa') + „Remove Duplicates" (by code, first
    row per order wins — the export repeats the order fields on every item line) + the >4d age
    gate. Each returned dict carries ``has_note`` (shopRemark non-empty → drives red-vs-AI) and
    the display fields. Orders whose date can't be parsed are skipped (never acted on blind)."""
    text = (orders_csv.decode("cp1250", errors="replace")
            if isinstance(orders_csv, bytes) else orders_csv)
    now = now or datetime.now()
    cutoff = timedelta(days=min_days)
    out, seen = [], set()
    for r in csv.DictReader(io.StringIO(text), delimiter=";"):
        code = (r.get("code") or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        if (r.get("statusName") or "").strip() != ORDER_STATUS:
            continue
        od = _parse_dt(r.get("date"))
        if od is None or (now - od) <= cutoff:
            continue
        note = (r.get("shopRemark") or "").strip()
        out.append({
            "code": code,
            "date": (r.get("date") or "").strip(),
            "days": max(0, (now - od).days),
            "shopRemark": note,
            "has_note": bool(note),
            "email": (r.get("email") or "").strip(),
            "phone": (r.get("phone") or "").strip(),
            "billFullName": (r.get("billFullName") or "").strip(),
            "itemName": (r.get("itemName") or "").strip(),
            "itemAmount": (r.get("itemAmount") or "").strip(),
            "totalPriceWithVat": (r.get("totalPriceWithVat") or "").strip(),
            "admin_link": ADMIN_ORDER_LINK.format(code=code),
        })
    return out


def build_reminder_email(bill_full_name: str, code: str) -> tuple[str, str]:
    """(subject, html_body) for the single customer reminder — the verbatim n8n „Edit Fields2"
    template. billFullName + code are HTML-escaped (the one hardening added over n8n)."""
    name_h = escape(bill_full_name)
    code_h = escape(code)
    body = (
        '<!DOCTYPE html>\n<html>\n'
        '  <body style="font-family: Arial, sans-serif; font-size: 16px; color: #333;">\n'
        f'    <p>Dobrý deň, <strong>{name_h}</strong>,</p>\n\n'
        f'    <p>Všimli sme si, že spracovanie vašej objednávky č. <strong>{code_h}</strong> '
        'trvá o niečo dlhšie než obvykle. Chceme vás ubezpečiť, že situáciu aktívne sledujeme '
        'a riešime.</p>\n\n'
        '    <p>Mierne zdržanie môže byť spôsobené dostupnosťou tovaru, dopĺňaním zásob alebo '
        'prepravnými okolnosťami. Rozumieme, že sa na svoju výbavu tešíte, a robíme všetko pre to, '
        'aby ste ju mali čo najskôr pri sebe.</p>\n\n'
        '    <p>👉 V prípade potreby zmeny, alternatívy alebo potvrdenia z vašej strany vás '
        'budeme osobne kontaktovať v najbližšom čase.</p>\n\n'
        '    <p>Ďakujeme vám za trpezlivosť a dôveru. Ak máte akékoľvek otázky, pokojne nás '
        'kontaktujte na <a href="mailto:eshop@forestshop.sk">eshop@forestshop.sk</a> alebo na '
        'telefónnom čísle <a href="tel:+421903670766">+421 903 670 766</a>.</p>\n\n'
        '    <p style="margin-top: 30px;">S pozdravom,<br>\n'
        '      <strong>Tím Forestshop.sk</strong><br>\n'
        '      <a href="https://www.forestshop.sk" target="_blank">www.forestshop.sk</a>\n'
        '    </p>\n'
        '  </body>\n</html>'
    )
    return EMAIL_SUBJECT, body


def build_classifier_messages(shop_remark: str) -> list[dict]:
    """OpenAI chat messages for classifying the internal shop note — a faithful port of the n8n
    „Kontaktovany?" Text Classifier system prompt + its two category descriptions, adapted to a
    single JSON-object chat completion (the app calls OpenAI directly, not via a LangChain node).
    Empty note → 'BEZ POZNAMKY' (matching the n8n inputText fallback; the clean state machine never
    routes an empty note here, but this stays defensive)."""
    note = (shop_remark or "").strip() or "BEZ POZNAMKY"
    system = f"""\
Si klasifikátor interných poznámok predajne k objednávke. Rozhoduješ, či zákazník UŽ BOL \
kontaktovaný ohľadom svojej objednávky.

Kategória "{CATEGORY_CONTACTED}": zákazník UŽ BOL kontaktovaný/informovaný. Dokazuje to \
DOKONČENÝ telefonát v minulom čase (napr. „volané so zákazníkom/zákazníčkou", „dovolal som sa", \
„hovoril som so zákazníkom"), najmä ak nasleduje dohoda („počká", „bude sa čakať", „po dohode \
so zákazníkom"), ALEBO už odoslaná SMS („poslaná sms", „sms poslaná"). Sem patrí aj „volané ... \
počká".

Kategória "{CATEGORY_NOT_CONTACTED}": zákazník ešte NEBOL úspešne kontaktovaný. Patrí sem: \
prázdna poznámka / „BEZ POZNAMKY"; len interné poznámky o tovare („nemáme", „objednané", \
„nedostupné", „chýba kus"); BUDÚCI zámer zavolať ešte nesplnený („volať zákazníka", „budeme \
volať", „treba volať", „ja vybavím"); a IBA NEÚSPEŠNÝ pokus o telefonát bez SMS („nedovolal \
som sa", „nezdvíha"). Tu sa posiela pripomienkový e-mail.

KĽÚČOVÝ ROZDIEL: „volané" = telefonát sa UŽ uskutočnil (kontaktovaný), ale „volať" / „budeme \
volať" / „treba volať" = ešte sa NEvolalo (nekontaktovaný). Neodôvodňuj, vráť IBA JSON objekt \
v tvare {{"kategoria": "{CATEGORY_CONTACTED}"}} alebo {{"kategoria": "{CATEGORY_NOT_CONTACTED}"}}."""
    user = f"Interná poznámka:\n{note}"
    return [{"role": "system", "content": system},
            {"role": "user", "content": user}]


def parse_classification(content: str) -> bool:
    """Parse the model's JSON reply → True when the customer was already CONTACTED (skip the
    e-mail), False when NOT contacted (send the reminder). Tolerant of a ```json fence and of the
    bare category string. Raises ValueError on an unrecognised reply — the caller then records that
    order as an error and does NOT e-mail (never guesses)."""
    s = (content or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s).strip()
    kat = ""
    try:
        d = json.loads(s)
        if isinstance(d, dict):
            kat = str(d.get("kategoria") or d.get("category") or "").strip().lower()
        elif isinstance(d, str):
            kat = d.strip().lower()
    except (ValueError, TypeError):
        kat = s.strip().strip('"').lower()
    if kat == CATEGORY_CONTACTED:
        return True
    if kat == CATEGORY_NOT_CONTACTED:
        return False
    raise ValueError(f"klasifikátor nevrátil platnú kategóriu: {content!r}")
