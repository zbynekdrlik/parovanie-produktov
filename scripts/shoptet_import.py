#!/usr/bin/env python3
"""Opatrný auto-import do Shoptet adminu.

Reťazec poistiek:
  pred-letová kontrola CSV → potvrdenie → ZÁLOHA exportu (data/backups/) →
  login → Produkty/Import (UTF-8, „nahradiť prázdne" VYPNUTÉ, párovať podľa Kódu)
  → READ-BACK kontrola tých parametrov → spustenie → prečítanie skutočného
  výsledku z Logu.

Prihlásenie aj export URL z data/.shoptet_admin (mimo gitu, chmod 600).

Beh:
  PYTHONPATH=src .venv/bin/python scripts/shoptet_import.py [--file CSV] [--dry-run] [--yes] [--headful]

Lokálny setup (raz):
  .venv/bin/pip install -r requirements-import.txt && .venv/bin/playwright install chromium
"""
import argparse
import re
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from parovanie.shoptet_import import (  # noqa: E402
    ShoptetError,
    has_log_entries,
    load_credentials,
    log_entry_id,
    parse_import_log,
    pick_result_row,
    preflight_csv,
    result_exit_code,
)

CRED_PATH = "data/.shoptet_admin"
DEFAULT_CSV = "data/out/import_links.csv"
AUDIT_DIR = Path("data/out")
BACKUP_DIR = Path("data/backups")


# --------------------------------------------------------------------------- #
# Záloha (bez prehliadača)
# --------------------------------------------------------------------------- #
def _backup_export(creds):
    """Stiahni CELÝ katalóg PRED importom → data/backups/export_<ts>.csv, aby sa
    dal stav vrátiť späť. Chýba URL / sieťová chyba / prázdny súbor => ShoptetError
    (=> volajúci NEimportuje)."""
    url = creds.get("SHOPTET_EXPORT_URL")
    if not url:
        raise ShoptetError("chýba SHOPTET_EXPORT_URL v data/.shoptet_admin — bez zálohy neimportujem")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    dest = BACKUP_DIR / f"export_{time.strftime('%Y%m%d-%H%M%S')}.csv"
    print("[záloha] sťahujem export katalógu …")
    try:
        r = requests.get(url, timeout=120)
        r.raise_for_status()
    except requests.RequestException as e:
        # NEvkladaj `e` do hlášky — obsahuje URL s tajným hashom; len typ chyby
        raise ShoptetError(f"stiahnutie zálohy exportu zlyhalo: {type(e).__name__} "
                           "(URL skrytá — over SHOPTET_EXPORT_URL)")
    if not r.content:
        raise ShoptetError("export zálohy je prázdny — neimportujem")
    dest.write_bytes(r.content)
    print(f"[záloha] {len(r.content)} B → {dest}")
    return str(dest)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _args():
    ap = argparse.ArgumentParser(description="Opatrný auto-import CSV do Shoptetu")
    ap.add_argument("--file", default=DEFAULT_CSV, help="ktoré CSV nahrať")
    ap.add_argument("--yes", action="store_true", help="bez interaktívneho potvrdenia")
    ap.add_argument("--dry-run", action="store_true",
                    help="len kontrola + login + dôjsť na import; NIČ nenahrá")
    ap.add_argument("--headful", action="store_true", help="ukázať okno prehliadača")
    return ap.parse_args()


def _print_plan(plan):
    print(f"\nSúbor:   {plan['path']}")
    print(f"Riadkov: {plan['total']}")
    print(f"  • napárované (link):        {plan['link']}")
    print(f"  • nie je skladom:           {plan['unavailable']}")
    print(f"  • už sa nebude predávať:    {plan['discontinued']}")
    if plan.get("externalcode"):
        print(f"  • grube kód (externalCode): {plan['externalcode']}")
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


# --------------------------------------------------------------------------- #
# Prehliadač (Playwright) — lazy import, aby jadro v CI nepotrebovalo browser
# --------------------------------------------------------------------------- #
def _run_browser(args, creds, plan):
    from playwright.sync_api import sync_playwright
    ts = time.strftime("%Y%m%d-%H%M%S")
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headful)
        ctx = browser.new_context(accept_downloads=False)
        page = ctx.new_page()
        try:
            _login(page, creds)
            # #23: snapshot the topmost Log row BEFORE we touch anything, so
            # _read_result can tell a genuinely NEW row (this run's result)
            # apart from a row that was already there (a stale/previous run).
            baseline = _capture_baseline(page, creds)
            _goto_import(page, creds)
            if args.dry_run:
                page.screenshot(path=str(AUDIT_DIR / f"shoptet_import_{ts}_dryrun.png"))
                print("DRY-RUN OK: prihlásený, viem dôjsť na import. Nič sa nenahralo.")
                return 0
            # #257: baseline ALONE cannot tell our Log entry from a foreign one written
            # seconds later — the number of rows we submit (Shoptet echoes it back as
            # 'Spracované: N') is what identifies this run's own result row.
            result_text = _do_import(page, args.file, baseline=baseline,
                                     expected_rows=plan["total"])
        except Exception as e:  # noqa: BLE001 — surface any UI failure loudly
            shot = AUDIT_DIR / f"shoptet_import_{ts}_FAIL.png"
            try:
                page.screenshot(path=str(shot))
            except Exception:  # noqa: BLE001
                shot = "(screenshot sa nepodaril)"
            print(f"STOP: import zlyhal: {e}\n  screenshot: {shot}", file=sys.stderr)
            return 2
        finally:
            browser.close()
    log = parse_import_log(result_text)
    (AUDIT_DIR / f"shoptet_import_{ts}.log").write_text(
        result_text or "(žiadny výsledok sa nepodarilo prečítať — over Log ručne)",
        encoding="utf-8")
    print(f"\nVÝSLEDOK: spracované={log['processed']} upravené={log['updated']} "
          f"zlyhania={log['failed']}")
    rc = result_exit_code(log)
    if rc != 0:
        if log.get("error_detail"):
            # Shoptet aborted the WHOLE import (e.g. duplicate 'code') — no
            # Spracované/Zlyhanie summary at all, only this hard error line. Print it
            # to STDOUT too (AFTER the VÝSLEDOK marker), so the app still sees WHY
            # when it parses only its own result slice.
            print(f"CHYBA LOGU: {log['error_detail']}")
            print(f"POZOR: Shoptet hlási chybu: {log['error_detail']}", file=sys.stderr)
        elif log["processed"] is None:
            # Výsledok sa nedal PRIRADIŤ tomuto behu (#257): buď sa náš riadok Logu
            # neobjavil, alebo tam bežal ešte jeden import s rovnakým počtom riadkov.
            # NIKDY nehlás cudzí riadok ako svoj — radšej "neprečítané" (exit 2).
            print("POZOR: výsledok tohto importu sa nepodarilo priradiť "
                  "(náš riadok Logu sa neobjavil alebo je nejednoznačný) — over Log ručne.",
                  file=sys.stderr)
        else:
            print("POZOR: Shoptet hlási zlyhania — skontroluj log.", file=sys.stderr)
    return rc


def _login(page, creds):
    """Prihlásenie do Shoptet adminu. Selektory overené na verejnej login stránke
    (placeholder 'E-mail' / 'Vaše heslo', tlačidlo 'Prihlásenie')."""
    url = creds["SHOPTET_ADMIN_URL"]
    print(f"[login] {url}")
    page.goto(url, wait_until="domcontentloaded")
    page.get_by_placeholder("E-mail").fill(creds["SHOPTET_USER"])
    page.get_by_placeholder("Vaše heslo").fill(creds["SHOPTET_PASS"])
    page.get_by_role("button", name="Prihlásenie").click()
    page.wait_for_load_state("networkidle")
    if "/login" in page.url:
        raise ShoptetError("prihlásenie zlyhalo (stále na /login) — skontroluj meno/heslo")
    print(f"[login] OK → {page.url}")


IMPORT_PATH = "import-produktov/"
LOG_PATH = "import-produktov/log/"
# Bezpečný režim: produkty/varianty MIMO súboru NEMENIŤ (nikdy "Zmazať").
_SAFE_RADIO = "Nemeniť produkty a varianty, ktoré nie sú obsiahnuté v importovanom súbore."
_URL_BY_NAME = "Zmeňte adresu URL produktu podľa názvu produktu."


def _capture_baseline(page, creds):
    """#23: read the topmost Log row BEFORE this import is submitted, so
    _read_result can tell a genuinely NEW row (this run's result) apart from
    a row that was already there (a stale/previous run). Read-only page visit
    — safe to do before touching the upload form. Returns None if the Log is
    empty/unreadable (no previous runs, or first-ever import)."""
    base = creds["SHOPTET_ADMIN_URL"].rstrip("/")
    page.goto(f"{base}/{LOG_PATH}", wait_until="domcontentloaded")
    page.wait_for_load_state("networkidle")
    baseline = pick_result_row(_row_texts(page))
    # Print the entry NUMBER, never the row text: webreview/app.py parses this stdout
    # for the import result, and an echoed 'Spracované: N' from the PREVIOUS entry
    # sits AHEAD of our own result line — parse_import_log takes the first match, so
    # the app read the baseline's counts as this run's result (#196/#257).
    bid = log_entry_id(baseline) if baseline else None
    print(f"[import] baseline (posledný záznam Logu pred behom): "
          f"{f'#{bid}' if bid else '(bez čísla)' if baseline else '(žiadny)'}")
    return baseline


def _goto_import(page, creds):
    """Otvor Produkty → Import (/admin/import-produktov/). Fail-loud, ak formulár
    (skrytý file input) chýba."""
    base = creds["SHOPTET_ADMIN_URL"].rstrip("/")
    page.goto(f"{base}/{IMPORT_PATH}", wait_until="domcontentloaded")
    page.wait_for_load_state("networkidle")
    if page.locator('input[type="file"][name="file"]').count() == 0:
        raise ShoptetError(f"nenašiel som import formulár (file input) na {page.url}")
    print(f"[import] na stránke importu → {page.url}")


def _ensure_safe_settings(page):
    """Nastav a OVER (read-back) bezpečné parametre. Shoptet auto-detekuje kódovanie
    (náš BOM = UTF-8) a páruje podľa 'code' — na formulári to voľby nemajú. Jediné
    rizikové voľby sú: režim mimo-súboru (musí byť 'Nemeniť', NIKDY 'Zmazať') a
    'Zmeniť URL podľa názvu' (musí byť VYPNUTÉ). Nesedí → ShoptetError, neimportuje."""
    safe = page.get_by_role("radio", name=_SAFE_RADIO)
    if safe.count() == 0:
        raise ShoptetError("nenašiel som voľbu režimu importu (radio 'Nemeniť …')")
    safe.check()
    url_by_name = page.get_by_role("checkbox", name=_URL_BY_NAME)
    if url_by_name.count() == 0:
        # checkbox chýba na formulári → funkcia je vypnutá; nič netreba meniť
        print("[import] checkbox 'Zmeniť URL podľa názvu' nenájdený — predpokladám VYPNUTÉ")
    elif url_by_name.is_checked():
        url_by_name.uncheck()
    if not safe.is_checked():
        raise ShoptetError("bezpečný režim 'Nemeniť produkty mimo súboru' sa nepodarilo zvoliť")
    if url_by_name.count() and url_by_name.is_checked():
        raise ShoptetError("'Zmeniť URL podľa názvu' ostáva zapnuté — neimportujem")
    print("[import] parametre OK: režim=Nemeniť-mimo-súboru, URL-podľa-názvu=VYPNUTÉ, "
          "kódovanie=auto(BOM→UTF-8), párovanie=code")


def _row_texts(page):
    """Text of every <tr> on the current page, top-to-bottom (Shoptet renders
    the Log NEWEST FIRST), whitespace-collapsed. Pure DOM read — the actual
    row-picking logic lives in parovanie.shoptet_import.pick_result_row
    (testable with fixtures, no browser needed)."""
    return page.evaluate(
        "() => Array.from(document.querySelectorAll('tr'))"
        ".map(r => (r.innerText || '').replace(/\\s+/g,' ').trim())"
    )


def _entry_key(row):
    """WHICH Log entry a row text points at — Shoptet's own entry number when the row
    carries one, else the text itself. The rendered text is NOT stable (a late-rendered
    link, a relative time), so keying on it alone would make a perfectly good import
    never agree with itself and end up unattributable."""
    eid = log_entry_id(row)
    return eid if eid is not None else row


def _read_result(page, baseline=None, expected_rows=None, retries=6, wait_s=2.0):
    """Read back THIS run's result row from /admin/import-produktov/log/ (#23).

    `baseline` is the topmost log-entry text captured BEFORE the import was
    submitted (see _capture_baseline) and `expected_rows` is how many rows the
    submitted CSV carries — together they identify THIS run's entry (see
    pick_result_row). A large/async import may not have written its own row yet
    on the first read, and a foreign import may write ITS row first (#257) —
    poll (wait + reload) until our own entry appears, up to `retries` times.
    Never falls back to an OLDER row further down the table: that was the exact
    issue #23 bug — an import that aborted with only a 'Chyba | Číslo riadku: N -
    …' entry (no Spracované/Zlyhanie line) made the OLD keyword-only picker
    skip past it to a PREVIOUS run's 'Spracované' row and report a stale
    success.

    The WHOLE poll window is always exhausted before anything is accepted, and a pick
    counts only if EXACTLY ONE entry matched across every read AND it is still the
    match at the LAST read. The row count alone cannot tell our entry from a concurrent
    import of the same size — and every large batch is chunked to exactly
    IMPORT_CHUNK_ROWS rows, so "same size" is the normal collision, not an exotic one.
    Returning as soon as two consecutive reads agreed (the first shape of this settle
    check) covered only ~ONE wait_s interval: a foreign 300-row import writing between
    our baseline and our first read was accepted ~2 s later, before Shoptet had written
    OUR entry at all — chunk_outcome booked `ok`, its 300 codes were recorded uploaded
    and new_pairing_keys skipped them forever (a permanent, silent loss). Waiting the
    window out lets our own entry appear, which makes the read ambiguous → None → exit 2
    (fail closed, the rows are simply re-sent next run) instead of a false success.

    The verdict is taken from the last read that could actually SEE the Log
    (has_log_entries), not simply from the last read: `page.reload(networkidle)` can
    return before Shoptet paints the table, and one such empty read at the end of the
    window used to discard a window in which every real read had agreed on our entry
    — booking a successful chunk as unreadable and, through the caller's `break`,
    skipping the rest of the night's chunks. A read that DID render log rows and still
    could not attribute them stays fatal: on a LATE ambiguity pick_result_row returns
    None without adding to `seen`, so `len(seen) == 1` alone would credit a run
    Shoptet never confirmed.

    Deliberate trade in the settle check: `_entry_key` falls back to the raw row text
    for an entry that carries no '#id', so a ticking relative time in such a row makes
    every read a different key → ambiguous → None, where the older two-consecutive
    settle would have returned it. That path is only reachable on the "older/other
    renderings" log_entry_id documents (every live fixture and every archived log
    carries a '#12689'-style id) and it fails CLOSED — the rows are re-sent next run.
    Trading a rare needless retry for never crediting an unidentifiable entry is the
    intended behaviour here, not an oversight.

    Returns the picked row text, or None if our own entry never appeared / no
    log entry is found at all / two same-sized imports made it ambiguous.
    parse_import_log(None) then yields processed=None, which result_exit_code()
    treats as an UNREADABLE result (exit 2) — never a silent success."""
    seen, verdict, verdict_is_real = set(), None, False
    for attempt in range(retries):
        texts = _row_texts(page)
        row = pick_result_row(texts, baseline=baseline, expected_rows=expected_rows)
        if row is not None:
            seen.add(_entry_key(row))
        if has_log_entries(texts):
            # this read really saw the Log — its outcome (a row, or None for
            # "rendered but unattributable") supersedes any earlier one
            verdict, verdict_is_real = row, True
        if attempt < retries - 1:
            page.wait_for_timeout(int(wait_s * 1000))
            page.reload(wait_until="networkidle")
    if not verdict_is_real or verdict is None or len(seen) != 1:
        return None
    return verdict


def _do_import(page, csv_path, baseline=None, expected_rows=None):
    """Nahraj CSV (cez file-chooser — Shoptet widget inak súbor nezaregistruje),
    over bezpečné parametre, spusti import (Importovať → Log), vráť text výsledku
    (viď _read_result — vráti LEN riadok Logu patriaci tomuto behu, inak None)."""
    print(f"[import] nahrávam súbor {csv_path}")
    with page.expect_file_chooser() as fc:
        page.locator('button:has-text("Vyberte súbor")').first.click()
    fc.value.set_files(csv_path)
    page.wait_for_timeout(800)   # widget zaregistruje súbor (názov sa zobrazí na tlačidle)
    _ensure_safe_settings(page)
    print("[import] spúšťam import …")
    page.get_by_test_id("buttonImport").click()
    page.wait_for_url(re.compile(r"import-produktov/log"), timeout=120000)
    page.wait_for_load_state("networkidle")
    return _read_result(page, baseline=baseline, expected_rows=expected_rows)


if __name__ == "__main__":
    raise SystemExit(main())
