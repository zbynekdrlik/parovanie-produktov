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

    page.locator('[data-testid="order-statuses-to_order"]').fill("Spracúva sa\nOsob. odber")
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
        == "Osob. odber\nSpracúva sa"

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
