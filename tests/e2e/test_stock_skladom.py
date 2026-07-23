"""E2E of the „Máme skladom → Skladom" auto-restock tab (#98) — real Chromium.

Against the seeded automations_server (a pre-existing stock_skladom.json with one
flipped candidate, NO automations.json -> runner defaults DISABLED). Verifies the
tab renders default-Zastavené with Štart/Stop + Spustiť teraz, the candidate table +
import outcome render, and the toggle persists across a reload. It NEVER clicks
„Spustiť teraz" — that WRITES to the live eshop (needs a real products.csv + import).
"""


def _console(page):
    msgs = []
    page.on("console", lambda m: msgs.append(f"[{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)
    return msgs


def _open_tab(page, base):
    page.goto(base)
    page.wait_for_selector('[data-testid="version"]')
    page.get_by_role("button", name="Máme skladom → Skladom").click()
    page.wait_for_selector('[data-testid="stock-skladom-status"]')


def test_tab_renders_default_stopped_with_controls(page, automations_server):
    console = _console(page)
    _open_tab(page, automations_server)

    # SAFETY: fresh deploy (no automations.json) = Zastavené, not running (#93 contract)
    assert page.locator('[data-testid="stock-skladom-status"]').evaluate(
        "el => el.textContent") == "Zastavené"
    assert page.locator('[data-testid="stock-skladom-toggle"]').inner_text().strip() == "▶ Štart"
    assert page.locator('[data-testid="stock-skladom-run"]').is_visible()
    assert "denne o 06:45" in page.locator("#tab-stock_skladom .autometa").inner_text()

    assert console == [], f"console not clean: {console}"


def test_candidate_table_and_import_outcome_render(page, automations_server):
    console = _console(page)
    _open_tab(page, automations_server)

    tbl = page.locator('[data-testid="stock-skladom-table"]')
    assert tbl.is_visible()
    body = tbl.inner_text()
    assert "Fotopasca Máme Skladom Test" in body and "9/M" in body
    assert "129.90" in body and "5 ks" in body and "Vypredané" in body

    # the last run's import outcome line (prepnuté = updated)
    status = page.locator('#tab-stock_skladom .autostatus').inner_text()
    assert "Prepnutých na Skladom: 1" in status

    assert console == [], f"console not clean: {console}"


def test_start_stop_toggle_persists_across_reload(page, automations_server):
    console = _console(page)
    _open_tab(page, automations_server)

    with page.expect_response("**/api/automations/stock_skladom/toggle"):
        page.locator('[data-testid="stock-skladom-toggle"]').click()
    page.wait_for_function(
        "() => document.querySelector('[data-testid=stock-skladom-status]')"
        ".textContent === 'Beží'")

    page.reload()
    page.wait_for_selector('[data-testid="version"]')
    page.get_by_role("button", name="Máme skladom → Skladom").click()
    page.wait_for_selector('[data-testid="stock-skladom-status"]')
    assert page.locator('[data-testid="stock-skladom-status"]').evaluate(
        "el => el.textContent") == "Beží"

    # Stop again — leave the fixture in its original state
    with page.expect_response("**/api/automations/stock_skladom/toggle"):
        page.locator('[data-testid="stock-skladom-toggle"]').click()
    page.wait_for_function(
        "() => document.querySelector('[data-testid=stock-skladom-status]')"
        ".textContent === 'Zastavené'")

    assert console == [], f"console not clean: {console}"
