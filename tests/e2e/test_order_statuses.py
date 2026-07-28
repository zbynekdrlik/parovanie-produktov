"""E2E of #209 — the manager classifies Shoptet's order statuses himself.

Shoptet's status names are a text field the shop owner edits. Until #209 they were baked
into the code, so a rename emptied „Na objednanie", „Nedostupné" and the customer reminders
in silence and narrowed the prune. The whole value of the fix is what the manager SEES and
can DO on the card, so it needs browser coverage — a unit test over the loader proves only
half (`.claude/rules/automation-health.md` §3).

It runs against `sync_prune_blocked_server` (function-scoped, so the test may write the
configuration) whose last recorded run is exactly the state that sends him here: the prune
refused with `no-open-orders`, i.e. „a status was renamed".
"""
import re

from playwright.sync_api import expect


def _console(page):
    msgs = []
    page.on("console", lambda m: msgs.append(f"[{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)
    return msgs


# Chrome logs every non-2xx resource load as a console error. The refusal test below
# DELIBERATELY provokes a 400 (a configuration the server must reject) — filter exactly
# that line, same convention as test_ui_labels.py / test_auth.py.
_PROVOKED = re.compile(r"Failed to load resource: .*\b400\b")


def _unexpected(console):
    return [m for m in console if not _PROVOKED.search(m)]


def _open_tab(page, base):
    page.goto(base)
    page.wait_for_selector('[data-testid="version"]')
    page.get_by_role("button", name="Sync zo Shoptetu").click()
    page.wait_for_selector('[data-testid="order-statuses"]')


def test_the_card_shows_the_three_sets_with_their_measured_defaults(
        page, sync_prune_blocked_server):
    console = _console(page)
    _open_tab(page, sync_prune_blocked_server)

    panel = page.locator('[data-testid="order-statuses"]')
    assert "Stavy objednávok v Shoptete" in panel.inner_text()
    assert page.locator('[data-testid="order-statuses-to_order"]').input_value() \
        == "Vybavuje sa"
    assert page.locator('[data-testid="order-statuses-terminal"]').input_value().split("\n") \
        == ["Stornovaná", "Vybavená", "Vybavená výmena", "Vybavený Dobropis"]
    # the third box is what makes „unknown" mean genuinely UNJUDGED (store-prune §1a)
    assert "Kompletná" in page.locator(
        '[data-testid="order-statuses-known_open"]').input_value()

    assert console == [], f"console not clean: {console}"


def test_the_blocked_banner_points_at_this_very_panel(page, sync_prune_blocked_server):
    """#293's refusal says „a status was probably renamed" — and since #209 the place to
    fix that is right below it, so the banner has to say so instead of sending him to
    settings that no longer decide this."""
    _open_tab(page, sync_prune_blocked_server)
    banner = page.locator(".autoerr").inner_text()

    assert "nižšie na tejto karte" in banner, banner
    # this recorded run predates the field, so the banner falls back to the built-in name
    # rather than rendering „undefined" at the manager
    assert "„Vybavuje sa\"" in banner, banner
    assert "undefined" not in banner, banner


def test_the_banner_names_the_CONFIGURED_statuses_when_the_run_reported_them(
        page, sync_prune_blocked_server):
    """After a rename the hard-coded literal is exactly the wrong thing to send him looking
    for. A run that reported what it searched for is quoted verbatim."""
    _open_tab(page, sync_prune_blocked_server)
    page.evaluate("""() => {
      const a = autoByKey('shoptet_sync');
      a.last_result.flags_open_statuses = ['Spracúva sa', 'Čaká na dodávateľa'];
      renderShoptetSync();
    }""")

    banner = page.locator(".autoerr").inner_text()
    assert "„Spracúva sa\" / „Čaká na dodávateľa\"" in banner, banner


def test_saving_a_renamed_status_persists_and_takes_effect(page, sync_prune_blocked_server):
    console = _console(page)
    _open_tab(page, sync_prune_blocked_server)

    # two names the shop does not use yet — a status that is already classified in another
    # box would (correctly) be refused as being in two lists at once
    #
    # #297 — „Čaká na dodávateľa" is now carried by two orders in this fixture's export, so
    # this edit really does widen who can be mailed and the card asks before saving. The
    # confirmation is the NEW behaviour, not a regression, so the test answers it instead of
    # letting Playwright auto-dismiss (which would silently turn this into a test of the
    # cancel path). What it pins — the rename persists and survives a reload — is unchanged.
    page.on("dialog", lambda d: d.accept())
    page.locator('[data-testid="order-statuses-to_order"]').fill(
        "Spracúva sa\nČaká na dodávateľa")
    with page.expect_response("**/api/order-statuses"):
        page.locator('[data-testid="order-statuses-save"]').click()
    page.wait_for_function(
        "() => document.querySelector('[data-testid=order-statuses-msg]')"
        ".textContent.includes('Uložené')")

    # it must survive a reload — the app reads the store, not the open page
    page.reload()
    page.wait_for_selector('[data-testid="version"]')
    page.get_by_role("button", name="Sync zo Shoptetu").click()
    page.wait_for_selector('[data-testid="order-statuses"]')
    assert page.locator('[data-testid="order-statuses-to_order"]').input_value() \
        == "Spracúva sa\nČaká na dodávateľa"

    assert console == [], f"console not clean: {console}"


def test_a_configuration_that_would_break_the_prune_is_REFUSED_with_a_readable_reason(
        page, sync_prune_blocked_server):
    """A status meaning both „still being handled" and „over" would delete the marks of
    live orders. The server refuses it; the card must show WHY, not a generic failure —
    a refusal he cannot read is a refusal he will work around."""
    console = _console(page)
    _open_tab(page, sync_prune_blocked_server)

    page.locator('[data-testid="order-statuses-to_order"]').fill("Vybavuje sa\nVybavená")
    with page.expect_response("**/api/order-statuses"):
        page.locator('[data-testid="order-statuses-save"]').click()

    msg = page.locator('[data-testid="order-statuses-msg"]')
    page.wait_for_function(
        "() => document.querySelector('[data-testid=order-statuses-msg]')"
        ".textContent.includes('⛔')")
    assert "Vybavená" in msg.inner_text(), msg.inner_text()
    assert "vylučuje" in msg.inner_text(), msg.inner_text()
    # …and nothing was stored: the boxes still hold what the app is really going by
    assert page.request.get(sync_prune_blocked_server + "/api/order-statuses").json()[
        "statuses"]["to_order"] == ["Vybavuje sa"]

    assert _unexpected(console) == [], f"console not clean: {console}"


def test_landing_DIRECTLY_on_the_tab_loads_the_configuration(page, sync_prune_blocked_server):
    """A reload, a bookmark or a remembered tab must not show „Nastavenie sa nepodarilo
    načítať." — the fetch simply never ran. `init()` special-cases the other tabs that need
    their own data; this one was missing, and both other E2E tests hid it by always
    clicking the nav button."""
    console = _console(page)
    page.goto(sync_prune_blocked_server + "/?tab=shoptet_sync")
    page.wait_for_selector('[data-testid="order-statuses"]')

    panel = page.locator('[data-testid="order-statuses"]')
    assert "nepodarilo" not in panel.inner_text(), panel.inner_text()
    assert page.locator('[data-testid="order-statuses-to_order"]').input_value() \
        == "Vybavuje sa"

    assert console == [], f"console not clean: {console}"


def test_a_NON_ADMIN_sees_the_sets_but_cannot_edit_them(page, sync_prune_blocked_server,
                                                        admin_api, context):
    """Reading is not admin-only: the card's own counts and its „stavy, ktoré nepoznám" line
    only make sense next to the classification the app is going by. Writing is."""
    admin_api(sync_prune_blocked_server, "/api/users",
              {"email": "clen@e2e.sk", "password": "clen-heslo-12345", "is_admin": False})
    context.clear_cookies()
    page.goto(sync_prune_blocked_server + "/login")
    page.fill('input[name="email"]', "clen@e2e.sk")
    page.fill('input[name="password"]', "clen-heslo-12345")
    page.click('button[type="submit"]')
    page.wait_for_selector('[data-testid="version"]')
    page.get_by_role("button", name="Sync zo Shoptetu").click()
    page.wait_for_selector('[data-testid="order-statuses"]')

    panel = page.locator('[data-testid="order-statuses"]')
    assert "Vybavuje sa" in panel.inner_text()
    assert page.locator('[data-testid="order-statuses-to_order"]').count() == 0
    assert page.locator('[data-testid="order-statuses-save"]').count() == 0


# ── PR #295 review B1 — an unusable configuration must be VISIBLE in all four places ──
#    The loader falls back to the built-in defaults, so before this the app looked
#    perfectly healthy while it was mailing customers on statuses the manager had removed.
#    `automation-health.md` §3 asks for the stat, the ERROR line, the red banner AND the
#    side ⚠ badge — the badge is the step that keeps being forgotten (`autoByKey('posta')`
#    went unnoticed for a year), so it is checked here in the browser.

def test_an_unusable_config_says_the_app_is_running_on_DEFAULTS(page,
                                                                bad_status_config_server):
    """The panel is the card the banners send him to, so it must not present the built-in
    defaults as though he had typed them."""
    console = _console(page)
    _open_tab(page, bad_status_config_server)

    panel = page.locator('[data-testid="order-statuses"]')
    body = panel.inner_text()
    assert "sa nedá použiť" in body, body
    assert "PREDVOLENÝCH" in body, body
    # …and the sets are still shown, so he can see what the app is going by meanwhile
    assert page.locator('[data-testid="order-statuses-to_order"]').input_value() \
        == "Vybavuje sa"

    assert console == [], f"console not clean: {console}"


def test_saving_a_repaired_config_clears_the_stale_banner_without_a_reload(
        page, sync_prune_blocked_server):
    """This is exactly the card the mail-refusal banner sends him to (#295 review B1). Saving
    a config the server accepts writes `ORDER_STATUSES.statuses` on the client — but until
    `ORDER_STATUSES.reason` is ALSO cleared, `renderOrderStatusConfig()` keeps drawing the ⛔
    banner for a configuration that no longer exists on disk, right above the ✅ that says it
    is fixed. He would only find out on the next reload.

    `bad_status_config_server`'s corrupt-on-disk config cannot be used here — a corrupt file
    makes `_atomic_write_json`'s `protect=True` guard REFUSE the write outright (503,
    `store-prune.md` §1: „unreadable, so we cannot know how much work it holds"), which tests
    that guard, not this bug. So the reason is set the same way the sibling test two above
    does (`page.evaluate` + `renderShoptetSync()`) on a server whose store genuinely accepts
    a save — proving the CLIENT state, not the corrupt-store refusal path."""
    console = _console(page)
    _open_tab(page, sync_prune_blocked_server)

    page.evaluate("""() => {
      ORDER_STATUSES.reason = 'bad-status-config';
      renderShoptetSync();
    }""")
    panel = page.locator('[data-testid="order-statuses"]')
    # sanity: the stale-config banner is really there before the fix
    expect(panel.locator(".statuscfgerr")).to_have_count(1, timeout=15000)

    # the boxes still hold a legitimate config (untouched) — saving it as-is is exactly
    # „he repaired it" from the client's point of view: the server accepts it
    with page.expect_response("**/api/order-statuses"):
        page.locator('[data-testid="order-statuses-save"]').click()

    msg = page.locator('[data-testid="order-statuses-msg"]')
    expect(msg).to_contain_text("Uložené", timeout=15000)
    expect(panel.locator(".statuscfgerr")).to_have_count(0, timeout=15000)

    assert console == [], f"console not clean: {console}"


def test_an_unusable_config_stops_the_reminders_LOUDLY(page, bad_status_config_server):
    """The card of the automation that would have mailed says so in red, and the side ⚠
    badge lights — a run that cannot read its own configuration has failed even though it
    ended `ok`."""
    console = _console(page)
    page.goto(bad_status_config_server)
    page.wait_for_selector('[data-testid="version"]')

    # `[data-testid="version"]` is server-rendered markup and is present before ANY fetch
    # runs, so it proves nothing about init()'s `await Promise.all([...])` → render() —
    # which is what actually paints the badge. A bare count() right after it therefore
    # races loadAutomations() and fails on a loaded runner while passing in isolation.
    # expect() retries, so it waits for the state instead of sampling it once (same
    # reasoning as test_automations.py::test_degraded_source_lights_the_nav_badge_from_any_page).
    nav = page.locator(".tabs .tab", has_text="Pripomienky objednávok")
    expect(nav.locator(".navwarn")).to_have_count(1, timeout=15000)

    page.get_by_role("button", name="Pripomienky objednávok").click()
    page.wait_for_selector('[data-testid="ordrem-status"]')
    banner = page.locator('[data-testid="tab-orders_reminder"] .autoerr, '
                          '#tab-orders_reminder .autoerr')
    expect(banner).not_to_have_count(0, timeout=15000)
    text = banner.first.inner_text()
    assert "NEPOSIELAJÚ" in text, text
    assert "Stavy objednávok" in text, text

    assert console == [], f"console not clean: {console}"
