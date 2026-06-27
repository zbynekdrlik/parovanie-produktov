"""GRUBE gather via HEADLESS PLAYWRIGHT — grube.sk (Shopware) renders its search
results only in a real browser (a plain requests fetch returns 0 product boxes),
so a normal SearchClient fetch can't see them. This injects a Playwright-page
fetcher into the standard gather pipeline (parser, query-ladder, top-K, checkpoint
all unchanged), for GRUBE only, into a separate out dir.

Usage: PYTHONPATH=src .venv/bin/python scripts/gather_grube.py [smoke]
Resumable via data/out_grube/gather_checkpoint.json.
"""
import os
import sys

from playwright.sync_api import sync_playwright

from parovanie.cli import run_gather
from parovanie.client import SearchClient

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"


class PlaywrightFetcher:
    """fetch(url) -> rendered HTML. Reuses one headless page; dismisses the cookie
    consent once; waits for the Shopware product grid before returning."""

    def __init__(self):
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        self._page = self._browser.new_context(user_agent=UA).new_page()
        self._page.goto("https://www.grube.sk/", wait_until="domcontentloaded", timeout=40000)
        for sel in ("button#onetrust-accept-btn-handler",
                    "button[data-testid='uc-accept-all-button']",
                    "#usercentrics-root >>> button[data-testid='uc-accept-all-button']"):
            try:
                self._page.click(sel, timeout=2500)
                break
            except Exception:  # noqa: BLE001
                pass

    def __call__(self, url: str) -> str:
        self._page.goto(url, wait_until="domcontentloaded", timeout=40000)
        try:
            self._page.wait_for_selector(".product-box", timeout=8000)
        except Exception:  # noqa: BLE001 — a no-results page has no product boxes; that's fine
            pass
        return self._page.content()

    def close(self):
        try:
            self._browser.close()
        finally:
            self._pw.stop()


def main():
    os.makedirs("data/out_grube", exist_ok=True)
    fetch = PlaywrightFetcher()
    try:
        if len(sys.argv) > 1 and sys.argv[1] == "smoke":
            from parovanie.cli import load_rows
            from parovanie.grouping import group_products
            from parovanie.matcher import gather_candidates
            prods = group_products(load_rows("data/products.csv", {"GRUBE"}))
            print(f"GRUBE products: {len(prods)}")
            cl = SearchClient(fetch=fetch, throttle=0)
            for p in prods[:2]:
                qs, cands = gather_candidates(p, cl, k=8)
                print(f"  {p.name[:45]!r} -> {len(cands)} cands | {[c.name[:30] for c in cands[:2]]}")
            return
        records = run_gather("data/products.csv", "data/out_grube", {"GRUBE"},
                             client=SearchClient(fetch=fetch, throttle=0),
                             checkpoint="data/out_grube/gather_checkpoint.json", k=8)
        print(f"GATHER DONE GRUBE: {len(records)} products -> data/out_grube/candidates.json")
    finally:
        fetch.close()


if __name__ == "__main__":
    main()
