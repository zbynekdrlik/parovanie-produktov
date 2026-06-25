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
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from parovanie.shoptet_import import (  # noqa: E402
    ShoptetError,
    load_credentials,
    parse_import_log,
    preflight_csv,
)

CRED_PATH = "data/.shoptet_admin"
DEFAULT_CSV = "data/out/import_forestshop.csv"
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
        raise ShoptetError(f"stiahnutie zálohy exportu zlyhalo: {e}")
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
            _goto_import(page, creds)
            if args.dry_run:
                page.screenshot(path=str(AUDIT_DIR / f"shoptet_import_{ts}_dryrun.png"))
                print("DRY-RUN OK: prihlásený, viem dôjsť na import. Nič sa nenahralo.")
                return 0
            result_text = _do_import(page, args.file)
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
    (AUDIT_DIR / f"shoptet_import_{ts}.log").write_text(result_text, encoding="utf-8")
    print(f"\nVÝSLEDOK: spracované={log['processed']} upravené={log['updated']} "
          f"zlyhania={log['failed']}")
    if log["failed"]:
        print("POZOR: Shoptet hlási zlyhania — skontroluj log.", file=sys.stderr)
        return 2
    return 0


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


def _goto_import(page, creds):
    """Otvor Produkty → Import. Presná cesta/selektory sa potvrdia v živom behu
    s prihlásením; fail-loud ak sa import formulár (file input) nenájde."""
    base = creds["SHOPTET_ADMIN_URL"].rstrip("/")
    page.goto(base + "/produkty-import/", wait_until="domcontentloaded")
    page.wait_for_load_state("networkidle")
    if page.locator('input[type="file"]').count() == 0:
        raise ShoptetError("nenašiel som import formulár (file input) na "
                           f"{page.url} — over cestu na Produkty → Import")
    print(f"[import] na stránke importu → {page.url}")


# Požadované hodnoty importačných parametrov (DOHODNUTÉ pravidlá, viď skill shoptet).
_WANT = {"encoding": "utf-8", "replace_empty": False, "pair_by": "code"}


def _set_import_options(page):
    """Nastav kódovanie UTF-8, „nahradiť prázdne" VYPNUTÉ, párovať podľa Kódu.
    Presné selektory sa dolaďujú v živom behu s prihlásením (fail-loud, ak chýbajú)."""
    raise ShoptetError("_set_import_options: import formulár sa dolaďuje v živom "
                       "behu s prihlásením (selektory ešte neoverené)")


def _read_back_options(page):
    """Prečítaj SKUTOČNÝ stav formulára: {'encoding','replace_empty','pair_by'}.
    Dolaďuje sa naživo (fail-loud)."""
    raise ShoptetError("_read_back_options: dolaďuje sa v živom behu s prihlásením")


def _do_import(page, csv_path):
    """Nahraj CSV, nastav parametre, OVER ich (read-back), spusti, vráť text výsledku."""
    print(f"[import] nahrávam súbor {csv_path}")
    page.locator('input[type="file"]').first.set_input_files(csv_path)
    _set_import_options(page)
    got = _read_back_options(page)
    bad = [k for k, want in _WANT.items() if got.get(k) != want]
    if bad:
        raise ShoptetError(f"zlé importačné parametre (neimportujem): {bad}; "
                           f"chcené={_WANT}, prečítané={got}")
    print(f"[import] parametre OK {got} — spúšťam import")
    page.get_by_role("button", name="Importovať").click()
    page.wait_for_load_state("networkidle")
    return page.inner_text("body")


if __name__ == "__main__":
    raise SystemExit(main())
