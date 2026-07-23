"""E2E of the „Poľovnícke výstavy" tab (#111) — real Chromium.

Against the seeded vystavy_server (one 'akcia bude' výstava with a feed entry + one Nová).
Verifies the KARTY render grouped by state with the per-state action button, then drives the
full add → edit → delete round-trip on a fresh test výstava (leave-no-trace), asserting a clean
console throughout. The send buttons ('Pošli otázku' / 'Ideme') are NEVER clicked — they would
attempt a real organizer e-mail (MAIL_HOST="" in the fixture only makes that a hermetic 502).
"""


def _console(page):
    msgs = []
    page.on("console", lambda m: msgs.append(f"[{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)
    return msgs


def _open_tab(page, base):
    page.goto(base)
    page.wait_for_selector('[data-testid="version"]')
    page.get_by_role("button", name="Poľovnícke výstavy").click()
    page.wait_for_selector('[data-testid="vy-card-vy-akcia"]')


def test_cards_render_grouped_with_action_buttons(page, vystavy_server):
    console = _console(page)
    _open_tab(page, vystavy_server)

    # the 'akcia bude' card shows its badge + the highlighted „Ideme" action (NOT clicked)
    akcia = page.locator('[data-testid="vy-card-vy-akcia"]')
    assert "Deň Sv. Huberta Test" in akcia.inner_text()
    assert "Odpovedali" in akcia.inner_text()
    assert page.locator('[data-testid="vy-ideme-vy-akcia"]').is_visible()

    # the Nová card shows the „Pošli otázku" action (NOT clicked)
    nova = page.locator('[data-testid="vy-card-vy-nova"]')
    assert "Nová Výstava Test" in nova.inner_text()
    assert page.locator('[data-testid="vy-otazka-vy-nova"]').is_visible()

    # opening the 'akcia bude' card reveals its info feed (the Discord replacement)
    akcia.locator(".vy-head").click()
    page.wait_for_selector('[data-testid="vy-card-vy-akcia"] .vy-feed')
    assert "Prišla odpoveď" in akcia.locator(".vy-feed").inner_text()

    assert console == [], f"console not clean: {console}"


def test_add_edit_delete_roundtrip(page, vystavy_server):
    console = _console(page)
    page.on("dialog", lambda d: d.accept())   # accept the delete-confirm dialog
    _open_tab(page, vystavy_server)

    # ── ADD ──────────────────────────────────────────────────────────────
    page.locator('[data-testid="vy-add-btn"]').click()
    page.wait_for_selector('[data-testid="vy-add-nazov"]')
    page.locator('[data-testid="vy-add-nazov"]').fill("E2E Test Výstava")
    page.locator('[data-testid="vy-add-email"]').fill("e2e@test.sk")
    with page.expect_response(
            lambda r: r.url.endswith("/api/vystavy") and r.request.method == "POST"):
        page.locator('[data-testid="vy-add-submit"]').click()

    added = page.locator(".vy-card").filter(has_text="E2E Test Výstava")
    added.wait_for()
    assert added.count() == 1

    # ── EDIT (open detail, change Miesto, save) ─────────────────────────────
    added.locator(".vy-head").click()
    miesto = added.locator('.vy-field:has(.vy-flabel:has-text("Miesto")) input')
    miesto.fill("E2E Miesto Test")
    with page.expect_response(
            lambda r: r.url.endswith("/api/vystava") and r.request.method == "POST"):
        added.get_by_role("button", name="Uložiť").click()
    # after the reload the card still carries the saved place in its meta line
    page.wait_for_selector('.vy-card:has-text("E2E Miesto Test")')
    assert "E2E Miesto Test" in \
        page.locator(".vy-card").filter(has_text="E2E Test Výstava").inner_text()

    # ── DELETE (leave-no-trace) ─────────────────────────────────────────────
    # detail stays expanded across the save re-render (VY_OPEN persists), so the
    # 🗑 delete button is already visible — no need to re-open (that would collapse it).
    card = page.locator(".vy-card").filter(has_text="E2E Test Výstava")
    with page.expect_response(
            lambda r: r.url.endswith("/api/vystava") and r.request.method == "POST"):
        card.get_by_role("button", name="Zmazať").click()
    page.wait_for_function(
        "() => !document.body.innerText.includes('E2E Test Výstava')")
    assert page.locator(".vy-card").filter(has_text="E2E Test Výstava").count() == 0

    assert console == [], f"console not clean: {console}"
