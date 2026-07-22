"""E2E of the „Pripomienky objednávok" tab (#105) — real Chromium.

Against the seeded automations_server (a pre-existing orders_reminder.json with one red no-note
order + one order where a reminder was e-mailed, NO automations.json -> runner defaults DISABLED).
Verifies the tab renders default-Zastavené with Štart/Stop + Spustiť teraz, both the red and orange
sections render, and the toggle persists across a reload. It NEVER clicks „Spustiť teraz" — that
SENDS real customer e-mails + costs OpenAI (needs the live orders export + a real key).
"""


def _console(page):
    msgs = []
    page.on("console", lambda m: msgs.append(f"[{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)
    return msgs


def _open_tab(page, base):
    page.goto(base)
    page.wait_for_selector('[data-testid="version"]')
    page.get_by_role("button", name="Pripomienky objednávok").click()
    page.wait_for_selector('[data-testid="ordrem-status"]')


def test_tab_renders_default_stopped_with_controls(page, automations_server):
    console = _console(page)
    _open_tab(page, automations_server)

    # SAFETY: fresh deploy (no automations.json) = Zastavené, not running (#93 contract)
    assert page.locator('[data-testid="ordrem-status"]').evaluate(
        "el => el.textContent") == "Zastavené"
    assert page.locator('[data-testid="ordrem-toggle"]').inner_text().strip() == "▶ Štart"
    assert page.locator('[data-testid="ordrem-run"]').is_visible()
    assert "denne o 08:00" in page.locator(".autometa").inner_text()

    assert console == [], f"console not clean: {console}"


def test_red_and_orange_sections_render(page, automations_server):
    console = _console(page)
    _open_tab(page, automations_server)

    red = page.locator('[data-testid="ordrem-red"]')
    assert red.is_visible()
    assert "Bunda Test Red" in red.inner_text() and "20261000" in red.inner_text()

    orange = page.locator('[data-testid="ordrem-orange"]')
    assert orange.is_visible()
    body = orange.inner_text()
    assert "Nohavice Test Orange" in body and "Eva Nová" in body and "volať zákazníka" in body

    assert console == [], f"console not clean: {console}"


def test_start_stop_toggle_persists_across_reload(page, automations_server):
    console = _console(page)
    _open_tab(page, automations_server)

    with page.expect_response("**/api/automations/orders_reminder/toggle"):
        page.locator('[data-testid="ordrem-toggle"]').click()
    page.wait_for_function(
        "() => document.querySelector('[data-testid=ordrem-status]')"
        "&& document.querySelector('[data-testid=ordrem-status]').textContent === 'Beží'")

    # reload → the enabled state must have persisted to automations.json
    page.reload()
    page.wait_for_selector('[data-testid="version"]')
    page.get_by_role("button", name="Pripomienky objednávok").click()
    page.wait_for_selector('[data-testid="ordrem-status"]')
    assert page.locator('[data-testid="ordrem-status"]').evaluate(
        "el => el.textContent") == "Beží"

    # tidy up: stop it again so the shared session-scoped server ends Zastavené
    with page.expect_response("**/api/automations/orders_reminder/toggle"):
        page.locator('[data-testid="ordrem-toggle"]').click()
    page.wait_for_function(
        "() => document.querySelector('[data-testid=ordrem-status]')"
        "&& document.querySelector('[data-testid=ordrem-status]').textContent === 'Zastavené'")

    assert console == [], f"console not clean: {console}"


# ── manual per-row override (#153) ───────────────────────────────────────────────
def test_mark_red_order_as_contacted_moves_it_to_skipped(page, automations_server):
    console = _console(page)
    _open_tab(page, automations_server)

    row = page.locator('tr[data-code="20261000"]')
    assert row.is_visible()
    with page.expect_response("**/api/orders-reminder/override"):
        row.locator(".ordrem-act-contact").click()
    # moved: no longer in the red table, now shows in the skipped table
    page.wait_for_function(
        "() => !document.querySelector('tr[data-code=\"20261000\"]')"
        " || document.querySelector('[data-testid=ordrem-skipped]')")
    assert page.locator('[data-testid="ordrem-red"] tr[data-code="20261000"]').count() == 0
    skipped = page.locator('[data-testid="ordrem-skipped"]')
    assert skipped.is_visible()
    assert "20261000" in skipped.inner_text()

    assert console == [], f"console not clean: {console}"


def test_send_now_from_red_row_moves_it_to_orange(page, automations_server):
    console = _console(page)
    _open_tab(page, automations_server)

    row = page.locator('tr[data-code="20261000"]')
    with page.expect_response("**/api/orders-reminder/override"):
        row.locator(".ordrem-act-send").click()
    page.wait_for_function(
        "() => !document.querySelector('tr[data-code=\"20261000\"]')"
        " || document.querySelector('[data-testid=ordrem-orange]')")
    assert page.locator('[data-testid="ordrem-red"] tr[data-code="20261000"]').count() == 0
    orange = page.locator('[data-testid="ordrem-orange"]')
    assert "20261000" in orange.inner_text()

    assert console == [], f"console not clean: {console}"
