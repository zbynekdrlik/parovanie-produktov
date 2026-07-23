"""E2E of the automations tab „Kontrola obrázkov" (#135) — real Chromium.

Against the seeded automations_server (no review_data.json → 0 products, so 0
our_images URLs): clicking ⚡ Spustiť teraz is hermetic here — the run finds no
URLs to HEAD-check, so it completes with zero network calls (unlike every other
scrape/write automation's e2e, which must never click Spustiť teraz for real).
"""


def _console(page):
    msgs = []
    page.on("console", lambda m: msgs.append(f"[{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)
    return msgs


def _open_tab(page, base):
    page.goto(base)
    page.wait_for_selector('[data-testid="version"]')
    page.get_by_role("button", name="Kontrola obrázkov").click()
    page.wait_for_selector('[data-testid="image-health-status"]')


def test_tab_renders_default_stopped(page, automations_server):
    console = _console(page)
    _open_tab(page, automations_server)

    # SAFETY: fresh deploy (no automations.json entry) = Zastavené (#93 contract)
    assert page.locator('[data-testid="image-health-status"]').evaluate(
        "el => el.textContent") == "Zastavené"
    assert page.locator('[data-testid="image-health-toggle"]').inner_text().strip() == "▶ Štart"
    assert "denne o 04:30" in page.locator(".autometa").inner_text()
    assert "zatiaľ nikdy" in page.locator(".autometa").inner_text()

    assert console == [], f"console not clean: {console}"


def test_start_stop_toggle_persists_across_reload(page, automations_server):
    console = _console(page)
    _open_tab(page, automations_server)

    with page.expect_response("**/api/automations/image_health/toggle"):
        page.locator('[data-testid="image-health-toggle"]').click()
    page.wait_for_function(
        "() => document.querySelector('[data-testid=image-health-status]')"
        ".textContent === 'Beží'")

    page.reload()
    page.wait_for_selector('[data-testid="version"]')
    page.get_by_role("button", name="Kontrola obrázkov").click()
    page.wait_for_selector('[data-testid="image-health-status"]')
    assert page.locator('[data-testid="image-health-status"]').evaluate(
        "el => el.textContent") == "Beží"

    # Stop again — leave the fixture in its original state
    with page.expect_response("**/api/automations/image_health/toggle"):
        page.locator('[data-testid="image-health-toggle"]').click()
    page.wait_for_function(
        "() => document.querySelector('[data-testid=image-health-status]')"
        ".textContent === 'Zastavené'")

    assert console == [], f"console not clean: {console}"


def test_run_now_zero_products_completes_with_no_network(page, automations_server):
    console = _console(page)
    _open_tab(page, automations_server)

    with page.expect_response("**/api/automations/image_health/run"):
        page.locator('[data-testid="image-health-run"]').click()
    page.wait_for_function(
        "() => document.querySelector('.autometa') && "
        "document.querySelector('.autometa').textContent.includes('OK')",
        timeout=15000)
    meta = page.locator(".autometa").inner_text()
    assert "Posledný beh" in meta and "OK" in meta
    body = page.locator(".autostatus").inner_text()
    assert "Skontrolovaných: 0" in body
    assert "mŕtvych odkazov: 0" in body

    assert console == [], f"console not clean: {console}"
