"""E2E of #173 — admin-set custom nav/automation names + plain-language
automation descriptions.

Renaming lives in data/out/ui_labels.json (key -> label). The rename tests use
automations_server (function-scoped — a fresh process + tmp out dir per test),
so a renamed tab never leaks into another test's assertions; no manual cleanup
needed. The non-admin visibility test mirrors test_auth.py's
test_nonadmin_has_no_user_management pattern (@pytest.mark.anonymous + a real
non-admin login via admin_api, cleaned up in a finally)."""
import re

import pytest


def _console(page):
    msgs = []
    page.on("console", lambda m: msgs.append(f"[{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)
    return msgs


# Chrome logs every non-2xx resource load as a console error. The last test
# below DELIBERATELY provokes a 403 (non-admin POST /api/ui-label) — filter
# exactly that line, same as test_auth.py's non-admin coverage.
_PROVOKED = re.compile(r"Failed to load resource: .*\b403\b")


def _unexpected(console):
    return [m for m in console if not _PROVOKED.search(m)]


def test_automation_description_visible_in_panel(page, automations_server):
    """#173 part 1 — a clear, accurate description of what the automation does
    and when it runs is shown right in its own tab."""
    console = _console(page)
    page.goto(automations_server)
    page.wait_for_selector('[data-testid="version"]')
    page.get_by_role("button", name="Sync zo Shoptetu").click()
    page.wait_for_selector('[data-testid="shoptet-sync-status"]')

    desc = page.locator(".autodesc").inner_text()
    assert "Každú hodinu" in desc          # WHEN it runs
    assert "katalóg" in desc               # WHAT it does

    assert console == [], f"console not clean: {console}"


def test_admin_renames_a_tab_and_it_persists_across_reload(page, automations_server):
    """#173 parts 2+3 — admin renames a nav tab (✏️ next to it); the nav shows
    the new name immediately, the top-bar page title picks it up too, and it
    survives a full reload (server-side store, not just in-page state)."""
    console = _console(page)
    page.goto(automations_server)
    page.wait_for_selector('[data-testid="version"]')
    page.wait_for_selector(".sidebar #tabs button")

    edit = page.locator('[data-testid="navedit-notes"]')
    assert edit.count() == 1   # admin sees the rename pencil

    def accept_new_name(dialog):
        assert dialog.type == "prompt"
        dialog.accept("Moje poznámky")
    page.once("dialog", accept_new_name)
    with page.expect_response("**/api/ui-label"):
        edit.click()

    # nav shows the new name straight away — no reload needed
    page.wait_for_function(
        "() => [...document.querySelectorAll('#tabs .tab .tlabel')]"
        ".some(e => e.textContent === 'Moje poznámky')")

    # the top-bar page title picks up the override too, once that tab is open
    page.get_by_role("button", name="Moje poznámky").click()
    page.wait_for_function(
        "() => document.getElementById('pageTitle').textContent.trim()"
        " === 'Moje poznámky'")

    # persists across a full reload (the server-side store, not just in-page state)
    page.reload()
    page.wait_for_selector('[data-testid="version"]')
    page.wait_for_function(
        "() => [...document.querySelectorAll('#tabs .tab .tlabel')]"
        ".some(e => e.textContent === 'Moje poznámky')")

    assert console == [], f"console not clean: {console}"


def test_clearing_the_label_reverts_to_default(page, automations_server):
    """An empty rename input clears the override — the tab goes back to its
    built-in default name."""
    console = _console(page)
    page.goto(automations_server)
    page.wait_for_selector('[data-testid="version"]')
    page.wait_for_selector(".sidebar #tabs button")

    page.once("dialog", lambda d: d.accept("Dočasný názov"))
    with page.expect_response("**/api/ui-label"):
        page.locator('[data-testid="navedit-notes"]').click()
    page.wait_for_function(
        "() => [...document.querySelectorAll('#tabs .tab .tlabel')]"
        ".some(e => e.textContent === 'Dočasný názov')")

    page.once("dialog", lambda d: d.accept(""))   # empty = revert to default
    with page.expect_response("**/api/ui-label"):
        page.locator('[data-testid="navedit-notes"]').click()
    page.wait_for_function(
        "() => [...document.querySelectorAll('#tabs .tab .tlabel')]"
        ".some(e => e.textContent === 'Poznámky')")

    assert console == [], f"console not clean: {console}"


def test_renames_the_posta_automation_tab(page, automations_server):
    """Regression: the „Nevyzdvihnuté zásielky" (Pošta) tab's NAV key is the
    legacy "posta", while its Automation.key is "posta_uncollected" — those
    are two different strings for the one automation. Renaming must use the
    nav key (what app.js actually sends), not the registry key."""
    console = _console(page)
    page.goto(automations_server)
    page.wait_for_selector('[data-testid="version"]')
    page.wait_for_selector(".sidebar #autoTabs button")

    page.once("dialog", lambda d: d.accept("Balíky na pošte"))
    with page.expect_response("**/api/ui-label") as resp:
        page.locator('[data-testid="navedit-posta"]').click()
    assert resp.value.status == 200

    page.wait_for_function(
        "() => [...document.querySelectorAll('#autoTabs .tab .tlabel')]"
        ".some(e => e.textContent === 'Balíky na pošte')")

    # clean up — revert to default
    page.once("dialog", lambda d: d.accept(""))
    with page.expect_response("**/api/ui-label"):
        page.locator('[data-testid="navedit-posta"]').click()
    page.wait_for_function(
        "() => [...document.querySelectorAll('#autoTabs .tab .tlabel')]"
        ".some(e => e.textContent === 'Nevyzdvihnuté zásielky')")

    assert console == [], f"console not clean: {console}"


@pytest.mark.anonymous
def test_nonadmin_does_not_see_rename_pencil(page, live_server, admin_api):
    """A non-admin account never sees the ✏️ rename affordance anywhere in the
    sidebar — renaming stays admin-only."""
    admin_api(live_server, "/api/users",
              {"email": "bezny-ui@e2e.test", "password": "bezne-heslo-1",
               "is_admin": False})
    try:
        console = _console(page)
        page.goto(live_server + "/login")
        page.fill("#email", "bezny-ui@e2e.test")
        page.fill("#password", "bezne-heslo-1")
        page.click("button[type=submit]")
        page.wait_for_selector('[data-testid="version"]')
        page.wait_for_selector(".sidebar #tabs button")

        assert page.locator(".navedit").count() == 0
        # the admin-gated endpoint still refuses it server-side too
        status = page.evaluate(
            "async () => (await fetch('/api/ui-label', {method: 'POST', "
            "headers: {'Content-Type': 'application/json'}, "
            "body: JSON.stringify({key: 'notes', label: 'x'})})).status")
        assert status == 403

        assert _unexpected(console) == [], f"console not clean: {console}"
    finally:
        admin_api(live_server, "/api/users/delete", {"email": "bezny-ui@e2e.test"})
