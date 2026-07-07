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

    # Switch to the 'Na objednanie' tab — the order rows from the fixture render.
    page.get_by_role("button", name="Na objednanie").click()
    page.wait_for_selector(".toorder-row")
    assert page.locator(".toorder-row").count() >= 1

    # The BETALOV line (1/M) renders its order number as a clickable admin link
    # (target by code — robust to the oldest-first group ordering).
    beta = page.locator(".toorder-row[data-code='1/M']")
    assert "objednavky-detail/?code=20260900" in beta.locator(".to-order").first.get_attribute("href")

    # Tick 'objednané' on it; wait for the persist POST, then confirm it survives reload.
    cb = beta.locator("input[type='checkbox']").first
    with page.expect_response(
            lambda r: "/api/ordered" in r.url and r.request.method == "POST"):
        cb.check()
    assert cb.is_checked()

    page.reload()                                   # tab restored from localStorage
    page.wait_for_selector(".toorder-row")
    assert page.locator(".toorder-row[data-code='1/M'] input[type='checkbox']").first.is_checked()

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

    # Saving makes the row look like the other paired rows: a 🔗 link with the entered
    # URL, the input GONE, leaving only a small ✏️ edit icon.
    page.wait_for_selector(".toorder-row[data-code='77/X'] a.to-link")
    assert row.locator("a.to-link").first.get_attribute("href") == "https://supplier.test/rukavice"
    assert row.locator("input.to-pairurl").count() == 0
    assert row.locator(".to-pairedit").count() == 1

    # ✏️ reveals the editor again to fix a wrong link.
    row.locator(".to-pairedit").click()
    assert row.locator("input.to-pairurl").count() == 1

    # Persists server-side: after reload it's still the paired (link + edit) look.
    page.reload()
    page.wait_for_selector(".toorder-row[data-code='77/X'] a.to-link")
    assert (row.locator("a.to-link").first.get_attribute("href") == "https://supplier.test/rukavice")
    assert row.locator("input.to-pairurl").count() == 0

    assert console == [], f"console not clean: {console}"


def test_toorder_newest_first_ordering(page, live_server):
    console = []
    page.on("console", lambda m: console.append(f"[{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)

    page.goto(live_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-supplier")

    # Newest orders on top (like Shoptet) → the supplier with the newest pending order
    # sorts first. BETALOV holds 1/M = 20260900 (newest); ORBIS's newest is 20260700 →
    # BETALOV first.
    headers = page.locator(".toorder-supplier").all_inner_texts()
    assert headers[0].startswith("BETALOV"), headers
    assert any(h.startswith("ORBIS") for h in headers), headers

    # Within BETALOV the newer order (1/M = 20260900) precedes the older (2/M = 20260750).
    codes = page.locator(".toorder-row").evaluate_all("els => els.map(e => e.dataset.code)")
    assert codes.index("1/M") < codes.index("2/M"), codes

    # The order date renders on the row, formatted DD.MM.YYYY (ORBIS = 2026-04-24).
    dt = page.locator(".toorder-row[data-code='77/X'] .to-date").first.inner_text()
    assert dt.strip().endswith("24.04.2026"), dt

    assert console == [], f"console not clean: {console}"


def test_toorder_waiting_marker_toggles_and_persists(page, live_server):
    console = []
    page.on("console", lambda m: console.append(f"[{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)

    page.goto(live_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    # The 2/M BETALOV line: a '⏳ Počkať' chip marks an active order we can't stock yet.
    row = page.locator(".toorder-row[data-code='2/M']")
    wbtn = row.locator(".to-wait")
    assert "Počkať" in wbtn.inner_text()
    assert "waiting" not in (row.get_attribute("class") or "")

    # Click it → the row lights up amber (.waiting) and the chip flips to '⏳ Čaká sa'.
    with page.expect_response(
            lambda r: "/api/waiting" in r.url and r.request.method == "POST"):
        wbtn.click()
    assert "Čaká sa" in row.locator(".to-wait").inner_text()
    assert "waiting" in (row.get_attribute("class") or "")

    # Survives reload (persisted server-side in waiting_items.json).
    page.reload()
    page.wait_for_selector(".toorder-row[data-code='2/M']")
    row = page.locator(".toorder-row[data-code='2/M']")
    assert "waiting" in (row.get_attribute("class") or "")
    assert "Čaká sa" in row.locator(".to-wait").inner_text()

    # Toggle off again — clears the mark and leaves the shared fixture state clean.
    with page.expect_response(
            lambda r: "/api/waiting" in r.url and r.request.method == "POST"):
        row.locator(".to-wait").click()
    assert "waiting" not in (row.get_attribute("class") or "")

    assert console == [], f"console not clean: {console}"


def test_toorder_assign_supplier_for_no_supplier_row(page, live_server):
    console = []
    page.on("console", lambda m: console.append(f"[{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)

    page.goto(live_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    # 88/Z arrived WITHOUT a supplier → it shows the inline supplier-assign input,
    # and ORBIS currently holds only its own line (77/X).
    row = page.locator(".toorder-row[data-code='88/Z']")
    assert row.locator("input.to-supinput").count() == 1
    assert "ORBIS (1)" in page.locator("#filters").inner_text()

    # Assign ORBIS → the row regroups under ORBIS (filter count 1→2); the input is
    # replaced by a 🏷️ supplier tag + a small ✏️ to fix a wrong name (same as URL).
    row.locator("input.to-supinput").fill("ORBIS")
    with page.expect_response(
            lambda r: "/api/order-supplier" in r.url and r.request.method == "POST"):
        row.locator(".to-supsave").click()
    page.wait_for_selector(".toorder-row[data-code='88/Z'] .to-suptag")
    row = page.locator(".toorder-row[data-code='88/Z']")
    assert "ORBIS" in row.locator(".to-suptag").inner_text()
    assert row.locator("input.to-supinput").count() == 0
    assert row.locator(".to-supedit").count() == 1
    assert "ORBIS (2)" in page.locator("#filters").inner_text()

    # Persists across reload (server-side supplier_assignments.json).
    page.reload()
    page.wait_for_selector(".toorder-row[data-code='88/Z'] .to-suptag")
    assert "ORBIS (2)" in page.locator("#filters").inner_text()

    # ✏️ reveals the editor; clearing it returns the row to the '—' group — also leaves
    # the shared fixture server pristine for any later test.
    page.locator(".toorder-row[data-code='88/Z'] .to-supedit").click()
    page.wait_for_selector(".toorder-row[data-code='88/Z'] input.to-supinput")
    page.locator(".toorder-row[data-code='88/Z'] input.to-supinput").fill("")
    with page.expect_response(
            lambda r: "/api/order-supplier" in r.url and r.request.method == "POST"):
        page.locator(".toorder-row[data-code='88/Z'] .to-supsave").click()
    page.wait_for_selector(".toorder-row[data-code='88/Z'] input.to-supinput")
    assert "ORBIS (1)" in page.locator("#filters").inner_text()

    assert console == [], f"console not clean: {console}"


def test_toorder_assigned_supplier_name_is_html_escaped(page, live_server):
    # a supplier name is free text → it must be HTML-escaped everywhere it renders
    # (group header + filter label both go through the innerHTML-based el() helper).
    console = []
    page.on("console", lambda m: console.append(f"[{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)

    page.goto(live_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row[data-code='88/Z']")

    row = page.locator(".toorder-row[data-code='88/Z']")
    row.locator("input.to-supinput").fill("<i>x</i>")
    with page.expect_response(
            lambda r: "/api/order-supplier" in r.url and r.request.method == "POST"):
        row.locator(".to-supsave").click()

    # the name renders as literal text, NOT as an <i> element (no XSS sink)
    page.wait_for_selector(".toorder-row[data-code='88/Z'] .to-suptag")
    assert page.locator(".toorder-supplier i").count() == 0
    assert page.locator("#filters i").count() == 0
    assert "<i>x</i>" in page.locator(".toorder-supplier", has_text="<i>x</i>").inner_text()

    # cleanup: clear it so the shared fixture server is left pristine
    page.locator(".toorder-row[data-code='88/Z'] .to-supedit").click()
    page.wait_for_selector(".toorder-row[data-code='88/Z'] input.to-supinput")
    page.locator(".toorder-row[data-code='88/Z'] input.to-supinput").fill("")
    with page.expect_response(
            lambda r: "/api/order-supplier" in r.url and r.request.method == "POST"):
        page.locator(".toorder-row[data-code='88/Z'] .to-supsave").click()
    page.wait_for_selector(".toorder-row[data-code='88/Z'] input.to-supinput")

    assert console == [], f"console not clean: {console}"


def test_toorder_instock_and_unavailable_flags_toggle_and_persist(page, live_server):
    """#84 — the two independent 'skladom' / 'nedostupné' per-line flags, cloned from
    the 'čaká sa' toggle pattern. Toggled on the 2/M row (already used by the waiting
    test above, which cleans up after itself — independent stores, no interference)."""
    console = []
    page.on("console", lambda m: console.append(f"[{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)

    page.goto(live_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    row = page.locator(".toorder-row[data-code='2/M']")
    instock_btn = row.locator(".to-instock")
    unavail_btn = row.locator(".to-unavail")
    assert instock_btn.count() == 1 and unavail_btn.count() == 1
    assert "Skladom" in instock_btn.inner_text()
    assert "Nedostupné" in unavail_btn.inner_text()
    assert "on" not in (instock_btn.get_attribute("class") or "")
    assert "on" not in (unavail_btn.get_attribute("class") or "")
    assert "instock" not in (row.get_attribute("class") or "")
    assert "unavail" not in (row.get_attribute("class") or "")

    # Toggle 'skladom' on — row highlights green, persists across reload.
    with page.expect_response(
            lambda r: "/api/instock" in r.url and r.request.method == "POST"):
        instock_btn.click()
    assert "on" in (row.locator(".to-instock").get_attribute("class") or "")
    assert "instock" in (row.get_attribute("class") or "")

    page.reload()
    page.wait_for_selector(".toorder-row[data-code='2/M']")
    row = page.locator(".toorder-row[data-code='2/M']")
    assert "instock" in (row.get_attribute("class") or "")
    assert "on" in (row.locator(".to-instock").get_attribute("class") or "")

    # Toggle 'nedostupné' on too — independent of 'skladom', both stay active together.
    with page.expect_response(
            lambda r: "/api/unavailable" in r.url and r.request.method == "POST"):
        row.locator(".to-unavail").click()
    assert "unavail" in (row.get_attribute("class") or "")
    assert "instock" in (row.get_attribute("class") or "")   # still on

    # Toggle both off — leaves the shared fixture server pristine.
    with page.expect_response(
            lambda r: "/api/instock" in r.url and r.request.method == "POST"):
        row.locator(".to-instock").click()
    with page.expect_response(
            lambda r: "/api/unavailable" in r.url and r.request.method == "POST"):
        row.locator(".to-unavail").click()
    assert "instock" not in (row.get_attribute("class") or "")
    assert "unavail" not in (row.get_attribute("class") or "")

    assert console == [], f"console not clean: {console}"
