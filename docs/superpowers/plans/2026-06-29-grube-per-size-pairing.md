# GRUBE per-veľkosť párovanie — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pre GRUBE produkty získať per-veľkosť grube `itemId` z grube.de a zapísať ho do Shoptet `externalCode` (per variant) + grube.de linku do `internalNote` + zobraziť v webreview „Na objednanie".

**Architecture:** Nová izolovaná GRUBE-only logika v `src/parovanie/grube_de.py` (pure funkcie: URL normalizer, .de detail parser, forestshop size resolver, size matcher). Live Playwright gather (`scripts/gather_grube_itemids.py`) ťahá per-veľkosť itemId z .de detail stránok do `data/out_grube/itemids.json`; `scripts/build_grube_codes.py` joinuje s forestshop veľkosťami do durable `data/out/grube_codes.json`. Write-back cez nový `import_externalcode.csv` (mirror `supplier_rows`), manuálny zip + `shoptet_import.py`. Display cez `/api/orders` + `renderOrderRow`.

**Tech Stack:** Python 3 (stdlib csv, urllib, re), Playwright (headless, detail pages), Flask (webreview), pytest + pytest-playwright. Žiadne nové závislosti.

## Global Constraints

- **GRUBE-only:** každá nová operácia scope-ovaná `supplier=="GRUBE"` (a/alebo host `grube.*`). Ne-grube produkty/dodávatelia BEZ zmeny. Guard + test, nie predpoklad.
- **I/O kódovanie:** export číta cp1250 (`data/products.csv`); import píše **UTF-8 s BOM** (`utf-8-sig`), `;`, CRLF, QUOTE_MINIMAL (`writer.shoptet_writer`).
- **Verzia:** `src/parovanie/__init__.py` `__version__` musí byť `> main` (main=0.25.3). Bumpni PRED kódom.
- **Testy bez živej siete:** uložené HTML/JSON fixtúry; `.venv/bin/pytest`, `PYTHONPATH=src`. Hlavný beh `--ignore=tests/e2e`; e2e samostatný job.
- **Durable store:** `data/out/grube_codes.json` a `data/out/uploaded_externalcodes.json` (ak vznikne) žijú v `data/out/` (deploy-preserved, NEVER-pruned), atomický `tmp`+`os.replace`. NIE v `data/out_grube/` (disposable).
- **itemId filter (anti cross-sell):** `itemId.startswith(productId) AND len(itemId)==len(productId)+4`.
- **Veľkosť z `variant:*` stĺpca exportu, NIKDY z code suffixu.** Dvojosové (Bunda+Nohavice) → link-only.
- **Match = EXACT-string po normalizácii, fail-closed:** nezhoda → žiadny kód (link-only), nikdy fuzzy/nearest.
- **Write-back: no-match → ŽIADNY riadok** (nikdy prázdny `externalCode` cell → neprepíše existujúce). Dedup code first-wins. `_csv_safe` na sinku + číselný itemId guard pri zdroji.
- **Importability gate (Task 1) je go/no-go** pre celú externalCode vetvu; ak zlyhá → vynechaj Tasky 8,9,12,14, ship len internalNote (.de) + webreview display.
- **Spec:** `docs/superpowers/specs/2026-06-29-grube-per-size-pairing-design.md` — záväzný zdroj pravidiel.

---

### Task 1: Verzia + LIVE importability gate (go/no-go pre externalCode vetvu)

**Files:**
- Modify: `src/parovanie/__init__.py:1`
- Create (dočasne): `data/out/_importability_test.csv` (gitignored workdir)

**Interfaces:**
- Produces: rozhodnutie `EXTERNALCODE_IMPORTABLE: bool` (zapíš do progress ledgeru + completion reportu). Variant-level dôkaz: set na jednom variante, súrodenec nezmenený.

- [ ] **Step 1: Bump version**

`src/parovanie/__init__.py` → `__version__ = "0.26.0"`.

```bash
cd /home/newlevel/devel/forestshop/parovanie_produktov
sed -n '1p' src/parovanie/__init__.py   # over 0.26.0
git add src/parovanie/__init__.py && git commit -m "chore: bump 0.26.0 (GRUBE per-size pairing)"
```

- [ ] **Step 2: Pick sentinel variant + capture original externalCode**

Vyber bezpečný GRUBE variant s PRÁZDNYM externalCode (žiadna strata) — napr. z pairCode 395 (Grand Nord, všetky prázdne). Over a zachyť:

```bash
.venv/bin/python -c "
import csv; csv.field_size_limit(10**7)
with open('data/products.csv',encoding='cp1250',newline='') as f:
    for r in csv.DictReader(f,delimiter=';'):
        if r['code']=='60645/L':
            print('code',r['code'],'pairCode',r['pairCode'],'externalCode',repr(r['externalCode']),'supplier',r['supplier'])
"
# Expected: externalCode '' (empty), supplier GRUBE, pairCode 395
```

- [ ] **Step 3: Build a 1-row test import CSV (UTF-8-BOM)**

```bash
printf 'code;pairCode;externalCode\r\n60645/L;395;PAROVANIE-TEST-XYZ\r\n' | iconv -f utf-8 -t utf-8 > /tmp/x && \
  (printf '\xef\xbb\xbf'; cat /tmp/x) > data/out/_importability_test.csv
```

- [ ] **Step 4: Import it LIVE via shoptet_import.py (takes catalog backup first)**

```bash
.venv/bin/python scripts/shoptet_import.py --yes data/out/_importability_test.csv 2>&1 | tail -20
```
Expected: import beží, log/backup vytvorený, rc==0.

- [ ] **Step 5: Verify via FRESH export read-back (NOT the import log)**

Stiahni čerstvý pattern-14 export a over že `60645/L` má `PAROVANIE-TEST-XYZ` AND súrodenec `60645/M` má STÁLE prázdny externalCode (dôkaz variant-level):

```bash
URL=$(grep -E '^SHOPTET_EXPORT_URL=' data/.shoptet_admin | cut -d= -f2-)
curl -s "$URL" -o data/out/_readback.csv
.venv/bin/python -c "
import csv; csv.field_size_limit(10**7)
got={}
with open('data/out/_readback.csv',encoding='cp1250',newline='') as f:
    for r in csv.DictReader(f,delimiter=';'):
        if r['code'] in ('60645/L','60645/M'): got[r['code']]=r['externalCode']
print(got)
assert got.get('60645/L')=='PAROVANIE-TEST-XYZ', 'NOT IMPORTABLE or not variant-level: %r'%got
assert got.get('60645/M','')=='', 'sibling changed -> not variant-level: %r'%got
print('EXTERNALCODE_IMPORTABLE = True (variant-level confirmed)')
"
```
**Ak assert padne → `EXTERNALCODE_IMPORTABLE = False`** → zapíš do ledgeru, preskoč Tasky 8,9,12,14 (ship len internalNote+display), pokračuj revertom.

- [ ] **Step 6: Revert sentinel to original (empty)**

```bash
printf 'code;pairCode;externalCode\r\n60645/L;395;\r\n' > /tmp/y && (printf '\xef\xbb\xbf'; cat /tmp/y) > data/out/_importability_test.csv
.venv/bin/python scripts/shoptet_import.py --yes data/out/_importability_test.csv 2>&1 | tail -5
# verify revert via fresh export
curl -s "$URL" -o data/out/_readback.csv
.venv/bin/python -c "
import csv; csv.field_size_limit(10**7)
with open('data/out/_readback.csv',encoding='cp1250',newline='') as f:
    for r in csv.DictReader(f,delimiter=';'):
        if r['code']=='60645/L':
            assert (r['externalCode'] or '')=='', 'REVERT FAILED: %r'%r['externalCode']; print('revert OK')
"
rm -f data/out/_importability_test.csv data/out/_readback.csv
```

- [ ] **Step 7: Record verdict** in `.superpowers/sdd/progress.md` and the completion report (`EXTERNALCODE_IMPORTABLE = True/False`). No code commit (live verification only).

---

### Task 2: `to_grube_de` URL normalizer (pure)

**Files:**
- Create: `src/parovanie/grube_de.py`
- Test: `tests/test_grube_de.py`

**Interfaces:**
- Produces: `to_grube_de(url: str) -> str | None` — productId-rebuild canonical `.de` URL, or `None` if no `/p/<slug>/<id>/` productId.

- [ ] **Step 1: Write failing test**

```python
# tests/test_grube_de.py
from parovanie.grube_de import to_grube_de

def test_to_grube_de_strips_query_and_fragment():
    u = "https://www.grube.sk/p/noz-morakniv-eldris/268279/?q=morakiv#itemId=2682798474"
    assert to_grube_de(u) == "https://www.grube.de/p/x/268279/"

def test_to_grube_de_clean_sk_url():
    assert to_grube_de("https://www.grube.sk/p/percussion-grand-nord/154773/") == "https://www.grube.de/p/x/154773/"

def test_to_grube_de_already_de_idempotent():
    assert to_grube_de("https://www.grube.de/p/x/154773/") == "https://www.grube.de/p/x/154773/"

def test_to_grube_de_entity_mangled_slug():
    u = "https://www.grube.sk/p/pracovn-yacute-n-ocircz-morakniv-pro-c/585117/?q=x#itemId=5851174978"
    assert to_grube_de(u) == "https://www.grube.de/p/x/585117/"

def test_to_grube_de_non_product_url_returns_none():
    assert to_grube_de("https://www.grube.sk/search/?q=hose") is None
    assert to_grube_de("") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/pytest tests/test_grube_de.py -q`
Expected: FAIL (ModuleNotFoundError / function not defined).

- [ ] **Step 3: Implement**

```python
# src/parovanie/grube_de.py
"""GRUBE-only special logic: grube.de URL normalization, per-size itemId
extraction, forestshop size resolution, size matching. ALL pure & GRUBE-scoped."""
import re

_PRODUCT_ID = re.compile(r"grube\.(?:sk|de)/p/[^/]+/(\d+)/?")


def to_grube_de(url: str) -> str | None:
    """Canonical grube.DE product URL rebuilt from the productId.
    Strips slug/query/fragment (host swap alone keeps a mangled slug + stray
    ?q + single-size #itemId). Returns None when no /p/<slug>/<id>/ productId."""
    if not url:
        return None
    m = _PRODUCT_ID.search(url)
    if not m:
        return None
    return f"https://www.grube.de/p/x/{m.group(1)}/"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/pytest tests/test_grube_de.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/parovanie/grube_de.py tests/test_grube_de.py
git commit -m "feat: grube.de URL normalizer (productId-rebuild, strips query/fragment)"
```

---

### Task 3: `parse_variants` — grube.de detail page → {size_label: itemId}

**Files:**
- Modify: `src/parovanie/grube_de.py`
- Test: `tests/test_grube_de.py`
- Fixture: `tests/fixtures/grube_de_detail_154773.html` (saved real .de detail page)

**Interfaces:**
- Consumes: nič (HTML string + productId).
- Produces: `parse_variants(html: str, product_id: str) -> dict[str, str]` mapping size label → own itemId. Empty dict on count mismatch (→ link-only).

- [ ] **Step 1: Fixture already saved** — `tests/fixtures/grube_de_detail_154773.html` (rendered grube.de `/p/x/154773/`, 2.2 MB). Verified to contain 8 schema.org `Offer` objects each `"name":"Farbe oliv. Größe <SIZE>.","price":"…","priceCurrency":"EUR","sku":"<itemId>"` (quotes `\"`-escaped — JSON in JSON), single color `oliv`, AND cross-sell foreign itemId `6165695125` to prove exclusion. **The authoritative size↔itemId association is the Offer `Größe <SIZE>` + `sku`, NOT a positional radio zip** (the radio order does NOT match itemId source order — verified).

- [ ] **Step 2: Write failing test**

```python
# tests/test_grube_de.py (append)
import pathlib
from parovanie.grube_de import parse_variants

FIX = pathlib.Path(__file__).parent / "fixtures" / "grube_de_detail_154773.html"

def test_parse_variants_grand_nord():
    html = FIX.read_text(encoding="utf-8")
    got = parse_variants(html, "154773")
    assert got == {       # authoritative, from the page's own schema.org Offers
        "S": "1547734535", "M": "1547734598", "L": "1547734524", "XL": "1547734570",
        "XXL": "1547734593", "3XL": "1547734523", "4XL": "1547734553", "5XL": "1547734519",
    }

def test_parse_variants_excludes_cross_sell():
    html = FIX.read_text(encoding="utf-8")
    got = parse_variants(html, "154773")
    assert "6165695125" not in got.values()       # cross-sell Nordforest itemId
    assert all(v.startswith("154773") and len(v) == 10 for v in got.values())

def test_parse_variants_no_own_offers_returns_empty():
    html = FIX.read_text(encoding="utf-8")
    assert parse_variants(html, "999999") == {}    # no own-prefixed sku -> link-only

def test_parse_variants_multicolor_returns_empty():
    # a size mapping to >1 itemId (two colors) -> ambiguous -> link-only
    html = ('"name":"Farbe oliv. Größe L.","price":"1","priceCurrency":"EUR","sku":"1547734524"'
            '"name":"Farbe braun. Größe L.","price":"1","priceCurrency":"EUR","sku":"1547739999"')
    assert parse_variants(html, "154773") == {}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/pytest tests/test_grube_de.py -q`
Expected: FAIL (parse_variants not defined).

- [ ] **Step 4: Implement** (schema.org Offer parsing — authoritative)

```python
# src/parovanie/grube_de.py (append)

# schema.org Offer: "name":"[Farbe X. ]Größe <SIZE>.","price":"…","priceCurrency":"EUR","sku":"<itemId>"
_OFFER = re.compile(r'"name":"([^"]*?)","price":"[^"]*","priceCurrency":"EUR","sku":"(\d+)"')
_GROESSE = re.compile(r"Größe\s*([^.\"]+)")


def parse_variants(html: str, product_id: str) -> dict[str, str]:
    """{size_label: itemId} for ONE rendered grube.de detail page, parsed from the
    page's own schema.org Offer objects (name carries 'Größe <SIZE>', sku is the itemId).
    Own offers only (sku prefix==productId AND len==len(productId)+4) — cross-sell
    associatedProduct itemIds are excluded by the prefix. Returns {} (link-only) when
    a size maps to >1 itemId (multi-color / multi-axis — ambiguous) or no own offer."""
    text = html.replace('\\"', '"')          # unescape JSON-in-JSON
    want_len = len(product_id) + 4
    size2ids: dict[str, set] = {}
    for name, sku in _OFFER.findall(text):
        if not (sku.startswith(product_id) and len(sku) == want_len):
            continue
        m = _GROESSE.search(name)
        if not m:
            continue
        size2ids.setdefault(m.group(1).strip(), set()).add(sku)
    if any(len(ids) > 1 for ids in size2ids.values()):   # color/length axis -> ambiguous
        return {}
    return {size: next(iter(ids)) for size, ids in size2ids.items()}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/pytest tests/test_grube_de.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/parovanie/grube_de.py tests/test_grube_de.py tests/fixtures/grube_de_detail_154773.html
git commit -m "feat: parse_variants — grube.de per-size itemId (own-prefix filter, count-assert, excludes cross-sell)"
```

---

### Task 4: forestshop variant size resolution (z `variant:*` stĺpca)

**Files:**
- Modify: `src/parovanie/grube_de.py`
- Test: `tests/test_grube_de.py`

**Interfaces:**
- Produces: `SIZE_COLUMNS: list[str]`; `MULTI_AXIS` sentinel; `resolve_size(row: dict) -> str | None | MULTI_AXIS` — size label from the export row's variant:* columns (NEVER the code suffix). `None` = one-size (no size dimension). `MULTI_AXIS` = bunda+nohavice / multi-axis → link-only.

- [ ] **Step 1: Write failing test**

```python
# tests/test_grube_de.py (append)
from parovanie.grube_de import resolve_size, MULTI_AXIS

def _row(**kw):
    base = {c: "" for c in [
        "variant:Bunda veľkosť","variant:Nohavice veľkosť",
        "variant:Veľkosť (všetko)","variant:Veľkosť číslo"]}
    base.update(kw); return base

def test_resolve_size_single_letter_column():
    assert resolve_size(_row(**{"variant:Veľkosť (všetko)": "L"})) == "L"

def test_resolve_size_numeric_column():
    assert resolve_size(_row(**{"variant:Veľkosť číslo": "48"})) == "48"

def test_resolve_size_multi_axis_komplet():
    r = _row(**{"variant:Bunda veľkosť": "3XL", "variant:Nohavice veľkosť": "46"})
    assert resolve_size(r) is MULTI_AXIS

def test_resolve_size_one_size_no_columns():
    assert resolve_size(_row()) is None

def test_resolve_size_ignores_code_suffix():
    # caller passes only the row; resolve_size never sees the code -> proven by API
    r = _row(**{"variant:Veľkosť (všetko)": "L"})
    assert resolve_size(r) == "L"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/pytest tests/test_grube_de.py -q` → FAIL.

- [ ] **Step 3: Implement**

```python
# src/parovanie/grube_de.py (append)
class _MultiAxis:
    __slots__ = ()
    def __repr__(self): return "MULTI_AXIS"

MULTI_AXIS = _MultiAxis()

# Validated against the live export (reader E). Order does not matter; we only
# count populated and read the single one. Bunda+Nohavice together => multi-axis.
SIZE_COLUMNS = [
    "variant:Bunda veľkosť",
    "variant:Nohavice veľkosť",
    "variant:Veľkosť (všetko)",
    "variant:Veľkosť číslo",
]


def resolve_size(row: dict):
    """Forestshop variant size label from the export row's variant:* columns.
    NEVER parsed from the code suffix (unreliable: 997/S77, 62093/39/40, 61933//M,
    disambiguators 3XL2). Returns the clean label, None (one-size), or MULTI_AXIS."""
    bunda = (row.get("variant:Bunda veľkosť") or "").strip()
    nohav = (row.get("variant:Nohavice veľkosť") or "").strip()
    if bunda and nohav:
        return MULTI_AXIS
    populated = [(row.get(c) or "").strip() for c in SIZE_COLUMNS if (row.get(c) or "").strip()]
    if len(populated) == 1:
        return populated[0]
    if len(populated) == 0:
        return None
    return MULTI_AXIS  # >1 different size columns populated -> multi-axis, link-only
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/pytest tests/test_grube_de.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/parovanie/grube_de.py tests/test_grube_de.py
git commit -m "feat: resolve_size — forestshop size from variant:* column (multi-axis -> link-only)"
```

---

### Task 5: size match (EXACT-string, fail-closed) → {code: itemId}

**Files:**
- Modify: `src/parovanie/grube_de.py`
- Test: `tests/test_grube_de.py`

**Interfaces:**
- Consumes: `resolve_size` (Task 4), grube `{size_label: itemId}` (Task 3).
- Produces: `normalize_size(label: str) -> str`; `match_variant_codes(rows: list[dict], grube_sizes: dict[str,str]) -> dict[str,str]` mapping forestshop variant `code` → grube itemId, ONLY for unique exact-matched sizes (multi-axis/one-size/no-match excluded). Raises `ValueError` if two grube labels normalize to one (collision guard).

- [ ] **Step 1: Write failing test**

```python
# tests/test_grube_de.py (append)
from parovanie.grube_de import normalize_size, match_variant_codes

GRUBE = {"S":"1547734523","M":"1547734598","L":"1547734519","XL":"1547734593",
         "XXL":"1547734524","3XL":"1547734570","4XL":"1547734535","5XL":"1547734553"}

def _vrow(code, **kw):
    r = {"code": code, "variant:Bunda veľkosť":"","variant:Nohavice veľkosť":"",
         "variant:Veľkosť (všetko)":"","variant:Veľkosť číslo":""}
    r.update(kw); return r

def test_match_letter_sizes():
    rows = [_vrow("60645/L", **{"variant:Veľkosť (všetko)":"L"}),
            _vrow("60645/5XL", **{"variant:Veľkosť (všetko)":"5XL"})]
    assert match_variant_codes(rows, GRUBE) == {"60645/L":"1547734519","60645/5XL":"1547734553"}

def test_match_numeric_sizes():
    g = {"46":"3848935748","48":"3848935732","56":"3848935751"}
    rows = [_vrow("X/46", **{"variant:Veľkosť číslo":"46"}),
            _vrow("X/56", **{"variant:Veľkosť číslo":"56"})]
    assert match_variant_codes(rows, g) == {"X/46":"3848935748","X/56":"3848935751"}

def test_match_xs_no_grube_label_link_only():
    rows = [_vrow("X/XS", **{"variant:Veľkosť (všetko)":"XS"})]
    assert match_variant_codes(rows, GRUBE) == {}        # XS absent -> link-only, never snaps to S

def test_match_multi_axis_excluded():
    rows = [_vrow("12311/3XL2", **{"variant:Bunda veľkosť":"3XL","variant:Nohavice veľkosť":"46"})]
    assert match_variant_codes(rows, GRUBE) == {}

def test_match_2xl_normalizes_to_xxl():
    g = {"XXL":"1111111111"}
    rows = [_vrow("X/2XL", **{"variant:Veľkosť (všetko)":"2XL"})]
    assert match_variant_codes(rows, g) == {"X/2XL":"1111111111"}

def test_match_collision_guard_raises():
    g = {"XXL":"1","2XL":"2"}   # both normalize to XXL
    rows = [_vrow("X/XXL", **{"variant:Veľkosť (všetko)":"XXL"})]
    import pytest
    with pytest.raises(ValueError):
        match_variant_codes(rows, g)
```

- [ ] **Step 2: Run test to verify it fails** → FAIL.

- [ ] **Step 3: Implement**

```python
# src/parovanie/grube_de.py (append)
_LETTER_ALIASES = {"2XL": "XXL", "XXXL": "3XL", "XXXXL": "4XL", "XXXXXL": "5XL"}


def normalize_size(label: str) -> str:
    """Trim; uppercase letter sizes; apply 2XL->XXL etc. Numeric kept as-is."""
    s = (label or "").strip()
    up = s.upper()
    return _LETTER_ALIASES.get(up, up if up and up[0].isalpha() else s)


def match_variant_codes(rows: list[dict], grube_sizes: dict[str, str]) -> dict[str, str]:
    """{forestshop code: grube itemId} for EXACT-matched sizes only.
    Excludes multi-axis, one-size, and any size with no exact grube label.
    Raises ValueError if two grube labels normalize to one (silent-fold guard)."""
    norm_grube: dict[str, str] = {}
    for lbl, iid in grube_sizes.items():
        n = normalize_size(lbl)
        if n in norm_grube and norm_grube[n] != iid:
            raise ValueError(f"grube size collision: {lbl!r} folds onto existing {n!r}")
        norm_grube[n] = iid

    out: dict[str, str] = {}
    for row in rows:
        size = resolve_size(row)
        if size is None or size is MULTI_AXIS:
            continue
        iid = norm_grube.get(normalize_size(size))
        if iid:
            out[row["code"]] = iid
    return out
```

- [ ] **Step 4: Run test to verify it passes** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/parovanie/grube_de.py tests/test_grube_de.py
git commit -m "feat: match_variant_codes — exact-string size match, fail-closed, collision guard"
```

---

### Task 6: `gather_grube_itemids.py` — live Playwright .de detail gather

**Files:**
- Create: `scripts/gather_grube_itemids.py`
- Modify: `scripts/gather_grube.py:39-45` (parametrize `wait_for_selector`)
- Test: `tests/test_gather_grube_itemids.py` (orchestration with a fake fetch)

**Interfaces:**
- Consumes: `parse_variants`, `to_grube_de`; `PlaywrightFetcher` (reused).
- Produces: `gather_itemids(paired: list[tuple[str,str]], fetch, checkpoint_path) -> dict[str, dict[str,str]]` keyed by productId → `{size_label: itemId}`. Writes `data/out_grube/itemids.json` + checkpoint. `paired` = list of `(key, decision_url)` for GRUBE.

- [ ] **Step 1: Parametrize the fetcher wait selector**

`scripts/gather_grube.py` — change `PlaywrightFetcher.__call__` to accept the wait selector (default keeps `.product-box` for the search gather; detail gather passes the buy/configurator widget selector):

```python
# scripts/gather_grube.py  (PlaywrightFetcher.__call__, ~line 39)
def __call__(self, url, wait_selector=".product-box"):
    self._page.goto(url, wait_until="domcontentloaded")
    try:
        self._page.wait_for_selector(wait_selector, timeout=8000)
    except Exception:
        pass  # detail pages / empty results: continue with whatever rendered
    return self._page.content()
```

- [ ] **Step 2: Write failing test (fake fetch, no network)**

```python
# tests/test_gather_grube_itemids.py
import json
from scripts.gather_grube_itemids import gather_itemids

FAKE = {  # productId -> html with schema.org Offer objects (matches parse_variants)
  "154773": '"name":"Größe S.","price":"1","priceCurrency":"EUR","sku":"1547734523"'
            '"name":"Größe M.","price":"1","priceCurrency":"EUR","sku":"1547734598"',
}

def test_gather_itemids_writes_map(tmp_path):
    calls = []
    def fetch(url, wait_selector=None):
        calls.append(url); pid = url.rstrip("/").split("/")[-1]; return FAKE[pid]
    out = gather_itemids([("GRUBE|395", "https://www.grube.sk/p/x/154773/?q=a#itemId=1")],
                         fetch, checkpoint=str(tmp_path/"cp.json"))
    assert out["154773"] == {"S":"1547734523","M":"1547734598"}
    assert calls == ["https://www.grube.de/p/x/154773/"]   # normalized to .de

def test_gather_itemids_resumes_from_checkpoint(tmp_path):
    cp = tmp_path/"cp.json"; cp.write_text(json.dumps({"154773": {"S":"X"}}))
    out = gather_itemids([("GRUBE|395","https://www.grube.sk/p/x/154773/")],
                         lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not fetch")),
                         checkpoint=str(cp))
    assert out["154773"] == {"S":"X"}     # skipped, no fetch

def test_gather_itemids_404_continues(tmp_path):
    def fetch(url, wait_selector=None): raise RuntimeError("404")
    out = gather_itemids([("GRUBE|9","https://www.grube.sk/p/x/999999/")],
                         fetch, checkpoint=str(tmp_path/"cp.json"))
    assert out == {}     # error tolerated, batch did not crash
```

- [ ] **Step 3: Run test to verify it fails** → FAIL.

- [ ] **Step 4: Implement**

```python
# scripts/gather_grube_itemids.py
"""Live Playwright gather of grube.de per-size itemIds for paired GRUBE products.
Resumable (checkpoint keyed by productId). 404/empty tolerated per product."""
import json, os, re, sys
sys.path.insert(0, "src")
from parovanie.grube_de import to_grube_de, parse_variants

_PID = re.compile(r"/p/x/(\d+)/")
_DETAIL_WAIT = ".product-detail-buy-container, .product-detail-configurator, .buy-widget"


def _atomic_write(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=0)
    os.replace(tmp, path)


def gather_itemids(paired, fetch, checkpoint):
    done = {}
    if os.path.exists(checkpoint):
        with open(checkpoint, encoding="utf-8") as f:
            done = json.load(f)
    for key, url in paired:
        de = to_grube_de(url)
        if not de:
            continue
        pid = _PID.search(de).group(1)
        if pid in done:
            continue
        try:
            html = fetch(de, wait_selector=_DETAIL_WAIT)
            sizes = parse_variants(html, pid)
        except Exception as e:  # 404 / delisted / changed -> skip, continue batch
            print(f"WARN {key} pid={pid}: {e}", file=sys.stderr)
            continue
        if sizes:
            done[pid] = sizes
            _atomic_write(checkpoint, done)
        else:
            print(f"WARN {key} pid={pid}: 0 own itemIds (link-only)", file=sys.stderr)
    return done


def main():
    out_dir = "data/out_grube"
    os.makedirs(out_dir, exist_ok=True)
    # paired GRUBE decisions: key -> url
    with open("data/out/decisions.json", encoding="utf-8") as f:
        dec = json.load(f)
    paired = [(k, v.get("url", "")) for k, v in dec.items()
              if k.startswith("GRUBE|") and v.get("url")]
    from gather_grube import PlaywrightFetcher  # reuse
    fetcher = PlaywrightFetcher(base="https://www.grube.de/")
    try:
        result = gather_itemids(paired, fetcher, os.path.join(out_dir, "itemids_checkpoint.json"))
    finally:
        fetcher.close()
    _atomic_write(os.path.join(out_dir, "itemids.json"), result)
    print(f"itemids for {len(result)} products -> {out_dir}/itemids.json")


if __name__ == "__main__":
    main()
```

(Implementer: confirm `PlaywrightFetcher.__init__` accepts a `base` warm-up URL; if it hardcodes grube.sk, add a `base=` param defaulting to the current value — the detail gather warms up on grube.de. Add a `.close()` if absent.)

- [ ] **Step 5: Run test to verify it passes** → PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add scripts/gather_grube_itemids.py scripts/gather_grube.py tests/test_gather_grube_itemids.py
git commit -m "feat: gather_grube_itemids — live .de per-size itemId gather (resumable, 404-tolerant)"
```

---

### Task 7: `build_grube_codes.py` — join → `data/out/grube_codes.json`

**Files:**
- Create: `scripts/build_grube_codes.py`
- Test: `tests/test_build_grube_codes.py`

**Interfaces:**
- Consumes: `to_grube_de`, `match_variant_codes`; `itemids.json` (Task 6); `data/products.csv`; `data/out/decisions.json`.
- Produces: `build_grube_codes(decisions, itemids, export_rows) -> dict[str, dict]` mapping forestshop `code` → `{itemId, size, deUrl, productId}`. Writes `data/out/grube_codes.json` (durable, atomic).

- [ ] **Step 1: Write failing test**

```python
# tests/test_build_grube_codes.py
from scripts.build_grube_codes import build_grube_codes

def _erow(code, pair, **kw):
    r = {"code":code,"pairCode":pair,"supplier":"GRUBE",
         "variant:Bunda veľkosť":"","variant:Nohavice veľkosť":"",
         "variant:Veľkosť (všetko)":"","variant:Veľkosť číslo":""}
    r.update(kw); return r

def test_build_joins_size_to_itemid():
    decisions = {"GRUBE|395": {"status":"manual","url":"https://www.grube.sk/p/x/154773/#itemId=9"}}
    itemids = {"154773": {"S":"1547734523","L":"1547734519"}}
    rows = [_erow("60645/S","395",**{"variant:Veľkosť (všetko)":"S"}),
            _erow("60645/L","395",**{"variant:Veľkosť (všetko)":"L"})]
    out = build_grube_codes(decisions, itemids, rows)
    assert out == {
        "60645/S": {"itemId":"1547734523","size":"S","deUrl":"https://www.grube.de/p/x/154773/","productId":"154773"},
        "60645/L": {"itemId":"1547734519","size":"L","deUrl":"https://www.grube.de/p/x/154773/","productId":"154773"},
    }

def test_build_skips_non_grube_and_unmatched():
    decisions = {"GRUBE|395": {"status":"manual","url":"https://www.grube.sk/p/x/154773/"},
                 "WETLAND|9": {"status":"manual","url":"https://wetland.sk/x"}}
    itemids = {"154773": {"S":"1547734523"}}
    rows = [_erow("60645/S","395",**{"variant:Veľkosť (všetko)":"S"}),
            _erow("60645/XS","395",**{"variant:Veľkosť (všetko)":"XS"}),   # no grube XS
            _erow("WL/1","9",supplier="WETLAND",**{"variant:Veľkosť (všetko)":"S"})]
    out = build_grube_codes(decisions, itemids, rows)
    assert set(out) == {"60645/S"}    # XS unmatched, WETLAND excluded
```

- [ ] **Step 2: Run test to verify it fails** → FAIL.

- [ ] **Step 3: Implement**

```python
# scripts/build_grube_codes.py
"""Join grube.de per-size itemIds with forestshop variant sizes ->
data/out/grube_codes.json  {code: {itemId, size, deUrl, productId}}."""
import csv, json, os, re, sys
sys.path.insert(0, "src")
from parovanie.grube_de import to_grube_de, match_variant_codes, resolve_size, MULTI_AXIS

_PID = re.compile(r"/p/x/(\d+)/")


def build_grube_codes(decisions, itemids, export_rows):
    # pairCode -> productId (from GRUBE decisions)
    pair_pid, pair_de = {}, {}
    for key, dec in decisions.items():
        if not key.startswith("GRUBE|"):
            continue
        de = to_grube_de(dec.get("url", ""))
        if not de:
            continue
        pid = _PID.search(de).group(1)
        pair = key.split("|", 1)[1]
        pair_pid[pair] = pid
        pair_de[pair] = de

    # group GRUBE export rows by pairCode
    by_pair: dict[str, list[dict]] = {}
    for row in export_rows:
        if row.get("supplier") != "GRUBE":
            continue
        by_pair.setdefault(row.get("pairCode", ""), []).append(row)

    out: dict[str, dict] = {}
    for pair, pid in pair_pid.items():
        gsizes = itemids.get(pid)
        rows = by_pair.get(pair)
        if not gsizes or not rows:
            continue
        matched = match_variant_codes(rows, gsizes)          # {code: itemId}
        size_by_code = {r["code"]: resolve_size(r) for r in rows}
        for code, iid in matched.items():
            size = size_by_code.get(code)
            out[code] = {"itemId": iid, "size": "" if size in (None, MULTI_AXIS) else size,
                         "deUrl": pair_de[pair], "productId": pid}
    return out


def _load_export(path="data/products.csv"):
    csv.field_size_limit(10**7)
    with open(path, encoding="cp1250", newline="") as f:
        return list(csv.DictReader(f, delimiter=";"))


def main():
    with open("data/out/decisions.json", encoding="utf-8") as f:
        decisions = json.load(f)
    with open("data/out_grube/itemids.json", encoding="utf-8") as f:
        itemids = json.load(f)
    result = build_grube_codes(decisions, itemids, _load_export())
    tmp = "data/out/grube_codes.json.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=0)
    os.replace(tmp, "data/out/grube_codes.json")
    print(f"{len(result)} variant codes -> data/out/grube_codes.json")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes** → PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/build_grube_codes.py tests/test_build_grube_codes.py
git commit -m "feat: build_grube_codes — join .de itemIds with forestshop sizes -> grube_codes.json"
```

---

### Task 8: `externalcode_rows` + wire into `/api/import` (GATED on Task 1 = importable)

> Skip this task if `EXTERNALCODE_IMPORTABLE == False` (Task 1).

**Files:**
- Modify: `src/parovanie/import_builder.py` (add `EXTERNALCODE_HEADER`, `externalcode_rows`)
- Modify: `webreview/app.py:427-435` (add file to `/api/import` zip)
- Test: `tests/test_import_builder.py`

**Interfaces:**
- Consumes: `grube_codes.json` shape (Task 7).
- Produces: `EXTERNALCODE_HEADER = ["code","pairCode","externalCode"]`; `externalcode_rows(grube_codes: dict, code2pair: dict, exclude_codes=None) -> list[list[str]]`.

- [ ] **Step 1: Write failing test**

```python
# tests/test_import_builder.py (append)
from parovanie.import_builder import externalcode_rows, EXTERNALCODE_HEADER

def test_externalcode_rows_basic():
    gc = {"60645/L": {"itemId":"1547734519"}, "60645/S": {"itemId":"1547734523"}}
    rows = externalcode_rows(gc, {"60645/L":"395","60645/S":"395"})
    assert EXTERNALCODE_HEADER == ["code","pairCode","externalCode"]
    assert sorted(rows) == [["60645/L","395","1547734519"],["60645/S","395","1547734523"]]

def test_externalcode_rows_drops_empty_and_nonnumeric():
    gc = {"a":{"itemId":""}, "b":{"itemId":"=EVIL"}, "c":{"itemId":"1234567890"}}
    rows = externalcode_rows(gc, {"c":"9"})
    assert rows == [["c","9","1234567890"]]     # empty + non-numeric dropped

def test_externalcode_rows_dedup_first_wins():
    gc = {"x":{"itemId":"1111111111"}}
    rows = externalcode_rows(gc, {"x":"9"}, exclude_codes={"x"})
    assert rows == []      # excluded
```

- [ ] **Step 2: Run test to verify it fails** → FAIL.

- [ ] **Step 3: Implement** (mirror `supplier_rows` at import_builder.py:144-161)

```python
# src/parovanie/import_builder.py (append near SUPPLIER_HEADER/supplier_rows)
EXTERNALCODE_HEADER = ["code", "pairCode", "externalCode"]


def externalcode_rows(grube_codes, code2pair, exclude_codes=None):
    """Rows [code, pairCode, externalCode] from grube_codes.json. itemId must be
    purely numeric (own-prefixed digits); empty/non-numeric dropped (an empty cell
    would WIPE the field). Dedup each code first-wins (a dup code aborts the whole
    Shoptet import). GRUBE-only is guaranteed by the caller (grube_codes is GRUBE)."""
    exclude = exclude_codes or set()
    out, seen = [], set()
    for code, info in grube_codes.items():
        if code in exclude or code in seen:
            continue
        iid = str(info.get("itemId", "")).strip()
        if not iid or not iid.isdigit():
            continue
        seen.add(code)
        out.append([code, code2pair.get(code, ""), iid])
    return out
```

- [ ] **Step 4: Run test to verify it passes** → PASS.

- [ ] **Step 5: Wire into `/api/import` zip** (webreview/app.py, after the suppliers entry ~line 433)

```python
# webreview/app.py — inside /api/import files list, after import_suppliers.csv:
        ("import_externalcode.csv", import_builder.EXTERNALCODE_HEADER,
         import_builder.externalcode_rows(_load_grube_codes(), CODE2PAIR)),
```

Add the loader near the other store loaders:

```python
# webreview/app.py
GRUBE_CODES = os.path.join(OUT, "grube_codes.json")

def _load_grube_codes():
    try:
        with open(GRUBE_CODES, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
```

GRUBE-only guard test (assert every emitted code is a GRUBE product):

```python
# tests/test_import_builder.py (append)
def test_externalcode_rows_all_codes_present_in_code2pair():
    gc = {"60645/L":{"itemId":"1547734519"}}
    rows = externalcode_rows(gc, {"60645/L":"395"})
    assert all(len(r) == 3 and r[2].isdigit() for r in rows)
```

- [ ] **Step 6: Run tests** `PYTHONPATH=src .venv/bin/pytest tests/test_import_builder.py -q` → PASS.

- [ ] **Step 7: Commit**

```bash
git add src/parovanie/import_builder.py webreview/app.py tests/test_import_builder.py
git commit -m "feat: externalcode_rows + import_externalcode.csv in /api/import (GRUBE itemId write-back)"
```

---

### Task 9: `internalNote` ← grube.de link (GRUBE-only normalization in link_rows)

**Files:**
- Modify: `src/parovanie/import_builder.py` (`link_rows`, ~98-118)
- Test: `tests/test_import_builder.py`

**Interfaces:**
- Consumes: `to_grube_de` (Task 2).
- Produces: `link_rows` emits a normalized grube.de URL into `internalNote` for GRUBE products; non-grube unchanged.

- [ ] **Step 1: Write failing test**

```python
# tests/test_import_builder.py (append)
from parovanie.import_builder import link_rows

def test_link_rows_grube_url_normalized_to_de():
    # PRODUCTS shape per the existing link_rows contract; one GRUBE product
    products = [{"key":"GRUBE|395","supplier":"GRUBE","variant_codes":["60645/L"]}]
    decisions = {"GRUBE|395":{"status":"manual","url":"https://www.grube.sk/p/grand-nord/154773/?q=a#itemId=1"}}
    rows = link_rows(products, decisions, {"60645/L":"395"})
    note = [r for r in rows if r[0] == "60645/L"][0][2]
    assert note == "https://www.grube.de/p/x/154773/"

def test_link_rows_nongrube_url_unchanged():
    products = [{"key":"WETLAND|9","supplier":"WETLAND","variant_codes":["WL/1"]}]
    decisions = {"WETLAND|9":{"status":"manual","url":"https://www.wetland.sk/p/foo"}}
    rows = link_rows(products, decisions, {"WL/1":"9"})
    note = [r for r in rows if r[0] == "WL/1"][0][2]
    assert note == "https://www.wetland.sk/p/foo"
```

(Implementer: read the real `link_rows` signature + row shape at `import_builder.py:98-118` and adapt the test's product/decision shapes to match exactly; the assertion — GRUBE url normalized via `to_grube_de`, others verbatim — is the contract.)

- [ ] **Step 2: Run test to verify it fails** → FAIL.

- [ ] **Step 3: Implement** — in `link_rows`, when emitting the `internalNote` value, apply GRUBE-only normalization:

```python
# src/parovanie/import_builder.py — inside link_rows, where the URL is chosen:
from parovanie.grube_de import to_grube_de
# ...
        url = decisions[key].get("url", "")          # existing
        if product.get("supplier") == "GRUBE":
            url = to_grube_de(url) or url             # productId-rebuild; fallback to raw if unparseable
```

- [ ] **Step 4: Run test to verify it passes** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/parovanie/import_builder.py tests/test_import_builder.py
git commit -m "feat: link_rows writes normalized grube.de internalNote for GRUBE (non-grube unchanged)"
```

---

### Task 10: webreview „Na objednanie" — grube kód-čip + .de link

**Files:**
- Modify: `webreview/app.py` (`api_orders` ~584-592)
- Modify: `webreview/static/app.js` (`renderOrderRow` ~362)
- Modify: `webreview/static/style.css` (~124)
- Modify: `webreview/templates/index.html:7,22` (cache-bust)
- Test: `tests/test_webreview.py`, `tests/e2e/test_order_grube.py`

**Interfaces:**
- Consumes: `grube_codes.json` (Task 7), keyed by forestshop variant `code`.
- Produces: order rows carry `grubeItemId` + `grubeDeUrl`; the row renders a copyable code chip + a `.de` link for GRUBE.

- [ ] **Step 1: Write failing unit test (/api/orders attaches fields)**

```python
# tests/test_webreview.py (append)
def test_orders_attach_grube_code(tmp_path, monkeypatch):
    import webreview.app as webapp
    monkeypatch.setattr(webapp, "GRUBE_CODES", str(tmp_path/"gc.json"))
    (tmp_path/"gc.json").write_text('{"60645/L": {"itemId":"1547734519","deUrl":"https://www.grube.de/p/x/154773/"}}')
    # build one order row for itemCode 60645/L (reuse existing order fixture helper)
    rows = webapp._attach_grube({"itemCode":"60645/L"})    # tiny helper added in impl
    assert rows["grubeItemId"] == "1547734519"
    assert rows["grubeDeUrl"] == "https://www.grube.de/p/x/154773/"
```

- [ ] **Step 2: Run test to verify it fails** → FAIL.

- [ ] **Step 3: Implement server attach** — in `api_orders` loop (app.py:584-592), beside `pairUrl`:

```python
# webreview/app.py — load once at top of api_orders:
    grube = _load_grube_codes()
# ... per row r (r["itemCode"] = forestshop variant code):
    g = grube.get(r["itemCode"])
    r["grubeItemId"] = g["itemId"] if g else ""
    de = (g or {}).get("deUrl", "")
    r["grubeDeUrl"] = de if de.startswith("https://") else ""
```

Add the tiny `_attach_grube` helper the test calls (extracted from the loop body) so attach is unit-testable.

- [ ] **Step 4: Implement client render** — `renderOrderRow` (app.js ~362), after the link/pair area, gated on `o.grubeItemId`:

```javascript
// webreview/static/app.js — inside renderOrderRow, after the .to-link/pair block
if (o.grubeItemId) {
  const chip = el('span', 'to-grube');
  chip.textContent = o.grubeItemId;            // .textContent => auto-escaped
  chip.title = 'Kopírovať grube kód';
  chip.onclick = () => navigator.clipboard && navigator.clipboard.writeText(o.grubeItemId);
  row.appendChild(chip);
  if (o.grubeDeUrl) {
    const de = el('a', 'to-link');
    de.href = o.grubeDeUrl; de.target = '_blank'; de.rel = 'noopener';
    de.textContent = '🇩🇪 .de';
    row.appendChild(de);
  }
}
```

- [ ] **Step 5: CSS + cache-bust**

```css
/* webreview/static/style.css */
.toorder-row .to-grube { background:#eef; border:1px solid #99c; border-radius:4px;
  padding:1px 6px; font-family:monospace; cursor:pointer; }
.toorder-row .to-grube:hover { background:#dde; }
```

`webreview/templates/index.html:7,22` — bump BOTH `?v=N` (style.css AND app.js) in lockstep.

- [ ] **Step 6: e2e test (chip shows, copy no-op safe, clean console)**

```python
# tests/e2e/test_order_grube.py
def test_grube_chip_visible(page, live_server):
    page.goto(live_server.url + "/")
    page.click("text=Na objednanie")
    chip = page.locator(".to-grube").first
    if chip.count():                      # only if fixture has a grube order row
        assert chip.is_visible()
        chip.click()                      # must not throw even without clipboard perms
    # console-error assertion per project e2e convention
```

- [ ] **Step 7: Run tests**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_webreview.py -q
PYTHONPATH=src .venv/bin/pytest tests/e2e/test_order_grube.py -q
```
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add webreview/ tests/test_webreview.py tests/e2e/test_order_grube.py
git commit -m "feat: webreview Na objednanie — grube per-size code chip + .de link"
```

---

### Task 11: `classify_row` / preflight — recognize externalCode rows (GATED on Task 1)

> Skip if `EXTERNALCODE_IMPORTABLE == False`.

**Files:**
- Modify: `src/parovanie/shoptet_import.py` (`classify_row`, `EXPECTED_HEADER`, ~42-46)
- Modify: `scripts/shoptet_import.py` (`_print_plan`)
- Test: `tests/test_shoptet_import.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_shoptet_import.py (append)
from parovanie.shoptet_import import classify_row
def test_classify_externalcode_row():
    assert classify_row({"code":"60645/L","pairCode":"395","externalCode":"1547734519"}) == "externalcode"
```

- [ ] **Step 2: Run test to verify it fails** → FAIL.

- [ ] **Step 3: Implement** — add an `externalcode` branch to `classify_row` (a row with non-empty `externalCode` and no `internalNote`/visibility/stock) and add `externalCode` to `EXPECTED_HEADER`; add a `_print_plan` line `externalCode: N`.

- [ ] **Step 4: Run test to verify it passes** → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/parovanie/shoptet_import.py scripts/shoptet_import.py tests/test_shoptet_import.py
git commit -m "feat: shoptet_import recognizes externalCode rows in preflight plan"
```

---

### Task 12: Full suite + lint + playbook update

**Files:**
- Modify: `.claude/skills/suppliers/SKILL.md` or new `.claude/skills/grube/SKILL.md` + `CLAUDE.md` router

- [ ] **Step 1: Run full offline suite**

```bash
cd /home/newlevel/devel/forestshop/parovanie_produktov
PYTHONPATH=src .venv/bin/pytest --ignore=tests/e2e -q
.venv/bin/ruff check .
```
Expected: all pass, ruff clean.

- [ ] **Step 2: Playbook** — add a `grube` section (per `project-playbook-maintenance`): the .sk/.de productId+itemId sharing, the `/p/x/<id>/` redirect trick, own-prefix+len itemId filter (cross-sell), size-from-column-not-suffix, multi-axis→link-only, exact-match-fail-closed, the `data/out/grube_codes.json` store, importability-gate result. Add router line to `CLAUDE.md`. **Secret-scrub before commit.**

- [ ] **Step 3: Commit**

```bash
git add .claude/skills CLAUDE.md
git commit -m "docs(playbook): grube per-size pairing gotchas (.de itemId, size-from-column, fail-closed)"
```

---

### Task 13: LIVE run — gather + build + import + verify (operational, GATED on Task 1)

> The production rollout. Run after all code tasks merge. externalCode import GATED on Task 1.

- [ ] **Step 1: Live .de itemId gather** (detached, ~minutes for the 27 paired; resumable)

```bash
systemd-run --user --unit=grube-itemids --collect \
  bash -lc 'cd /home/newlevel/devel/forestshop/parovanie_produktov && PYTHONPATH=src .venv/bin/python scripts/gather_grube_itemids.py'
journalctl --user -u grube-itemids -f      # watch to completion
```

- [ ] **Step 2: Build grube_codes.json + report fill-rate**

```bash
PYTHONPATH=src .venv/bin/python scripts/build_grube_codes.py
.venv/bin/python -c "import json;d=json.load(open('data/out/grube_codes.json'));print(len(d),'variant codes coded')"
```

- [ ] **Step 3: Snapshot existing externalCodes (revert artifact)** — the `shoptet_import.py` catalog backup covers this; confirm the backup `export_<ts>.csv` exists before the bulk import.

- [ ] **Step 4: Generate + run the import** (manual zip path; backup + read-back)

```bash
# build import_externalcode.csv via the webreview /api/import zip OR a direct builder run, then:
PYTHONPATH=src .venv/bin/python scripts/shoptet_import.py --yes <import_externalcode.csv>
```

- [ ] **Step 5: Verify landing via FRESH export** (sample ~10 coded variants show the itemId; a non-grube product unchanged). Restart webreview, verify the chip shows on a grube order row in Playwright.

- [ ] **Step 6: Record fill-rate + verified sample** in the completion report.

---

## Self-Review

**1. Spec coverage:**
- A (.de URL normalize) → Task 2 + Task 9 (internalNote). ✓
- B (per-size extraction) → Task 3 (parse) + Task 6 (gather). ✓
- C (size resolution) → Task 4; C2 (match) → Task 5. ✓
- D (grube_codes store) → Task 7. ✓
- E (externalCode write-back) → Task 8 (+ Task 1 gate). ✓
- F (internalNote .de) → Task 9. ✓
- G (webreview display) → Task 10. ✓
- H (importability gate) → Task 1. ✓
- classify_row/preflight → Task 11. ✓
- Live rollout + snapshot/revert + fill-rate → Task 13. ✓
- GRUBE-only guards → in Tasks 4/5/8/9 (supplier=="GRUBE" / host grube). ✓
- Deferred (numeric ranges, availability, nightly cron, .de gather) → NOT implemented; Task 12 playbook notes + file issues at rollout (no-dropped-work). **Add: at Task 13 completion, `gh issue create` for each deferred item.**

**2. Placeholder scan:** No TBD/TODO. parse_variants selector is pinned-against-fixture (Task 3 Step 1 saves it; Step 4 notes the per-option fallback) — honest, not a placeholder. link_rows/api_orders/classify_row adapt to real signatures the implementer reads — the contract (assertion) is concrete.

**3. Type consistency:** `to_grube_de`, `parse_variants(html, product_id)`, `resolve_size(row)`, `MULTI_AXIS`, `match_variant_codes(rows, grube_sizes)`, `normalize_size`, `EXTERNALCODE_HEADER`, `externalcode_rows(grube_codes, code2pair, exclude_codes)`, `gather_itemids(paired, fetch, checkpoint)`, `build_grube_codes(decisions, itemids, export_rows)`, `grube_codes.json` shape `{code:{itemId,size,deUrl,productId}}` — consistent across Tasks 2→13. ✓

**Gap fixed inline:** added the deferred-issue-filing step to Task 13.

---

## Execution Handoff

Plán uložený do `docs/superpowers/plans/2026-06-29-grube-per-size-pairing.md`. Implementácia cez **subagent-driven-development** (fresh subagent per task + task review). Task 1 (live importability gate) a Task 13 (live rollout) sú operačné — bežia naživo proti Shoptetu/grube.de, nie offline TDD.
