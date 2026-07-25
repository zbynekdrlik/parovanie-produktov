"""E2E of the „Pripomienky objednávok" tab (#105) — real Chromium.

Against the seeded automations_server (a pre-existing orders_reminder.json with one red no-note
order + one order where a reminder was e-mailed, NO automations.json -> runner defaults DISABLED).
Verifies the tab renders default-Zastavené with Štart/Stop + Spustiť teraz, both the red and orange
sections render, and the toggle persists across a reload. It NEVER clicks „Spustiť teraz" — that
SENDS real customer e-mails + costs OpenAI (needs the live orders export + a real key).
"""
import re


def _console(page):
    msgs = []
    page.on("console", lambda m: msgs.append(f"[{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)
    return msgs


# Chrome logs every non-2xx resource load as a console error (mirrors test_auth.py's own
# helper) — the #153 override 'send' failure test deliberately provokes a 502.
_PROVOKED = re.compile(r"Failed to load resource: .*\b502\b")


def _unexpected(console):
    return [m for m in console if not _PROVOKED.search(m)]


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
    # moved: no longer in the red table (the SKIPPED table already exists in this fixture from
    # a DIFFERENT seeded code, so wait precisely for THIS code to leave the red table, not just
    # for a skipped table to exist somewhere).
    page.wait_for_function(
        "() => document.querySelectorAll('[data-testid=ordrem-red]"
        " tr[data-code=\"20261000\"]').length === 0")
    assert page.locator('[data-testid="ordrem-red"] tr[data-code="20261000"]').count() == 0
    skipped = page.locator('[data-testid="ordrem-skipped"]')
    assert skipped.is_visible()
    assert "20261000" in skipped.inner_text()

    assert console == [], f"console not clean: {console}"


def test_send_now_button_posts_and_surfaces_smtp_failure(page, automations_server):
    # The 'send' override reaches the REAL _send_mail_html path — automations_server pins
    # MAIL_HOST="" (see conftest) so this is a deterministic, hermetic no-network failure on
    # every machine (never a genuine send, even on a dev box with real SMTP creds checked out).
    # This proves the button → endpoint → error-surfacing wiring; the HAPPY path (row moves
    # red → orange on a successful send) is covered by the backend unit tests with a mocked SMTP.
    console = _console(page)
    _open_tab(page, automations_server)

    row = page.locator('tr[data-code="20261000"]')
    with page.expect_response("**/api/orders-reminder/override") as resp_info, \
         page.expect_event("dialog") as dialog_info:
        row.locator(".ordrem-act-send").click()
    assert resp_info.value.status == 502
    dialog = dialog_info.value
    assert "Nepodarilo sa" in dialog.message
    dialog.accept()
    # nothing was persisted on failure — the row stays exactly where it was
    assert page.locator('[data-testid="ordrem-red"] tr[data-code="20261000"]').count() == 1

    assert _unexpected(console) == [], f"console not clean: {console}"


# ── BUG 4 — orders whose customer has no e-mail get their own section ──────────────
def test_no_email_section_renders_and_offers_no_send_button(page, automations_server):
    """An order with no customer address can never be reminded, so the run surfaces it here
    (instead of paying for an AI classification every run). It must be visible to the manager
    — and must NOT offer a 'send' button that could only ever fail."""
    console = _console(page)
    _open_tab(page, automations_server)

    tbl = page.locator('[data-testid="ordrem-noemail"]')
    assert tbl.is_visible()
    body = tbl.inner_text()
    assert "20261004" in body and "Bez Mailu" in body and "Rukavice Test NoMail" in body
    assert "treba doriešiť" in body                 # the note the AI never had to read
    assert "chýba e-mail" in body
    row = tbl.locator('tr[data-code="20261004"]')
    assert row.locator(".ordrem-act-send").count() == 0
    assert row.locator(".ordrem-act-contact").count() == 0
    # and it is NOT mixed into the „bez internej poznámky" (red) list — it HAS a note
    assert page.locator('[data-testid="ordrem-red"] tr[data-code="20261004"]').count() == 0

    assert console == [], f"console not clean: {console}"


# ── B1 M2 — an order the run could not finish stays on the tab, with its reason ────
def test_an_unfinished_order_shows_its_reason_and_stays_actionable(page, automations_server):
    """Every branch where the run gives up mid-flight used to just skip the order, and because
    the display lists are rebuilt from scratch each run the row DISAPPEARED from the tab until
    the next one — while „▶ Poslať pripomienku" on the row the manager still had on screen
    answered 404. The row must be visible, say WHY it is unfinished, and still be actionable."""
    console = _console(page)
    _open_tab(page, automations_server)

    # its OWN section — putting it under „AI usúdilo, že zákazník je už kontaktovaný" would
    # state something that never happened (with no MAIL_BCC the AI does not even run)
    tbl = page.locator('[data-testid="ordrem-pending"]')
    assert tbl.is_visible()
    row = tbl.locator('tr[data-code="20261006"]')
    assert row.count() == 1
    body = row.inner_text()
    assert "Čelovka Test Pending" in body and "Nedokončený Beh" in body
    assert "odoslanie e-mailu zlyhalo" in body          # the reason, on the row itself
    assert row.locator(".ordrem-act-send").count() == 1  # …and the manager can finish it by hand

    # …and it is NOT mixed into the AI-verdict table
    assert page.locator('[data-testid="ordrem-skipped"] tr[data-code="20261006"]').count() == 0
    assert "nestihol vybaviť" in page.locator(".warnhead", has_text="automat").inner_text()

    assert console == [], f"console not clean: {console}"
