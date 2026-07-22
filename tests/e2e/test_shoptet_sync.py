"""E2E of the automations tab „Sync zo Shoptetu" (#119) — real Chromium.

Against the seeded automations_server (see conftest — Shoptet creds point at a
nonexistent file, so no code path can reach the live shop): clicking ⚡ Spustiť
teraz hits the missing-credentials RuntimeError immediately — a hermetic,
network-free proof that the automation DEGRADES (❌ CHYBA shown, no crash, no
console error) instead of blowing up when creds are absent.
"""


def _console(page):
    msgs = []
    page.on("console", lambda m: msgs.append(f"[{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)
    return msgs


def _open_tab(page, base):
    page.goto(base)
    page.wait_for_selector('[data-testid="version"]')
    page.get_by_role("button", name="Sync zo Shoptetu").click()
    page.wait_for_selector('[data-testid="shoptet-sync-status"]')


def test_tab_renders_default_stopped(page, automations_server):
    console = _console(page)
    _open_tab(page, automations_server)

    # SAFETY: fresh deploy (no automations.json) = Zastavené, not running (#93 contract)
    assert page.locator('[data-testid="shoptet-sync-status"]').evaluate(
        "el => el.textContent") == "Zastavené"
    assert page.locator('[data-testid="shoptet-sync-toggle"]').inner_text().strip() == "▶ Štart"
    assert "každú hodinu" in page.locator(".autometa").inner_text()
    assert "zatiaľ nikdy" in page.locator(".autometa").inner_text()

    assert console == [], f"console not clean: {console}"


def test_start_stop_toggle_persists_across_reload(page, automations_server):
    console = _console(page)
    _open_tab(page, automations_server)

    with page.expect_response("**/api/automations/shoptet_sync/toggle"):
        page.locator('[data-testid="shoptet-sync-toggle"]').click()
    page.wait_for_function(
        "() => document.querySelector('[data-testid=shoptet-sync-status]')"
        ".textContent === 'Beží'")

    page.reload()
    page.wait_for_selector('[data-testid="version"]')
    page.get_by_role("button", name="Sync zo Shoptetu").click()
    page.wait_for_selector('[data-testid="shoptet-sync-status"]')
    assert page.locator('[data-testid="shoptet-sync-status"]').evaluate(
        "el => el.textContent") == "Beží"

    # Stop again — leave the fixture in its original state
    with page.expect_response("**/api/automations/shoptet_sync/toggle"):
        page.locator('[data-testid="shoptet-sync-toggle"]').click()
    page.wait_for_function(
        "() => document.querySelector('[data-testid=shoptet-sync-status]')"
        ".textContent === 'Zastavené'")

    assert console == [], f"console not clean: {console}"


def test_run_now_missing_creds_degrades_gracefully(page, automations_server):
    console = _console(page)
    _open_tab(page, automations_server)

    with page.expect_response("**/api/automations/shoptet_sync/run"):
        page.locator('[data-testid="shoptet-sync-run"]').click()
    page.wait_for_function(
        "() => document.querySelector('.autometa') && "
        "document.querySelector('.autometa').textContent.includes('CHYBA')",
        timeout=15000)
    meta = page.locator(".autometa").inner_text()
    assert "Posledný beh" in meta and "CHYBA" in meta
    err = page.locator(".autoerr").inner_text()
    assert "SHOPTET_ORDERS_URL" in err

    # the app itself survived (no crash) — other tabs stay usable
    page.get_by_role("button", name="Nevyzdvihnuté zásielky").click()
    page.wait_for_selector('[data-testid="posta-status"]')

    assert console == [], f"console not clean: {console}"
