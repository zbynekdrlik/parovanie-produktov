"""E2E for the matched review card's 3 direct action buttons (they replace the single
'✗ Zlé'): 'vyber url' (opens the resolution panel to pick/paste a URL), '📦 Nie je
skladom' → 'unavailable', and '🚫 Už sa nebude predávať' → 'discontinued' — the SAME
one-click statuses the resolution panel writes, now surfaced directly on the card.
Runs against an ISOLATED function-scoped `matched_server`, so the shared session
server is never touched and nothing is left decided behind us."""
import json


def test_matched_card_three_direct_buttons(page, matched_server):
    console = []
    page.on("console", lambda m: console.append(f"[{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)

    page.goto(matched_server)
    page.wait_for_selector('[data-testid="version"]')
    page.wait_for_selector(".card")

    # The matched card now carries 3 direct buttons; the old single '✗ Zlé' is gone,
    # while '✓ Dobré' stays. (exact=True keeps '📦 Nie je skladom' off the '📦 Nie
    # skladom' filter button and '✗ Zlé' off the good-card '✗ Zmeniť / iný link'.)
    for name in ("vyber url", "📦 Nie je skladom", "🚫 Už sa nebude predávať"):
        assert page.get_by_role("button", name=name, exact=True).count() == 1, name
    assert page.get_by_role("button", name="✓ Dobré", exact=True).count() == 1
    assert page.get_by_role("button", name="✗ Zlé", exact=True).count() == 0

    # Switch to the 'Všetky' filter so a decided card stays visible (the default
    # 'Nezrevidované' filter drops it) and we can watch the in-place re-render.
    page.get_by_role("button", name="Všetky", exact=True).click()

    # Every /api/decision POST is CONSUMED in its OWN expect_response window before
    # the next action begins — otherwise a late-flushing undo POST bleeds into the
    # next button's window and the wrong request is captured (the CI race). Note
    # saveDecision() calls render() synchronously BEFORE `await fetch`, so the UI is
    # already re-rendered when the POST fires; consuming the response is what proves
    # the POST landed before we open the next window (wait_for_selector alone can't).

    def click_and_assert_status(button_name, expected_status):
        """Click a mutating button, consume its /api/decision POST, assert the status."""
        with page.expect_response(
                lambda r: "/api/decision" in r.url
                and r.request.method == "POST") as resp:
            page.get_by_role("button", name=button_name, exact=True).click()
        assert json.loads(resp.value.request.post_data)["status"] == expected_status

    # '📦 Nie je skladom' → one click writes status 'unavailable' (the panel's status).
    click_and_assert_status("📦 Nie je skladom", "unavailable")
    # Card re-renders to the unavailable state (shows '↩ Vrátiť'); undo it and
    # consume the undo POST (client sends status 'undo' → server un-decides).
    page.wait_for_selector("button:has-text('↩ Vrátiť')")
    click_and_assert_status("↩ Vrátiť", "undo")
    # After undo the matched card's own buttons are back; wait for them.
    page.wait_for_selector("button:has-text('vyber url')")

    # '🚫 Už sa nebude predávať' → one click writes status 'discontinued'; undo.
    click_and_assert_status("🚫 Už sa nebude predávať", "discontinued")
    page.wait_for_selector("button:has-text('↩ Vrátiť')")
    click_and_assert_status("↩ Vrátiť", "undo")
    page.wait_for_selector("button:has-text('vyber url')")

    # 'vyber url' opens the resolution panel (candidate row + manual-URL input).
    page.get_by_role("button", name="vyber url", exact=True).click()
    page.wait_for_selector(".card .panel")
    assert page.locator(".card .panel .cand").count() >= 1
    assert page.locator(".card .panel input[type='url']").count() == 1

    # Leave the isolated server clean (no decision persisted).
    assert page.evaluate("() => Object.keys(DECISIONS).length") == 0

    assert console == [], f"console not clean: {console}"
