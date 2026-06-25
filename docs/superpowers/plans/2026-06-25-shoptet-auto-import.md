# Shoptet Auto-Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A CLI script that logs into the Shoptet admin, uploads our generated import CSV via Produkty → Import with the agreed settings, and reads the real result back from the Log — with a pre-flight CSV check + confirmation as the safety net.

**Architecture:** Pure, CI-tested logic lives in `src/parovanie/shoptet_import.py` (credential loading, CSV pre-flight, result-log parsing — NO browser import). A thin Playwright shell `scripts/shoptet_import.py` wires that logic to the live admin (login → upload → run → read Log). CSV generation stays separate (web button / `build_decisions_import.py`); this script only uploads an existing file.

**Tech Stack:** Python 3.12, `csv` (utf-8-sig, `;`, CRLF), Playwright (Python, sync API) for the browser shell only, pytest for the pure core.

## Global Constraints

- Import CSV encoding = **UTF-8 with BOM** (`utf-8-sig`); delimiter `;`; the in-admin import options MUST be: encoding UTF-8, **"replace empty values" OFF**, **pair by Code** (per `.claude/skills/shoptet`).
- The generated import header is exactly: `code;pairCode;textProperty10;textProperty11;productVisibility;stock;availabilityInStock;availabilityOutOfStock`.
- Secret file `data/.shoptet_admin` is `chmod 600`, **never committed** (`data/` is already gitignored). Keys: `SHOPTET_ADMIN_URL`, `SHOPTET_USER`, `SHOPTET_PASS`, `SHOPTET_EXPORT_URL` (pattern-14 export with hash, used for the pre-import backup).
- **Backup before every real import:** download `SHOPTET_EXPORT_URL` to `data/backups/export_<ts>.csv` first; if the backup fails (no URL / network / empty) → STOP, do NOT import. Skipped on `--dry-run`.
- **Parameter read-back before running:** after setting the import options, re-read the live form and assert encoding=UTF-8, "replace empty values"=OFF, pair-by=Code; abort the import if any is wrong.
- The pure core module `src/parovanie/shoptet_import.py` MUST NOT import `playwright` (so CI tests + coverage run without a browser). Playwright is imported lazily **inside** `scripts/shoptet_import.py`.
- `playwright` is NOT added to `requirements.txt` (CI never installs/run the browser). It goes in a separate `requirements-import.txt` for local use only.
- Nothing is written to the live eshop before the CSV passes pre-flight AND the user confirms (or `--yes`).
- CI: ruff lints `src tests scripts`; pytest `--cov=parovanie --cov-fail-under=80`.

---

### Task 1: Credential loader (pure)

**Files:**
- Create: `src/parovanie/shoptet_import.py`
- Test: `tests/test_shoptet_import.py`

**Interfaces:**
- Produces: `REQUIRED_KEYS: tuple[str, ...]`; `class ShoptetError(Exception)`; `load_credentials(path: str) -> dict` returning ALL parsed keys (incl. optional `SHOPTET_EXPORT_URL`), after validating the 3 required login keys are present/non-empty. Raises `ShoptetError` (clear message) if the file is missing or any required key is absent/empty.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_shoptet_import.py
import pytest
from parovanie.shoptet_import import load_credentials, ShoptetError


def _write(tmp_path, text):
    p = tmp_path / ".shoptet_admin"
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_load_credentials_ok(tmp_path):
    path = _write(tmp_path,
                  "SHOPTET_ADMIN_URL=https://www.forestshop.sk/admin/\n"
                  "# comment line\n"
                  'SHOPTET_USER="bob@x.sk"\n'
                  "SHOPTET_PASS=secret pass\n")
    c = load_credentials(path)
    assert c["SHOPTET_ADMIN_URL"] == "https://www.forestshop.sk/admin/"
    assert c["SHOPTET_USER"] == "bob@x.sk"          # quotes stripped
    assert c["SHOPTET_PASS"] == "secret pass"       # spaces kept


def test_load_credentials_missing_file(tmp_path):
    with pytest.raises(ShoptetError, match="chýba"):
        load_credentials(str(tmp_path / "nope"))


def test_load_credentials_missing_key(tmp_path):
    path = _write(tmp_path, "SHOPTET_ADMIN_URL=https://x/\nSHOPTET_USER=a\n")
    with pytest.raises(ShoptetError, match="SHOPTET_PASS"):
        load_credentials(path)
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_shoptet_import.py -q`
Expected: FAIL (ImportError: cannot import name 'load_credentials').

- [ ] **Step 3: Implement**

```python
# src/parovanie/shoptet_import.py
"""Pure logic for the Shoptet auto-import script (no browser import).

Credential loading, import-CSV pre-flight, and result-log parsing. The browser
driving lives in scripts/shoptet_import.py. See docs spec
2026-06-25-shoptet-auto-import-design.md and .claude/skills/shoptet.
"""
import csv
import os

csv.field_size_limit(10**9)

REQUIRED_KEYS = ("SHOPTET_ADMIN_URL", "SHOPTET_USER", "SHOPTET_PASS")


class ShoptetError(Exception):
    """Any pre-flight / config problem — fail loud, never proceed silently."""


def load_credentials(path):
    """Parse a KEY=value secret file (chmod 600, gitignored). Strips surrounding
    quotes; keeps inner spaces. Raises ShoptetError if the file is missing or any
    of REQUIRED_KEYS is absent/empty."""
    if not os.path.exists(path):
        raise ShoptetError(f"Súbor s prihlásením chýba: {path} "
                           f"(vytvor ho s kľúčmi {', '.join(REQUIRED_KEYS)}, chmod 600).")
    creds = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            creds[k.strip()] = v
    missing = [k for k in REQUIRED_KEYS if not creds.get(k)]
    if missing:
        raise ShoptetError(f"V {path} chýbajú kľúče: {', '.join(missing)}.")
    return dict(creds)   # all parsed keys (incl. optional SHOPTET_EXPORT_URL for backup)
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/pytest tests/test_shoptet_import.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/parovanie/shoptet_import.py tests/test_shoptet_import.py
git commit -m "feat: Shoptet import credential loader (pure, tested)"
```

---

### Task 2: CSV pre-flight + plan builder (pure)

**Files:**
- Modify: `src/parovanie/shoptet_import.py`
- Test: `tests/test_shoptet_import.py`

**Interfaces:**
- Consumes: `ShoptetError` from Task 1.
- Produces:
  - `EXPECTED_HEADER: list[str]` (the 8 import columns).
  - `classify_row(row: dict) -> str` → one of `"link"`, `"unavailable"`, `"discontinued"`, `"other"`.
  - `preflight_csv(path: str) -> dict` → `{"path","total","link","unavailable","discontinued","other","columns"}`. Raises `ShoptetError` if the file is missing, unreadable as utf-8-sig, has no rows, or its header lacks `code`/`pairCode`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_shoptet_import.py
import csv
from parovanie.shoptet_import import classify_row, preflight_csv, EXPECTED_HEADER


def _csv(tmp_path, rows):
    p = tmp_path / "import.csv"
    with open(p, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";", lineterminator="\r\n")
        w.writerow(EXPECTED_HEADER)
        w.writerows(rows)
    return str(p)


def test_classify_row_types():
    assert classify_row({"textProperty10": "https://h/x", "productVisibility": ""}) == "link"
    assert classify_row({"textProperty10": "", "productVisibility": "detailOnly"}) == "discontinued"
    assert classify_row({"textProperty10": "", "productVisibility": "visible",
                         "availabilityInStock": "Vypredané"}) == "unavailable"
    assert classify_row({"textProperty10": "", "productVisibility": ""}) == "other"


def test_preflight_counts_breakdown(tmp_path):
    path = _csv(tmp_path, [
        ["A/1", "100", "https://h/x", "human matched", "", "", "", ""],     # link
        ["B", "200", "", "", "visible", "0", "Vypredané", "Vypredané"],     # unavailable
        ["C", "300", "", "", "detailOnly", "0", "Predaj výrobku skončil",
         "Predaj výrobku skončil"],                                         # discontinued
    ])
    plan = preflight_csv(path)
    assert plan["total"] == 3
    assert plan["link"] == 1 and plan["unavailable"] == 1 and plan["discontinued"] == 1
    assert plan["other"] == 0


def test_preflight_rejects_missing_paircode(tmp_path):
    p = tmp_path / "bad.csv"
    with open(p, "w", encoding="utf-8-sig", newline="") as f:
        f.write("code;textProperty10\r\nX;https://h\r\n")
    with pytest.raises(ShoptetError, match="pairCode"):
        preflight_csv(str(p))


def test_preflight_rejects_empty(tmp_path):
    p = tmp_path / "empty.csv"
    with open(p, "w", encoding="utf-8-sig", newline="") as f:
        f.write(";".join(EXPECTED_HEADER) + "\r\n")
    with pytest.raises(ShoptetError, match="žiadne"):
        preflight_csv(str(p))


def test_preflight_missing_file(tmp_path):
    with pytest.raises(ShoptetError, match="chýba"):
        preflight_csv(str(tmp_path / "nope.csv"))
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_shoptet_import.py -q`
Expected: FAIL (ImportError: cannot import name 'classify_row').

- [ ] **Step 3: Implement (append to `src/parovanie/shoptet_import.py`)**

```python
EXPECTED_HEADER = ["code", "pairCode", "textProperty10", "textProperty11",
                   "productVisibility", "stock", "availabilityInStock",
                   "availabilityOutOfStock"]


def classify_row(row):
    """Classify one generated import row by its column values (mirrors
    import_builder.import_rows)."""
    if (row.get("textProperty10") or "").strip():
        return "link"
    vis = (row.get("productVisibility") or "").strip().lower()
    avail = ((row.get("availabilityInStock") or "")
             + " " + (row.get("availabilityOutOfStock") or "")).lower()
    if vis == "detailonly":
        return "discontinued"
    if vis == "visible" and "vypredan" in avail:
        return "unavailable"
    return "other"


def preflight_csv(path):
    """Read the generated import CSV (utf-8-sig, ';'), verify it is well-formed,
    and return a plan with per-type counts. Raises ShoptetError on any problem so
    nothing gets uploaded blindly."""
    if not os.path.exists(path):
        raise ShoptetError(f"Import súbor chýba: {path}")
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f, delimiter=";")
            columns = reader.fieldnames or []
            counts = {"link": 0, "unavailable": 0, "discontinued": 0, "other": 0}
            total = 0
            for row in reader:
                total += 1
                counts[classify_row(row)] += 1
    except UnicodeError as e:
        raise ShoptetError(f"Import súbor sa nedá prečítať ako UTF-8: {path} ({e})")
    if "code" not in columns or "pairCode" not in columns:
        raise ShoptetError(f"Import súbor nemá stĺpce code+pairCode (má: {columns}).")
    if total == 0:
        raise ShoptetError(f"Import súbor nemá žiadne riadky: {path}")
    return {"path": path, "total": total, "columns": columns, **counts}
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/pytest tests/test_shoptet_import.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/parovanie/shoptet_import.py tests/test_shoptet_import.py
git commit -m "feat: Shoptet import CSV pre-flight + plan breakdown (pure, tested)"
```

---

### Task 3: Result-log parser (pure)

**Files:**
- Modify: `src/parovanie/shoptet_import.py`
- Test: `tests/test_shoptet_import.py`

**Interfaces:**
- Produces: `parse_import_log(text: str) -> dict` → `{"processed": int|None, "updated": int|None, "failed": int|None, "raw": str}`. Pulls the integer that follows each Slovak keyword group (spracovan… / uprav… / zlyhan…|chyb…), case-insensitive, tolerant of `:` and spaces. `None` when a number is not found.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_shoptet_import.py
from parovanie.shoptet_import import parse_import_log


def test_parse_import_log_known_phrasing():
    txt = "Spracované 3776, Upravené 784, Zlyhanie variantov 1"
    r = parse_import_log(txt)
    assert r["processed"] == 3776 and r["updated"] == 784 and r["failed"] == 1


def test_parse_import_log_colon_and_newlines():
    txt = "Spracované záznamy: 12\nUpravené produkty: 5\nChyby: 0\n"
    r = parse_import_log(txt)
    assert r["processed"] == 12 and r["updated"] == 5 and r["failed"] == 0


def test_parse_import_log_missing_numbers():
    r = parse_import_log("import prebehol")
    assert r["processed"] is None and r["updated"] is None and r["failed"] is None
    assert r["raw"] == "import prebehol"
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_shoptet_import.py -q`
Expected: FAIL (cannot import name 'parse_import_log').

- [ ] **Step 3: Implement (append to `src/parovanie/shoptet_import.py`)**

```python
import re

_LOG_PATTERNS = {
    "processed": r"spracovan\w*",
    "updated": r"uprav\w*",
    "failed": r"(?:zlyhan\w*|chyb\w*)",
}


def parse_import_log(text):
    """Extract processed/updated/failed counts from the Shoptet import result
    text. Robust to ':'/word variants; returns None for any count not found."""
    text = text or ""
    out = {"raw": text}
    low = text.lower()
    for key, kw in _LOG_PATTERNS.items():
        m = re.search(kw + r"[^0-9]{0,40}?(\d+)", low)
        out[key] = int(m.group(1)) if m else None
    return out
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/pytest tests/test_shoptet_import.py -q`
Expected: PASS. Then full suite + coverage:
Run: `.venv/bin/pytest --cov=parovanie --cov-fail-under=80 -q`
Expected: PASS, coverage ≥ 80%.

- [ ] **Step 5: Commit**

```bash
git add src/parovanie/shoptet_import.py tests/test_shoptet_import.py
git commit -m "feat: parse Shoptet import result log (processed/updated/failed)"
```

---

### Task 4: Browser shell + CLI (live, NOT in CI)

> **Implemented by the main agent against the live admin** (selector discovery needs the real Shoptet admin + the stored credentials). NOT a blind subagent task, NOT run in CI. Verified end-to-end live per `autonomous-verification`.

**Files:**
- Create: `scripts/shoptet_import.py`
- Create: `requirements-import.txt`

**Interfaces:**
- Consumes: `load_credentials`, `preflight_csv`, `parse_import_log`, `ShoptetError` from `src/parovanie/shoptet_import.py`.

- [ ] **Step 1: `requirements-import.txt`** (local-only, NOT in CI)

```
playwright>=1.45
```

Local setup: `.venv/bin/pip install -r requirements-import.txt && .venv/bin/playwright install chromium`.

- [ ] **Step 2: CLI scaffold + pre-flight + confirm** (no browser yet — verify this runs and ruff-clean)

```python
#!/usr/bin/env python3
"""Opatrný auto-import do Shoptet adminu: pred-letová kontrola CSV → potvrdenie →
login → Produkty/Import (UTF-8, "nahradiť prázdne" VYPNUTÉ, párovať podľa Kódu) →
prečítanie skutočného výsledku z Logu. Heslo z data/.shoptet_admin (mimo gitu).

Beh:  PYTHONPATH=src .venv/bin/python scripts/shoptet_import.py [--file ...] [--dry-run] [--yes] [--headful]
"""
import argparse
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from parovanie.shoptet_import import (  # noqa: E402
    ShoptetError, load_credentials, preflight_csv, parse_import_log,
)

CRED_PATH = "data/.shoptet_admin"
DEFAULT_CSV = "data/out/import_forestshop.csv"
AUDIT_DIR = Path("data/out")
BACKUP_DIR = Path("data/backups")


def _backup_export(creds):
    """Download the FULL catalog export BEFORE importing → data/backups/export_<ts>.csv,
    so the pre-import state can be restored. Missing URL / network error / empty file
    => ShoptetError (=> caller does NOT import)."""
    url = creds.get("SHOPTET_EXPORT_URL")
    if not url:
        raise ShoptetError("chýba SHOPTET_EXPORT_URL v data/.shoptet_admin — bez zálohy neimportujem")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    dest = BACKUP_DIR / f"export_{time.strftime('%Y%m%d-%H%M%S')}.csv"
    try:
        r = requests.get(url, timeout=120)
        r.raise_for_status()
    except requests.RequestException as e:
        raise ShoptetError(f"stiahnutie zálohy exportu zlyhalo: {e}")
    if not r.content:
        raise ShoptetError("export zálohy je prázdny — neimportujem")
    dest.write_bytes(r.content)
    return str(dest)


def _args():
    ap = argparse.ArgumentParser(description="Opatrný auto-import CSV do Shoptetu")
    ap.add_argument("--file", default=DEFAULT_CSV, help="ktoré CSV nahrať")
    ap.add_argument("--yes", action="store_true", help="bez interaktívneho potvrdenia")
    ap.add_argument("--dry-run", action="store_true",
                    help="len kontrola + login + dôjsť na import; NIČ nenahrá")
    ap.add_argument("--headful", action="store_true", help="ukázať okno prehliadača")
    return ap.parse_args()


def _print_plan(plan):
    print(f"\nSúbor:  {plan['path']}")
    print(f"Riadkov: {plan['total']}")
    print(f"  • napárované (link):        {plan['link']}")
    print(f"  • nie je skladom:           {plan['unavailable']}")
    print(f"  • už sa nebude predávať:    {plan['discontinued']}")
    if plan["other"]:
        print(f"  • iné/neznáme:              {plan['other']}")


def _confirm():
    return input("\nNaozaj naimportovať do OSTRÉHO eshopu? napíš 'ano': ").strip().lower() == "ano"


def main():
    args = _args()
    try:
        creds = load_credentials(CRED_PATH)
        plan = preflight_csv(args.file)
    except ShoptetError as e:
        print(f"STOP: {e}", file=sys.stderr)
        return 1
    _print_plan(plan)
    if not args.yes and not args.dry_run and not _confirm():
        print("Zrušené, nič sa nezmenilo.")
        return 1
    if not args.dry_run:                       # záloha pred KAŽDÝM ostrým importom
        try:
            backup = _backup_export(creds)
        except ShoptetError as e:
            print(f"STOP: {e}", file=sys.stderr)
            return 1
        print(f"Záloha katalógu: {backup}")
    return _run_browser(args, creds, plan)
```

- [ ] **Step 3: Browser driver** (login → import → read Log). Implemented against the live admin; the exact selectors are confirmed by driving the real admin (Playwright MCP) during implementation. Concrete action sequence:

```python
def _run_browser(args, creds, plan):
    from playwright.sync_api import sync_playwright  # lazy: keeps core CI browser-free
    ts = time.strftime("%Y%m%d-%H%M%S")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headful)
        ctx = browser.new_context(accept_downloads=False)
        page = ctx.new_page()
        try:
            _login(page, creds)                       # fill user/pass, submit, assert logged in
            if args.dry_run:
                _goto_import(page)                    # reach Produkty → Import
                print("DRY-RUN OK: prihlásený, viem dôjsť na import. Nič sa nenahralo.")
                return 0
            result_text = _do_import(page, args.file) # upload + set options + run, read result area
        except Exception as e:                         # noqa: BLE001 — surface any UI failure loudly
            shot = AUDIT_DIR / f"shoptet_import_{ts}_FAIL.png"
            page.screenshot(path=str(shot))
            print(f"STOP: import zlyhal: {e}\n  screenshot: {shot}", file=sys.stderr)
            return 2
        finally:
            browser.close()
    log = parse_import_log(result_text)
    (AUDIT_DIR / f"shoptet_import_{ts}.log").write_text(result_text, encoding="utf-8")
    print(f"\nVÝSLEDOK: spracované={log['processed']} upravené={log['updated']} "
          f"zlyhania={log['failed']}")
    if log["failed"]:
        print("POZOR: Shoptet hlási zlyhania — skontroluj log.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

`_login`, `_goto_import`, `_do_import` are filled in live. Intended steps for `_do_import`, in order: open `creds["SHOPTET_ADMIN_URL"]` admin; navigate Produkty → Import; set the file input to `args.file`; choose encoding **UTF-8**; ensure **"replace empty values" is OFF**; set **pair by Code**; **read-back verify** — re-read each control's actual state and assert encoding==UTF-8, replace-empty==unchecked, pair-by==Code, else `raise ShoptetError("zlý parameter: <ktorý>")` (no import runs); then click the run/import button; wait for the result area / Log entry; return its text. Each step logs what it did; on any missing element or failed read-back it raises (caught above → screenshot + exit 2 → NO import side-effects past the verified-correct form).

- [ ] **Step 4: Live verification** (main agent, real admin)
  1. User has put real credentials in `data/.shoptet_admin` (chmod 600), incl. `SHOPTET_EXPORT_URL`.
  2. `--dry-run --headful` → confirm login + reaching the import page (screenshot). Dry-run makes NO backup, NO import.
  3. Confirm the **backup** works: a real run first writes `data/backups/export_<ts>.csv` (non-empty); verify it before the import proceeds.
  4. Confirm the **read-back parameter check** fires: observe in `--headful` that encoding=UTF-8, replace-empty=OFF, pair-by=Code are asserted; (optionally force a wrong value once to see it abort without importing).
  5. Real run on the actual `import_forestshop.csv`, confirm the printed result matches the Shoptet Log (Spracované/Upravené/Zlyhania). Adjust the log parser keywords if the live phrasing differs.
  6. Report VERIFIED with the real counts + the backup path.

- [ ] **Step 5: Commit**

```bash
git add scripts/shoptet_import.py requirements-import.txt
git commit -m "feat: Shoptet admin auto-import browser shell + CLI"
```

---

### Task 5: Docs / playbook

**Files:**
- Modify: `.claude/skills/shoptet/SKILL.md` (add an "Auto-import (script)" section: command, the `data/.shoptet_admin` secret incl. `SHOPTET_EXPORT_URL`, --dry-run/--yes, the safety chain — pre-flight CSV check → confirm → **export backup to data/backups/** → sets UTF-8 + replace-empty-OFF + pair-by-code → **read-back verify of those params** → reads the Log back).
- Modify: `README.md` (one line: how to run the auto-import).

- [ ] **Step 1: Add the skill section** documenting the command, secret file, flags, and safety (pre-flight + confirm + read-back). Run secret-scrub: `grep -nE "SHOPTET_PASS=.+|password" .claude/skills/shoptet/SKILL.md` → must show only placeholders, no real value.

- [ ] **Step 2: Commit**

```bash
git add .claude/skills/shoptet/SKILL.md README.md
git commit -m "docs: document Shoptet auto-import script in shoptet skill + README"
```

---

## Self-Review

**Spec coverage:** §3 architecture → Tasks 1-4 (core split + shell). §4 secret (incl. `SHOPTET_EXPORT_URL`) → Task 1 loader. §5 flow → pre-flight (T2) + export backup `_backup_export` (T4 step3) + login/import (T4) + param read-back (T4 `_do_import`) + log parse (T3). §6 CLI flags → Task 4 `_args`. §7 safety → pre-flight (T2) + **backup before import** (T4) + **param read-back** (T4) + confirm (T4) + result read-back (T3) + `--dry-run` (T4). §8 testing → T1-T3 unit tests + T4 live verify. §9 deps → Task 4 `requirements-import.txt`. §10 out-of-scope honored (no cron/API/XML). Covered.

**Placeholder scan:** Tasks 1-3 carry complete code. Task 4's `_login/_goto_import/_do_import` are explicitly live-discovery (web selectors can't be known before seeing the admin) with a concrete ordered action list — not a lazy TODO.

**Type consistency:** `ShoptetError`, `load_credentials`, `preflight_csv`→dict keys (`path,total,link,unavailable,discontinued,other,columns`), `classify_row`→str, `parse_import_log`→dict keys (`processed,updated,failed,raw`), `EXPECTED_HEADER` — names used identically across tasks and tests.
