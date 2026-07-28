"""E2E of the „Sync do Shoptetu" card (#299 Task 7) — real Chromium.

Against the seeded `shoptet_upload_server` (see conftest — Shoptet creds point at a
nonexistent file, so no code path can reach the live shop, and the automation is
never run here). The card reads `/api/pending-shoptet` (read-only over
`pending_shoptet.json`) to say what the next hourly upload will send and what it
is holding back.
"""
from playwright.sync_api import expect


def _console(page):
    msgs = []
    page.on("console", lambda m: msgs.append(f"[{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)
    return msgs


def test_plural_zmeny_declines_every_slovak_case(shoptet_upload_server, page):
    """Table-driven over the whole declension rule (`toorder-e2e.md` §3), including the
    counts that only differ under a naive `n > 1 → plural` reading (11/21/101 stay
    genitive). `pluralZmeny` is a bare classic-script function, reachable in the
    browser realm the same way `itemsWord` is in test_order_group_header.py."""
    page.goto(shoptet_upload_server)
    page.wait_for_selector('[data-testid="version"]')

    out = page.evaluate("""() =>
      [0, 1, 2, 4, 5, 11, 21, 101].map(n => pluralZmeny(n))""")

    assert out == ["0 zmien", "1 zmena", "2 zmeny", "4 zmeny",
                   "5 zmien", "11 zmien", "21 zmien", "101 zmien"]


def test_the_card_sits_in_the_System_folder_under_the_download_sync(shoptet_upload_server, page):
    page.goto(shoptet_upload_server)
    labels = page.locator("#systemTabs .tlabel")
    expect(labels).to_have_count(2, timeout=15000)
    expect(labels.nth(0)).to_have_text("Sync zo Shoptetu")
    expect(labels.nth(1)).to_have_text("Sync do Shoptetu")


def test_the_card_names_how_many_changes_are_waiting_and_which_are_blocked(
        shoptet_upload_server, page):
    page.goto(shoptet_upload_server)
    page.locator("#systemTabs .tlabel", has_text="Sync do Shoptetu").click()
    expect(page.locator('[data-testid="pending-count"]')).to_have_text(
        "Čaká na nahratie: 2 zmeny", timeout=15000)
    blocked = page.locator('[data-testid="pending-blocked"]')
    expect(blocked).to_contain_text("Zablokované: 1")
    expect(blocked).to_contain_text("eshop tento kód v katalógu nemá")


def test_the_console_stays_clean(shoptet_upload_server, page):
    msgs = []
    page.on("console", lambda m: msgs.append(m))
    page.goto(shoptet_upload_server)
    page.locator("#systemTabs .tlabel", has_text="Sync do Shoptetu").click()
    page.wait_for_timeout(500)
    assert [m.text for m in msgs if m.type in ("error", "warning")] == []


# ── #299 review decision 2 — `run_shoptet_upload` fails by a NORMAL return, ── #
# never a raised exception, so `AutomationRunner` keeps `last_status='ok'` for
# "cyklus už beží" / "iný import práve beží" / unconfirmed-or-blocked rows. Both
# the card's own ❌ banner AND the sidebar ⚠ badge must read `last_result.ok`,
# never `last_status` alone — otherwise these three failure states render as a
# silently healthy run, exactly the class of bug `automation-health.md` §3 warns
# about (and the one #153's badge exists to catch).
def test_a_last_status_ok_run_that_actually_failed_still_shows_red(
        shoptet_upload_server, page):
    console = _console(page)
    page.goto(shoptet_upload_server)
    page.wait_for_selector('[data-testid="version"]')
    page.get_by_role("button", name="Sync do Shoptetu").click()
    page.wait_for_selector('[data-testid="shoptet-upload-status"]')

    # sidebar badge dark before the mutation (nothing has run yet)
    badge = page.locator('.tabs .navrow:has-text("Sync do Shoptetu") .navwarn')
    assert badge.count() == 0, page.locator(".tabs").inner_text()

    page.evaluate("""() => {
      const a = AUTOMATIONS.find(x => x.key === 'shoptet_upload');
      a.last_run = '2026-07-28T10:00:00+02:00';
      a.last_status = 'ok';           // #153 badge / .autoerr must NOT trust this alone
      a.last_error = '';
      a.last_result = {ok: false, error: 'iný import práve beží', queued: 3,
                       sent: 0, confirmed: 0, blocked: 1, stale_blocked: [],
                       resynced: 1, skipped_second_sync: false,
                       unconfirmed: 0};
      renderTabs();
      renderShoptetUpload();
    }""")

    banner = page.locator(".autostatus .autoerr").inner_text()
    assert "iný import práve beží" in banner, banner
    assert page.locator(
        '.tabs .navrow:has-text("Sync do Shoptetu") .navwarn').count() == 1

    # …and it goes dark again once the run is genuinely clean — proving the ⚠ is
    # a live signal, not permanently on (same paired check as test_shoptet_sync.py).
    page.evaluate("""() => {
      const a = AUTOMATIONS.find(x => x.key === 'shoptet_upload');
      a.last_result = {ok: true, error: '', queued: 0, sent: 0, confirmed: 0,
                       blocked: 0, stale_blocked: [],
                       resynced: 1, skipped_second_sync: true, unconfirmed: 0};
      renderTabs();
      renderShoptetUpload();
    }""")
    assert page.locator(".autostatus .autoerr").count() == 0
    assert page.locator(
        '.tabs .navrow:has-text("Sync do Shoptetu") .navwarn').count() == 0

    assert console == [], f"console not clean: {console}"
