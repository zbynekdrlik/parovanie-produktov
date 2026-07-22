"""E2E of the „Riziko výpadku" tab (#107) — real Chromium.

Against the seeded automations_server (a pre-existing riziko_vypadku.json with one
risky row, NO automations.json -> runner defaults DISABLED). Verifies the tab
renders default-Zastavené with Štart/Stop + Spustiť teraz, the risk table + CSV
download link render, and the toggle persists across a reload. It NEVER clicks
„Spustiť teraz" (that would need a real products.csv + read data/out live).
"""


def _console(page):
    msgs = []
    page.on("console", lambda m: msgs.append(f"[{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)
    return msgs


def _open_tab(page, base):
    page.goto(base)
    page.wait_for_selector('[data-testid="version"]')
    page.get_by_role("button", name="Riziko výpadku").click()
    page.wait_for_selector('[data-testid="riziko-status"]')


def test_tab_renders_default_stopped_with_controls(page, automations_server):
    console = _console(page)
    _open_tab(page, automations_server)

    # SAFETY: fresh deploy (no automations.json) = Zastavené, not running (#93 contract)
    assert page.locator('[data-testid="riziko-status"]').evaluate(
        "el => el.textContent") == "Zastavené"
    assert page.locator('[data-testid="riziko-toggle"]').inner_text().strip() == "▶ Štart"
    assert page.locator('[data-testid="riziko-run"]').is_visible()
    assert "denne o 06:15" in page.locator(".autometa").inner_text()

    assert console == [], f"console not clean: {console}"


def test_risk_table_and_csv_download_render(page, automations_server):
    console = _console(page)
    _open_tab(page, automations_server)

    tbl = page.locator('[data-testid="riziko-table"]')
    assert tbl.is_visible()
    body = tbl.inner_text()
    assert "Bunda Risk Test" in body and "BETALOV" in body and "Vypredané" in body
    assert "99.90" in body

    dl = page.locator('[data-testid="riziko-csv"]')
    assert dl.is_visible()
    assert dl.get_attribute("href") == "/api/riziko-vypadku/csv"

    assert console == [], f"console not clean: {console}"


def test_start_stop_toggle_persists_across_reload(page, automations_server):
    console = _console(page)
    _open_tab(page, automations_server)

    with page.expect_response("**/api/automations/riziko_vypadku/toggle"):
        page.locator('[data-testid="riziko-toggle"]').click()
    page.wait_for_function(
        "() => document.querySelector('[data-testid=riziko-status]')"
        ".textContent === 'Beží'")

    page.reload()
    page.wait_for_selector('[data-testid="version"]')
    page.get_by_role("button", name="Riziko výpadku").click()
    page.wait_for_selector('[data-testid="riziko-status"]')
    assert page.locator('[data-testid="riziko-status"]').evaluate(
        "el => el.textContent") == "Beží"

    # Stop again — leave the fixture in its original state
    with page.expect_response("**/api/automations/riziko_vypadku/toggle"):
        page.locator('[data-testid="riziko-toggle"]').click()
    page.wait_for_function(
        "() => document.querySelector('[data-testid=riziko-status]')"
        ".textContent === 'Zastavené'")

    assert console == [], f"console not clean: {console}"
