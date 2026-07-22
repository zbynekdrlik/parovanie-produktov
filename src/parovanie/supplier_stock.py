"""Dodávateľský sklad — supplier availability/price scraper (#106): pure logic.

In-app migration of the n8n workflow „Forestshop — Dodávateľský scraper"
(6kn7jzBXTjbmbiVa). This module holds the NETWORK-FREE, OpenAI-FREE core so it is
unit-testable with saved HTML fixtures and mocked LLM JSON (no real network):

- ``links_from_export`` — which supplier links to check (the Shoptet export's
  ``internalNote`` column, visible products only, one row per unique link);
- ``extract_static`` — STATIC extraction of availability + price from a fetched
  product page, in three tiers: JSON-LD Product schema → og/product meta tags →
  plain-text keyword classification (trusted only for the verified static-text
  domains);
- ``need_llm`` — whether the paid LLM fallback is needed (static could not
  determine availability OR price);
- ``build_llm_messages`` / ``parse_llm_json`` — the OpenAI structured-extraction
  prompt + tolerant parsing of its JSON reply (mirrors the n8n informationExtractor);
- ``is_recently_checked`` — the stale-skip: a link with a fresh OK result is not
  re-fetched, saving HTTP + LLM cost (the digest's "refetch only links not checked
  in the last N hours" optimization).

The Flask app (webreview/app.py) wires this to requests, OpenAI and the
data/out/supplier_stock.json store.

Note on classification: ``export_helpers.state_of`` classifies OUR eshop's
3-state visibility (skladom / vypredané / už-nepredávané, incl. hidden/blocked),
which is a different question from "can we order this from the SUPPLIER". So this
module keeps a dedicated, availability-only ``classify_availability`` — it shares
the out-of-stock keyword vocabulary in spirit but returns a plain orderable bool.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from html import unescape
from urllib.parse import urlparse

from parovanie.catalog_index import supplier_from_url

# The four verified static-text domains (dispatch + suppliers skill): for these we
# trust the plain-text keyword classifier when JSON-LD/meta don't resolve. Other
# domains fall through to the LLM instead of a guess from loose page text.
STATIC_TEXT_DOMAINS = ("huntingshop.eu", "betalov.sk", "zubicek.cz", "virginiashop.sk")

# Out-of-stock signals are checked FIRST (a page that says "Vypredané" is decisive);
# only then in-stock signals. Slovak + Czech + English, diacritics-insensitive via
# substring on the accented and unaccented forms.
_OUT_KEYWORDS = (
    "vypredané", "vypredane", "vyprodáno", "vyprodano", "nedostupné", "nedostupne",
    "nie je skladom", "není skladem", "neni skladem", "nedostupný", "nedostupny",
    "out of stock", "sold out", "vypredaný", "vypredany", "momentálne nedostupné",
    "predaj skončil", "predaj skoncil", "dočasne nedostupné", "docasne nedostupne",
)
_IN_KEYWORDS = (
    "skladom", "na sklade", "skladem", "dostupné", "dostupne", "dostupný", "dostupny",
    "in stock", "ihneď k odberu", "ihned k odberu", "expedujeme", "posledné kusy",
    "posledne kusy", "posledný kus", "posledny kus", "k dispozícii", "k dispozicii",
)


def host_of(url: str) -> str:
    """Lowercased host of ``url`` with a leading ``www.`` stripped (matches
    catalog_index._host — used for per-domain politeness + static-domain gating)."""
    h = (urlparse(url or "").netloc or "").lower()
    return h[4:] if h.startswith("www.") else h


def is_static_text_domain(url: str) -> bool:
    """True when ``url``'s host is (or is a subdomain of) a verified static-text
    domain — the only case the loose keyword classifier is trusted for."""
    h = host_of(url)
    return any(h == d or h.endswith("." + d) for d in STATIC_TEXT_DOMAINS)


def classify_availability(text: str) -> bool | None:
    """Plain-text availability from page/label text: True (orderable / skladom),
    False (vypredané / nedostupné), None (can't tell). Out-of-stock wins over
    in-stock when both appear (a decisive negative)."""
    t = (text or "").lower()
    if not t:
        return None
    if any(k in t for k in _OUT_KEYWORDS):
        return False
    if any(k in t for k in _IN_KEYWORDS):
        return True
    return None


def availability_from_schema(token: str) -> bool | None:
    """schema.org / og availability token → orderable bool. InStock /
    LimitedAvailability / OnlineOnly / PreOrder / BackOrder → True (we can order);
    OutOfStock / SoldOut / Discontinued → False; anything else → None. Matches on
    the last path segment, case/punctuation-insensitive."""
    if not token:
        return None
    seg = re.sub(r"[^a-z]", "", str(token).rsplit("/", 1)[-1].lower())
    if seg in ("instock", "limitedavailability", "onlineonly", "preorder",
               "backorder", "presale", "instoreonly"):
        return True
    if seg in ("outofstock", "soldout", "discontinued"):
        return False
    return None


def _to_price(v) -> float | None:
    """Lenient price → float: accepts '59,90', '59.90', '1 299,00 €', 59.9, None."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return None
    s = re.sub(r"[^0-9,.\-]", "", s)
    if not s:
        return None
    # If both separators present, the last one is the decimal separator.
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    else:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _iter_jsonld(html: str):
    """Yield parsed JSON objects from every <script type=application/ld+json> block
    (tolerant: skips blocks that don't parse)."""
    for m in re.finditer(
            r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html or "", re.IGNORECASE | re.DOTALL):
        raw = m.group(1).strip()
        if not raw:
            continue
        try:
            yield json.loads(raw)
        except (ValueError, TypeError):
            continue


def _find_products(obj):
    """Recursively yield dicts that look like a schema.org Product (@type Product,
    directly or inside a @graph / list)."""
    if isinstance(obj, list):
        for x in obj:
            yield from _find_products(x)
    elif isinstance(obj, dict):
        t = obj.get("@type")
        types = t if isinstance(t, list) else [t]
        if any(isinstance(x, str) and x.lower() == "product" for x in types):
            yield obj
        for key in ("@graph", "mainEntity", "itemListElement"):
            if key in obj:
                yield from _find_products(obj[key])


def _offer_fields(product: dict) -> tuple:
    """(available, price, currency) from a Product's offers (dict or list; first
    offer with any signal wins). Availability from schema token; price/currency raw."""
    offers = product.get("offers")
    if offers is None:
        return None, None, ""
    for off in (offers if isinstance(offers, list) else [offers]):
        if not isinstance(off, dict):
            continue
        avail = availability_from_schema(off.get("availability") or "")
        price = _to_price(off.get("price") if off.get("price") is not None
                          else off.get("lowPrice"))
        cur = str(off.get("priceCurrency") or "").strip().upper()
        if avail is not None or price is not None:
            return avail, price, cur
    return None, None, ""


def extract_jsonld(html: str) -> dict:
    """{available, price, currency} from JSON-LD Product offers (first product with
    a signal). All-None when no usable Product schema is present."""
    for obj in _iter_jsonld(html):
        for product in _find_products(obj):
            avail, price, cur = _offer_fields(product)
            if avail is not None or price is not None:
                return {"available": avail, "price": price, "currency": cur}
    return {"available": None, "price": None, "currency": ""}


_META_RE = (
    r'<meta[^>]+(?:property|name)=["\']{prop}["\'][^>]+content=["\']([^"\']*)["\']',
    r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:property|name)=["\']{prop}["\']',
)


def _meta(html: str, prop: str) -> str:
    """First <meta property|name=prop content=...> value (content before OR after
    the property attribute), '' when absent."""
    for pat in _META_RE:
        m = re.search(pat.format(prop=re.escape(prop)), html or "", re.IGNORECASE)
        if m:
            return unescape(m.group(1)).strip()
    return ""


def extract_meta(html: str) -> dict:
    """{available, price, currency} from Open Graph / product meta tags
    (og:availability|product:availability, og:price:amount|product:price:amount,
    og:price:currency|product:price:currency)."""
    avail_raw = (_meta(html, "og:availability") or _meta(html, "product:availability")
                 or _meta(html, "product:availability:key"))
    price_raw = (_meta(html, "og:price:amount") or _meta(html, "product:price:amount"))
    cur_raw = (_meta(html, "og:price:currency") or _meta(html, "product:price:currency"))
    avail = availability_from_schema(avail_raw)
    if avail is None and avail_raw:                # og uses bare 'instock'/'oos'
        avail = classify_availability(avail_raw)
    return {"available": avail, "price": _to_price(price_raw),
            "currency": (cur_raw or "").strip().upper()}


def page_text(html: str, limit: int = 7000) -> str:
    """Visible-ish text of a page (scripts/styles stripped, tags → spaces, entities
    unescaped, whitespace collapsed), truncated to ``limit`` chars — used for the
    LLM input and the static keyword classifier. Deterministic (regex, no parser)."""
    s = re.sub(r"<script\b[^>]*>.*?</script>", " ", html or "", flags=re.IGNORECASE | re.DOTALL)
    s = re.sub(r"<style\b[^>]*>.*?</style>", " ", s, flags=re.IGNORECASE | re.DOTALL)
    s = re.sub(r"<[^>]+>", " ", s)
    s = unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit]


def extract_static(html: str, url: str) -> dict:
    """STATIC extraction: JSON-LD → meta → (verified-domain) text keywords, in that
    priority. Returns
    {available, price, currency, availabilityText, variants, extractedBy}
    where extractedBy is the tier that produced availability (jsonld/meta/text) or,
    if availability is unknown, the tier that produced the price — else None
    (nothing static resolved → needs the LLM)."""
    jl = extract_jsonld(html)
    mt = extract_meta(html)

    available = jl["available"]
    avail_src = "jsonld" if available is not None else None
    if available is None and mt["available"] is not None:
        available, avail_src = mt["available"], "meta"

    text = ""
    if available is None and is_static_text_domain(url):
        text = page_text(html)
        cls = classify_availability(text)
        if cls is not None:
            available, avail_src = cls, "text"

    price = jl["price"]
    price_src = "jsonld" if price is not None else None
    if price is None and mt["price"] is not None:
        price, price_src = mt["price"], "meta"

    currency = jl["currency"] or mt["currency"] or ""
    availability_text = ""
    if available is True:
        availability_text = "Skladom"
    elif available is False:
        availability_text = "Vypredané"

    return {
        "available": available,
        "price": price,
        "currency": currency,
        "availabilityText": availability_text,
        "variants": [],
        "extractedBy": avail_src or price_src,
    }


def need_llm(static: dict) -> bool:
    """The LLM fallback runs only when the static tier could not determine
    availability OR price (the ~2/3-of-calls-saved optimization from the digest)."""
    return static.get("available") is None or static.get("price") is None


LLM_MODEL = "gpt-4o-mini"


def build_llm_messages(text: str, url: str) -> list[dict]:
    """OpenAI chat messages for structured availability/price extraction — mirrors
    the n8n informationExtractor (JSON-object output, no invention)."""
    system = (
        "Si extraktor údajov z produktovej stránky dodávateľa. Z dodaného textu urči "
        "dostupnosť a cenu produktu. Odpovedz VÝHRADNE jedným JSON objektom s kľúčmi: "
        '{"available": boolean|null, "price": number|null, "currency": string, '
        '"availabilityText": string, "variants": [{"size": string, "available": boolean}]}. '
        "available=true ak je skladom / dá sa objednať, false ak je vypredané / nedostupné, "
        "null ak sa nedá určiť. price = číslo bez meny (napr. 59.90). currency = kód meny "
        "(EUR/CZK/…). Nič si nevymýšľaj; ak údaj v texte nie je, daj null."
    )
    user = f"URL: {url}\n\nText produktovej stránky:\n{text}"
    return [{"role": "system", "content": system},
            {"role": "user", "content": user}]


def parse_llm_json(content: str) -> dict:
    """Parse the model's JSON reply into the normalized result dict. Tolerant of a
    ```json code fence. Raises ValueError on unparseable content (the caller records
    that link as an error row)."""
    s = (content or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s).strip()
    try:
        d = json.loads(s)
    except (ValueError, TypeError) as e:
        raise ValueError(f"LLM nevrátil platný JSON: {e}") from None
    if not isinstance(d, dict):
        raise ValueError("LLM JSON nie je objekt")
    avail = d.get("available")
    if avail is not None:
        avail = bool(avail)
    variants = d.get("variants")
    variants = variants if isinstance(variants, list) else []
    return {
        "available": avail,
        "price": _to_price(d.get("price")),
        "currency": str(d.get("currency") or "").strip().upper(),
        "availabilityText": str(d.get("availabilityText") or "").strip(),
        "variants": variants,
    }


def is_recently_checked(prev_row: dict | None, now: datetime, max_age_hours: float) -> bool:
    """True when ``prev_row`` is a SUCCESSFUL result checked within ``max_age_hours``
    of ``now`` → skip re-fetching it (saves HTTP + LLM). A previous ERROR row is
    never skipped (retry it); a missing/blank checkedAt is never skipped."""
    if not prev_row or not prev_row.get("ok"):
        return False
    ts = prev_row.get("checkedAt") or ""
    try:
        prev = datetime.fromisoformat(ts)
    except ValueError:
        return False
    if prev.tzinfo is None:
        prev = prev.replace(tzinfo=now.tzinfo)
    return (now - prev) < timedelta(hours=max_age_hours)


def links_from_export(csv_text: str, suppliers: dict, visible_only: bool = True) -> list[dict]:
    """Unique supplier links from a Shoptet export (cp1250-decoded ``csv_text``):
    rows whose ``internalNote`` starts with http and (by default) whose
    ``productVisibility`` is 'visible' — the n8n filter "only enabled products with
    a supplier link". One entry per unique link:
    {link, supplier, name, codes:[variant codes], count}. ``supplier`` is the
    export's own supplier column, falling back to host inference from the URL."""
    import csv as _csv
    import io as _io

    agg: dict[str, dict] = {}
    for row in _csv.DictReader(_io.StringIO(csv_text or ""), delimiter=";"):
        note = (row.get("internalNote") or "").strip()
        if not note.lower().startswith("http"):
            continue
        if visible_only and (row.get("productVisibility") or "").strip() != "visible":
            continue
        supplier = (row.get("supplier") or "").strip() or supplier_from_url(note, suppliers)
        code = (row.get("code") or "").strip()
        name = (row.get("name") or "").strip()
        ent = agg.get(note)
        if ent is None:
            ent = agg[note] = {"link": note, "supplier": supplier, "name": name,
                               "codes": [], "count": 0}
        if not ent["supplier"] and supplier:
            ent["supplier"] = supplier
        if not ent["name"] and name:
            ent["name"] = name
        if code and code not in ent["codes"]:
            ent["codes"].append(code)
        ent["count"] += 1
    return list(agg.values())
