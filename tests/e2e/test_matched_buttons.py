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


def test_urledit_buttons_stay_onscreen_on_narrow_viewport(page, longcontent_matched_server):
    """Regression for #82: on a narrow screen (manager's phone), opening the URL-edit
    resolution panel with a REALISTIC long product name/candidate/URL must NOT clip the
    green 'Uložiť URL' / 'Vybrať' buttons off the right edge. Root cause: .manualrow's
    <input> and .cand's content had no min-width:0/flex-wrap, so their unshrinkable
    intrinsic width drove the .card grid track's automatic min-content past the
    viewport — the excess (incl. the green buttons) landing in the area .card clips via
    overflow:hidden, invisible + unclickable. (Short test content doesn't reproduce this
    — nothing needs to shrink — hence the long fixture content here.)"""
    console = []
    page.on("console", lambda m: console.append(f"[{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)

    page.set_viewport_size({"width": 390, "height": 800})
    page.goto(longcontent_matched_server)
    page.wait_for_selector('[data-testid="version"]')
    page.wait_for_selector(".card")

    page.get_by_role("button", name="vyber url", exact=True).click()
    page.wait_for_selector(".card .panel")

    save_btn = page.locator(".manualrow button.good")
    pick_btn = page.locator(".cand button.good").first
    assert save_btn.count() == 1
    assert pick_btn.count() == 1

    vw = page.evaluate("() => window.innerWidth")
    save_box = save_btn.bounding_box()
    pick_box = pick_btn.bounding_box()
    assert save_box is not None and pick_box is not None
    assert save_box["x"] + save_box["width"] <= vw, f"Uložiť URL clipped off-screen: {save_box}, vw={vw}"
    assert pick_box["x"] + pick_box["width"] <= vw, f"Vybrať clipped off-screen: {pick_box}, vw={vw}"

    # Playwright's click() itself asserts actionability (visible, not obscured by an
    # ancestor's overflow:hidden, receives pointer events) — a clipped button would
    # time out here instead of merely failing a coordinate assertion.
    save_btn.click(trial=True)
    pick_btn.click(trial=True)

    assert console == [], f"console not clean: {console}"


def test_urledit_buttons_unwrapped_on_desktop(page, longcontent_matched_server):
    """The #82 responsive CSS fix must NOT alter the ≥780px desktop layout: the
    candidate thumbnail/button and the manual-URL input/button still sit on the SAME
    visual line (align-items:center keeps their vertical centers close) — no forced
    wrap onto a lower line — even with the same long fixture content."""
    console = []
    page.on("console", lambda m: console.append(f"[{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)

    page.set_viewport_size({"width": 1200, "height": 900})
    page.goto(longcontent_matched_server)
    page.wait_for_selector('[data-testid="version"]')
    page.wait_for_selector(".card")

    page.get_by_role("button", name="vyber url", exact=True).click()
    page.wait_for_selector(".card .panel")

    def _center_y(box):
        return box["y"] + box["height"] / 2

    # Candidate row: the thumbnail and the 'Vybrať' button must share a line —
    # wrapping would drop the button noticeably lower (its own line).
    thumb_box = page.locator(".cand .thumb").first.bounding_box()
    pick_box = page.locator(".cand button.good").first.bounding_box()
    assert abs(_center_y(thumb_box) - _center_y(pick_box)) < 10, \
        f"candidate row wrapped on desktop: thumb={thumb_box} button={pick_box}"

    # Manual-URL row: the input and 'Uložiť URL' button must share a line.
    input_box = page.locator(".manualrow input").bounding_box()
    save_box = page.locator(".manualrow button.good").bounding_box()
    assert abs(_center_y(input_box) - _center_y(save_box)) < 10, \
        f"manual row wrapped on desktop: input={input_box} button={save_box}"

    assert console == [], f"console not clean: {console}"
