"""E2E of the „Vypredané → Skladom" restock tab (#108) — real Chromium.

Against the seeded automations_server (a pre-existing restock_skladom.json with one
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
    page.get_by_role("button", name="Vypredané → Skladom").click()
    page.wait_for_selector('[data-testid="restock-status"]')


def test_tab_renders_default_stopped_with_controls(page, automations_server):
    console = _console(page)
    _open_tab(page, automations_server)

    # SAFETY: fresh deploy (no automations.json) = Zastavené, not running (#93 contract)
    assert page.locator('[data-testid="restock-status"]').evaluate(
        "el => el.textContent") == "Zastavené"
    assert page.locator('[data-testid="restock-toggle"]').inner_text().strip() == "▶ Štart"
    assert page.locator('[data-testid="restock-run"]').is_visible()
    assert "denne o 06:00" in page.locator(".autometa").inner_text()

    assert console == [], f"console not clean: {console}"


def test_candidate_table_and_import_outcome_render(page, automations_server):
    console = _console(page)
    _open_tab(page, automations_server)

    tbl = page.locator('[data-testid="restock-table"]')
    assert tbl.is_visible()
    body = tbl.inner_text()
    assert "Bunda Restock Test" in body and "BETALOV" in body and "Skladom" in body
    assert "89.90" in body and "75.5" in body

    # the last run's import outcome line (naskladnené = updated)
    status = page.locator('#tab-restock_skladom .autostatus').inner_text()
    assert "Naskladnených: 1" in status

    assert console == [], f"console not clean: {console}"


def test_start_stop_toggle_persists_across_reload(page, automations_server):
    console = _console(page)
    _open_tab(page, automations_server)

    with page.expect_response("**/api/automations/restock_skladom/toggle"):
        page.locator('[data-testid="restock-toggle"]').click()
    page.wait_for_function(
        "() => document.querySelector('[data-testid=restock-status]')"
        ".textContent === 'Beží'")

    page.reload()
    page.wait_for_selector('[data-testid="version"]')
    page.get_by_role("button", name="Vypredané → Skladom").click()
    page.wait_for_selector('[data-testid="restock-status"]')
    assert page.locator('[data-testid="restock-status"]').evaluate(
        "el => el.textContent") == "Beží"

    # Stop again — leave the fixture in its original state
    with page.expect_response("**/api/automations/restock_skladom/toggle"):
        page.locator('[data-testid="restock-toggle"]').click()
    page.wait_for_function(
        "() => document.querySelector('[data-testid=restock-status]')"
        ".textContent === 'Zastavené'")

    assert console == [], f"console not clean: {console}"
