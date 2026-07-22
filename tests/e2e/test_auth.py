"""Auth E2E (#91): anonymous → /login redirect, login/logout through the real
form, admin 'Užívatelia' section (add / toggle admin / delete), non-admin sees
no user management. Runs against the shared session `live_server`; every test
leaves the user store exactly as it found it (bootstrap admin only)."""
import re

import pytest
from playwright.sync_api import expect


def _console(page):
    msgs = []
    page.on("console", lambda m: msgs.append(f"[{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)
    return msgs


# Chrome logs every non-2xx resource load as a console error. Tests that
# DELIBERATELY provoke a 401 (wrong password) / 403 (non-admin on /api/users)
# filter exactly that line — any other console error still fails the test.
_PROVOKED = re.compile(r"Failed to load resource: .*\b(401|403)\b")


def _unexpected(console):
    return [m for m in console if not _PROVOKED.search(m)]


@pytest.mark.anonymous
def test_anonymous_is_redirected_to_login(page, live_server):
    console = _console(page)
    page.goto(live_server)
    expect(page).to_have_url(re.compile(r"/login"))
    expect(page.locator("form[action='/login']")).to_be_visible()
    # login page carries the version label (server-rendered)
    expect(page.locator('[data-testid="version"]')).to_have_text(re.compile(r"^v\d+\."))
    assert console == [], f"console not clean: {console}"


@pytest.mark.anonymous
def test_wrong_password_shows_error_and_stays_out(page, live_server, admin_creds):
    email, _pw = admin_creds
    console = _console(page)
    page.goto(live_server + "/login")
    page.fill("#email", email)
    page.fill("#password", "urcite-zle-heslo")
    page.click("button[type=submit]")
    expect(page.locator(".err")).to_be_visible()
    page.goto(live_server)
    expect(page).to_have_url(re.compile(r"/login"))
    assert _unexpected(console) == [], f"console not clean: {console}"


@pytest.mark.anonymous
def test_login_and_logout_flow(page, live_server, admin_creds):
    email, pw = admin_creds
    console = _console(page)
    page.goto(live_server + "/login")
    page.fill("#email", email)
    page.fill("#password", pw)
    page.click("button[type=submit]")
    # in: the SPA shell loads, the sidebar shows who is logged in
    page.wait_for_selector('[data-testid="version"]')
    expect(page.locator("#userEmail")).to_have_text(email)
    # admin sees the 'Užívatelia' nav item
    expect(page.get_by_role("button", name="Užívatelia")).to_be_visible()
    # out: logout kills the session for good
    page.click("#logoutBtn")
    expect(page).to_have_url(re.compile(r"/login"))
    page.goto(live_server)
    expect(page).to_have_url(re.compile(r"/login"))
    assert console == [], f"console not clean: {console}"


def test_admin_manages_users(page, live_server):
    """Add → make admin → delete, through the real UI. Self-cleaning: the added
    account is removed at the end (live_server is session-scoped)."""
    console = _console(page)
    page.goto(live_server)
    page.get_by_role("button", name="Užívatelia").click()
    expect(page.locator(".user-row")).to_have_count(1)   # bootstrap admin only

    page.fill(".user-add input[type=email]", "novy@e2e.test")
    page.fill(".user-add input[type=password]", "nove-heslo-123")
    page.click(".user-add button")
    expect(page.locator(".user-row")).to_have_count(2)
    row = page.locator(".user-row", has_text="novy@e2e.test")
    expect(row.locator(".user-badge")).to_have_count(0)  # not admin yet

    row.get_by_role("button", name="Spraviť adminom").click()
    row = page.locator(".user-row", has_text="novy@e2e.test")
    expect(row.locator(".user-badge")).to_have_text("admin")

    page.once("dialog", lambda d: d.accept())            # confirm() the delete
    row.get_by_role("button", name="✕ Zmazať").click()
    expect(page.locator(".user-row")).to_have_count(1)
    assert console == [], f"console not clean: {console}"


@pytest.mark.anonymous
def test_nonadmin_has_no_user_management(page, live_server, admin_api):
    """A non-admin account: no 'Užívatelia' nav item, /api/users answers 403."""
    admin_api(live_server, "/api/users",
              {"email": "bezny@e2e.test", "password": "bezne-heslo-1",
               "is_admin": False})
    try:
        console = _console(page)
        page.goto(live_server + "/login")
        page.fill("#email", "bezny@e2e.test")
        page.fill("#password", "bezne-heslo-1")
        page.click("button[type=submit]")
        page.wait_for_selector('[data-testid="version"]')
        expect(page.locator("#userEmail")).to_have_text("bezny@e2e.test")
        expect(page.get_by_role("button", name="Užívatelia")).to_have_count(0)
        status = page.evaluate("async () => (await fetch('/api/users')).status")
        assert status == 403
        assert _unexpected(console) == [], f"console not clean: {console}"
    finally:
        admin_api(live_server, "/api/users/delete", {"email": "bezny@e2e.test"})
