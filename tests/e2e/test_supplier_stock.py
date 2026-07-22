"""E2E of the „Dodávateľský sklad" tab (#106) — real Chromium.

Against the seeded automations_server (a pre-existing supplier_stock.json with one
OK + one error row, NO automations.json → runner defaults DISABLED). Verifies the
tab renders default-Zastavené with Štart/Stop + Spustiť teraz, the availability
table + filters render, and the toggle persists across a reload. It NEVER clicks
„Spustiť teraz" (that would scrape live supplier sites + spend OpenAI).
"""


def _console(page):
    msgs = []
    page.on("console", lambda m: msgs.append(f"[{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)
    return msgs


def _open_tab(page, base):
    page.goto(base)
    page.wait_for_selector('[data-testid="version"]')
    page.get_by_role("button", name="Dodávateľský sklad").click()
    page.wait_for_selector('[data-testid="sklad-status"]')


def test_tab_renders_default_stopped_with_controls(page, automations_server):
    console = _console(page)
    _open_tab(page, automations_server)

    # SAFETY: fresh deploy (no automations.json) = Zastavené, not running (#93 contract)
    assert page.locator('[data-testid="sklad-status"]').evaluate(
        "el => el.textContent") == "Zastavené"
    assert page.locator('[data-testid="sklad-toggle"]').inner_text().strip() == "▶ Štart"
    assert page.locator('[data-testid="sklad-run"]').is_visible()
    assert "denne o 05:00" in page.locator(".autometa").inner_text()

    assert console == [], f"console not clean: {console}"


def test_table_and_filters_render(page, automations_server):
    console = _console(page)
    _open_tab(page, automations_server)

    tbl = page.locator('[data-testid="sklad-table"]')
    assert tbl.is_visible()
    body = tbl.inner_text()
    assert "Poľovnícka bunda" in body and "Skladom" in body and "129.9" in body
    assert "BETALOV" in body and "ZUBÍČEK" in body
    # the error row surfaces its error text
    assert "503" in body

    # filters present; "Len chyby" narrows to the single error row
    page.get_by_role("button", name="Len chyby (1)").click()
    assert "Lovecký nôž" in tbl.inner_text()
    assert "Poľovnícka bunda" not in tbl.inner_text()

    assert console == [], f"console not clean: {console}"


def test_start_stop_toggle_persists_across_reload(page, automations_server):
    console = _console(page)
    _open_tab(page, automations_server)

    with page.expect_response("**/api/automations/dodavatelsky_sklad/toggle"):
        page.locator('[data-testid="sklad-toggle"]').click()
    page.wait_for_function(
        "() => document.querySelector('[data-testid=sklad-status]')"
        ".textContent === 'Beží'")

    page.reload()
    page.wait_for_selector('[data-testid="version"]')
    page.get_by_role("button", name="Dodávateľský sklad").click()
    page.wait_for_selector('[data-testid="sklad-status"]')
    assert page.locator('[data-testid="sklad-status"]').evaluate(
        "el => el.textContent") == "Beží"

    # Stop again — leave the fixture in its original state
    with page.expect_response("**/api/automations/dodavatelsky_sklad/toggle"):
        page.locator('[data-testid="sklad-toggle"]').click()
    page.wait_for_function(
        "() => document.querySelector('[data-testid=sklad-status]')"
        ".textContent === 'Zastavené'")

    assert console == [], f"console not clean: {console}"
