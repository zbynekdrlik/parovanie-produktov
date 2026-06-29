"""Real-browser E2E of the GRUBE per-size code chip on the 'Na objednanie' tab.

The fixture (conftest.py) seeds grube_codes.json for the 1/M order row, so that
row renders a copyable per-size code chip + a grube.de order link. The chip copies
to the clipboard on click (no throw), the .de link is the normalized product page,
and the console stays clean (per the project's zero-console-errors convention)."""


def test_grube_chip_copies_and_de_link(page, live_server):
    console = []
    page.on("console", lambda m: console.append(f"[{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)
    # grant clipboard so the chip's navigator.clipboard.writeText resolves cleanly —
    # an un-granted permission reject would surface as an unhandled-rejection console
    # error (which the clean-console assertion below would then catch).
    page.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=live_server)

    page.goto(live_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    # The 1/M row carries a grube code (fixture grube_codes.json) → a copyable chip.
    row = page.locator(".toorder-row[data-code='1/M']")
    chip = row.locator(".to-grube")
    assert chip.count() == 1, "grube chip must render on the 1/M row"
    assert chip.is_visible()
    assert chip.inner_text().strip() == "1547734519"      # the per-size grube itemId

    # Clicking copies the code to the clipboard — and must not throw (a thrown onclick
    # would log a console error, failing the clean-console assertion at the end).
    chip.click()
    assert page.evaluate("() => navigator.clipboard.readText()") == "1547734519"

    # The .de order link is the normalized grube.de product page, opens in a new tab.
    de = row.locator("a.to-link[href*='grube.de']")
    assert de.count() == 1
    assert de.get_attribute("href") == "https://www.grube.de/p/x/154773/"
    assert de.get_attribute("target") == "_blank"
    assert de.get_attribute("rel") == "noopener"
    assert "🇩🇪" in de.inner_text()

    assert console == [], f"console not clean: {console}"
