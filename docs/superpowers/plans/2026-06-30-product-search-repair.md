# Product Search + Re-pair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Manager can search the whole catalog by name/code/supplier and fix (or first-time set) a product's supplier pairing using the existing candidate-pick + manual-URL panel.

**Architecture:** Extend the startup read of `data/products.csv` to also build an in-memory per-product search index (`CATALOG`). New `GET /api/search` filters it server-side. New `POST /api/search-pair` promotes a not-yet-reviewed product into `review_data.json` (so `link_rows` reaches it) and writes its decision. A new "Hľadať" tab reuses the existing `resolutionPanel`/`saveDecision`.

**Tech Stack:** Python 3 (Flask, stdlib only — `unicodedata`, `urllib.parse`), vanilla JS SPA, pytest + pytest-playwright.

## Global Constraints

- **Version:** bump `src/parovanie/__init__.py` `__version__` to `0.27.0-dev.2` as the FIRST commit (dev must stay > main 0.26.0). Shown in web footer via `/api/version`.
- **Encoding:** export read = cp1250; CSV import = UTF-8-BOM, `;`, CRLF. Not directly touched here but never break it.
- **Tests offline:** `PYTHONPATH=src .venv/bin/pytest --ignore=tests/e2e`; e2e is a separate job booting `webreview/app.py` against a fixture via `WEBREVIEW_OUT`/`WEBREVIEW_PRODUCTS`/`WEBREVIEW_PORT`.
- **No copied logic:** reuse `export_helpers.current_of`, `_csv_safe`, the atomic `_save_*`+`_lock` store pattern, `resolutionPanel`/`saveDecision`. 3-state classification only via `export_helpers`.
- **Security:** URL inputs must pass `^https?://` (else HTTP 400); codes/URLs into any CSV via `_csv_safe`. No secrets in code.
- **Durability:** `data/out/*` survives deploy (gitignored); promote appends to `review_data.json` atomically AND to in-memory `PRODUCTS`.
- **YAGNI:** no live supplier search, no fuzzy ranking, no price/stock editing.

---

### Task 1: Catalog search index (pure)

**Files:**
- Create: `src/parovanie/catalog_index.py`
- Test: `tests/test_catalog_index.py`
- Modify: `src/parovanie/__init__.py` (version bump, this task's first commit)

**Interfaces:**
- Produces: `normalize_text(s:str)->str`, `build_catalog_index(rows:Iterable[dict], review_keys:set|None=None)->dict[str,dict]`, `search_catalog(catalog:dict, q:str, limit:int=50)->list[dict]`. A catalog entry has keys `pairCode,name,supplier,variant_codes,image,name_norm,in_review`.

- [ ] **Step 1: Bump version (first commit of the feature)**

Edit `src/parovanie/__init__.py`: `__version__ = "0.27.0-dev.2"`. Commit:
```bash
git add src/parovanie/__init__.py
git commit -m "chore: bump 0.27.0-dev.2 (product search + re-pair)"
```

- [ ] **Step 2: Write failing tests**

`tests/test_catalog_index.py`:
```python
from parovanie.catalog_index import normalize_text, build_catalog_index, search_catalog


def _row(code, pair, name, supplier="WETLAND", img=""):
    return {"code": code, "pairCode": pair, "name": name, "supplier": supplier, "defaultImage": img}


def test_normalize_strips_diacritics_and_lowercases():
    assert normalize_text("Bundá ČIERNA") == "bunda cierna"
    assert normalize_text("") == ""


def test_build_groups_by_paircode_and_collects_codes():
    rows = [_row("4931/S", "512", "Mikina GARDE", img="a.jpg"),
            _row("4931/L", "512", "Mikina GARDE"),
            _row("60611/M", "425", "Bunda Tradition", supplier="GRUBE")]
    cat = build_catalog_index(rows, review_keys={"425"})
    assert set(cat) == {"512", "425"}
    assert cat["512"]["variant_codes"] == ["4931/S", "4931/L"]
    assert cat["512"]["image"] == "a.jpg"
    assert cat["512"]["in_review"] is False
    assert cat["425"]["in_review"] is True
    assert cat["425"]["supplier"] == "GRUBE"


def test_build_skips_rows_without_code_or_paircode():
    rows = [_row("", "512", "x"), _row("4931/S", "", "x")]
    assert build_catalog_index(rows) == {}


def test_search_by_name_accent_insensitive():
    cat = build_catalog_index([_row("4931/S", "512", "Mikina GARDE HART")])
    assert [e["pairCode"] for e in search_catalog(cat, "garde")] == ["512"]
    assert [e["pairCode"] for e in search_catalog(cat, "mikìna")] == []  # wrong accent on consonant still substring? ensure normalize
    assert search_catalog(cat, "garde hart")[0]["pairCode"] == "512"


def test_search_by_code_and_supplier():
    cat = build_catalog_index([_row("60611/M", "425", "Bunda", supplier="GRUBE")])
    assert search_catalog(cat, "60611")[0]["pairCode"] == "425"
    assert search_catalog(cat, "grube")[0]["pairCode"] == "425"


def test_search_empty_or_short_query_returns_empty():
    cat = build_catalog_index([_row("4931/S", "512", "Mikina")])
    assert search_catalog(cat, "") == []
    assert search_catalog(cat, "m") == []


def test_search_limit():
    rows = [_row(f"c{i}", str(i), "Spolocny nazov") for i in range(60)]
    cat = build_catalog_index(rows)
    assert len(search_catalog(cat, "spolocny", limit=50)) == 50
```

(Note: the `"mikìna"` line documents that an accented query still normalizes; replace its assertion with the actual normalized behavior — `normalize_text("mikìna")=="mikina"` IS a substring of `"mikina garde hart"`, so it WOULD match. Fix the test to assert a true non-match, e.g. `search_catalog(cat, "nohavice") == []`.)

- [ ] **Step 3: Run tests to verify they fail**

Run: `PYTHONPATH=src .venv/bin/pytest tests/test_catalog_index.py -q`
Expected: FAIL (module `parovanie.catalog_index` not found).

- [ ] **Step 4: Implement**

`src/parovanie/catalog_index.py`:
```python
"""Catalog-wide product search index (pure). Built once at app start from the
Shoptet export rows; grouped per product (pairCode). Search is accent-insensitive
substring over name / supplier / variant code. No live network, no fuzzy ranking."""
import unicodedata
from typing import Iterable


def normalize_text(s: str) -> str:
    """Lowercase + strip diacritics (NFKD) for accent-insensitive matching."""
    if not s:
        return ""
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()


def build_catalog_index(rows: Iterable[dict], review_keys=None) -> dict:
    """Group export variant rows into {pairCode: entry}. Each `row` needs at least
    code, pairCode, name, supplier, defaultImage. `review_keys` marks in_review.
    First row of a pairCode supplies name/supplier/image; codes accumulate."""
    review_keys = review_keys or set()
    out: dict = {}
    for r in rows:
        pc = (r.get("pairCode") or "").strip()
        code = (r.get("code") or "").strip()
        if not pc or not code:
            continue
        e = out.get(pc)
        if e is None:
            name = (r.get("name") or "").strip()
            e = out[pc] = {
                "pairCode": pc,
                "name": name,
                "supplier": (r.get("supplier") or "").strip(),
                "variant_codes": [],
                "image": (r.get("defaultImage") or "").strip(),
                "name_norm": normalize_text(name),
                "in_review": pc in review_keys,
            }
        if code not in e["variant_codes"]:
            e["variant_codes"].append(code)
    return out


def search_catalog(catalog: dict, q: str, limit: int = 50) -> list:
    """Up to `limit` product entries whose name (accent-insensitive), supplier, or
    any variant code contains `q`. Query shorter than 2 chars (normalized) -> []."""
    qn = normalize_text(q)
    if len(qn) < 2:
        return []
    results = []
    for e in catalog.values():
        if (qn in e["name_norm"]
                or qn in normalize_text(e["supplier"])
                or any(qn in normalize_text(c) for c in e["variant_codes"])):
            results.append(e)
            if len(results) >= limit:
                break
    return results
```

- [ ] **Step 5: Run tests (fix the `mikìna` test per the note) — expect PASS**

Run: `PYTHONPATH=src .venv/bin/pytest tests/test_catalog_index.py -q` → all green.
Run: `.venv/bin/ruff check src/parovanie/catalog_index.py tests/test_catalog_index.py` → clean.

- [ ] **Step 6: Commit**

```bash
git add src/parovanie/catalog_index.py tests/test_catalog_index.py
git commit -m "feat: catalog search index (pure, accent-insensitive substring)"
```

---

### Task 2: Supplier inference from URL (pure)

**Files:**
- Modify: `src/parovanie/catalog_index.py` (add `supplier_from_url`)
- Test: `tests/test_catalog_index.py` (add cases)

**Interfaces:**
- Consumes: `config.SUPPLIERS` (dict `name -> obj/dict` carrying `base_url`).
- Produces: `supplier_from_url(url:str, suppliers:dict)->str` (supplier key or "").

- [ ] **Step 1: Write failing tests**

Append to `tests/test_catalog_index.py`:
```python
from parovanie.catalog_index import supplier_from_url


class _Cfg:
    def __init__(self, base_url):
        self.base_url = base_url


_SUP = {
    "WETLAND": _Cfg("https://www.wetland.sk"),
    "BETALOV": _Cfg("https://www.huntingshop.eu"),
    "GRUBE": _Cfg("https://www.grube.sk"),
    "ODIMON": _Cfg("https://www.odimon.sk"),
}


def test_supplier_from_url_matches_host():
    assert supplier_from_url("https://www.wetland.sk/p/x", _SUP) == "WETLAND"
    assert supplier_from_url("https://wetland.sk/p/x", _SUP) == "WETLAND"
    assert supplier_from_url("https://www.huntingshop.eu/h?search=x", _SUP) == "BETALOV"


def test_supplier_from_url_grube_de_maps_to_grube():
    assert supplier_from_url("https://www.grube.de/p/x/154773/", _SUP) == "GRUBE"


def test_supplier_from_url_unknown_returns_empty():
    assert supplier_from_url("https://example.com/x", _SUP) == ""
    assert supplier_from_url("", _SUP) == ""
```

- [ ] **Step 2: Run to verify fail**

Run: `PYTHONPATH=src .venv/bin/pytest tests/test_catalog_index.py::test_supplier_from_url_matches_host -q` → FAIL (not defined).

- [ ] **Step 3: Implement**

Add to `src/parovanie/catalog_index.py`:
```python
from urllib.parse import urlparse


def _host(url: str) -> str:
    h = (urlparse(url or "").netloc or "").lower()
    return h[4:] if h.startswith("www.") else h


def supplier_from_url(url: str, suppliers: dict) -> str:
    """Infer supplier key from a pasted product URL's host vs SUPPLIERS base_url
    hosts. grube.de/grube.sk both -> GRUBE. Unknown host -> ''. Only used to set the
    `supplier` field so link_rows applies GRUBE .de normalization correctly."""
    host = _host(url)
    if not host:
        return ""
    if "grube.de" in host or "grube.sk" in host:
        return "GRUBE"
    for name, cfg in suppliers.items():
        base = getattr(cfg, "base_url", None)
        if base is None and isinstance(cfg, dict):
            base = cfg.get("base_url", "")
        bhost = _host(base or "")
        if bhost and (host == bhost or host.endswith("." + bhost)):
            return name
    return ""
```

- [ ] **Step 4: Run — PASS**

Run: `PYTHONPATH=src .venv/bin/pytest tests/test_catalog_index.py -q` → green; `ruff check` clean.

- [ ] **Step 5: Commit**

```bash
git add src/parovanie/catalog_index.py tests/test_catalog_index.py
git commit -m "feat: infer supplier from pasted URL host"
```

---

### Task 3: Promoted review-entry builder (pure)

**Files:**
- Modify: `src/parovanie/catalog_index.py` (add `build_promoted_entry`)
- Test: `tests/test_catalog_index.py`

**Interfaces:**
- Produces: `build_promoted_entry(catalog_entry:dict, current:dict, our_url, supplier:str, idx:int)->dict` — a review_data entry whose keys match `build_review_data` output: `idx,supplier,name,pairCode,variant_codes,our_images,ai_status,ai_chosen_url,ai_reason,candidates,our_url,key,current`.

- [ ] **Step 1: Write failing test**

Append to `tests/test_catalog_index.py`:
```python
from parovanie.catalog_index import build_promoted_entry


def test_build_promoted_entry_shape():
    ce = {"pairCode": "425", "name": "Bunda Tradition", "supplier": "GRUBE",
          "variant_codes": ["60611/L", "60611/M"], "image": "img.jpg"}
    cur = {"state": 1, "off": False, "vis": "visible", "avail": "", "price": "99,00", "std": "", "stock": "3"}
    e = build_promoted_entry(ce, cur, "https://www.forestshop.sk/x/", "GRUBE", 2600)
    assert e["key"] == "425" and e["pairCode"] == "425"
    assert e["variant_codes"] == ["60611/L", "60611/M"]
    assert e["our_images"] == ["img.jpg"]
    assert e["candidates"] == [] and e["ai_status"] == "unmatched"
    assert e["supplier"] == "GRUBE"
    assert e["our_url"] == "https://www.forestshop.sk/x/"
    assert e["current"] == cur and e["idx"] == 2600
    # supplier falls back to catalog supplier when inferred is empty
    e2 = build_promoted_entry(ce, cur, None, "", 1)
    assert e2["supplier"] == "GRUBE" and e2["our_url"] is None and e2["our_images"] == ["img.jpg"]
```

- [ ] **Step 2: Run — FAIL**

Run: `PYTHONPATH=src .venv/bin/pytest tests/test_catalog_index.py::test_build_promoted_entry_shape -q` → FAIL.

- [ ] **Step 3: Implement**

Add to `src/parovanie/catalog_index.py`:
```python
def build_promoted_entry(catalog_entry: dict, current: dict, our_url, supplier: str, idx: int) -> dict:
    """Minimal review_data entry for a catalog product paired for the first time via
    search. Shape mirrors build_review_data so link_rows + the UI card consume it.
    `supplier` is the URL-inferred key; falls back to the catalog row's supplier."""
    img = catalog_entry.get("image")
    return {
        "idx": idx,
        "supplier": supplier or catalog_entry.get("supplier", ""),
        "name": catalog_entry["name"],
        "pairCode": catalog_entry["pairCode"],
        "variant_codes": list(catalog_entry["variant_codes"]),
        "our_images": [img] if img else [],
        "ai_status": "unmatched",
        "ai_chosen_url": "",
        "ai_reason": "Ručne pridané cez vyhľadávanie.",
        "candidates": [],
        "our_url": our_url,
        "key": catalog_entry["pairCode"],
        "current": current,
    }
```

- [ ] **Step 4: Run — PASS**; `ruff check` clean.

- [ ] **Step 5: Commit**

```bash
git add src/parovanie/catalog_index.py tests/test_catalog_index.py
git commit -m "feat: build promoted review-data entry for first-time catalog pairing"
```

---

### Task 4: Backend wiring — CATALOG at startup, /api/search, /api/search-pair

**Files:**
- Modify: `webreview/app.py`
- Test: `tests/test_webreview_search.py` (new)

**Interfaces:**
- Consumes: `catalog_index.build_catalog_index/search_catalog/supplier_from_url/build_promoted_entry`, `export_helpers.current_of`, existing `_load_decisions/_save_decisions`, `_lock`, `PRODUCTS`, the products.csv path (`WEBREVIEW_PRODUCTS`).
- Produces endpoints: `GET /api/search?q=` → `{"results":[{pairCode,name,supplier,codes,image,in_review,our_url,idx}]}`; `POST /api/search-pair {pairCode,url}` → `{ok,promoted:bool,key}`.

**Read these first (match existing style):** the startup `CODE2PAIR` loop (app.py ~lines 53-69), an existing `_load_*`/`_save_*` atomic store + `_lock` usage, the `/api/order-pair` route (URL `^https?://` guard pattern + `_csv_safe`), and `scripts/add_supplier_review_data.py` entry-build for reference.

- [ ] **Step 1: Build CATALOG at startup (one pass with CODE2PAIR)**

Replace the inline `CODE2PAIR` build so the products.csv `DictReader` pass also keeps each full row needed for `build_catalog_index`. After `PRODUCTS` is loaded, add:
```python
from parovanie.catalog_index import build_catalog_index, search_catalog, supplier_from_url, build_promoted_entry
from parovanie import config

def _load_catalog_and_code2pair(path):
    """Single cp1250 pass over the Shoptet export: returns (code2pair, catalog)."""
    code2pair, rows = {}, []
    try:
        with open(path, encoding="cp1250", newline="") as f:
            for r in csv.DictReader(f, delimiter=";"):
                code = (r.get("code") or "").strip()
                pair = (r.get("pairCode") or "").strip()
                if code:
                    code2pair[code] = pair
                rows.append(r)
    except FileNotFoundError:
        return {}, {}
    review_keys = {p.get("key") for p in PRODUCTS}
    return code2pair, build_catalog_index(rows, review_keys)

CODE2PAIR, CATALOG = _load_catalog_and_code2pair(PRODUCTS_CSV)  # PRODUCTS_CSV = existing WEBREVIEW_PRODUCTS path
```
(Keep the old behavior if products.csv missing → both empty; the app already tolerates that.)

- [ ] **Step 2: Write failing endpoint tests**

`tests/test_webreview_search.py` (mirror the existing webreview test setup — monkeypatch store paths + CATALOG/PRODUCTS):
```python
import json, importlib
import pytest


@pytest.fixture
def app(tmp_path, monkeypatch):
    import webreview.app as webapp
    importlib.reload(webapp)
    # seed an in-memory catalog + empty review set
    webapp.CATALOG = webapp.build_catalog_index([
        {"code": "60611/L", "pairCode": "425", "name": "Bunda Tradition", "supplier": "GRUBE", "defaultImage": "i.jpg"},
        {"code": "4931/S", "pairCode": "512", "name": "Mikina GARDE", "supplier": "WETLAND", "defaultImage": ""},
    ], review_keys=set())
    webapp.PRODUCTS = []
    webapp.CODE2PAIR = {"60611/L": "425", "4931/S": "512"}
    monkeypatch.setattr(webapp, "DECISIONS", str(tmp_path / "decisions.json"))
    monkeypatch.setattr(webapp, "DATA", str(tmp_path / "review_data.json"))
    json.dump([], open(webapp.DATA, "w"))
    return webapp.app.test_client(), webapp


def test_search_endpoint_filters(app):
    c, _ = app
    r = c.get("/api/search?q=tradition").get_json()
    assert [x["pairCode"] for x in r["results"]] == ["425"]
    assert c.get("/api/search?q=").get_json()["results"] == []


def test_search_pair_promotes_and_writes_decision(app):
    c, webapp = app
    r = c.post("/api/search-pair", json={"pairCode": "425", "url": "https://www.grube.de/p/x/154773/"})
    assert r.status_code == 200 and r.get_json()["promoted"] is True
    # review_data now has the promoted product
    rd = json.load(open(webapp.DATA))
    e = [p for p in rd if p["key"] == "425"][0]
    assert e["supplier"] == "GRUBE" and e["variant_codes"] == ["60611/L"]
    # decision written
    dec = json.load(open(webapp.DECISIONS))
    assert dec["425"]["status"] == "manual" and dec["425"]["url"].startswith("https://www.grube.de/")
    # in-memory PRODUCTS appended (no restart needed)
    assert any(p["key"] == "425" for p in webapp.PRODUCTS)


def test_search_pair_rejects_bad_url(app):
    c, _ = app
    assert c.post("/api/search-pair", json={"pairCode": "425", "url": "javascript:x"}).status_code == 400


def test_search_pair_unknown_paircode_404(app):
    c, _ = app
    assert c.post("/api/search-pair", json={"pairCode": "999", "url": "https://x.sk/"}).status_code == 404
```

- [ ] **Step 3: Run — FAIL** (endpoints missing).

Run: `PYTHONPATH=src .venv/bin/pytest tests/test_webreview_search.py -q`

- [ ] **Step 4: Implement endpoints**

Add to `webreview/app.py` (use existing `_lock`, `_save_decisions`/`_load_decisions`, and an atomic review_data save mirroring the other `_save_*`):
```python
import re
_HTTP = re.compile(r"^https?://", re.I)


def _save_products(products):
    """Atomic write of review_data.json (tmp + os.replace)."""
    tmp = DATA + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(products, f, ensure_ascii=False)
    os.replace(tmp, DATA)


def _current_for_paircode(pair):
    """Build the `current` snapshot for a catalog product by scanning the export
    for its variant rows (rare action -> acceptable one-off scan). Empty -> {}."""
    rows = []
    try:
        with open(PRODUCTS_CSV, encoding="cp1250", newline="") as f:
            for r in csv.DictReader(f, delimiter=";"):
                if (r.get("pairCode") or "").strip() == pair:
                    rows.append(r)
    except FileNotFoundError:
        return {}
    from parovanie.export_helpers import current_of
    return current_of(rows) if rows else {}


def _search_result(e):
    return {"pairCode": e["pairCode"], "name": e["name"], "supplier": e["supplier"],
            "codes": e["variant_codes"], "image": e["image"], "in_review": e["in_review"],
            "idx": next((p["idx"] for p in PRODUCTS if p.get("key") == e["pairCode"]), None)}


@app.route("/api/search")
def api_search():
    q = request.args.get("q", "")
    return jsonify({"results": [_search_result(e) for e in search_catalog(CATALOG, q)]})


@app.route("/api/search-pair", methods=["POST"])
def api_search_pair():
    body = request.get_json(silent=True) or {}
    pair = (body.get("pairCode") or "").strip()
    url = (body.get("url") or "").strip()
    if not _HTTP.match(url):
        return jsonify({"error": "bad url"}), 400
    ce = CATALOG.get(pair)
    if not ce:
        return jsonify({"error": "unknown pairCode"}), 404
    with _lock:
        promoted = False
        if not any(p.get("key") == pair for p in PRODUCTS):
            supplier = supplier_from_url(url, config.SUPPLIERS)
            cur = _current_for_paircode(pair)
            our_url = _our_url_for_paircode(pair)  # see Step 5; may be None
            entry = build_promoted_entry(ce, cur, our_url, supplier, len(PRODUCTS))
            PRODUCTS.append(entry)
            _save_products(PRODUCTS)
            promoted = True
        dec = _load_decisions()
        dec[pair] = {"status": "manual", "url": url}
        _save_decisions(dec)
    return jsonify({"ok": True, "promoted": promoted, "key": pair})
```

- [ ] **Step 5: our_url lazy resolver (marketing XML, best-effort)**

Add `_our_url_for_paircode(pair)`: if `data/out/marketing.xml` exists, lazily build/cache a `{code: ORIG_URL}` map via `scripts/url_from_marketing_xml.build_code2url` (import the pure function) and return the URL for the product's first variant code; else `None`. Guard the import so a missing marketing.xml never 500s. Cache the map in a module global so it's built once.
```python
_CODE2URL = None
def _our_url_for_paircode(pair):
    global _CODE2URL
    ce = CATALOG.get(pair)
    if not ce or not ce["variant_codes"]:
        return None
    if _CODE2URL is None:
        try:
            from url_from_marketing_xml import build_code2url  # scripts/ on path
            mx = os.path.join(OUT, "marketing.xml")
            _CODE2URL = build_code2url(mx) if os.path.exists(mx) else {}
        except Exception:
            _CODE2URL = {}
    for c in ce["variant_codes"]:
        if c in _CODE2URL:
            return _CODE2URL[c]
    return None
```
(If `scripts/` isn't importable as a module, inline a tiny exact-`code`→`ORIG_URL` lxml scan instead — keep it best-effort/None on any failure. Confirm `build_code2url`'s real signature before wiring.)

- [ ] **Step 6: Run — PASS**

Run: `PYTHONPATH=src .venv/bin/pytest tests/test_webreview_search.py -q` → green.
Run full offline suite: `PYTHONPATH=src .venv/bin/pytest --ignore=tests/e2e -q` → green. `ruff check` clean.

- [ ] **Step 7: Commit**

```bash
git add webreview/app.py tests/test_webreview_search.py
git commit -m "feat: /api/search + /api/search-pair (catalog index, promote-on-pair)"
```

---

### Task 5: Frontend — "Hľadať / opraviť" tab

**Files:**
- Modify: `webreview/static/app.js`, `webreview/static/style.css`, `webreview/templates/index.html`

**Interfaces:**
- Consumes: `GET /api/search`, `POST /api/search-pair`, existing `resolutionPanel(p)` / `saveDecision(p,status,url)` and in-memory `PRODUCTS` lookup by `key`.

**Read first:** how the two existing tabs switch, `renderOrderRow` in-place re-render, `resolutionPanel`/`saveDecision`, the documented ordered/waiting per-row pattern in `.claude/skills/webreview`.

- [ ] **Step 1: Add the tab + search UI**

In `index.html` add a third tab button `📋`→ add `🔎 Hľadať / opraviť` and a `<section id="tab-search">` with `<input id="searchBox">` + `<div id="searchResults">`. Bump `?v=N` on BOTH `style.css` and `app.js`.

- [ ] **Step 2: JS — debounced search + render results**

In `app.js` add:
```javascript
let SEARCH_T = null;
function initSearch(){
  const box = document.getElementById('searchBox');
  box.addEventListener('input', () => {
    clearTimeout(SEARCH_T);
    SEARCH_T = setTimeout(() => runSearch(box.value), 250);
  });
}
async function runSearch(q){
  const out = document.getElementById('searchResults');
  if ((q||'').trim().length < 2){ out.innerHTML=''; return; }
  const r = await (await fetch('/api/search?q='+encodeURIComponent(q))).json();
  out.innerHTML = '';
  r.results.forEach(res => out.appendChild(renderSearchRow(res)));
}
```
`renderSearchRow(res)` builds a compact row (image, name, supplier, codes, badge `v appke`/`nenapárované`) with a click handler that:
- if `res.in_review` and a matching `PRODUCTS` entry exists (lookup by `key===res.pairCode`) → call existing `resolutionPanel(product)` inline (candidates + manual URL);
- else → render a manual-only panel: an input + "Uložiť odkaz" → `POST /api/search-pair {pairCode:res.pairCode, url}`; on success flip the row badge to `napárované ✓` and show the URL (in-place, no full re-render).

Use `.textContent`/`el()` helpers (never innerHTML with data) — mirror `renderOrderRow`. Console must stay clean.

- [ ] **Step 3: Wire tab switching + init**

Hook the new tab into the existing tab-switch function; call `initSearch()` once on load. `style.css`: add `#tab-search`, `.search-row`, badges (mirror `.toorder-row`/`.to-grube`).

- [ ] **Step 4: Manual verify locally (no commit yet)**

`systemctl --user restart parovanie-web`; the e2e in Task 6 is the real gate. Just confirm the page loads.

- [ ] **Step 5: Commit**

```bash
git add webreview/static/app.js webreview/static/style.css webreview/templates/index.html
git commit -m "feat: Hľadať/opraviť tab — catalog search + inline re-pair"
```

---

### Task 6: E2E (Playwright) — search → pair → persist, clean console

**Files:**
- Create: `tests/e2e/test_search_repair.py`

**Read first:** an existing `tests/e2e/test_*.py` for the fixture-server boot (`WEBREVIEW_OUT`/`WEBREVIEW_PRODUCTS`/`WEBREVIEW_PORT`) + the console-error assertion pattern. The fixture server is session-scoped/shared — the test MUST clean up after itself (revert any decision it writes).

- [ ] **Step 1: Write the e2e test**

`tests/e2e/test_search_repair.py`: collect console errors+warnings; open the page; click the `🔎 Hľadať / opraviť` tab; type a query that matches a fixture catalog product; assert ≥1 result row renders; click a result; for a not-in-review result, fill the manual URL with `https://www.example-supplier.sk/p/x`, click save; reload; re-run the same search; assert the row now shows the `napárované` badge (decision persisted). Final assertions: `expect(consoleMessages).toEqual([])`. Cleanup: delete the test's decision from the store (or write the e2e fixture so it targets a throwaway pairCode and reset it at test end).

The fixture must include a `products.csv` (so `CATALOG` is non-empty) and a `review_data.json` — extend the existing e2e fixture inputs if needed; document the catalog rows used.

- [ ] **Step 2: Run e2e**

Run: `PYTHONPATH=src .venv/bin/pytest tests/e2e/test_search_repair.py -q` (install chromium if needed: `.venv/bin/python -m playwright install chromium`). Expect PASS, clean console.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_search_repair.py tests/fixtures/  # + any fixture additions
git commit -m "test(e2e): search + re-pair flow, persistence, clean console"
```

---

### Task 7: Playbook + full suite + version sanity

**Files:**
- Modify: `.claude/skills/webreview/SKILL.md` (document the search tab + CATALOG index + promote-on-pair), possibly `CLAUDE.md` router if a new area emerges (it doesn't — webreview covers it).

- [ ] **Step 1: Run the `playbook-review` flow** — add a short section to the webreview skill: "Vyhľadávanie = `CATALOG` index z `products.csv` pri štarte (`build_catalog_index`), `/api/search` server-side filter, `/api/search-pair` povýši nenapárovaný produkt do `review_data.json` (reuse `build_promoted_entry`) → `link_rows` ho odtiaľ emituje. Dodávateľ z domény URL."

- [ ] **Step 2: Full offline suite + e2e + ruff green**

```bash
PYTHONPATH=src .venv/bin/pytest --ignore=tests/e2e -q
PYTHONPATH=src .venv/bin/pytest tests/e2e -q
.venv/bin/ruff check .
```

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/webreview/SKILL.md
git commit -m "docs(playbook): webreview search + promote-on-pair"
```

---

## Notes for the executor

- After all tasks: ONE push `git push origin dev`, ONE PR dev→main, drive CI green, auto-merge (no manual marker), then deploy = `systemctl --user restart parovanie-web` + Playwright verify the new tab live + `/api/version` shows `0.27.0-dev.2`, stores intact before/after.
- `current_of`, `build_code2url`, `_save_*` signatures: confirm against the real files before wiring (the plan's calls match the documented shapes but verify).
- Search results are unranked (first-N substring matches) — acceptable per spec YAGNI; if a common term floods >50, the manager refines the query.
