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


def all_order_codes(orders_csv) -> set[str]:
    """EVERY order code in the export — the „source window" the dedup store is pruned against
    (#220). Deliberately NOT ``select_orders``' filtered output: a „Vybavená" order can be
    reopened to „Vybavuje sa" tomorrow and a fresh order crosses the 4-day line in a few days,
    so both are still live as far as the dedup store is concerned. An empty/unreadable export
    yields an empty set, which the caller treats as „window unknown → prune nothing"."""
    text = (orders_csv.decode("cp1250", errors="replace")
            if isinstance(orders_csv, bytes) else (orders_csv or ""))
    return {c for r in csv.DictReader(io.StringIO(text), delimiter=";")
            if (c := (r.get("code") or "").strip())}


# How long a dedup record survives after its order left the export, and the hard cap on how
# many such records are kept at all (#220). Generous on purpose: the ONLY cost of keeping a
# record is a few hundred bytes, while dropping one too early costs a duplicate customer mail.
DEDUP_RETENTION_DAYS = 180
DEDUP_MAX_RECORDS = 500


def _record_date(entry) -> datetime | None:
    """When a dedup record was resolved (or claimed) — lenient, never raises. Handles the
    ISO 'YYYY-MM-DDTHH:MM:SS+02:00' the run writes as well as a bare date."""
    if not isinstance(entry, dict):
        return None
    for key in ("date", "claimed_at"):
        d = _parse_dt(str(entry.get(key) or "").replace("T", " "))
        if d is not None:
            return d
    return None


def prune_done(done: dict, window_codes, now: datetime | None = None,
               retention_days: int = DEDUP_RETENTION_DAYS,
               max_records: int = DEDUP_MAX_RECORDS) -> dict:
    """Bound the per-order dedup map (#220) without ever losing a record that still matters.

    A record is the ONLY thing that stops a customer being reminded a second time, so the rules
    are asymmetric — cheap to keep, expensive to drop:

    1. A code still present in the export (``window_codes``) is kept, whatever its age. Its
       order can return to „Vybavuje sa" at any moment, and then a missing record = a duplicate
       mail. This is the invariant; nothing overrides it.
    2. ``window_codes`` EMPTY means the export was empty or unreadable — the window is unknown,
       so nothing is pruned at all (fail closed, the same reflex as the supplier upload: no
       source of truth, no destructive action).
    3. Everything else is kept while it is younger than ``retention_days``, newest first, up to
       ``max_records``. A record with no usable timestamp sorts oldest (dropped first) but is
       never dropped by age alone.

    REOPEN — the deliberate decision this bounding does NOT change: an order that becomes
    „Vybavuje sa" again keeps its terminal record and therefore never receives a second
    reminder while it is still in the export. One reminder per order code is the safer default
    (a duplicate mail is visible to the customer; a missing one is not), and the manager can
    always send one by hand from the tab („▶ Poslať pripomienku" answers 409 only for an
    already-emailed order — the record is the audit trail of that). Only once the order has
    left the export AND the retention window does the record go, so a genuinely new order cycle
    months later starts clean."""
    if not isinstance(done, dict):
        return {}
    window = set(window_codes or ())
    if not window:
        return dict(done)                       # window unknown → prune nothing (rule 2)
    now = now or datetime.now()
    cutoff = now - timedelta(days=retention_days)
    kept = {c: v for c, v in done.items() if c in window}
    rest = []
    for c, v in done.items():
        if c in window:
            continue
        ts = _record_date(v)
        if ts is None or ts >= cutoff:
            rest.append((ts or datetime.min, c, v))
    rest.sort(key=lambda t: t[0], reverse=True)
    for _, c, v in rest[:max_records]:
        kept[c] = v
    return kept


def order_fingerprint(o: dict) -> str:
    """Stable per-code fingerprint of the fields that decide the AI/e-mail outcome (#153 —
    incremental processing) — the order date and the internal note. ``days`` is deliberately
    excluded: it advances every day even for an order nobody touched, so including it would
    defeat incrementality (every order would look 'changed' on every single run)."""
    return f"{o.get('date', '')}|{o.get('shopRemark', '')}"


def partition_incremental(orders: list[dict], prev_fingerprints: dict,
                          done_codes) -> tuple[list[dict], list[dict], dict]:
    """Split select_orders()'s output into (to_process, unchanged, fingerprints) for the
    incremental run (#153): today every run re-processes every currently-eligible order even when
    nothing about it changed since yesterday. An order is ``unchanged`` ONLY when BOTH its
    fingerprint matches the prior run's AND its code is already terminally resolved
    (``done_codes`` — emailed or skipped_contacted, from the dedup store). Everything else — a
    brand-new eligible order (just crossed the 4-day threshold today), a note that changed since
    last time, or an order not yet terminal (e.g. retried because OPENAI_API_KEY was missing) —
    goes to ``to_process``, so a newly-eligible order is NEVER skipped and a not-yet-resolved
    retry is NEVER silently dropped. ``fingerprints`` is the full map to persist for the next run
    (every currently-eligible code, so a later change is detected against ITS last fingerprint)."""
    to_process, unchanged, fingerprints = [], [], {}
    for o in orders:
        code = o["code"]
        fp = order_fingerprint(o)
        fingerprints[code] = fp
        if code in done_codes and prev_fingerprints.get(code) == fp:
            unchanged.append(o)
        else:
            to_process.append(o)
    return to_process, unchanged, fingerprints
