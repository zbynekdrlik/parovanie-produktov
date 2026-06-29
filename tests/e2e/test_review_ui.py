"""Real-browser E2E of the review UI: load page, approve a match, confirm the
decision persists (progress increments + card leaves the 'Nezrevidované' filter)
and the console stays clean."""


def test_approve_match_updates_progress_and_console_clean(page, live_server):
    console = []
    page.on("console", lambda m: console.append(f"[{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)

    page.goto(live_server)

    # Version label is rendered (post-deploy verification signal).
    page.wait_for_selector('[data-testid="version"]')
    assert page.locator('[data-testid="version"]').inner_text().startswith("v")

    # The matched product renders as a card with a '✓ Dobré' button.
    page.wait_for_selector(".card")
    assert page.locator(".card").count() == 1

    # Approve the AI match (exact name — avoids the '✓ Dobré/Vybrané' filter button).
    page.get_by_role("button", name="✓ Dobré", exact=True).first.click()

    # Progress jumps to 1/1 and the now-reviewed card leaves the default
    # 'Nezrevidované' (unreviewed) filter.
    page.wait_for_function(
        "() => document.getElementById('progressText')"
        ".textContent.startsWith('1 / 1')")
    page.wait_for_function("() => document.querySelectorAll('.card').length === 0")
    assert page.locator("#empty").is_visible()

    assert console == [], f"console not clean: {console}"


def test_toorder_tab_lists_items_and_checkbox_persists(page, live_server):
    console = []
    page.on("console", lambda m: console.append(f"[{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)

    page.goto(live_server)
    page.wait_for_selector('[data-testid="version"]')

    # Switch to the 'Na objednanie' tab — the order row from the fixture renders.
    page.get_by_role("button", name="Na objednanie").click()
    page.wait_for_selector(".toorder-row")
    assert page.locator(".toorder-row").count() >= 1

    # The order number renders as a clickable admin link (opens that exact order).
    order_link = page.locator(".toorder-row .to-order").first
    assert "objednavky-detail/?code=O1" in order_link.get_attribute("href")

    # Tick 'objednané'; wait for the persist POST, then confirm it survives a reload.
    cb = page.locator(".toorder-row input[type='checkbox']").first
    with page.expect_response(
            lambda r: "/api/ordered" in r.url and r.request.method == "POST"):
        cb.check()
    assert cb.is_checked()

    page.reload()                                   # tab restored from localStorage
    page.wait_for_selector(".toorder-row")
    assert page.locator(".toorder-row input[type='checkbox']").first.is_checked()

    assert console == [], f"console not clean: {console}"


def test_toorder_deeplink_and_inline_pairing(page, live_server):
    console = []
    page.on("console", lambda m: console.append(f"[{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)

    # ?tab=toorder deep-link (Discord posts this) opens the to-order tab directly.
    page.goto(live_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    # The unpaired ORBIS line (code 77/X, not in the review set) shows an inline
    # pairing field instead of a reorder link.
    row = page.locator(".toorder-row[data-code='77/X']")
    inp = row.locator("input.to-pairurl")
    assert inp.count() == 1
    inp.fill("https://supplier.test/rukavice")
    with page.expect_response(
            lambda r: "/api/order-pair" in r.url and r.request.method == "POST"):
        row.locator(".to-pairsave").click()

    # Saving re-renders the row as a reorder link carrying the entered URL.
    page.wait_for_selector(".toorder-row[data-code='77/X'] a.to-link")
    href = page.locator(".toorder-row[data-code='77/X'] a.to-link").first.get_attribute("href")
    assert href == "https://supplier.test/rukavice"

    # Persists server-side: survives a reload.
    page.reload()
    page.wait_for_selector(".toorder-row[data-code='77/X'] a.to-link")
    assert (page.locator(".toorder-row[data-code='77/X'] a.to-link").first
            .get_attribute("href") == "https://supplier.test/rukavice")

    assert console == [], f"console not clean: {console}"
