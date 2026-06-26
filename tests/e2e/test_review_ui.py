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
