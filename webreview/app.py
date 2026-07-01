"""Web na ručnú kontrolu párovania: vľavo náš produkt, vpravo dodávateľ,
fajka/krížik (matched) alebo ručný výber/URL (unmatched). Rozhodnutia sa
ukladajú do data/out/decisions.json.

Run: PYTHONPATH=src .venv/bin/python webreview/app.py   (počúva na 0.0.0.0:8799)
"""
from __future__ import annotations
import csv
import hmac
import io
import json
import logging
import os
import re
import hashlib
import signal
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, request, send_from_directory, Response

from parovanie import __version__, config, import_builder
from parovanie.catalog_index import (
    build_catalog_index, build_promoted_entry, search_catalog, supplier_from_url)
from parovanie.export_helpers import current_of
from parovanie.shoptet_import import parse_import_log

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Data dir is env-overridable so tests/E2E can boot the app against a fixture.
OUT = os.environ.get("WEBREVIEW_OUT") or os.path.join(ROOT, "data", "out")
DATA = os.path.join(OUT, "review_data.json")
DECISIONS = os.path.join(OUT, "decisions.json")
IMGCACHE = os.path.join(OUT, "imgcache")
os.makedirs(IMGCACHE, exist_ok=True)

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
app = Flask(__name__, static_folder="static", template_folder="templates")
_lock = threading.Lock()
_import_lock = threading.Lock()   # one Shoptet import at a time (browser automation)
CRED_PATH = os.environ.get("SHOPTET_CRED") or os.path.join(ROOT, "data", ".shoptet_admin")
IMPORT_SCRIPT = os.path.join(ROOT, "scripts", "shoptet_import.py")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("webreview")
log.info("starting webreview v%s", __version__)

try:
    with open(DATA, encoding="utf-8") as f:
        PRODUCTS = json.load(f)
    log.info("loaded %d products from %s", len(PRODUCTS), DATA)
except FileNotFoundError:
    PRODUCTS = []
    log.warning("review data missing: %s — starting with 0 products", DATA)

# ONE cp1250 pass over the Shoptet export builds BOTH:
#   CODE2PAIR — code -> pairCode (the Shoptet import needs both present), and
#   CATALOG   — the catalog-wide search index grouped per pairCode (canonical
#               build_catalog_index), powering /api/search + promote-on-pair.
SRC = os.environ.get("WEBREVIEW_PRODUCTS") or os.path.join(ROOT, "data", "products.csv")


def _load_catalog(path, review_keys):
    """Single cp1250 pass over the Shoptet export → (code2pair, catalog). Missing
    export → ({}, {}) (the app already tolerates a dataless boot). `rows` is held only
    for the duration of the build, then released."""
    code2pair: dict = {}
    rows: list = []
    if not os.path.exists(path):
        return code2pair, {}
    csv.field_size_limit(10**9)
    with open(path, encoding="cp1250", errors="replace") as _f:
        for _row in csv.DictReader(_f, delimiter=";"):
            _c = (_row.get("code") or "").strip()
            if _c:
                code2pair[_c] = (_row.get("pairCode") or "").strip()
            rows.append(_row)
    return code2pair, build_catalog_index(rows, review_keys)


# review_keys are the products' BARE pairCodes — build_catalog_index marks a catalog
# entry in_review via `pairCode in review_keys`, and the index is grouped by bare
# pairCode. Most review entries are keyed "SUPPLIER|pairCode" (e.g. GRUBE|425), so
# collecting `key` here marked every such product not-in-review (C1). Collect pairCode.
CODE2PAIR, CATALOG = _load_catalog(SRC, {p.get("pairCode") for p in PRODUCTS})
log.info("catalog: %d products indexed (%d codes) from %s", len(CATALOG), len(CODE2PAIR), SRC)


def _load_decisions() -> dict:
    if os.path.exists(DECISIONS):
        with open(DECISIONS, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_decisions(d: dict) -> None:
    tmp = DECISIONS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DECISIONS)


# Per-line "objednané" state for the Na-objednanie tab (key = '<orderCode>|<itemCode>').
ORDERED = os.path.join(OUT, "ordered_items.json")


def _load_ordered() -> dict:
    if os.path.exists(ORDERED):
        with open(ORDERED, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_ordered(d: dict) -> None:
    tmp = ORDERED + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, ORDERED)


# Inline pairings entered on the Na-objednanie tab: {forestshop_code: supplier_url}.
# Lets the manager paste a reorder URL straight onto an order line he's ordering —
# covers ANY ordered code, not only the review-dataset subset (decisions.json). Same
# safe load/save as ordered/decisions; NEVER pruned (an order code may be outside the
# review set, so a prune would wrongly drop it). Gitignored data/out → survives deploy.
ORDER_PAIRINGS = os.path.join(OUT, "order_pairings.json")


def _load_order_pairings() -> dict:
    if os.path.exists(ORDER_PAIRINGS):
        with open(ORDER_PAIRINGS, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_order_pairings(d: dict) -> None:
    tmp = ORDER_PAIRINGS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, ORDER_PAIRINGS)


# Per-line "čaká sa" flag (key='<orderCode>|<itemCode>'): an ACTIVE order line that
# can't be stocked yet — waiting on the supplier, batching more items, or deferred by
# agreement with the customer. Independent of "objednané". Same safe gitignored store;
# NEVER pruned → survives deploy.
WAITING = os.path.join(OUT, "waiting_items.json")


def _load_waiting() -> dict:
    if os.path.exists(WAITING):
        with open(WAITING, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_waiting(d: dict) -> None:
    tmp = WAITING + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, WAITING)


# Supplier assigned on the Na-objednanie tab for an order line that arrived WITHOUT a
# supplier: {forestshop_code: supplier_name}. Keyed by code (a property of the product,
# like order_pairings) so it applies across every order line of that product and is the
# natural key for the eshop write-back. Same safe gitignored store; NEVER pruned →
# survives deploy. Written back to the eshop `supplier` field by the nightly upload.
SUPPLIER_ASSIGN = os.path.join(OUT, "supplier_assignments.json")


def _load_supplier_assign() -> dict:
    if os.path.exists(SUPPLIER_ASSIGN):
        with open(SUPPLIER_ASSIGN, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_supplier_assign(d: dict) -> None:
    tmp = SUPPLIER_ASSIGN + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, SUPPLIER_ASSIGN)


# GRUBE per-size externalCode store (durable, built by scripts/build_grube_codes.py):
# {code: {itemId, size, deUrl, productId}}. Read-only here — feeds the externalCode
# write-back CSV. Missing/corrupt → {} (the file may not exist until the first gather).
GRUBE_CODES = os.path.join(OUT, "grube_codes.json")


def _load_grube_codes() -> dict:
    try:
        with open(GRUBE_CODES, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _attach_grube(r, store=None):
    """Attach the GRUBE per-size order code + grube.de link to an order row, keyed by
    its forestshop variant code (r['itemCode']). Mutates and returns r so it's both a
    tiny unit-testable helper and usable inline in the api_orders loop.

    - r['grubeItemId'] = the per-size grube itemId (copyable code) or '' (non-grube /
      unmatched line — most rows).
    - r['grubeDeUrl']  = the grube.de order link, but ONLY if it is https:// (it lands
      in an <a href> on the client; a non-https value is dropped server-side so a
      javascript:/data:/http url can never reach the DOM).

    `store` (the grube_codes map) may be passed once per request; else loaded here."""
    if store is None:
        store = _load_grube_codes()
    g = store.get((r.get("itemCode") or "").strip()) or {}
    r["grubeItemId"] = str(g.get("itemId", "") or "")
    de = str(g.get("deUrl", "") or "")
    r["grubeDeUrl"] = de if de.startswith("https://") else ""
    return r


# at startup, prune orphan decisions whose key matches no product (e.g. a stale
# 'None'/'bad' from before stable keys) so the progress count == the import count
_VALID_KEYS = {p.get("key") for p in PRODUCTS}
with _lock:
    _d0 = _load_decisions()
    _d1 = {k: v for k, v in _d0.items() if k in _VALID_KEYS}
    if len(_d1) != len(_d0):
        log.info("pruned %d orphan decisions at startup", len(_d0) - len(_d1))
        _save_decisions(_d1)


_IMG_NOISE = ("logo", "/producer/", ".svg", "/svg/", "placeholder", "no-image",
              "banner", "/img/m/")  # m/ = presta related-product thumbs


def _extract_images(html: str, base: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    imgs: list[str] = []

    def add(s):
        if not s:
            return
        u = urljoin(base, s)
        low = u.lower()
        if any(x in low for x in _IMG_NOISE):
            return
        if u not in imgs:
            imgs.append(u)

    # og:image is reliably THE product's main image on both supplier platforms.
    # Gallery selectors leak related/carousel products (user-confirmed), so we
    # trust ONLY og:image, with a single product-detail image as fallback.
    og = soup.find("meta", attrs={"property": "og:image"})
    if og:
        add(og.get("content"))
    if not imgs:
        for sel in [".p-detail img", ".product-detail img", ".product-images img",
                    "[itemprop='image']"]:
            el = soup.select_one(sel)
            if el:
                add(el.get("src") or el.get("data-src") or el.get("data-zoom-image"))
                if imgs:
                    break
    return imgs[:1]


@app.after_request
def _no_cache(resp):
    # tool is actively developed + the index/decisions must always be fresh
    resp.headers["Cache-Control"] = "no-cache, must-revalidate, max-age=0"
    return resp


_AVAIL_WORDS = ("Skladom", "Na sklade", "Vypredané", "Momentálne nedostupné",
                "Na objednávku", "Posledný kus", "Predaj výrobku skončil", "Na dotaz")


def _supplier_meta(html: str):
    """Best-effort price + availability from a supplier product page."""
    price = ""
    m = re.search(r'(?:product:price:amount|og:price:amount)"\s+content="([0-9]+(?:[.,][0-9]+)?)"', html)
    if not m:
        m = re.search(r'"price"\s*:\s*"?([0-9]+(?:[.,][0-9]+)?)', html)
    if m:
        price = m.group(1).replace(".", ",")   # match our EUR formatting (5,41)
    avail = next((w for w in _AVAIL_WORDS if w in html), "")
    return price, avail


# --------------------------------------------------------------------------- #
# Na objednanie: forestshop "Vybavuje sa" orders → supplier reorder links
# --------------------------------------------------------------------------- #
ORDERS_CACHE = os.path.join(OUT, "orders_cache.csv")
ORDERS_MAXAGE = 300   # s — refresh the cached orders export at most every 5 min


def _cred(key: str):
    """Read a single KEY=value from the gitignored creds file (data/.shoptet_admin).
    None if missing — callers degrade/refuse rather than crash."""
    try:
        with open(CRED_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith(key + "=") and "=" in line:
                    return line.split("=", 1)[1].strip().strip("'\"") or None
    except FileNotFoundError:
        return None
    return None


def build_to_order_rows(orders_csv, products, decisions, code2pair):
    """Forestshop orders.csv (cp1250 bytes or str) → to-order rows.

    Keeps statusName=='Vybavuje sa', drops SHIPPING*/BILLING* pseudo-items, and joins
    each item code to its supplier reorder URL via the canonical
    import_builder.link_rows() (code -> internalNote). One row per order line; row
    key = '<orderCode>|<itemCode>'. Pure (no network) -> unit-testable."""
    text = (orders_csv.decode("cp1250", errors="replace")
            if isinstance(orders_csv, bytes) else orders_csv)
    code2url = {r[0]: r[2] for r in import_builder.link_rows(products, decisions, code2pair)}
    rows = []
    for r in csv.DictReader(io.StringIO(text), delimiter=";"):
        if (r.get("statusName") or "").strip() != "Vybavuje sa":
            continue
        code = (r.get("itemCode") or "").strip()
        if not code or re.match(r"^(SHIPPING|BILLING)", code, re.I):
            continue
        order = (r.get("code") or "").strip()
        rows.append({
            "key": f"{order}|{code}",
            "orderCode": order,
            "orderDate": (r.get("date") or "").strip()[:10],   # YYYY-MM-DD (drop time)
            "itemCode": code,
            "size": (r.get("itemVariantName") or "").strip(),
            "qty": (r.get("itemAmount") or "").strip(),
            "supplier": (r.get("itemSupplier") or "").strip(),
            "name": (r.get("itemName") or "").strip(),
            "supplierUrl": code2url.get(code, ""),
        })
    return rows


def _fetch_orders_csv() -> bytes:
    base = _cred("SHOPTET_ORDERS_URL")
    if not base:
        raise RuntimeError(f"SHOPTET_ORDERS_URL chýba v {CRED_PATH}")
    today = time.strftime("%Y-%m-%d")
    frm = time.strftime("%Y-%m-%d", time.localtime(time.time() - 90 * 86400))
    sep = "&" if "?" in base else "?"
    r = requests.get(f"{base}{sep}dateFrom={frm}&dateUntil={today}",
                     headers={"User-Agent": UA}, timeout=60)
    r.raise_for_status()
    return r.content


def _orders_csv_cached() -> bytes:
    if (os.path.exists(ORDERS_CACHE)
            and time.time() - os.path.getmtime(ORDERS_CACHE) < ORDERS_MAXAGE):
        with open(ORDERS_CACHE, "rb") as f:
            return f.read()
    data = _fetch_orders_csv()
    tmp = ORDERS_CACHE + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, ORDERS_CACHE)
    return data


@app.route("/")
def index():
    return send_from_directory("templates", "index.html")


@app.route("/favicon.ico")
def favicon():
    return ("", 204)


@app.route("/api/version")
def api_version():
    """Deployed version (single source: parovanie.__version__) — shown in the
    footer for post-deploy verification."""
    return Response(f"v{__version__}", content_type="text/plain; charset=utf-8")


def _grube_de_display(products, decisions):
    """Serve-time DISPLAY normalization for /api/products: a GRUBE product's
    supplier URLs are rebuilt to the canonical grube.DE detail URL in the RESPONSE
    only (review card AND search tab both render these hrefs). GRUBE == grube.de
    (German availability); the eshop internalNote + 'Na objednanie' chip already
    normalize via import_builder.link_rows — this mirrors the SAME rebuild on the
    display path (import_builder.to_grube_de, productId-based).

    The manager's stored .sk pairings are PRESERVED: in-memory PRODUCTS is never
    mutated and decisions.json is never rewritten — only SHALLOW COPIES of the
    GRUBE entries are swapped. Non-GRUBE products/decisions are returned unchanged.
    Fallback to the raw URL when to_grube_de can't parse a productId."""
    to_de = import_builder.to_grube_de
    out_products = []
    grube_keys = set()
    for p in products:
        if p.get("supplier") != "GRUBE":
            out_products.append(p)
            continue
        grube_keys.add(p.get("key"))
        q = dict(p)                                   # shallow copy — don't mutate PRODUCTS
        cands = p.get("candidates")
        if cands:
            new_cands = []
            for c in cands:
                url = c.get("url")
                if url:
                    c = {**c, "url": to_de(url) or url}
                new_cands.append(c)                   # url-less candidate kept as-is
            q["candidates"] = new_cands
        ai_url = p.get("ai_chosen_url")
        if ai_url:
            q["ai_chosen_url"] = to_de(ai_url) or ai_url
        out_products.append(q)
    out_decisions = {}
    for k, d in decisions.items():
        if k in grube_keys and isinstance(d, dict) and (d.get("url") or "").strip():
            d = {**d, "url": to_de(d["url"]) or d["url"]}   # shallow copy of GRUBE decision
        out_decisions[k] = d
    return {"products": out_products, "decisions": out_decisions}


@app.route("/api/products")
def api_products():
    return jsonify(_grube_de_display(PRODUCTS, _load_decisions()))


@app.route("/api/images")
def api_images():
    """Title + images for any supplier URL (so a manually entered link pulls its
    data). Cached on disk."""
    url = request.args.get("url", "").strip()
    if not url.startswith("http"):
        return jsonify({"title": "", "images": []})
    key = hashlib.sha1(url.encode()).hexdigest()
    cache = os.path.join(IMGCACHE, key + ".json")
    if os.path.exists(cache):
        with open(cache, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):              # legacy cache format
            data = {"title": "", "images": data}
        data.setdefault("price", "")
        data.setdefault("availability", "")
        return jsonify(data)
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=20)
        if r.ok:
            from parovanie.verify import extract_page
            title = extract_page(r.text).get("title", "")
            imgs = _extract_images(r.text, url)
            price, avail = _supplier_meta(r.text)
        else:
            log.warning("image fetch non-OK url=%s status=%s", url, r.status_code)
            title, imgs, price, avail = "", [], "", ""
    except Exception as e:  # noqa: BLE001 — best-effort scrape; log cause and degrade
        log.warning("image fetch failed url=%s: %r", url, e)
        title, imgs, price, avail = "", [], "", ""
    data = {"title": title, "images": imgs, "price": price, "availability": avail}
    with open(cache, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return jsonify(data)


@app.route("/api/decision", methods=["POST"])
def api_decision():
    body = request.get_json(force=True)
    key = str(body.get("key"))
    status = body.get("status")
    with _lock:
        d = _load_decisions()
        if status in (None, "", "undo"):          # undo / un-decide
            d.pop(key, None)
        else:
            d[key] = {"status": status, "url": body.get("url", "").strip()}
        _save_decisions(d)
    log.info("decision key=%s status=%s url=%s", key, status, body.get("url", ""))
    return jsonify({"ok": True})


# CSV/spreadsheet formula-injection guard. A cell beginning with one of these is a
# live formula when the file is opened in Excel/LibreOffice. Real forestshop codes,
# pairCodes and http(s) URLs never start with these, so legit cells are untouched
# (Shoptet matching unaffected); a malicious cell is prefixed with ' → inert text.
_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value):
    s = str(value)
    return "'" + s if s[:1] in _FORMULA_LEAD else s


def _csv_response(header, rows, filename):
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    w.writerow(header)
    w.writerows(rows)
    # UTF-8 with BOM — universal, avoids the cp1250 'č'→'è' mojibake. Import into
    # Shoptet as UTF-8.
    data = buf.getvalue().encode("utf-8-sig")
    return Response(data, content_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.route("/api/import")
def api_import():
    # TWO files (Shoptet wipes empty cells, so columns are split — see import_builder):
    #   import_links.csv  = code;pairCode;internalNote (reorder URL in the private field)
    #   import_states.csv = code;pairCode;productVisibility;stock;availability (Vypredané / Predaj skončil)
    dec = _load_decisions()
    # reviewed pairings (decisions) + inline pairings from the Na-objednanie tab.
    # A reviewed decision is authoritative, so inline rows skip any code it already
    # covers (Shoptet aborts on a duplicate code).
    link = import_builder.link_rows(PRODUCTS, dec, CODE2PAIR)
    link += import_builder.order_pairing_rows(
        _load_order_pairings(), CODE2PAIR, exclude_codes={r[0] for r in link})
    files = [
        ("import_links.csv", import_builder.LINK_HEADER, link),
        ("import_states.csv", import_builder.STATE_HEADER,
         import_builder.state_rows(PRODUCTS, dec, CODE2PAIR)),
        # supplier write-back: only code;pairCode;supplier (own file → can't wipe
        # internalNote/state). Independent column from the link rows, so no exclude.
        ("import_suppliers.csv", import_builder.SUPPLIER_HEADER,
         import_builder.supplier_rows(_load_supplier_assign(), CODE2PAIR)),
        # GRUBE per-size externalCode write-back: only code;pairCode;externalCode (own
        # file → can't wipe internalNote/state). Independent column, so no exclude.
        ("import_externalcode.csv", import_builder.EXTERNALCODE_HEADER,
         import_builder.externalcode_rows(_load_grube_codes(), CODE2PAIR)),
    ]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, header, rows in files:
            s = io.StringIO()
            w = csv.writer(s, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
            w.writerow(header)
            w.writerows([_csv_safe(c) for c in row] for row in rows)   # formula-injection guard
            z.writestr(name, s.getvalue().encode("utf-8-sig"))
    return Response(buf.getvalue(), content_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="import_forestshop.zip"'})


@app.route("/api/export")
def api_export():
    """All decisions joined to products — for building the corrected import +
    the unavailable list. Stable key = supplier|pairCode."""
    dec = _load_decisions()
    rows = []
    for p in PRODUCTS:
        d = dec.get(p.get("key"))
        if not d:
            continue
        rows.append({"key": p.get("key"), "supplier": p["supplier"], "name": p["name"],
                     "variant_codes": p["variant_codes"], "status": d.get("status"),
                     "url": d.get("url", "")})
    return jsonify({"decisions": rows})


# --------------------------------------------------------------------------- #
# Catalog search + promote-on-pair (CATALOG built at startup from the export)
# --------------------------------------------------------------------------- #
def _save_products(products) -> None:
    """Atomic write of review_data.json (tmp + os.replace). Mirrors the other _save_*
    stores; ensure_ascii=False to keep the Slovak names readable, like build_review_data."""
    tmp = DATA + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(products, f, ensure_ascii=False)
    os.replace(tmp, DATA)


def _current_for_paircode(pair: str) -> dict:
    """Build the eshop-side `current` snapshot for a freshly paired catalog product by
    scanning the Shoptet export for the FIRST row of its pairCode. A rare manual action,
    so a one-off cp1250 scan is acceptable. Column mapping mirrors build_review_data's
    current_of() call. Missing export / no matching row -> {} (the card just renders
    without our-side state — never a 500)."""
    if not os.path.exists(SRC):
        return {}
    csv.field_size_limit(10**9)
    try:
        with open(SRC, encoding="cp1250", errors="replace") as f:
            for r in csv.DictReader(f, delimiter=";"):
                if (r.get("pairCode") or "").strip() == pair:
                    # Column names + arg order MUST match build_review_data.py /
                    # resync_export.py (productVisibility — there is NO "visibility"
                    # column; reading the wrong one left vis="" so hidden/blocked
                    # products never got state 3 — snapshot drift).
                    return current_of(
                        (r.get("productVisibility") or "").strip(),
                        (r.get("availabilityInStock") or "").strip(),
                        (r.get("availabilityOutOfStock") or "").strip(),
                        (r.get("price") or "").strip(),
                        (r.get("standardPrice") or "").strip(),
                        (r.get("stock") or "").strip(),
                    )
    except (OSError, csv.Error) as e:
        # Best-effort contract: a missing/unreadable export OR a malformed row
        # (csv.Error — NUL byte / oversized field) degrades to {}, never a 500.
        log.warning("current_for_paircode scan failed pair=%s: %r", pair, e)
    return {}


# Lazily-built {code: ORIG_URL} from the marketing XML — None = not yet attempted.
_CODE2URL = None


def _our_url_for_paircode(pair: str):
    """Best-effort forestshop our_url for a promoted product, from the marketing XML's
    ORIG_URL (the authoritative eshop URL) by exact variant code. Built once and cached.
    ANY failure (missing XML, parse error, scripts not importable) -> None, which is an
    acceptable result (the UI falls back to a search link)."""
    global _CODE2URL
    ce = CATALOG.get(pair)
    if not ce or not ce.get("variant_codes"):
        return None
    if _CODE2URL is None:
        _CODE2URL = {}
        try:
            mx = os.path.join(OUT, "marketing.xml")
            if os.path.exists(mx):
                # scripts/ is not on sys.path; load the pure function from the file.
                import importlib.util
                _p = os.path.join(ROOT, "scripts", "url_from_marketing_xml.py")
                _spec = importlib.util.spec_from_file_location("_uxml", _p)
                _mod = importlib.util.module_from_spec(_spec)
                _spec.loader.exec_module(_mod)
                _CODE2URL = _mod.build_code2url(mx)
                log.info("our_url: marketing XML loaded (%d codes)", len(_CODE2URL))
        except Exception as e:  # noqa: BLE001 — best-effort; our_url=None is acceptable
            log.warning("our_url marketing-XML resolve failed: %r", e)
            _CODE2URL = {}
    for c in ce["variant_codes"]:
        if c in _CODE2URL:
            return _CODE2URL[c]
    return None


def _search_result(e: dict) -> dict:
    """Shape one catalog entry for /api/search. idx + our_url come from the matching
    in-review product (if any) so the UI can deep-link an already-paired item. Match by
    pairCode — most review entries are keyed "SUPPLIER|pairCode", so a key==pairCode test
    missed them and dropped the our_url/idx deep-link (C1)."""
    p = next((x for x in PRODUCTS if x.get("pairCode") == e["pairCode"]), None)
    return {
        "pairCode": e["pairCode"],
        "name": e["name"],
        "supplier": e["supplier"],
        "codes": e["variant_codes"],
        "image": e["image"],
        "in_review": e["in_review"],
        "our_url": (p or {}).get("our_url"),
        "idx": (p or {}).get("idx"),
    }


@app.route("/api/search")
def api_search():
    """Accent-insensitive catalog search over name / supplier / variant code (pure
    search_catalog over the startup CATALOG). Empty/short query -> no results."""
    q = request.args.get("q", "")
    return jsonify({"results": [_search_result(e) for e in search_catalog(CATALOG, q)]})


@app.route("/api/search-pair", methods=["POST"])
def api_search_pair():
    """Manually pair a catalog product to a supplier URL from the search box. If the
    product is not yet in the review set it is PROMOTED (a minimal review_data entry
    built from the catalog row + the export `current` snapshot + best-effort our_url),
    appended to PRODUCTS and persisted; then a `manual` decision is recorded. The URL
    must be http(s) (else 400); an unknown pairCode -> 404."""
    body = request.get_json(silent=True) or {}
    pair = str(body.get("pairCode") or "").strip()
    url = str(body.get("url") or "").strip()
    # authoritative URL guard (matches /api/order-pair) — blocks javascript:/data: and
    # malformed values from reaching the import's internalNote / a CSV cell.
    if not re.match(r"^https?://", url):
        return jsonify({"ok": False, "error": "url must start with http(s)://"}), 400
    ce = CATALOG.get(pair)
    if not ce:
        return jsonify({"ok": False, "error": "unknown pairCode"}), 404
    # Match an already-in-review product by pairCode — NOT by key. Most review entries are
    # keyed "SUPPLIER|pairCode" (e.g. GRUBE|425); a key==pair test missed every such entry
    # → it wrongly promoted a DUPLICATE bare-key entry AND wrote the decision under "425"
    # where link_rows (which reads dec["GRUBE|425"]) never finds it, silently dropping the
    # manager's corrected URL while the card showed a false "napárované ✓" (C1).
    in_review = any(p.get("pairCode") == pair for p in PRODUCTS)
    # The two heavy read-only scans (55 MB cp1250 export + 59 MB marketing XML) depend
    # ONLY on `pair`, never on mutable state → compute them OUTSIDE the lock so a promote
    # never stalls every other write endpoint for seconds. Needed only when promoting a
    # genuinely NEW catalog product; an existing entry just gets its decision rewritten.
    if not in_review:
        snapshot = _current_for_paircode(pair)
        our_url = _our_url_for_paircode(pair)
        supplier = supplier_from_url(url, config.SUPPLIERS)
    with _lock:
        # re-check under the lock (append-only store → monotonic; a tiny TOCTOU on
        # concurrent same-pair promotes is fine — single manager user — and this dedups)
        existing = next((p for p in PRODUCTS if p.get("pairCode") == pair), None)
        if existing is None:
            entry = build_promoted_entry(ce, snapshot, our_url, supplier, len(PRODUCTS))
            PRODUCTS.append(entry)
            _save_products(PRODUCTS)
            ce["in_review"] = True   # keep the catalog snapshot consistent for re-search
            target_key = pair        # promoted entry's key == pair (self-consistent)
            promoted = True
            log.info("search-pair promoted key=%s supplier=%s codes=%d our_url=%s",
                     pair, entry["supplier"], len(entry["variant_codes"]), entry["our_url"])
        else:
            target_key = existing["key"]   # write under the REAL key (e.g. GRUBE|425)
            promoted = False
        dec = _load_decisions()
        dec[target_key] = {"status": "manual", "url": url}
        _save_decisions(dec)
    log.info("search-pair decision key=%s url=%s promoted=%s", target_key, url, promoted)
    return jsonify({"ok": True, "promoted": promoted, "key": target_key})


@app.route("/api/ordered", methods=["GET", "POST"])
def api_ordered():
    """Per-line 'objednané' state (key='<orderCode>|<itemCode>'), persisted like
    decisions. GET -> the map; POST {key, ordered} toggles a single line."""
    if request.method == "GET":
        return jsonify({"ordered": _load_ordered()})
    body = request.get_json(force=True)
    key = str(body.get("key"))
    ordered = bool(body.get("ordered"))
    with _lock:
        d = _load_ordered()
        if ordered:
            d[key] = True
        else:
            d.pop(key, None)
        _save_ordered(d)
    log.info("ordered key=%s ordered=%s", key, ordered)
    return jsonify({"ok": True})


@app.route("/api/waiting", methods=["GET", "POST"])
def api_waiting():
    """Per-line 'čaká sa' flag (key='<orderCode>|<itemCode>'): active order line that
    can't be stocked yet. GET -> the map; POST {key, waiting} toggles a single line.
    Same shape as /api/ordered, independent state."""
    if request.method == "GET":
        return jsonify({"waiting": _load_waiting()})
    body = request.get_json(force=True)
    key = str(body.get("key"))
    waiting = bool(body.get("waiting"))
    with _lock:
        d = _load_waiting()
        if waiting:
            d[key] = True
        else:
            d.pop(key, None)
        _save_waiting(d)
    log.info("waiting key=%s waiting=%s", key, waiting)
    return jsonify({"ok": True})


@app.route("/api/order-pair", methods=["POST"])
def api_order_pair():
    """Save/clear an inline supplier reorder URL for a forestshop order code
    (keyed by itemCode). Mirrors /api/decision but keyed by the forestshop product
    code, so it covers order lines that are NOT in the review dataset. Empty url
    clears the pairing. The URL then shows as the row's reorder link and is included
    in the import (import_builder.order_pairing_rows)."""
    body = request.get_json(force=True)
    code = str(body.get("code") or "").strip()
    url = str(body.get("url") or "").strip()
    if not code:
        return jsonify({"ok": False, "error": "missing code"}), 400
    # forestshop codes always start alphanumeric — a leading formula char (=,+,-,@,…)
    # is either malformed or a CSV-injection attempt; reject it at the source.
    if code[:1] in _FORMULA_LEAD:
        return jsonify({"ok": False, "error": "invalid code"}), 400
    # authoritative URL guard (matches the client) — only real http(s) links reach
    # the import's internalNote; blocks javascript:/data: and malformed 'httpfoo'.
    if url and not re.match(r"^https?://", url):
        return jsonify({"ok": False, "error": "url must start with http(s)://"}), 400
    with _lock:
        d = _load_order_pairings()
        if url:
            d[code] = url
        else:
            d.pop(code, None)
        _save_order_pairings(d)
    log.info("order-pair code=%s url=%s", code, url)
    return jsonify({"ok": True})


@app.route("/api/order-supplier", methods=["POST"])
def api_order_supplier():
    """Assign/clear a supplier name for a forestshop order code (keyed by itemCode).
    Lets the manager fill in the supplier for an order line that arrived without one;
    the row then regroups under that supplier on the tab and the name is written back
    to the eshop `supplier` field by the nightly upload. Empty supplier clears it.
    Mirrors /api/order-pair (same code guard); the supplier name reaches a CSV, so a
    leading formula char is rejected here AND escaped at the CSV sink (_csv_safe)."""
    body = request.get_json(force=True)
    code = str(body.get("code") or "").strip()
    supplier = str(body.get("supplier") or "").strip()
    if not code:
        return jsonify({"ok": False, "error": "missing code"}), 400
    # forestshop codes always start alphanumeric — a leading formula char (=,+,-,@,…)
    # is malformed or a CSV-injection attempt; reject at the source.
    if code[:1] in _FORMULA_LEAD:
        return jsonify({"ok": False, "error": "invalid code"}), 400
    # supplier name is written verbatim into the import CSV's `supplier` column — a
    # leading formula char would be a CSV-injection vector; real names start
    # alphanumeric, so reject it here too (belt-and-braces with _csv_safe at the sink).
    if supplier and supplier[:1] in _FORMULA_LEAD:
        return jsonify({"ok": False, "error": "invalid supplier"}), 400
    with _lock:
        d = _load_supplier_assign()
        if supplier:
            d[code] = supplier
        else:
            d.pop(code, None)
        _save_supplier_assign(d)
    log.info("order-supplier code=%s supplier=%s", code, supplier)
    return jsonify({"ok": True})


@app.route("/api/orders")
def api_orders():
    """To-order list: forestshop 'Vybavuje sa' items joined to supplier reorder
    links, with the per-line 'ordered' state merged in. Degrades to [] on fetch
    error so the tab still renders."""
    try:
        csv_bytes = _orders_csv_cached()
    except Exception as e:  # noqa: BLE001 — degrade to empty list, log the cause
        log.warning("orders fetch failed: %r", e)
        return jsonify({"orders": [], "error": str(e)})
    rows = build_to_order_rows(csv_bytes, PRODUCTS, _load_decisions(), CODE2PAIR)
    ordered = _load_ordered()
    waiting = _load_waiting()
    pairings = _load_order_pairings()
    assigns = _load_supplier_assign()
    grube = _load_grube_codes()                      # loaded once per request
    for r in rows:
        r["ordered"] = bool(ordered.get(r["key"]))
        r["waiting"] = bool(waiting.get(r["key"]))   # 'čaká sa' — deferred active line
        # supplierUrl stays the reviewed-decision link (read-only); pairUrl is the
        # inline-entered one (editable on the tab). A row is "paired" if either is set.
        r["pairUrl"] = pairings.get(r["itemCode"], "")
        # supplier manually assigned for an order line that arrived without one — the
        # tab groups by (assignedSupplier OR supplier), so this regroups the row.
        r["assignedSupplier"] = assigns.get(r["itemCode"], "")
        # GRUBE per-size code chip + .de link (empty for every non-GRUBE / unmatched row)
        _attach_grube(r, grube)
    return jsonify({"orders": rows})


@app.route("/static/<path:p>")
def static_files(p):
    return send_from_directory("static", p)


# --------------------------------------------------------------------------- #
# n8n → Shoptet auto-import (vypredané → skladom)
# --------------------------------------------------------------------------- #
def _import_token():
    """Bearer token for the import endpoint, from the gitignored creds file
    (key N8N_IMPORT_TOKEN). None if not configured → endpoint refuses all calls."""
    return _cred("N8N_IMPORT_TOKEN")


MAX_IMPORT_BYTES = 5 * 1024 * 1024   # restock CSVs are a few kB; cap the in-memory read


def _safe_unlink(*paths):
    for p in paths:
        try:
            os.remove(p)
        except OSError:
            pass


def _client_ip():
    """Real caller IP behind the Cloudflare tunnel (so the unauthorized-attempt
    log is useful, not just the tunnel/local address)."""
    return (request.headers.get("CF-Connecting-IP")
            or request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            or request.remote_addr)


def run_import(csv_path, dry_run=False, timeout=300):
    """Run the existing careful import script as a subprocess (catalog backup +
    safe-mode + result read-back). Returns (returncode, stdout, stderr). Started in
    its own session so a timeout kills the WHOLE group (the Playwright/Chromium it
    spawns too), never an orphaned browser mid-import. `timeout` scales with the CSV
    size — a few thousand pairing rows legitimately take longer than a small restock.
    Stubbable in tests."""
    cmd = [sys.executable, IMPORT_SCRIPT, "--file", csv_path, "--yes"]
    if dry_run:
        cmd.append("--dry-run")
    env = {**os.environ, "PYTHONPATH": os.path.join(ROOT, "src")}
    p = subprocess.Popen(cmd, cwd=ROOT, env=env, text=True,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         start_new_session=True)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        p.communicate()
        raise
    return p.returncode, out, err


@app.route("/api/n8n/shoptet-import", methods=["POST"])
def n8n_shoptet_import():
    """n8n posts a restock CSV (multipart 'file', or raw body); we whitelist it to
    the safe restock columns and run the careful Shoptet import. Bearer-auth'd.
    Pass dry_run=1 (form/query) to reach the import form without changing anything."""
    token = _import_token()
    auth = request.headers.get("Authorization", "")
    expected = f"Bearer {token}".encode() if token else b""
    # compare bytes — a non-ASCII Authorization header must 401, not raise (latin-1
    # is how WSGI decodes the header; compare_digest rejects non-ASCII str args)
    if not token or not hmac.compare_digest(auth.encode("latin-1", "ignore"), expected):
        log.warning("n8n import: unauthorized call from %s", _client_ip())
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    f = request.files.get("file")
    raw = f.read() if f else request.get_data()
    if not raw:
        log.warning("n8n import: empty body")
        return jsonify({"ok": False, "error": "empty body"}), 400
    if len(raw) > MAX_IMPORT_BYTES:
        log.warning("n8n import: payload too large (%d B)", len(raw))
        return jsonify({"ok": False, "error": "payload too large"}), 413

    os.makedirs(OUT, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    # unique names (mkstemp) so two same-second calls never clobber each other's
    # file while a subprocess is reading it
    raw_fd, raw_path = tempfile.mkstemp(prefix=f"n8n_restock_{ts}_", suffix="_raw.csv", dir=OUT)
    out_fd, out_path = tempfile.mkstemp(prefix=f"n8n_restock_{ts}_", suffix=".csv", dir=OUT)
    os.close(out_fd)
    with os.fdopen(raw_fd, "wb") as w:
        w.write(raw)
    try:
        rows = import_builder.sanitize_csv(raw_path, out_path)
    except (ValueError, UnicodeDecodeError) as e:
        log.warning("n8n import: bad CSV: %s", e)
        _safe_unlink(raw_path, out_path)
        return jsonify({"ok": False, "error": str(e)}), 400
    finally:
        _safe_unlink(raw_path)   # sanitized file is the audit record; raw is transient
    if rows == 0:
        log.info("n8n import: 0 restock rows — nothing to import")
        _safe_unlink(out_path)
        return jsonify({"ok": True, "rows": 0, "message": "no restock rows"}), 200

    dry = str(request.values.get("dry_run", "")).lower() in ("1", "true", "yes")
    if not _import_lock.acquire(blocking=False):
        log.warning("n8n import: another import already running")
        _safe_unlink(out_path)
        return jsonify({"ok": False, "error": "import already running"}), 409
    log.info("n8n import: %d rows, dry_run=%s, file=%s", rows, dry, out_path)
    try:
        rc, out, err = run_import(out_path, dry_run=dry)
    except subprocess.TimeoutExpired:
        log.error("n8n import: subprocess timeout — killed import group")
        return jsonify({"ok": False, "error": "import timeout"}), 504
    finally:
        _import_lock.release()

    parsed = parse_import_log(out)
    result = {"ok": rc == 0, "exit_code": rc, "rows": rows, "dry_run": dry,
              "processed": parsed.get("processed"), "updated": parsed.get("updated"),
              "failed": parsed.get("failed"), "stdout_tail": (out or "")[-800:]}
    log.info("n8n import: rc=%s processed=%s updated=%s failed=%s",
             rc, parsed.get("processed"), parsed.get("updated"), parsed.get("failed"))
    if rc != 0:
        log.error("n8n import FAILED rc=%s stderr=%s", rc, (err or "")[-400:])
    return jsonify(result), (200 if rc == 0 else 502)


# --------------------------------------------------------------------------- #
# n8n → nightly upload of worker pairings (reorder links → eshop internalNote)
# --------------------------------------------------------------------------- #
PAIRINGS_STATE = os.path.join(OUT, "uploaded_pairings.json")


def _load_uploaded():
    """{key: url} of pairings already uploaded — so the nightly job only sends new
    or changed ones. Missing/corrupt → empty (treat everything as new)."""
    try:
        with open(PAIRINGS_STATE, encoding="utf-8") as f:
            d = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    # always a {key: url} map — a stray JSON array could repeat a key and break the
    # "total_uploaded never exceeds total_products" invariant in _pairing_summary
    return d if isinstance(d, dict) else {}


def _save_uploaded(d):
    tmp = PAIRINGS_STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PAIRINGS_STATE)


# Public URL of the review web — handed to the n8n notifier so the single summary
# Discord message can link straight to the pairing app.
PUBLIC_URL = os.environ.get("WEBREVIEW_PUBLIC_URL", "https://parovanie-forestshop.newlevel.media")


def _pairing_summary(uploaded):
    """Totals for the n8n summary notification: how many pairings are uploaded to the
    eshop in total, how many of our products still have none, and the review link.
    ``uploaded`` is the post-run map so ``total_uploaded`` already includes this run.
    Only keys still present in the current review set count, so a product removed
    since its upload can't push the ratio past total (e.g. avoid "Spolu 105 / 100")."""
    valid = {p.get("key") for p in PRODUCTS}
    total = len(valid)  # distinct product keys (de-dups), same unit as `up` below
    up = sum(1 for k in uploaded if k in valid)
    return {"total_products": total, "total_uploaded": up,
            "remaining": max(0, total - up), "review_url": PUBLIC_URL}


@app.route("/api/n8n/upload-pairings", methods=["POST"])
def n8n_upload_pairings():
    """Upload the workers' NEW pairings (reorder links) to the eshop. Reads the
    local review decisions, builds the link import (code;pairCode;internalNote) for
    only the pairings not yet uploaded, runs the careful import, and records what
    went up. Bearer-auth'd; dry_run=1 reaches the import without changing anything.
    Visibility/stock are NOT touched here — the morning restock job turns a product
    on once the supplier has it in stock."""
    token = _import_token()
    auth = request.headers.get("Authorization", "")
    expected = f"Bearer {token}".encode() if token else b""
    if not token or not hmac.compare_digest(auth.encode("latin-1", "ignore"), expected):
        log.warning("n8n pairings: unauthorized call from %s", _client_ip())
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    dry = str(request.values.get("dry_run", "")).lower() in ("1", "true", "yes")
    dec = _load_decisions()
    uploaded = _load_uploaded()
    new_keys = import_builder.new_pairing_keys(dec, uploaded)
    by_key = {p.get("key"): p for p in PRODUCTS}
    products = [{"name": by_key.get(k, {}).get("name", ""),
                 "our_url": by_key.get(k, {}).get("our_url", ""),
                 "supplier_url": dec[k].get("url", "")} for k in new_keys]
    if not new_keys:
        log.info("n8n pairings: 0 new pairings")
        return jsonify({"ok": True, "count": 0, "products": [],
                        **_pairing_summary(uploaded)}), 200

    rows = import_builder.link_rows(PRODUCTS, {k: dec[k] for k in new_keys}, CODE2PAIR)
    if not rows:
        log.warning("n8n pairings: %d new keys but 0 import rows (codes missing)", len(new_keys))
        # paired but un-uploadable (variant codes missing) — surface so the notifier
        # warns instead of staying silent (count:0 alone would send nothing)
        return jsonify({"ok": True, "count": 0, "products": products,
                        "message": "no import rows", "blocked": len(new_keys),
                        **_pairing_summary(uploaded)}), 200

    # surface a real data inconsistency: the same variant code paired to two different
    # supplier URLs (a code can hold only one link, so first-wins drops the rest)
    code_urls = {}
    for k in new_keys:
        for c in by_key.get(k, {}).get("variant_codes", []):
            code_urls.setdefault(c, set()).add((dec[k].get("url") or "").strip())
    conflicts = [c for c, u in code_urls.items() if len(u) > 1]
    if conflicts:
        log.warning("n8n pairings: %d codes paired to conflicting URLs (first wins): %s",
                    len(conflicts), conflicts[:10])

    os.makedirs(OUT, exist_ok=True)
    out_fd, out_path = tempfile.mkstemp(prefix="import_links_", suffix=".csv", dir=OUT)
    with os.fdopen(out_fd, "w", encoding="utf-8-sig", newline="") as f:
        from parovanie.writer import shoptet_writer
        w = shoptet_writer(f)
        w.writerow(import_builder.LINK_HEADER)
        w.writerows(rows)

    if not _import_lock.acquire(blocking=False):
        log.warning("n8n pairings: another import already running")
        _safe_unlink(out_path)
        return jsonify({"ok": False, "error": "import already running"}), 409
    log.info("n8n pairings: %d products, %d rows, dry_run=%s", len(new_keys), len(rows), dry)
    try:
        # pairing CSVs can be large (an initial bulk of thousands of rows) → more time
        rc, out, err = run_import(out_path, dry_run=dry, timeout=900)
        parsed = parse_import_log(out)
        ok = rc == 0
        if ok and not dry:                   # record only after a real success (inside the lock)
            for k in new_keys:               # mark with the SAME normalization as the selection
                uploaded[k] = (dec[k].get("url") or "").strip()
            _save_uploaded(uploaded)
    except subprocess.TimeoutExpired:
        log.error("n8n pairings: subprocess timeout — killed import group")
        _safe_unlink(out_path)
        return jsonify({"ok": False, "error": "import timeout"}), 504
    finally:
        _import_lock.release()

    if ok:
        _safe_unlink(out_path)               # success → drop the temp CSV (the catalog backup is the audit record)
    result = {"ok": ok, "exit_code": rc, "count": len(new_keys), "rows": len(rows),
              "dry_run": dry, "processed": parsed.get("processed"),
              "updated": parsed.get("updated"), "failed": parsed.get("failed"),
              "products": products, "stdout_tail": (out or "")[-800:],
              **_pairing_summary(uploaded)}
    log.info("n8n pairings: rc=%s processed=%s products=%d", rc, parsed.get("processed"), len(new_keys))
    if not ok:
        log.error("n8n pairings FAILED rc=%s stderr=%s", rc, (err or "")[-400:])
    return jsonify(result), (200 if ok else 502)


# --------------------------------------------------------------------------- #
# n8n → nightly upload of assigned supplier names (→ eshop `supplier` field)
# --------------------------------------------------------------------------- #
SUPPLIERS_STATE = os.path.join(OUT, "uploaded_suppliers.json")


def _load_uploaded_suppliers():
    """{code: supplier} already written back to the eshop — so the nightly job only
    sends new or changed assignments. Missing/corrupt → empty (everything is new).
    Always a dict (a stray array could repeat a code and break the summary invariant)."""
    try:
        with open(SUPPLIERS_STATE, encoding="utf-8") as f:
            d = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return d if isinstance(d, dict) else {}


def _save_uploaded_suppliers(d):
    tmp = SUPPLIERS_STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, SUPPLIERS_STATE)


def _supplier_summary(uploaded, assigns):
    """Totals for the n8n summary notification: assigned codes, how many are already
    written back (uploaded value still matches the current assignment), how many remain.
    A changed name counts as remaining (uploaded != current), matching new_supplier_keys."""
    valid = {c for c, s in assigns.items() if (c or "").strip() and (s or "").strip()}
    total = len(valid)
    up = sum(1 for c in valid if uploaded.get(c) == assigns.get(c))
    return {"total_assigned": total, "total_uploaded": up,
            "remaining": max(0, total - up), "review_url": PUBLIC_URL}


@app.route("/api/n8n/upload-suppliers", methods=["POST"])
def n8n_upload_suppliers():
    """Write newly assigned supplier names back to the eshop `supplier` field. Reads
    the local supplier assignments, builds code;pairCode;supplier for only the codes
    not yet uploaded (or whose name changed), runs the careful import, records what
    went up. Bearer-auth'd; dry_run=1 reaches the import without changing anything.
    Touches ONLY the supplier column — links/state/prices are left untouched."""
    token = _import_token()
    auth = request.headers.get("Authorization", "")
    expected = f"Bearer {token}".encode() if token else b""
    if not token or not hmac.compare_digest(auth.encode("latin-1", "ignore"), expected):
        log.warning("n8n suppliers: unauthorized call from %s", _client_ip())
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    dry = str(request.values.get("dry_run", "")).lower() in ("1", "true", "yes")
    assigns = _load_supplier_assign()
    uploaded = _load_uploaded_suppliers()
    new_codes = import_builder.new_supplier_keys(assigns, uploaded)
    products = [{"code": c, "supplier": assigns[c]} for c in new_codes]
    if not new_codes:
        log.info("n8n suppliers: 0 new assignments")
        return jsonify({"ok": True, "count": 0, "products": [],
                        **_supplier_summary(uploaded, assigns)}), 200

    rows = import_builder.supplier_rows({c: assigns[c] for c in new_codes}, CODE2PAIR)
    if not rows:
        log.warning("n8n suppliers: %d new codes but 0 import rows", len(new_codes))
        return jsonify({"ok": True, "count": 0, "products": products,
                        "message": "no import rows", "blocked": len(new_codes),
                        **_supplier_summary(uploaded, assigns)}), 200

    os.makedirs(OUT, exist_ok=True)
    out_fd, out_path = tempfile.mkstemp(prefix="import_suppliers_", suffix=".csv", dir=OUT)
    with os.fdopen(out_fd, "w", encoding="utf-8-sig", newline="") as f:
        from parovanie.writer import shoptet_writer
        w = shoptet_writer(f)
        w.writerow(import_builder.SUPPLIER_HEADER)
        # formula-injection guard at the sink (defense-in-depth alongside the endpoint
        # reject) — the supplier name is free text written into a CSV cell
        w.writerows([_csv_safe(c) for c in row] for row in rows)

    if not _import_lock.acquire(blocking=False):
        log.warning("n8n suppliers: another import already running")
        _safe_unlink(out_path)
        return jsonify({"ok": False, "error": "import already running"}), 409
    log.info("n8n suppliers: %d codes, %d rows, dry_run=%s", len(new_codes), len(rows), dry)
    try:
        rc, out, err = run_import(out_path, dry_run=dry, timeout=900)
        parsed = parse_import_log(out)
        ok = rc == 0
        if ok and not dry:                   # record only after a real success (inside the lock)
            for c in new_codes:
                uploaded[c] = (assigns[c] or "").strip()
            _save_uploaded_suppliers(uploaded)
    except subprocess.TimeoutExpired:
        log.error("n8n suppliers: subprocess timeout — killed import group")
        _safe_unlink(out_path)
        return jsonify({"ok": False, "error": "import timeout"}), 504
    finally:
        _import_lock.release()

    if ok:
        _safe_unlink(out_path)
    result = {"ok": ok, "exit_code": rc, "count": len(new_codes), "rows": len(rows),
              "dry_run": dry, "processed": parsed.get("processed"),
              "updated": parsed.get("updated"), "failed": parsed.get("failed"),
              "products": products, "stdout_tail": (out or "")[-800:],
              **_supplier_summary(uploaded, assigns)}
    log.info("n8n suppliers: rc=%s processed=%s codes=%d", rc, parsed.get("processed"), len(new_codes))
    if not ok:
        log.error("n8n suppliers FAILED rc=%s stderr=%s", rc, (err or "")[-400:])
    return jsonify(result), (200 if ok else 502)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("WEBREVIEW_PORT", "8801")),
            threaded=True)
