"""E2E of the automations tab „Nevyzdvihnuté zásielky" (#93) — real Chromium.

Against the seeded automations_server (see conftest): no network, no SMTP —
the manual run finds 0 shipments (orders fixture has no packageNumber), so
clicking ⚡ Spustiť teraz is a hermetic green run.
"""


def _console(page):
    msgs = []
    page.on("console", lambda m: msgs.append(f"[{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)
    return msgs


def _open_tab(page, base):
    page.goto(base)
    page.wait_for_selector('[data-testid="version"]')
    page.get_by_role("button", name="Nevyzdvihnuté zásielky").click()
    page.wait_for_selector('[data-testid="posta-status"]')


def test_tab_renders_seeded_data_default_stopped(page, automations_server):
    console = _console(page)
    _open_tab(page, automations_server)

    # SAFETY: fresh deploy (no automations.json) = Zastavené, not running
    # (.pill renders uppercase via CSS — compare the raw DOM text)
    assert page.locator('[data-testid="posta-status"]').evaluate(
        "el => el.textContent") == "Zastavené"
    assert page.locator('[data-testid="posta-toggle"]').inner_text().strip() == "▶ Štart"

    # the uncollected shipment from the last run is listed with its details
    table = page.locator('[data-testid="posta-table"]')
    row = table.locator("tbody tr").first
    assert "EF000000002SK" in row.inner_text()
    assert "Ján Vzor" in row.inner_text()
    assert "2026-08-03" in row.inner_text()          # vyzdvihnúť do
    assert "2/4" in row.inner_text()                 # escalation progress
    # tracking + admin links point where they should
    assert row.locator('a[href*="posta.sk/sledovanie-zasielok"]').count() == 1
    assert row.locator('a[href*="prehlad-objednavok"]').count() == 1

    # the invalid-format package (the class that broke n8n) is flagged, not hidden
    inv = page.locator('[data-testid="posta-invalid"]')
    assert "06565700348274" in inv.inner_text()
    assert "nesledovateľným" in inv.inner_text()

    # sidebar nav badge counts the uncollected shipments
    badge = page.locator("#autoTabs .navcount")
    assert badge.inner_text() == "1"

    assert console == [], f"console not clean: {console}"


def test_start_stop_toggle_persists_across_reload(page, automations_server):
    console = _console(page)
    _open_tab(page, automations_server)

    # Štart → Beží (persisted enabled=true + next run shown)
    with page.expect_response("**/api/automations/posta_uncollected/toggle"):
        page.locator('[data-testid="posta-toggle"]').click()
    page.wait_for_function(
        "() => document.querySelector('[data-testid=posta-status]')"
        ".textContent === 'Beží'")
    assert "Ďalší beh" in page.locator(".autometa").inner_text()

    # survives a full reload (state file, not just the page)
    page.reload()
    page.wait_for_selector('[data-testid="version"]')
    page.get_by_role("button", name="Nevyzdvihnuté zásielky").click()
    page.wait_for_selector('[data-testid="posta-status"]')
    assert page.locator('[data-testid="posta-status"]').evaluate(
        "el => el.textContent") == "Beží"

    # Stop → Zastavené again (leave the fixture in its original state)
    with page.expect_response("**/api/automations/posta_uncollected/toggle"):
        page.locator('[data-testid="posta-toggle"]').click()
    page.wait_for_function(
        "() => document.querySelector('[data-testid=posta-status]')"
        ".textContent === 'Zastavené'")

    assert console == [], f"console not clean: {console}"


def test_run_now_executes_and_reports_result(page, automations_server):
    console = _console(page)
    _open_tab(page, automations_server)

    # hermetic manual run: 0 shipments in the fixture orders cache → instant OK
    with page.expect_response("**/api/automations/posta_uncollected/run"):
        page.locator('[data-testid="posta-run"]').click()
    page.wait_for_function(
        "() => document.querySelector('.autometa') && "
        "document.querySelector('.autometa').textContent.includes('OK')",
        timeout=15000)
    meta = page.locator(".autometa").inner_text()
    assert "Posledný beh" in meta and "OK" in meta
    assert "Skontrolovaných zásielok: 0" in page.locator(".autostatus").inner_text()

    assert console == [], f"console not clean: {console}"
