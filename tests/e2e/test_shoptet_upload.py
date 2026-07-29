"""E2E of the „Sync do Shoptetu" card (#299 Task 7) — real Chromium.

Against the seeded `shoptet_upload_server` (see conftest — Shoptet creds point at a
nonexistent file, so no code path can reach the live shop, and the automation is
never run here). The card reads `/api/pending-shoptet` (read-only over
`pending_shoptet.json`) to say what the next hourly upload will send and what it
is holding back.
"""
import json

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
    """#299 opravné kolo 3 (1, Important) — the blocked banner used to print „eshop
    tento kód v katalógu nemá" for EVERY blocked field, no matter what
    `/api/pending-shoptet`'s own per-field `reason` actually said (the seeded fixture
    only ever carries `not-in-catalog`, so that bug never showed up here before).
    Stubs a SECOND blocked field with `stale-field` alongside the first's
    `not-in-catalog` — the two reasons must render two DIFFERENT sentences. A
    reversion to the old hard-coded text would still show „Zablokované: 2" and the
    catalogue sentence, but never the stale-field one, so it fails the second
    assertion below."""
    page.route("**/api/pending-shoptet", lambda route: route.fulfill(
        content_type="application/json",
        body=json.dumps({
            "pending": [
                {"code": "TESTUP1", "field": "internalNote", "value": "a",
                 "source": "test", "queued_at": "2026-07-29T00:00:00+02:00"},
                {"code": "TESTUP2", "field": "internalNote", "value": "b",
                 "source": "test", "queued_at": "2026-07-29T00:00:00+02:00"},
            ],
            "blocked": [
                {"code": "TESTUP3", "field": "internalNote", "value": "c",
                 "source": "test", "queued_at": "2026-07-29T00:00:00+02:00",
                 "reason": "not-in-catalog"},
                {"code": "TESTUP4", "field": "internalNote", "value": "d",
                 "source": "test", "queued_at": "2026-07-29T00:00:00+02:00",
                 "reason": "stale-field"},
            ],
        })))
    page.goto(shoptet_upload_server)
    page.locator("#systemTabs .tlabel", has_text="Sync do Shoptetu").click()
    expect(page.locator('[data-testid="pending-count"]')).to_have_text(
        "Čaká na nahratie: 2 zmeny", timeout=15000)
    blocked = page.locator('[data-testid="pending-blocked"]')
    expect(blocked).to_contain_text("Zablokované: 2")
    expect(blocked).to_contain_text("eshop tento kód v katalógu nemá")
    expect(blocked).to_contain_text("staršie, než je povolený limit")


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


# ── #299 Task 11 — the NAJDÔLEŽITEJŠIA POŽIADAVKA: the hourly cycle deploys ─── #
# ── DISABLED and is the ONLY path anything reaches the eshop by. A manager ─── #
# ── who forgets to turn it on must not be able to miss it — EVEN THOUGH the ── #
# ── cycle itself has never run (queue_stale_warning lives OUTSIDE last_result, ─
# ── see `_queue_stale_while_disabled_warning` in app.py). ────────────────────── #
def test_the_disabled_cycle_stale_queue_alarm_lights_the_badge_and_the_card(
        shoptet_upload_server, page):
    console = _console(page)
    page.goto(shoptet_upload_server)
    page.wait_for_selector('[data-testid="version"]')
    page.get_by_role("button", name="Sync do Shoptetu").click()
    page.wait_for_selector('[data-testid="shoptet-upload-status"]')

    # dark before the alarm — nothing simulated yet (mirrors the sibling test's
    # own "before" check, same pattern).
    assert page.locator('[data-testid="shoptet-upload-stale-disabled"]').count() == 0
    assert page.locator(
        '.tabs .navrow:has-text("Sync do Shoptetu") .navwarn').count() == 0

    page.evaluate("""() => {
      const a = AUTOMATIONS.find(x => x.key === 'shoptet_upload');
      a.queue_stale_warning = 'Hodinový cyklus „Sync do Shoptetu“ je vypnutý a '
        + 'vo fronte na neho čaká práca už 5 h — nič z toho sa nedostane do '
        + 'eshopu, kým ho nezapneš (▶ Štart na karte „Sync do Shoptetu“).';
      renderTabs();
      renderShoptetUpload();
    }""")

    banner = page.locator('[data-testid="shoptet-upload-stale-disabled"]').inner_text()
    assert "vypnutý" in banner and "5 h" in banner, banner
    assert page.locator(
        '.tabs .navrow:has-text("Sync do Shoptetu") .navwarn').count() == 1

    # …and it goes dark once the alarm clears (cycle started, or queue emptied)
    # — a live signal, never permanently on.
    page.evaluate("""() => {
      const a = AUTOMATIONS.find(x => x.key === 'shoptet_upload');
      a.queue_stale_warning = '';
      renderTabs();
      renderShoptetUpload();
    }""")
    assert page.locator('[data-testid="shoptet-upload-stale-disabled"]').count() == 0
    assert page.locator(
        '.tabs .navrow:has-text("Sync do Shoptetu") .navwarn').count() == 0

    assert console == [], f"console not clean: {console}"


def test_run_warnings_render_as_their_own_banner_and_light_the_badge(
        shoptet_upload_server, page):
    """`last_result.warnings` (#299 Task 11) — a run that itself reports
    `ok: true` (nothing THIS cycle attempted failed) can still be `degraded`
    (a producer's empty streak, a skipped second download). Each sentence
    renders as its own `.uploadwarn` block, and the sidebar badge lights from
    `last_result.degraded`, not from `ok`."""
    console = _console(page)
    page.goto(shoptet_upload_server)
    page.wait_for_selector('[data-testid="version"]')
    page.get_by_role("button", name="Sync do Shoptetu").click()
    page.wait_for_selector('[data-testid="shoptet-upload-status"]')

    page.evaluate("""() => {
      const a = AUTOMATIONS.find(x => x.key === 'shoptet_upload');
      a.last_run = '2026-07-28T10:00:00+02:00';
      a.last_status = 'ok';
      a.last_error = '';
      a.last_result = {ok: true, error: '', queued: 0, sent: 0, confirmed: 0,
                       blocked: 0, stale_blocked: [], resynced: 1,
                       skipped_second_sync: true, unconfirmed: 0,
                       degraded: true,
                       // #299 opravné kolo 2 review N5 — the old empty-queue-streak
                       // signal this text quoted ("nezaradili 3 hodinové behy po
                       // sebe nič") was removed in opravné kolo 1 (review C1); the
                       // server can no longer produce that sentence. This is the
                       // ACTUAL shape `_stale_producer_warnings()` writes today.
                       warnings: ['Automatizácia GRUBE kódy → eshop je zapnutá, '
                                  + 'ale nebežala už 50 h — over, či jej nezlyhal '
                                  + 'zdroj dát alebo plánovač.']};
      renderTabs();
      renderShoptetUpload();
    }""")

    warn = page.locator('[data-testid="shoptet-upload-warning"]')
    expect_text = "nebežala už 50 h"
    assert expect_text in warn.inner_text(), warn.inner_text()
    # `degraded: true` lights the badge even though `ok: true` — the whole
    # point of the widened predicate in navError().
    assert page.locator(
        '.tabs .navrow:has-text("Sync do Shoptetu") .navwarn').count() == 1
    # "Posledný beh" must read DEGRADOVANÝ, never OK, for a degraded run.
    assert "DEGRADOVANÝ" in page.locator(".autostatus .autometa").inner_text()

    assert console == [], f"console not clean: {console}"


# ── #299 opravné kolo 1 review I3 (Important) — every test above proves the ─── #
# ── RENDERING of this alarm by INJECTING it via `page.evaluate`; none of them ─
# ── ever exercises the REAL path the whole alarm exists for: an old field ──── #
# ── genuinely sitting in `pending_shoptet.json`, the cycle genuinely never ─── #
# ── started, `/api/automations` computing the warning server-side, and the ─── #
# ── sidebar + card rendering it from THAT real response. Zero injection here. #
def test_the_real_stale_disabled_path_lights_the_badge_and_banner_from_a_genuine_poll(
        shoptet_upload_stale_disabled_server, page):
    console = _console(page)
    page.goto(shoptet_upload_stale_disabled_server)
    page.wait_for_selector('[data-testid="version"]')

    # the sidebar badge is lit from the FIRST real `/api/automations` poll at
    # page load — before the "Sync do Shoptetu" tab is even opened.
    expect(page.locator(
        '.tabs .navrow:has-text("Sync do Shoptetu") .navwarn')).to_have_count(1)

    page.get_by_role("button", name="Sync do Shoptetu").click()
    banner = page.locator('[data-testid="shoptet-upload-stale-disabled"]')
    expect(banner).to_be_visible()
    text = banner.inner_text()
    assert "vypnutý" in text and "Sync do Shoptetu" in text and "h" in text, text

    assert console == [], f"console not clean: {console}"
