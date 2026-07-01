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

    # '📦 Nie je skladom' → one click writes status 'unavailable' (the panel's status).
    with page.expect_response(
            lambda r: "/api/decision" in r.url and r.request.method == "POST") as ri:
        page.get_by_role("button", name="📦 Nie je skladom", exact=True).click()
    assert json.loads(ri.value.request.post_data)["status"] == "unavailable"
    # Card re-renders to the unavailable state (shows '↩ Vrátiť'); undo it.
    page.wait_for_selector("button:has-text('↩ Vrátiť')")
    page.get_by_role("button", name="↩ Vrátiť", exact=True).click()
    page.wait_for_selector("button:has-text('vyber url')")

    # '🚫 Už sa nebude predávať' → one click writes status 'discontinued'; undo.
    with page.expect_response(
            lambda r: "/api/decision" in r.url and r.request.method == "POST") as ri:
        page.get_by_role("button", name="🚫 Už sa nebude predávať", exact=True).click()
    assert json.loads(ri.value.request.post_data)["status"] == "discontinued"
    page.wait_for_selector("button:has-text('↩ Vrátiť')")
    page.get_by_role("button", name="↩ Vrátiť", exact=True).click()
    page.wait_for_selector("button:has-text('vyber url')")

    # 'vyber url' opens the resolution panel (candidate row + manual-URL input).
    page.get_by_role("button", name="vyber url", exact=True).click()
    page.wait_for_selector(".card .panel")
    assert page.locator(".card .panel .cand").count() >= 1
    assert page.locator(".card .panel input[type='url']").count() == 1

    # Leave the isolated server clean (no decision persisted).
    assert page.evaluate("() => Object.keys(DECISIONS).length") == 0

    assert console == [], f"console not clean: {console}"
