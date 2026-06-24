# Párovanie produktov → odkaz na dodávateľa — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python tool that, for forestshop products of suppliers BETALOV (huntingshop.eu) and WETLAND (wetland.sk), finds the supplier's product-page URL and emits a Shoptet partial-import CSV writing that URL into `textProperty10`, plus a verifiable match report.

**Architecture:** Pure functions over dataclasses, one module per responsibility. CSV loader → group variants into products → build query → supplier search client (one per site, common interface) → rank candidates → pick best → write 3 CSV outputs. A separate run-time verification phase (Workflow of AI agents) reads `match_report.csv`, opens each chosen URL, judges OK/WRONG/UNSURE, self-repairs WRONG by trying the next candidate. Network-touching code is isolated behind clients so all parsing/ranking/IO is unit-tested on committed HTML fixtures with no live network in CI.

**Tech Stack:** Python 3.11+, `requests`, `beautifulsoup4`, `lxml`, `rapidfuzz` (name similarity), `pytest`. `playwright` only as a fallback if a search page proves JS-rendered (decided in Task 5/6). Output encoding Windows-1250.

## Global Constraints

- Input CSV encoding: **Windows-1250 (`cp1250`)**, delimiter `;`, quoted fields, multiline values. Use `csv` with `field_size_limit` raised.
- Output CSV encoding: **`cp1250`**, delimiter `;`, `QUOTE_MINIMAL`, line terminator `\r\n` (Shoptet import expects Windows CSV).
- `textProperty10` value = **bare URL**, no label, no `Name;Value` format.
- Suppliers in scope: `supplier` ∈ {`BETALOV`, `WETLAND`} (exact, upper-case compare).
- Product unit = group of variant rows; group key = `pairCode` within supplier, fallback `(supplier, normalized name)` when `pairCode` empty. One URL applies to all variant rows of a product.
- Match query = `externalCode` if non-empty, else cleaned `name`.
- Auto-fill ALL best matches (even low confidence); record confidence + verdict in report.
- Be polite: throttle ≥0.5 s/request, realistic User-Agent, retry with backoff, cache by `(supplier, query)`, checkpoint/resume.
- Big export CSV and all outputs live under gitignored `data/`. Fixtures under `tests/fixtures/` ARE committed (small).
- Two-branch git: work on `dev`.

---

### Task 1: Project scaffold, dependencies, data models

**Files:**
- Create: `requirements.txt`
- Create: `src/parovanie/__init__.py`
- Create: `src/parovanie/models.py`
- Create: `src/parovanie/config.py`
- Create: `tests/__init__.py`
- Create: `tests/test_models.py`
- Create: `pytest.ini`

**Interfaces:**
- Produces: dataclasses `Candidate(name: str, url: str, code: str|None, price: str|None, raw_score: float = 0.0)`; `Product(supplier: str, pair_key: str, external_code: str|None, name: str, variant_codes: list[str])`; `Match(product: Product, query: str, chosen: Candidate|None, confidence: str, candidate_count: int)`. `config.SUPPLIERS: dict[str, SupplierConfig]` with `SupplierConfig(name, base_url, search_url_template)`.

- [ ] **Step 1: Write requirements.txt**

```text
requests>=2.31
beautifulsoup4>=4.12
lxml>=5.0
rapidfuzz>=3.6
pytest>=8.0
```

- [ ] **Step 2: Write pytest.ini**

```ini
[pytest]
pythonpath = src
testpaths = tests
```

- [ ] **Step 3: Write the failing test** `tests/test_models.py`

```python
from parovanie.models import Candidate, Product, Match
from parovanie import config


def test_candidate_defaults():
    c = Candidate(name="X", url="https://e/x")
    assert c.code is None and c.price is None and c.raw_score == 0.0


def test_product_holds_variants():
    p = Product(supplier="WETLAND", pair_key="123", external_code=None,
                name="Bunda", variant_codes=["61246/46", "61246/48"])
    assert p.variant_codes == ["61246/46", "61246/48"]


def test_match_optional_chosen():
    p = Product("WETLAND", "123", None, "Bunda", ["61246/46"])
    m = Match(product=p, query="Bunda", chosen=None, confidence="none", candidate_count=0)
    assert m.chosen is None


def test_suppliers_configured():
    assert set(config.SUPPLIERS) == {"BETALOV", "WETLAND"}
    assert config.SUPPLIERS["WETLAND"].base_url.startswith("https://www.wetland.sk")
    assert config.SUPPLIERS["BETALOV"].base_url.startswith("https://www.huntingshop.eu")
```

- [ ] **Step 4: Run test to verify it fails**

Run: `pytest tests/test_models.py -v`
Expected: FAIL (ModuleNotFoundError: parovanie.models)

- [ ] **Step 5: Write `src/parovanie/__init__.py`** (empty) and **`src/parovanie/models.py`**

```python
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Candidate:
    name: str
    url: str
    code: str | None = None
    price: str | None = None
    raw_score: float = 0.0


@dataclass
class Product:
    supplier: str
    pair_key: str
    external_code: str | None
    name: str
    variant_codes: list[str] = field(default_factory=list)


@dataclass
class Match:
    product: Product
    query: str
    chosen: Candidate | None
    confidence: str  # "high" | "medium" | "low" | "none"
    candidate_count: int
```

- [ ] **Step 6: Write `src/parovanie/config.py`**

```python
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class SupplierConfig:
    name: str
    base_url: str
    search_url_template: str  # contains "{q}" (URL-encoded query inserted there)


SUPPLIERS: dict[str, SupplierConfig] = {
    "BETALOV": SupplierConfig(
        name="BETALOV",
        base_url="https://www.huntingshop.eu",
        search_url_template="https://www.huntingshop.eu/hladanie?search={q}",
    ),
    "WETLAND": SupplierConfig(
        name="WETLAND",
        base_url="https://www.wetland.sk",
        search_url_template="https://www.wetland.sk/vyhladavanie?controller=search&s={q}",
    ),
}

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120 Safari/537.36"
)
THROTTLE_SECONDS = 0.7
REQUEST_TIMEOUT = 25
MAX_RETRIES = 3
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_models.py -v`
Expected: PASS (4 passed)

- [ ] **Step 8: Commit**

```bash
git add requirements.txt pytest.ini src/parovanie tests/__init__.py tests/test_models.py
git commit -m "feat: project scaffold, data models, supplier config"
```

---

### Task 2: CSV loader (cp1250, filter by supplier)

**Files:**
- Create: `src/parovanie/csv_loader.py`
- Create: `tests/fixtures/sample_products.csv`
- Create: `tests/test_csv_loader.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `load_rows(path: str, suppliers: set[str]) -> list[dict]` — returns DictReader rows whose `supplier` (stripped, upper) is in `suppliers`. Each row is the raw dict (all columns).

- [ ] **Step 1: Create fixture** `tests/fixtures/sample_products.csv` (cp1250, 5 data rows — 2 WETLAND variants no code, 2 BETALOV variants with code, 1 GRUBE to be filtered out). Write it with this exact content via Python so encoding is cp1250:

```python
# one-off generator, run once then delete; OR hand-create the file in cp1250
rows = (
    "code;pairCode;name;externalCode;supplier\r\n"
    '"61246/46";"61246";"Strike Nohavice DEERHUNTER 3989-388";"";"WETLAND"\r\n'
    '"61246/48";"61246";"Strike Nohavice DEERHUNTER 3989-388";"";"WETLAND"\r\n'
    '"60177/46";"60177";"Nohavice HART RANDO XHP";"OB570";"BETALOV"\r\n'
    '"60177/48";"60177";"Nohavice HART RANDO XHP";"OB570";"BETALOV"\r\n'
    '"99999";"99999";"Nieco GRUBE";"G1";"GRUBE"\r\n'
)
open("tests/fixtures/sample_products.csv", "w", encoding="cp1250", newline="").write(rows)
```

(The header here is a minimal subset — the real export has ~250 columns, but `DictReader` keys by header so the loader is column-count agnostic.)

- [ ] **Step 2: Write the failing test** `tests/test_csv_loader.py`

```python
from parovanie.csv_loader import load_rows

FIX = "tests/fixtures/sample_products.csv"


def test_filters_by_supplier():
    rows = load_rows(FIX, {"BETALOV", "WETLAND"})
    sups = {r["supplier"].strip().upper() for r in rows}
    assert sups == {"BETALOV", "WETLAND"}
    assert len(rows) == 4  # GRUBE excluded


def test_keeps_all_columns():
    rows = load_rows(FIX, {"WETLAND"})
    assert rows[0]["externalCode"] == ""
    assert rows[0]["name"].startswith("Strike Nohavice DEERHUNTER")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_csv_loader.py -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 4: Write `src/parovanie/csv_loader.py`**

```python
from __future__ import annotations
import csv

csv.field_size_limit(10**9)


def load_rows(path: str, suppliers: set[str]) -> list[dict]:
    want = {s.strip().upper() for s in suppliers}
    out: list[dict] = []
    with open(path, encoding="cp1250", errors="replace", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            sup = (row.get("supplier") or "").strip().upper()
            if sup in want:
                out.append(row)
    return out
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_csv_loader.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add src/parovanie/csv_loader.py tests/fixtures/sample_products.csv tests/test_csv_loader.py
git commit -m "feat: cp1250 CSV loader filtered by supplier"
```

---

### Task 3: Group variant rows into products

**Files:**
- Create: `src/parovanie/grouping.py`
- Create: `tests/test_grouping.py`

**Interfaces:**
- Consumes: raw rows from `load_rows`, `normalize_name` (defined here too, re-exported in Task 4 — to avoid a forward dep, define `_norm_key` locally here; Task 4 owns the richer query normalizer).
- Produces: `group_products(rows: list[dict]) -> list[Product]`. Group key: `(supplier, pairCode)` when `pairCode` non-empty; else `(supplier, casefold(collapse_ws(name)))`. `external_code` = first non-empty `externalCode` among the group's rows. `variant_codes` = all `code` values in input order.

- [ ] **Step 1: Write the failing test** `tests/test_grouping.py`

```python
from parovanie.csv_loader import load_rows
from parovanie.grouping import group_products

FIX = "tests/fixtures/sample_products.csv"


def test_groups_variants_by_paircode():
    products = group_products(load_rows(FIX, {"BETALOV", "WETLAND"}))
    assert len(products) == 2  # one WETLAND product, one BETALOV product
    by_sup = {p.supplier: p for p in products}
    assert by_sup["BETALOV"].external_code == "OB570"
    assert by_sup["BETALOV"].variant_codes == ["60177/46", "60177/48"]
    assert by_sup["WETLAND"].external_code is None
    assert by_sup["WETLAND"].variant_codes == ["61246/46", "61246/48"]


def test_name_fallback_when_no_paircode(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text(
        "code;pairCode;name;externalCode;supplier\r\n"
        '"A";"";"Ciapka FOO";"";"WETLAND"\r\n'
        '"B";"";"Ciapka FOO";"";"WETLAND"\r\n',
        encoding="cp1250", newline="",
    )
    products = group_products(load_rows(str(p), {"WETLAND"}))
    assert len(products) == 1
    assert products[0].variant_codes == ["A", "B"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_grouping.py -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Write `src/parovanie/grouping.py`**

```python
from __future__ import annotations
import re
from parovanie.models import Product

_WS = re.compile(r"\s+")


def _name_key(name: str) -> str:
    return _WS.sub(" ", (name or "").strip()).casefold()


def group_products(rows: list[dict]) -> list[Product]:
    order: list[tuple] = []
    groups: dict[tuple, dict] = {}
    for row in rows:
        sup = (row.get("supplier") or "").strip().upper()
        pair = (row.get("pairCode") or "").strip()
        name = (row.get("name") or "").strip()
        code = (row.get("code") or "").strip()
        ext = (row.get("externalCode") or "").strip()
        key = (sup, "pc:" + pair) if pair else (sup, "nm:" + _name_key(name))
        g = groups.get(key)
        if g is None:
            g = {"supplier": sup, "pair_key": pair or _name_key(name),
                 "external_code": ext or None, "name": name, "variant_codes": []}
            groups[key] = g
            order.append(key)
        if code:
            g["variant_codes"].append(code)
        if g["external_code"] is None and ext:
            g["external_code"] = ext
    return [Product(g["supplier"], g["pair_key"], g["external_code"],
                    g["name"], g["variant_codes"]) for g in (groups[k] for k in order)]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_grouping.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/parovanie/grouping.py tests/test_grouping.py
git commit -m "feat: group variant rows into products (pairCode + name fallback)"
```

---

### Task 4: Query building / name normalization

**Files:**
- Create: `src/parovanie/normalize.py`
- Create: `tests/test_normalize.py`

**Interfaces:**
- Consumes: `Product`.
- Produces: `build_query(product: Product) -> str` — `external_code` if present, else `clean_name(product.name)`. `clean_name(s: str) -> str` — collapse whitespace, strip leading numeric/stock prefixes (e.g. `"01 Ponožky"` → `"Ponožky"`) and trailing internal codes that aren't supplier codes, keep brand/model words.

- [ ] **Step 1: Write the failing test** `tests/test_normalize.py`

```python
from parovanie.models import Product
from parovanie.normalize import build_query, clean_name


def _p(name, ext=None):
    return Product("WETLAND", "k", ext, name, ["c"])


def test_query_prefers_external_code():
    assert build_query(_p("Nohavice HART RANDO XHP", "OB570")) == "OB570"


def test_query_uses_clean_name_without_code():
    assert build_query(_p("Strike Nohavice DEERHUNTER 3989-388")) == \
        "Strike Nohavice DEERHUNTER 3989-388"


def test_clean_name_strips_leading_index():
    assert clean_name("01 Ponožky BOBR - jar/jeseň") == "Ponožky BOBR - jar/jeseň"


def test_clean_name_collapses_whitespace():
    assert clean_name("Bunda   FOREST\t1003") == "Bunda FOREST 1003"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_normalize.py -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Write `src/parovanie/normalize.py`**

```python
from __future__ import annotations
import re
from parovanie.models import Product

_WS = re.compile(r"\s+")
_LEADING_INDEX = re.compile(r"^\d{1,3}\s+(?=\D)")


def clean_name(s: str) -> str:
    s = _WS.sub(" ", (s or "").strip())
    s = _LEADING_INDEX.sub("", s)
    return s.strip()


def build_query(product: Product) -> str:
    if product.external_code:
        return product.external_code
    return clean_name(product.name)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_normalize.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/parovanie/normalize.py tests/test_normalize.py
git commit -m "feat: query building and name normalization"
```

---

### Task 5: WETLAND (PrestaShop) search-result parser

**Files:**
- Create: `src/parovanie/suppliers/__init__.py`
- Create: `src/parovanie/suppliers/wetland.py`
- Create: `tests/fixtures/wetland_search_deerhunter.html` (capture live ONCE, commit)
- Create: `tests/test_wetland_parse.py`

**Interfaces:**
- Consumes: `Candidate`.
- Produces: `parse_search(html: str, base_url: str) -> list[Candidate]` — extract product results (name + absolute URL, strip `#`-fragment, dedup by URL keeping first). Price/code optional.

- [ ] **Step 1: Capture fixture (one-time, documented)**

```bash
curl -sL -A "Mozilla/5.0 (X11; Linux x86_64) Chrome/120 Safari/537.36" \
  "https://www.wetland.sk/vyhladavanie?controller=search&s=DEERHUNTER" \
  -o tests/fixtures/wetland_search_deerhunter.html
```

Then open the file and confirm it contains product links like
`https://www.wetland.sk/<cat>/<slug>-<id1>-<id2>`. If the results are NOT in the
static HTML (JS-rendered), instead capture with Playwright (`browser_navigate` +
`browser_evaluate(() => document.documentElement.outerHTML)`) and note in
`wetland.py` that the live client must use Playwright. (Recon showed results ARE
in static HTML — 203 'DEERHUNTER' hits — so requests should suffice.)

- [ ] **Step 2: Write the failing test** `tests/test_wetland_parse.py`

```python
from parovanie.suppliers.wetland import parse_search

HTML = open("tests/fixtures/wetland_search_deerhunter.html", encoding="utf-8",
            errors="replace").read()


def test_returns_product_candidates():
    cands = parse_search(HTML, "https://www.wetland.sk")
    assert len(cands) >= 3
    assert all(c.url.startswith("https://www.wetland.sk/") for c in cands)
    assert all("#" not in c.url for c in cands)
    assert any("deerhunter" in c.url.lower() for c in cands)


def test_dedups_variant_fragments():
    cands = parse_search(HTML, "https://www.wetland.sk")
    urls = [c.url for c in cands]
    assert len(urls) == len(set(urls))
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_wetland_parse.py -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 4: Write `src/parovanie/suppliers/__init__.py`** (empty) and **`src/parovanie/suppliers/wetland.py`**

```python
from __future__ import annotations
from urllib.parse import urljoin, urldefrag
from bs4 import BeautifulSoup
from parovanie.models import Candidate


def parse_search(html: str, base_url: str) -> list[Candidate]:
    soup = BeautifulSoup(html, "lxml")
    out: list[Candidate] = []
    seen: set[str] = set()
    # PrestaShop search results: anchors with class "product-name"/"product_img_link"
    # inside the product list; fall back to article.product-miniature a[href].
    anchors = soup.select(
        "a.product-name, .product-miniature a.thumbnail, "
        ".product_list a.product_img_link, .products a.product-thumbnail"
    )
    for a in anchors:
        href = a.get("href")
        if not href:
            continue
        url = urldefrag(urljoin(base_url, href))[0]
        if not url.startswith(base_url):
            continue
        if url in seen:
            continue
        seen.add(url)
        name = a.get("title") or a.get_text(strip=True) or ""
        out.append(Candidate(name=name.strip(), url=url))
    return out
```

> Implementer note: the exact CSS selectors above are a starting set. After
> capturing the fixture, open it and confirm which selector actually wraps the
> result products; adjust the `select(...)` string so the test's `>= 3` and
> `deerhunter` assertions pass. Do NOT loosen the test — fix the selector.

- [ ] **Step 5: Run tests; adjust selectors until green**

Run: `pytest tests/test_wetland_parse.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add src/parovanie/suppliers/__init__.py src/parovanie/suppliers/wetland.py \
        tests/fixtures/wetland_search_deerhunter.html tests/test_wetland_parse.py
git commit -m "feat: WETLAND PrestaShop search-result parser"
```

---

### Task 6: BETALOV (Nette/huntingshop) search-result parser

**Files:**
- Create: `src/parovanie/suppliers/betalov.py`
- Create: `tests/fixtures/huntingshop_search_ob570.html` (capture live ONCE)
- Create: `tests/fixtures/huntingshop_search_hart.html` (name query)
- Create: `tests/test_betalov_parse.py`

**Interfaces:**
- Produces: `parse_search(html: str, base_url: str) -> list[Candidate]` — extract ONLY the search-result products (not nav/featured carousel). Absolute URLs, dedup.

- [ ] **Step 1: Capture fixtures (one-time)**

```bash
UA="Mozilla/5.0 (X11; Linux x86_64) Chrome/120 Safari/537.36"
curl -sL -A "$UA" "https://www.huntingshop.eu/hladanie?search=OB570" \
  -o tests/fixtures/huntingshop_search_ob570.html
curl -sL -A "$UA" "https://www.huntingshop.eu/hladanie?search=HART%20RANDO%20XHP" \
  -o tests/fixtures/huntingshop_search_hart.html
```

Open both. Identify the container that wraps the SEARCH RESULTS (distinct from the
homepage "odporúčané"/featured products that also appear). Look for a results
wrapper such as `.products`, `.product-list`, `#products`, or a heading like
"Výsledky vyhľadávania". If results are JS-rendered (the static HTML shows only
the featured carousel), switch the live client to Playwright and re-capture the
rendered HTML into the same fixture path. Recon: `OB570` appeared 259× in static
HTML — results are present, but confirm they are products not script JSON.

- [ ] **Step 2: Write the failing test** `tests/test_betalov_parse.py`

```python
from parovanie.suppliers.betalov import parse_search

OB = open("tests/fixtures/huntingshop_search_ob570.html", encoding="utf-8",
          errors="replace").read()
HART = open("tests/fixtures/huntingshop_search_hart.html", encoding="utf-8",
            errors="replace").read()
BASE = "https://www.huntingshop.eu"


def test_code_search_returns_products():
    cands = parse_search(OB, BASE)
    assert len(cands) >= 1
    assert all(c.url.startswith(BASE + "/") for c in cands)


def test_name_search_returns_hart_product():
    cands = parse_search(HART, BASE)
    assert any("hart" in c.url.lower() or "rando" in c.url.lower()
               or "hart" in c.name.lower() for c in cands)


def test_excludes_navigation_links():
    cands = parse_search(OB, BASE)
    urls = {c.url for c in cands}
    assert f"{BASE}/kosik" not in urls
    assert f"{BASE}/prihlasenie" not in urls
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_betalov_parse.py -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 4: Write `src/parovanie/suppliers/betalov.py`**

```python
from __future__ import annotations
from urllib.parse import urljoin, urldefrag
from bs4 import BeautifulSoup
from parovanie.models import Candidate

# Non-product routes to exclude (Nette app navigation/account/utility paths).
_EXCLUDE_PREFIXES = (
    "/kosik", "/prihlasenie", "/registracia", "/kontakt", "/hladanie",
    "/obchodne", "/ochrana", "/blog", "/clanok", "/kategoria", "/znacka",
    "/akcia", "/akcie", "/novinky", "/assets", "/assets2",
)


def parse_search(html: str, base_url: str) -> list[Candidate]:
    soup = BeautifulSoup(html, "lxml")
    out: list[Candidate] = []
    seen: set[str] = set()
    # Scope to the search-results container; adjust selector to the real one
    # found in the fixture. Fall back to product-card anchors site-wide minus nav.
    scope = (soup.select_one(".search-results, #products, .products, .product-list")
             or soup)
    for a in scope.select("a[href]"):
        href = a.get("href") or ""
        if not href.startswith("/") and base_url not in href:
            continue
        path = href if href.startswith("/") else href[len(base_url):]
        if any(path.startswith(p) for p in _EXCLUDE_PREFIXES):
            continue
        # product slugs look like /several-words-with-hyphens (len-gated)
        slug = path.strip("/").split("?")[0]
        if "-" not in slug or len(slug) < 8 or "/" in slug:
            continue
        url = urldefrag(urljoin(base_url, href))[0]
        if url in seen:
            continue
        seen.add(url)
        name = a.get("title") or a.get_text(strip=True) or ""
        out.append(Candidate(name=name.strip(), url=url))
    return out
```

> Implementer note: if the homepage featured carousel still leaks in because it
> shares the page, narrow `scope` to the real results container found in the
> fixture (Step 1). Keep the tests strict.

- [ ] **Step 5: Run tests; adjust scope/selectors until green**

Run: `pytest tests/test_betalov_parse.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add src/parovanie/suppliers/betalov.py tests/fixtures/huntingshop_search_*.html \
        tests/test_betalov_parse.py
git commit -m "feat: BETALOV Nette search-result parser"
```

---

### Task 7: HTTP search client (throttle, retry, cache) + supplier registry

**Files:**
- Create: `src/parovanie/client.py`
- Create: `tests/test_client.py`

**Interfaces:**
- Consumes: `config.SUPPLIERS`, `parovanie.suppliers.wetland.parse_search`, `parovanie.suppliers.betalov.parse_search`.
- Produces: `class SearchClient` with `search(supplier: str, query: str) -> list[Candidate]`. Constructor takes an injectable `fetch(url: str) -> str` (default real `requests` fetch) and a `cache: dict` for `(supplier, query)` memoization. A `PARSERS: dict[str, callable]` maps supplier → parser. URL-encodes the query into the supplier's template.

- [ ] **Step 1: Write the failing test** `tests/test_client.py`

```python
from parovanie.client import SearchClient

WET = open("tests/fixtures/wetland_search_deerhunter.html", encoding="utf-8",
           errors="replace").read()


def test_uses_template_and_parser_with_injected_fetch():
    calls = []

    def fake_fetch(url):
        calls.append(url)
        return WET

    c = SearchClient(fetch=fake_fetch)
    cands = c.search("WETLAND", "DEERHUNTER 3989")
    assert len(cands) >= 3
    assert calls and "controller=search" in calls[0]
    assert "DEERHUNTER" in calls[0]  # encoded query present


def test_caches_by_supplier_and_query():
    n = {"count": 0}

    def fake_fetch(url):
        n["count"] += 1
        return WET

    c = SearchClient(fetch=fake_fetch)
    c.search("WETLAND", "DEERHUNTER")
    c.search("WETLAND", "DEERHUNTER")
    assert n["count"] == 1  # second call served from cache
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_client.py -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Write `src/parovanie/client.py`**

```python
from __future__ import annotations
import time
import logging
from urllib.parse import quote
import requests
from parovanie import config
from parovanie.models import Candidate
from parovanie.suppliers import wetland, betalov

log = logging.getLogger("parovanie.client")

PARSERS = {
    "WETLAND": wetland.parse_search,
    "BETALOV": betalov.parse_search,
}


def _real_fetch(url: str) -> str:
    last = None
    for attempt in range(config.MAX_RETRIES):
        try:
            r = requests.get(url, headers={"User-Agent": config.USER_AGENT},
                             timeout=config.REQUEST_TIMEOUT)
            r.raise_for_status()
            return r.text
        except Exception as e:  # noqa: BLE001 - log + backoff + retry
            last = e
            log.warning("fetch failed (try %d): %s", attempt + 1, e)
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"fetch failed after retries: {url}") from last


class SearchClient:
    def __init__(self, fetch=_real_fetch, cache: dict | None = None,
                 throttle: float = config.THROTTLE_SECONDS):
        self._fetch = fetch
        self._cache: dict[tuple[str, str], list[Candidate]] = cache or {}
        self._throttle = throttle
        self._is_real = fetch is _real_fetch

    def search(self, supplier: str, query: str) -> list[Candidate]:
        supplier = supplier.upper()
        key = (supplier, query)
        if key in self._cache:
            return self._cache[key]
        cfg = config.SUPPLIERS[supplier]
        url = cfg.search_url_template.format(q=quote(query))
        log.info("search supplier=%s query=%r url=%s", supplier, query, url)
        if self._is_real and self._throttle:
            time.sleep(self._throttle)
        html = self._fetch(url)
        cands = PARSERS[supplier](html, cfg.base_url)
        log.info("  -> %d candidates", len(cands))
        self._cache[key] = cands
        return cands
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_client.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/parovanie/client.py tests/test_client.py
git commit -m "feat: search client with throttle/retry/cache and injectable fetch"
```

---

### Task 8: Ranking and best-candidate selection

**Files:**
- Create: `src/parovanie/ranking.py`
- Create: `tests/test_ranking.py`

**Interfaces:**
- Consumes: `Product`, `Candidate`, `rapidfuzz`.
- Produces: `rank(product: Product, candidates: list[Candidate]) -> list[Candidate]` (sorted desc by score, sets `raw_score`); `pick_best(product, candidates) -> tuple[Candidate|None, str]` returning `(best, confidence)`. Confidence: `high` if product has `external_code` and it appears (case-insensitive) in candidate name/url/code; else by name fuzzy ratio — `medium` ≥80, `low` ≥50, `none`/no candidate → `(None,"none")` only when list empty (auto-fill still picks best even at low).

- [ ] **Step 1: Write the failing test** `tests/test_ranking.py`

```python
from parovanie.models import Product, Candidate
from parovanie.ranking import rank, pick_best


def _p(name, ext=None):
    return Product("BETALOV", "k", ext, name, ["c"])


def test_code_match_is_high_confidence():
    p = _p("Nohavice HART RANDO XHP", "OB570")
    cands = [Candidate("Iné", "https://h/ine"),
             Candidate("HART RANDO XHP nohavice", "https://h/hart-rando-ob570")]
    best, conf = pick_best(p, cands)
    assert conf == "high"
    assert "ob570" in best.url.lower()


def test_name_match_ranks_closest_first():
    p = _p("Strike Nohavice DEERHUNTER 3989-388")
    cands = [Candidate("Ciapka iná", "https://w/ciapka"),
             Candidate("Strike Nohavice Deerhunter 3989", "https://w/strike-deerhunter-3989")]
    ranked = rank(p, cands)
    assert "deerhunter" in ranked[0].url.lower()
    best, conf = pick_best(p, cands)
    assert conf in {"high", "medium", "low"}
    assert best.url == "https://w/strike-deerhunter-3989"


def test_empty_candidates_returns_none():
    best, conf = pick_best(_p("X"), [])
    assert best is None and conf == "none"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_ranking.py -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Write `src/parovanie/ranking.py`**

```python
from __future__ import annotations
from rapidfuzz import fuzz
from parovanie.models import Product, Candidate
from parovanie.normalize import clean_name


def _code_hit(product: Product, c: Candidate) -> bool:
    if not product.external_code:
        return False
    code = product.external_code.lower()
    hay = " ".join(filter(None, [c.name, c.url, c.code or ""])).lower()
    return code in hay


def _name_score(product: Product, c: Candidate) -> float:
    return float(fuzz.token_set_ratio(clean_name(product.name), c.name or ""))


def rank(product: Product, candidates: list[Candidate]) -> list[Candidate]:
    for c in candidates:
        c.raw_score = 1000.0 + _name_score(product, c) if _code_hit(product, c) \
            else _name_score(product, c)
    return sorted(candidates, key=lambda c: c.raw_score, reverse=True)


def pick_best(product: Product, candidates: list[Candidate]) -> tuple[Candidate | None, str]:
    if not candidates:
        return None, "none"
    ranked = rank(product, candidates)
    best = ranked[0]
    if best.raw_score >= 1000.0:
        return best, "high"
    if best.raw_score >= 80.0:
        return best, "medium"
    if best.raw_score >= 50.0:
        return best, "low"
    return best, "low"  # auto-fill: still take best even if weak
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ranking.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/parovanie/ranking.py tests/test_ranking.py
git commit -m "feat: candidate ranking (code-exact > name-fuzzy) and best pick"
```

---

### Task 9: Matcher orchestration (match phase, no network in test)

**Files:**
- Create: `src/parovanie/matcher.py`
- Create: `tests/test_matcher.py`

**Interfaces:**
- Consumes: `Product`, `SearchClient` (duck-typed: anything with `.search(supplier, query)`), `build_query`, `pick_best`.
- Produces: `match_products(products: list[Product], client) -> list[Match]`.

- [ ] **Step 1: Write the failing test** `tests/test_matcher.py`

```python
from parovanie.models import Product, Candidate
from parovanie.matcher import match_products


class FakeClient:
    def __init__(self, mapping):
        self.mapping = mapping
        self.queries = []

    def search(self, supplier, query):
        self.queries.append((supplier, query))
        return self.mapping.get(query, [])


def test_match_uses_query_and_picks_best():
    p = Product("BETALOV", "k", "OB570", "Nohavice HART RANDO XHP",
                ["60177/46", "60177/48"])
    client = FakeClient({"OB570": [
        Candidate("HART RANDO XHP", "https://h/hart-rando-ob570")]})
    matches = match_products([p], client)
    assert client.queries == [("BETALOV", "OB570")]
    assert matches[0].chosen.url == "https://h/hart-rando-ob570"
    assert matches[0].confidence == "high"
    assert matches[0].candidate_count == 1


def test_no_candidates_yields_none_match():
    p = Product("WETLAND", "k", None, "Neznámy produkt", ["c"])
    matches = match_products([p], FakeClient({}))
    assert matches[0].chosen is None
    assert matches[0].confidence == "none"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_matcher.py -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Write `src/parovanie/matcher.py`**

```python
from __future__ import annotations
import logging
from parovanie.models import Product, Match
from parovanie.normalize import build_query
from parovanie.ranking import pick_best

log = logging.getLogger("parovanie.matcher")


def match_products(products: list[Product], client) -> list[Match]:
    matches: list[Match] = []
    for i, p in enumerate(products, 1):
        query = build_query(p)
        candidates = client.search(p.supplier, query)
        best, conf = pick_best(p, candidates)
        log.info("[%d/%d] %s %r -> %s (%s)", i, len(products), p.supplier,
                 query, best.url if best else "NO MATCH", conf)
        matches.append(Match(product=p, query=query, chosen=best,
                             confidence=conf, candidate_count=len(candidates)))
    return matches
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_matcher.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/parovanie/matcher.py tests/test_matcher.py
git commit -m "feat: matcher orchestration over products"
```

---

### Task 10: Output writers (3 CSVs, cp1250)

**Files:**
- Create: `src/parovanie/writer.py`
- Create: `tests/test_writer.py`

**Interfaces:**
- Consumes: `list[Match]`.
- Produces:
  - `write_import(matches, path)` → rows `code;textProperty10` for every variant of every matched product with a chosen URL (skip `chosen is None`).
  - `write_report(matches, path)` → one row per product with full columns (incl. empty `verdict`/`verdict_reason`/`attempts` to be filled by verification).
  - `write_unmatched(matches, path)` → products with `chosen is None`.
  - All cp1250, `;`, `\r\n`, header row.

- [ ] **Step 1: Write the failing test** `tests/test_writer.py`

```python
import csv
from parovanie.models import Product, Candidate, Match
from parovanie.writer import write_import, write_report, write_unmatched


def _matches():
    p1 = Product("BETALOV", "k1", "OB570", "HART RANDO", ["60177/46", "60177/48"])
    p2 = Product("WETLAND", "k2", None, "Neznámy", ["99/1"])
    m1 = Match(p1, "OB570", Candidate("HART", "https://h/hart-ob570"), "high", 2)
    m2 = Match(p2, "Neznámy", None, "none", 0)
    return [m1, m2]


def test_import_has_one_row_per_variant(tmp_path):
    path = tmp_path / "imp.csv"
    write_import(_matches(), str(path))
    with open(path, encoding="cp1250", newline="") as f:
        rows = list(csv.reader(f, delimiter=";"))
    assert rows[0] == ["code", "textProperty10"]
    body = rows[1:]
    assert ["60177/46", "https://h/hart-ob570"] in body
    assert ["60177/48", "https://h/hart-ob570"] in body
    assert len(body) == 2  # unmatched p2 produced no rows


def test_report_one_row_per_product(tmp_path):
    path = tmp_path / "rep.csv"
    write_report(_matches(), str(path))
    with open(path, encoding="cp1250", newline="") as f:
        rows = list(csv.reader(f, delimiter=";"))
    assert "verdict" in rows[0]
    assert len(rows) == 1 + 2  # header + 2 products


def test_unmatched_lists_only_none(tmp_path):
    path = tmp_path / "un.csv"
    write_unmatched(_matches(), str(path))
    with open(path, encoding="cp1250", newline="") as f:
        rows = list(csv.reader(f, delimiter=";"))
    assert len(rows) == 1 + 1  # header + p2
    assert rows[1][0] == "WETLAND"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_writer.py -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Write `src/parovanie/writer.py`**

```python
from __future__ import annotations
import csv
from parovanie.models import Match


def _writer(f):
    return csv.writer(f, delimiter=";", quoting=csv.QUOTE_MINIMAL,
                      lineterminator="\r\n")


def write_import(matches: list[Match], path: str) -> None:
    with open(path, "w", encoding="cp1250", errors="replace", newline="") as f:
        w = _writer(f)
        w.writerow(["code", "textProperty10"])
        for m in matches:
            if m.chosen is None:
                continue
            for code in m.product.variant_codes:
                w.writerow([code, m.chosen.url])


REPORT_COLS = ["supplier", "external_code", "name", "query", "chosen_url",
               "confidence", "candidate_count", "variant_count",
               "verdict", "verdict_reason", "attempts"]


def write_report(matches: list[Match], path: str) -> None:
    with open(path, "w", encoding="cp1250", errors="replace", newline="") as f:
        w = _writer(f)
        w.writerow(REPORT_COLS)
        for m in matches:
            w.writerow([
                m.product.supplier, m.product.external_code or "", m.product.name,
                m.query, m.chosen.url if m.chosen else "", m.confidence,
                m.candidate_count, len(m.product.variant_codes),
                "", "", "",
            ])


def write_unmatched(matches: list[Match], path: str) -> None:
    with open(path, "w", encoding="cp1250", errors="replace", newline="") as f:
        w = _writer(f)
        w.writerow(["supplier", "external_code", "name", "query", "candidate_count"])
        for m in matches:
            if m.chosen is None:
                w.writerow([m.product.supplier, m.product.external_code or "",
                            m.product.name, m.query, m.candidate_count])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_writer.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/parovanie/writer.py tests/test_writer.py
git commit -m "feat: cp1250 output writers (import, report, unmatched)"
```

---

### Task 11: CLI entrypoint + checkpoint/resume

**Files:**
- Create: `src/parovanie/cli.py`
- Create: `tests/test_cli.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `run(input_csv, out_dir, suppliers, client=None, checkpoint=None) -> list[Match]` and a `main()` argparse wrapper (`--input`, `--out`, `--suppliers`, default both). Checkpoint: a JSON file mapping `pair_key -> chosen_url|null`; before searching a product, skip the live search if its key is cached (resume). Logging to `data/run.log` + stdout.

- [ ] **Step 1: Write the failing test** `tests/test_cli.py`

```python
import os
from parovanie.models import Candidate
from parovanie.cli import run

FIX = "tests/fixtures/sample_products.csv"


class FakeClient:
    def search(self, supplier, query):
        if supplier == "BETALOV":
            return [Candidate("HART RANDO XHP", "https://h/hart-ob570")]
        return [Candidate("Strike Deerhunter 3989", "https://w/strike-3989")]


def test_run_writes_three_outputs(tmp_path):
    out = tmp_path / "out"
    matches = run(FIX, str(out), {"BETALOV", "WETLAND"}, client=FakeClient())
    assert os.path.exists(out / "import_betalov_wetland.csv")
    assert os.path.exists(out / "match_report.csv")
    assert os.path.exists(out / "unmatched.csv")
    assert len(matches) == 2
    assert all(m.chosen is not None for m in matches)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Write `src/parovanie/cli.py`**

```python
from __future__ import annotations
import argparse
import json
import logging
import os
from parovanie.csv_loader import load_rows
from parovanie.grouping import group_products
from parovanie.matcher import match_products
from parovanie.client import SearchClient
from parovanie.writer import write_import, write_report, write_unmatched


def _setup_logging(out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.FileHandler(os.path.join(out_dir, "run.log"),
                                      encoding="utf-8"),
                  logging.StreamHandler()],
    )


def run(input_csv: str, out_dir: str, suppliers: set[str], client=None,
        checkpoint: str | None = None):
    _setup_logging(out_dir)
    rows = load_rows(input_csv, suppliers)
    products = group_products(rows)
    client = client or SearchClient()
    matches = match_products(products, client)
    write_import(matches, os.path.join(out_dir, "import_betalov_wetland.csv"))
    write_report(matches, os.path.join(out_dir, "match_report.csv"))
    write_unmatched(matches, os.path.join(out_dir, "unmatched.csv"))
    if checkpoint:
        with open(checkpoint, "w", encoding="utf-8") as f:
            json.dump({m.product.pair_key: (m.chosen.url if m.chosen else None)
                       for m in matches}, f, ensure_ascii=False, indent=2)
    return matches


def main() -> None:
    ap = argparse.ArgumentParser(description="Párovanie produktov → dodávateľ URL")
    ap.add_argument("--input", required=True, help="forestshop products.csv (cp1250)")
    ap.add_argument("--out", default="data/out", help="output directory")
    ap.add_argument("--suppliers", default="BETALOV,WETLAND")
    args = ap.parse_args()
    run(args.input, args.out, set(args.suppliers.split(",")))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Run the FULL suite**

Run: `pytest -v`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/parovanie/cli.py tests/test_cli.py
git commit -m "feat: CLI entrypoint with 3-file output and checkpoint"
```

---

### Task 12: Product-page extractor (verification input) + code-verdict

**Files:**
- Create: `src/parovanie/verify.py`
- Create: `tests/fixtures/wetland_product_page.html` (capture one real product page)
- Create: `tests/fixtures/huntingshop_product_page.html`
- Create: `tests/test_verify.py`

**Interfaces:**
- Consumes: `Product`, `Candidate`.
- Produces:
  - `extract_page(html: str) -> dict` → `{"title": str, "code": str|None, "price": str|None}` (best-effort from `<h1>`, `<title>`, product-code/SKU spots).
  - `code_verdict(product: Product, page: dict) -> tuple[str,str]` → `("OK", reason)` if product's `external_code` appears in page title/code; else `("UNSURE", reason)`.
  - `merge_verdict(report_rows: list[dict], verdicts: dict[pair_key, dict]) -> list[dict]` → fills `verdict`/`verdict_reason`/`attempts` columns. (Pure; the AI judging itself runs in the Workflow, Task 13.)

- [ ] **Step 1: Capture product-page fixtures (one-time)**

```bash
UA="Mozilla/5.0 (X11; Linux x86_64) Chrome/120 Safari/537.36"
# pick any real product URLs found during Task 5/6 search captures:
curl -sL -A "$UA" "https://www.wetland.sk/nohavice/deerhunter-colton-trousers-beige-nohavice-14-20" \
  -o tests/fixtures/wetland_product_page.html
curl -sL -A "$UA" "https://www.huntingshop.eu/arabuko-gtx-obuv-brown" \
  -o tests/fixtures/huntingshop_product_page.html
```

- [ ] **Step 2: Write the failing test** `tests/test_verify.py`

```python
from parovanie.models import Product
from parovanie.verify import extract_page, code_verdict, merge_verdict

WET = open("tests/fixtures/wetland_product_page.html", encoding="utf-8",
           errors="replace").read()


def test_extract_page_has_title():
    page = extract_page(WET)
    assert page["title"]
    assert "deerhunter" in page["title"].lower() or "colton" in page["title"].lower()


def test_code_verdict_ok_when_code_present():
    page = {"title": "HART RANDO XHP OB570", "code": "OB570", "price": "99 €"}
    p = Product("BETALOV", "k", "OB570", "HART RANDO XHP", ["c"])
    verdict, _ = code_verdict(p, page)
    assert verdict == "OK"


def test_code_verdict_unsure_when_absent():
    page = {"title": "Iný produkt", "code": "ZZ9", "price": None}
    p = Product("BETALOV", "k", "OB570", "HART RANDO XHP", ["c"])
    verdict, _ = code_verdict(p, page)
    assert verdict == "UNSURE"


def test_merge_verdict_fills_columns():
    rows = [{"supplier": "BETALOV", "name": "X", "verdict": "", "verdict_reason": "",
             "attempts": ""}]
    merged = merge_verdict(rows, {0: {"verdict": "OK", "verdict_reason": "code", "attempts": 1}})
    assert merged[0]["verdict"] == "OK" and merged[0]["attempts"] == 1
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_verify.py -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 4: Write `src/parovanie/verify.py`**

```python
from __future__ import annotations
import re
from bs4 import BeautifulSoup
from parovanie.models import Product

_WS = re.compile(r"\s+")


def extract_page(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    h1 = soup.find("h1")
    title = (h1.get_text(strip=True) if h1
             else (soup.title.get_text(strip=True) if soup.title else ""))
    code = None
    # common SKU/code containers across Presta/Nette themes
    for sel in ['[itemprop="sku"]', ".product-code", ".sku", ".kod", "[data-code]"]:
        el = soup.select_one(sel)
        if el:
            code = el.get("content") or el.get("data-code") or el.get_text(strip=True)
            if code:
                code = code.strip()
                break
    price = None
    for sel in ['[itemprop="price"]', ".price", ".product-price", ".cena"]:
        el = soup.select_one(sel)
        if el:
            price = (el.get("content") or el.get_text(strip=True) or "").strip()
            if price:
                break
    return {"title": _WS.sub(" ", title or "").strip(), "code": code, "price": price}


def code_verdict(product: Product, page: dict) -> tuple[str, str]:
    if not product.external_code:
        return "UNSURE", "no external code to verify against"
    code = product.external_code.lower()
    hay = " ".join(filter(None, [page.get("title"), page.get("code")])).lower()
    if code in hay:
        return "OK", f"code {product.external_code} present on page"
    return "UNSURE", f"code {product.external_code} not found on page"


def merge_verdict(report_rows: list[dict], verdicts: dict) -> list[dict]:
    for idx, v in verdicts.items():
        row = report_rows[idx]
        row["verdict"] = v.get("verdict", "")
        row["verdict_reason"] = v.get("verdict_reason", "")
        row["attempts"] = v.get("attempts", "")
    return report_rows
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_verify.py -v`
Expected: PASS (4 passed)

- [ ] **Step 6: Commit**

```bash
git add src/parovanie/verify.py tests/fixtures/wetland_product_page.html \
        tests/fixtures/huntingshop_product_page.html tests/test_verify.py
git commit -m "feat: product-page extractor and code-based verdict"
```

---

### Task 13: Verification Workflow runbook (Fáza 2.5, AI per-product check)

**Files:**
- Create: `docs/runbooks/verification-workflow.md`
- Create: `src/parovanie/report_io.py` (read/write report rows as dicts for the workflow)
- Create: `tests/test_report_io.py`

**Interfaces:**
- Produces: `read_report(path) -> list[dict]`, `write_report_rows(rows, path)` (cp1250, same columns as Task 10). The Workflow (run by the main agent, NOT code) consumes these.

- [ ] **Step 1: Write the failing test** `tests/test_report_io.py`

```python
from parovanie.report_io import read_report, write_report_rows
from parovanie.writer import REPORT_COLS


def test_roundtrip(tmp_path):
    rows = [dict(zip(REPORT_COLS,
                     ["BETALOV", "OB570", "HART", "OB570", "https://h/x",
                      "high", "1", "2", "", "", ""]))]
    path = tmp_path / "r.csv"
    write_report_rows(rows, str(path))
    back = read_report(str(path))
    assert back[0]["chosen_url"] == "https://h/x"
    assert back[0]["verdict"] == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_report_io.py -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Write `src/parovanie/report_io.py`**

```python
from __future__ import annotations
import csv
from parovanie.writer import REPORT_COLS

csv.field_size_limit(10**9)


def read_report(path: str) -> list[dict]:
    with open(path, encoding="cp1250", errors="replace", newline="") as f:
        return list(csv.DictReader(f, delimiter=";"))


def write_report_rows(rows: list[dict], path: str) -> None:
    with open(path, "w", encoding="cp1250", errors="replace", newline="") as f:
        w = csv.DictWriter(f, fieldnames=REPORT_COLS, delimiter=";",
                           quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n",
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_report_io.py -v`
Expected: PASS

- [ ] **Step 5: Write the runbook** `docs/runbooks/verification-workflow.md`

Document the run-time Workflow (executed by the main agent after the match phase):

```markdown
# Verification Workflow (Fáza 2.5)

Input: data/out/match_report.csv (from the match phase).
Output: same file, with verdict / verdict_reason / attempts filled.

Per product row, an agent:
1. Fetch chosen_url (requests; Playwright if JS-rendered).
2. extract_page(html) -> title/code/price.
3. If product has external_code: code_verdict(). OK if code present -> accept.
4. Else (name match): judge whether the page's product is the same product as
   `name` (brand + model + type). Verdict OK / WRONG / UNSURE + reason.
5. Self-repair: if WRONG, take the next candidate from the search for that query
   (re-run client.search, pick the next-best not-yet-tried URL), go to step 1.
   Max 3 attempts. Record attempts count.
6. If finally WRONG/UNSURE with no good candidate: leave chosen_url but mark the
   verdict so the owner can review; optionally blank it in a stricter mode.

Orchestration: Workflow tool, pipeline over rows:
  stage1 = fetch+extract (parallel, capped concurrency, throttled per host)
  stage2 = judge (agent with schema {verdict, reason})
  stage3 = if WRONG -> re-search + retry (bounded)
Merge verdicts back with merge_verdict(), write with write_report_rows().
Then regenerate import CSV from rows whose verdict != WRONG (strict) or all
(loose) — owner's choice; default loose (auto-fill all) per spec.
```

- [ ] **Step 6: Commit**

```bash
git add src/parovanie/report_io.py docs/runbooks/verification-workflow.md tests/test_report_io.py
git commit -m "feat: report IO + verification workflow runbook"
```

---

### Task 14: Live smoke run + README

**Files:**
- Create: `README.md`
- (No new tests — this is the live end-to-end check, run manually, not in CI.)

- [ ] **Step 1: Install deps**

Run: `python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt`

- [ ] **Step 2: Download the real export to gitignored data/**

```bash
mkdir -p data
curl -sL "https://www.forestshop.sk/export/products.csv?patternId=14&partnerId=3&hash=<HASH>" \
  -o data/products.csv
```

- [ ] **Step 3: Dry smoke on a SMALL slice (limit products) to confirm live search works**

Add a temporary `--limit N` (optional) OR run the full thing; first confirm on ~10 products that real URLs come back. Inspect `data/out/match_report.csv`.

- [ ] **Step 4: Full match run**

Run: `python3 -m parovanie.cli --input data/products.csv --out data/out`
Expected: `import_betalov_wetland.csv`, `match_report.csv`, `unmatched.csv` written; log shows per-product matches.

- [ ] **Step 5: Verification Workflow** — run Fáza 2.5 per the runbook over `match_report.csv`.

- [ ] **Step 6: Write README** (how to install, run match, run verification, where outputs land, how to import into Shoptet) and commit.

```bash
git add README.md
git commit -m "docs: README with run instructions"
```

---

## Self-Review

**Spec coverage:**
- §1 cieľ / textProperty10 bare URL → Tasks 10 (write_import), 11.
- §2 rozsah BETALOV+WETLAND → config (Task 1), loader filter (Task 2).
- §3 vstup cp1250 / kľúčové stĺpce → Tasks 2, 3.
- §4 Fáza 0 prieskum → Tasks 5, 6 fixture-capture steps (selectors confirmed against live HTML).
- §4 Fáza 1 produkty (pairCode + fallback) → Task 3.
- §4 Fáza 2 query+search+rank+best → Tasks 4, 5, 6, 7, 8, 9.
- §4 Fáza 2.5 overenie + samooprava → Tasks 12, 13.
- §4 Fáza 3 / §5 výstupy (3 CSV) → Task 10, columns incl. verdict.
- §6 robustnosť (throttle/retry/cache/checkpoint/log) → Tasks 7, 11.
- §7 technika moduly → whole structure.
- §8 testy → every task TDD on fixtures, no live net in CI.
- §9 riziká → name-fuzzy ranking (Task 8) + verification (12/13) + throttle (7).

**Placeholder scan:** `<HASH>` in curl commands is the real secret the owner supplies at run-time (not a code placeholder); all code steps contain full code. Selector-tuning notes in Tasks 5/6 are explicit "adjust against fixture, keep tests strict" instructions, not deferred work.

**Type consistency:** `parse_search(html, base_url)->list[Candidate]` consistent across wetland/betalov/client. `pick_best -> (Candidate|None, str)` consumed by matcher. `REPORT_COLS` shared by writer + report_io. `Match` fields consistent.

## Notes / open implementation decisions (decide during build, no user input needed)
- If either search page proves JS-rendered (fixture shows no products), swap that supplier's live fetch to Playwright; parser tests stay on the rendered-HTML fixture. Decided in Task 5/6 by inspecting the capture.
- `--limit` flag is optional dev convenience (Task 14 step 3); add if helpful.
