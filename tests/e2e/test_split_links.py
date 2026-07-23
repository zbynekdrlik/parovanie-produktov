"""E2E of the automations tab „Veľkostné linky → eshop" (#192) — real Chromium.

Against the seeded automations_server (see conftest — no variant_links.json / no
review_data.json, Shoptet creds point at a nonexistent file): the tab defaults to
Zastavené (#93 safety), Štart/Stop persists across a reload, and a ⚡ Spustiť teraz run
finds 0 split products / 0 new links, so it calls NO Shoptet import at all (hermetic,
network-free) and reports a clean OK — never touching the live eshop.
"""


def _console(page):
    msgs = []
    page.on("console", lambda m: msgs.append(f"[{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)
    return msgs


def _open_tab(page, base):
    page.goto(base)
    page.wait_for_selector('[data-testid="version"]')
    page.get_by_role("button", name="Veľkostné linky → eshop").click()
    page.wait_for_selector('[data-testid="splitlinks-status"]')


def test_tab_renders_default_stopped(page, automations_server):
    console = _console(page)
    _open_tab(page, automations_server)

    # SAFETY: fresh deploy (no automations.json entry) = Zastavené, not running (#93)
    assert page.locator('[data-testid="splitlinks-status"]').evaluate(
        "el => el.textContent") == "Zastavené"
    assert page.locator('[data-testid="splitlinks-toggle"]').inner_text().strip() == "▶ Štart"
    assert "denne o 03:45" in page.locator(".autometa").inner_text()
    assert "zatiaľ nikdy" in page.locator(".autometa").inner_text()

    assert console == [], f"console not clean: {console}"


def test_start_stop_toggle_persists_across_reload(page, automations_server):
    console = _console(page)
    _open_tab(page, automations_server)

    with page.expect_response("**/api/automations/split_links/toggle"):
        page.locator('[data-testid="splitlinks-toggle"]').click()
    page.wait_for_function(
        "() => document.querySelector('[data-testid=splitlinks-status]')"
        ".textContent === 'Beží'")

    page.reload()
    page.wait_for_selector('[data-testid="version"]')
    page.get_by_role("button", name="Veľkostné linky → eshop").click()
    page.wait_for_selector('[data-testid="splitlinks-status"]')
    assert page.locator('[data-testid="splitlinks-status"]').evaluate(
        "el => el.textContent") == "Beží"

    # Stop again — leave the fixture in its original state
    with page.expect_response("**/api/automations/split_links/toggle"):
        page.locator('[data-testid="splitlinks-toggle"]').click()
    page.wait_for_function(
        "() => document.querySelector('[data-testid=splitlinks-status]')"
        ".textContent === 'Zastavené'")

    assert console == [], f"console not clean: {console}"


def test_run_now_zero_new_reports_ok_without_touching_eshop(page, automations_server):
    console = _console(page)
    _open_tab(page, automations_server)

    with page.expect_response("**/api/automations/split_links/run"):
        page.locator('[data-testid="splitlinks-run"]').click()
    # no variant_links.json / no split products seeded → 0 new to push → no Shoptet
    # import → clean OK
    page.wait_for_function(
        "() => document.querySelector('.autometa') && "
        "document.querySelector('.autometa').textContent.includes('OK')",
        timeout=15000)
    meta = page.locator(".autometa").inner_text()
    assert "Posledný beh" in meta and "OK" in meta and "CHYBA" not in meta
    result_text = page.locator(".autoresult").inner_text()
    assert "Veľkostné linky" in result_text and "+0 nových" in result_text

    # the app itself survived (no crash) — other tabs stay usable
    page.get_by_role("button", name="Nevyzdvihnuté zásielky").click()
    page.wait_for_selector('[data-testid="posta-status"]')

    assert console == [], f"console not clean: {console}"
