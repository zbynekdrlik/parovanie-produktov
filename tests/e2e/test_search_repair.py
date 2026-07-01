"""Real-browser E2E of the '🔎 Hľadať / opraviť' tab (catalog search + re-pair).

Drives the full user flow: search the whole catalog → a not-yet-paired product shows
as a 'nenapárované' result row → click it → paste a supplier URL → save → the badge
flips to 'napárované ✓' in place → reload + re-search proves the pairing persisted
(the product is now promoted into the review set) and the saved supplier URL survived.
The browser console must stay clean throughout (project zero-console convention); the
fixture catalog image is a hermetic data: URI so no image network request can fail.

Runs against an ISOLATED webreview instance (the `search_server` fixture) — promoting a
product writes review_data.json/decisions.json and mutates in-memory state, so it must
NOT share the session server the other E2E tests use."""

SEARCH_Q = "hladaci"          # matches 'Hľadací Test Produkt' (accent-insensitive)
PAIR_CODE = "SRCHP9"          # the fixture catalog product's pairCode (NOT in review)
IN_REVIEW_Q = "kontrolny"     # matches the IN-review 'Kontrolný Produkt V Appke'
IN_REVIEW_CODE = "DUMMY1"     # its pairCode (review key is 'TESTSUP|DUMMY1')


def _open_search(page, base):
    """Load the app and switch to the search tab via its real tab button."""
    page.goto(base)
    page.wait_for_selector('[data-testid="version"]')
    page.get_by_role("button", name="Hľadať / opraviť").click()   # 🔎 tab
    page.wait_for_selector("#searchBox")


def test_search_repair_flow_persists_clean_console(page, search_server):
    # The supplier URL is LOCAL (a 404 route on the fixture server): re-opening the
    # promoted row now renders the FULL review card, whose lazy /api/images fetch of
    # the decision URL must stay hermetic — no outbound network in CI.
    supplier_url = f"{search_server}/p/x"
    console = []
    page.on("console", lambda m: console.append(f"[{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)

    _open_search(page, search_server)

    # Typing a >=2-char query renders the catalog product as a result row with the
    # 'nenapárované' badge (it is not yet in the review set).
    page.fill("#searchBox", SEARCH_Q)
    row_sel = f"#searchResults .search-row[data-key='{PAIR_CODE}']"
    page.wait_for_selector(row_sel)
    row = page.locator(row_sel)
    assert row.count() == 1, "exactly one search result for the fixture product"
    badge = row.locator(".sbadge")
    assert "nenapárované" in badge.inner_text(), badge.inner_text()
    assert "new" in (badge.get_attribute("class") or "")

    # Click the result → a not-in-review hit opens the manual-pair panel. Fill the
    # supplier URL and save; the POST /api/search-pair must land.
    row.locator(".srch-head").click()
    panel = row.locator(".srch-panel")
    page.wait_for_selector(f"{row_sel} .srch-panel .manualrow input")
    panel.locator(".manualrow input").fill(supplier_url)
    with page.expect_response(
            lambda r: "/api/search-pair" in r.url and r.request.method == "POST"):
        panel.get_by_role("button", name="Uložiť odkaz").click()

    # In-place success: the badge flips to 'napárované ✓', the row shows the saved URL,
    # and the panel confirms the save — all without a full re-render.
    page.wait_for_selector(f"{row_sel} .sbadge.paired")
    assert "napárované" in row.locator(".sbadge").inner_text()
    assert row.locator(f".srch-link a[href='{supplier_url}']").count() == 1
    assert "Odkaz uložený" in panel.inner_text()

    # Reload + re-search: the pairing persisted server-side, so the product is now IN
    # the review set → its badge is the in-app 'v appke' (inreview) one, no longer
    # 'nenapárované'. (The 'napárované ✓' above is the transient in-place save state.)
    page.reload()
    page.wait_for_selector('[data-testid="version"]')
    page.get_by_role("button", name="Hľadať / opraviť").click()
    page.fill("#searchBox", SEARCH_Q)
    page.wait_for_selector(row_sel)
    row = page.locator(row_sel)
    badge = row.locator(".sbadge")
    assert "v appke" in badge.inner_text(), badge.inner_text()
    assert "inreview" in (badge.get_attribute("class") or "")

    # The re-searched row also carries the saved decision as the '🔗 dodávateľ' link.
    assert row.locator(f".srch-link a[href='{supplier_url}']").count() == 1

    # The manual decision (the supplier URL) also survived: opening the row now shows
    # the FULL review card ('✓ Vybraný link' badge) with the saved supplier link.
    row.locator(".srch-head").click()
    page.wait_for_selector(f"{row_sel} .srch-panel .card")
    card = row.locator(".srch-panel .card")
    assert "Vybraný link" in card.inner_text()
    assert card.locator(f"a.supurl[href='{supplier_url}']").count() == 1

    assert console == [], f"console not clean: {console}"


def test_search_row_commerce_line_and_in_review_full_card(page, search_server):
    """Result rows carry the commerce line (OUR price + state chip + stock) and
    clicking an IN-review hit opens the FULL review card (decision buttons, our
    side) — not the bare candidates panel. Console stays clean."""
    console = []
    page.on("console", lambda m: console.append(f"[{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)

    _open_search(page, search_server)

    # commerce line on the not-in-review result (price 12,50 / visible+Skladom / 7 ks)
    page.fill("#searchBox", SEARCH_Q)
    row_sel = f"#searchResults .search-row[data-key='{PAIR_CODE}']"
    page.wait_for_selector(row_sel)
    comm = page.locator(f"{row_sel} .srch-comm").inner_text()
    assert "12,50 €" in comm, comm
    assert "🟢 Skladom" in comm, comm
    assert "(7 ks)" in comm, comm

    # the IN-review product's result row → full review card with decision buttons
    page.fill("#searchBox", IN_REVIEW_Q)
    in_sel = f"#searchResults .search-row[data-key='{IN_REVIEW_CODE}']"
    page.wait_for_selector(in_sel)
    row = page.locator(in_sel)
    assert "89,90 €" in row.locator(".srch-comm").inner_text()
    assert "v appke" in row.locator(".sbadge").inner_text()
    row.locator(".srch-head").click()
    page.wait_for_selector(f"{in_sel} .srch-panel .card")
    card = row.locator(".srch-panel .card")
    # left (our) side — .label is CSS-uppercased, inner_text() reflects the render
    assert "náš produkt" in card.inner_text().lower()
    assert card.get_by_role("button", name="✓ Dobré").count() == 1  # decision button

    assert console == [], f"console not clean: {console}"
