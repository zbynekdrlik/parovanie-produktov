"""Web na ručnú kontrolu párovania: vľavo náš produkt, vpravo dodávateľ,
fajka/krížik (matched) alebo ručný výber/URL (unmatched). Rozhodnutia sa
ukladajú do data/out/decisions.json.

Run: PYTHONPATH=src .venv/bin/python webreview/app.py   (počúva na 0.0.0.0:8799)
"""
from __future__ import annotations
import json
import os
import hashlib
import threading
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, request, send_from_directory

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "out")
DATA = os.path.join(OUT, "review_data.json")
DECISIONS = os.path.join(OUT, "decisions.json")
IMGCACHE = os.path.join(OUT, "imgcache")
os.makedirs(IMGCACHE, exist_ok=True)

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
app = Flask(__name__, static_folder="static", template_folder="templates")
_lock = threading.Lock()

with open(DATA, encoding="utf-8") as f:
    PRODUCTS = json.load(f)


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

    og = soup.find("meta", attrs={"property": "og:image"})
    if og:
        add(og.get("content"))
    # product-detail-scoped galleries only (avoids related/carousel leakage)
    for sel in [".p-detail img", ".product-detail img", "#product .images img",
                ".product-images img", ".images-container img",
                ".product-gallery img", "[itemprop='image']"]:
        for im in soup.select(sel):
            add(im.get("src") or im.get("data-src") or im.get("data-zoom-image")
                or im.get("data-image"))
    return imgs[:5]


@app.route("/")
def index():
    return send_from_directory("templates", "index.html")


@app.route("/favicon.ico")
def favicon():
    return ("", 204)


@app.route("/api/products")
def api_products():
    return jsonify({"products": PRODUCTS, "decisions": _load_decisions()})


@app.route("/api/images")
def api_images():
    url = request.args.get("url", "").strip()
    if not url.startswith("http"):
        return jsonify({"images": []})
    key = hashlib.sha1(url.encode()).hexdigest()
    cache = os.path.join(IMGCACHE, key + ".json")
    if os.path.exists(cache):
        with open(cache, encoding="utf-8") as f:
            return jsonify({"images": json.load(f)})
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=20)
        imgs = _extract_images(r.text, url) if r.ok else []
    except Exception:
        imgs = []
    with open(cache, "w", encoding="utf-8") as f:
        json.dump(imgs, f)
    return jsonify({"images": imgs})


@app.route("/api/decision", methods=["POST"])
def api_decision():
    body = request.get_json(force=True)
    idx = str(body.get("idx"))
    with _lock:
        d = _load_decisions()
        d[idx] = {"status": body.get("status"), "url": body.get("url", "")}
        _save_decisions(d)
    return jsonify({"ok": True})


@app.route("/static/<path:p>")
def static_files(p):
    return send_from_directory("static", p)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8801, threaded=True)
