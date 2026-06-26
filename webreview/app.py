"""Web na ručnú kontrolu párovania: vľavo náš produkt, vpravo dodávateľ,
fajka/krížik (matched) alebo ručný výber/URL (unmatched). Rozhodnutia sa
ukladajú do data/out/decisions.json.

Run: PYTHONPATH=src .venv/bin/python webreview/app.py   (počúva na 0.0.0.0:8799)
"""
from __future__ import annotations
import csv
import io
import json
import logging
import os
import re
import hashlib
import threading
import zipfile
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, request, send_from_directory, Response

from parovanie import __version__, import_builder

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

# code -> pairCode (Shoptet import needs BOTH code and pairCode present)
SRC = os.environ.get("WEBREVIEW_PRODUCTS") or os.path.join(ROOT, "data", "products.csv")
CODE2PAIR = {}
if os.path.exists(SRC):
    csv.field_size_limit(10**9)
    with open(SRC, encoding="cp1250", errors="replace") as _f:
        for _row in csv.DictReader(_f, delimiter=";"):
            _c = (_row.get("code") or "").strip()
            if _c:
                CODE2PAIR[_c] = (_row.get("pairCode") or "").strip()


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


@app.route("/api/products")
def api_products():
    return jsonify({"products": PRODUCTS, "decisions": _load_decisions()})


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
            d[key] = {"status": status, "url": body.get("url", "")}
        _save_decisions(d)
    log.info("decision key=%s status=%s url=%s", key, status, body.get("url", ""))
    return jsonify({"ok": True})


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
    files = [
        ("import_links.csv", import_builder.LINK_HEADER,
         import_builder.link_rows(PRODUCTS, dec, CODE2PAIR)),
        ("import_states.csv", import_builder.STATE_HEADER,
         import_builder.state_rows(PRODUCTS, dec, CODE2PAIR)),
    ]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, header, rows in files:
            s = io.StringIO()
            w = csv.writer(s, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
            w.writerow(header)
            w.writerows(rows)
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


@app.route("/static/<path:p>")
def static_files(p):
    return send_from_directory("static", p)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("WEBREVIEW_PORT", "8801")),
            threaded=True)
