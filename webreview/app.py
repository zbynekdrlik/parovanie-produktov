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

from parovanie import __version__, import_builder
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
            d[key] = {"status": status, "url": body.get("url", "").strip()}
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


# --------------------------------------------------------------------------- #
# n8n → Shoptet auto-import (vypredané → skladom)
# --------------------------------------------------------------------------- #
def _import_token():
    """Bearer token for the import endpoint, from the gitignored creds file
    (key N8N_IMPORT_TOKEN). None if not configured → endpoint refuses all calls."""
    try:
        with open(CRED_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("N8N_IMPORT_TOKEN=") and "=" in line:
                    v = line.split("=", 1)[1].strip().strip("'\"")
                    return v or None
    except FileNotFoundError:
        return None
    return None


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
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_uploaded(d):
    tmp = PAIRINGS_STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PAIRINGS_STATE)


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
        return jsonify({"ok": True, "count": 0, "products": []}), 200

    rows = import_builder.link_rows(PRODUCTS, {k: dec[k] for k in new_keys}, CODE2PAIR)
    if not rows:
        log.warning("n8n pairings: %d new keys but 0 import rows (codes missing)", len(new_keys))
        return jsonify({"ok": True, "count": 0, "products": products,
                        "message": "no import rows"}), 200

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
              "products": products, "stdout_tail": (out or "")[-800:]}
    log.info("n8n pairings: rc=%s processed=%s products=%d", rc, parsed.get("processed"), len(new_keys))
    if not ok:
        log.error("n8n pairings FAILED rc=%s stderr=%s", rc, (err or "")[-400:])
    return jsonify(result), (200 if ok else 502)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("WEBREVIEW_PORT", "8801")),
            threaded=True)
