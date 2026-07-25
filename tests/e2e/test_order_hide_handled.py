"""E2E of #205 — prepínač „skryť poriešené riadky" on the „Na objednanie" tab.

Handled lines used to stay in the list at opacity .55 forever, so a long supplier group
never got shorter as the manager worked it down. The toggle is a VIEW filter only:
nothing is written, the supplier chips keep counting every line, and the choice is
remembered across visits (localStorage).

The one behaviour worth pinning beyond „it hides rows": a row holding UNSAVED editor
work must NOT vanish under the manager's hands. Hiding it would strand the half-typed
pair URL / supplier / comment (a snapshot whose row is gone is dropped by
restoreOpenEditors) with no message — the silent loss #214/#233 removed from the flag
writes. The exemption uses the same predicate restoreOpenEditors uses.

`toorder_server` serves 7 lines: 4 CITRADE (C1-C4), 2 ORBIS sharing itemCode S1, 1 N1
without a supplier.
"""

TOTAL_LINES = 7


def _console_watch(page):
    errs = []
    page.on("console", lambda m: errs.append(f"[{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)
    return errs


def _toggle(page):
    return page.locator("#toToolbar .to-hidehandled")


def test_hide_handled_toggles_and_survives_a_reload(page, toorder_server):
    console = _console_watch(page)
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")
    assert page.locator(".toorder-row").count() == TOTAL_LINES

    _toggle(page).click()                      # nothing handled yet → nothing disappears
    assert page.locator(".toorder-row").count() == TOTAL_LINES

    # Flagging a line does NOT repaint the list, so the row the manager is looking at
    # stays put mid-interaction (it disappears on the next real repaint / visit).
    page.locator(".toorder-row[data-code='C1'] .to-instock").click()
    page.wait_for_function(
        "() => /Ostáva vybaviť 6 položiek z 7/.test(document.getElementById('toToolbar').textContent)")
    assert page.locator(".toorder-row").count() == TOTAL_LINES

    # …and the choice is remembered: after a reload the handled line is gone.
    page.reload()
    page.wait_for_selector(".toorder-row")
    assert page.locator(".toorder-row").count() == TOTAL_LINES - 1
    assert page.locator(".toorder-row[data-code='C1']").count() == 0
    assert "Poriešené skryté" in _toggle(page).inner_text()
    # the chips still count EVERY line — hiding is a view, not a filter on the data
    assert "Všetci (7)" in page.locator("#filters").inner_text()

    _toggle(page).click()                      # back to showing everything
    page.wait_for_selector(".toorder-row[data-code='C1']")
    assert page.locator(".toorder-row").count() == TOTAL_LINES

    assert console == [], f"console not clean: {console}"


def test_a_row_with_unsaved_typing_is_never_hidden_under_the_manager(page, toorder_server):
    console = _console_watch(page)
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    _toggle(page).click()
    assert "Poriešené skryté" in _toggle(page).inner_text()

    # half-typed pair URL on C1 — NOT saved
    page.locator(".toorder-row[data-code='C1'] .to-pairurl").fill("https://citrade.sk/rozp")
    # …and C1 gets flagged (the manager marks it while the note is still open)
    page.locator(".toorder-row[data-code='C1'] .to-instock").click()
    page.wait_for_function(
        "() => /Ostáva vybaviť 6 položiek z 7/.test(document.getElementById('toToolbar').textContent)")

    # Now force a REAL repaint from another row: marking the whole ORBIS group ordered.
    page.locator(".toorder-supplier").filter(has_text="ORBIS").locator(".tosup-bulk").click()
    page.wait_for_function(
        "() => document.querySelectorAll(\"[data-code='S1']\").length === 0")

    # ORBIS's handled lines are gone — but C1 stayed, with the typing intact.
    assert page.locator(".toorder-row[data-code='C1']").count() == 1
    assert (page.locator(".toorder-row[data-code='C1'] .to-pairurl").input_value()
            == "https://citrade.sk/rozp")

    # once the work is committed the row is ordinary again → the next repaint hides it
    with page.expect_response(lambda r: "/api/order-pair" in r.url
                             and r.request.method == "POST"):
        page.locator(".toorder-row[data-code='C1'] .to-pairsave").click()
    page.wait_for_function(
        "() => document.querySelectorAll(\"[data-code='C1']\").length === 0")

    assert console == [], f"console not clean: {console}"


def test_everything_handled_reads_as_success_not_as_missing_data(page, toorder_server):
    """With the filter on and the whole day worked down, the list is legitimately empty —
    but the shared `#empty` box used to say „Žiadne produkty v tomto filtri.", which reads
    like the data failed to load, right under a toolbar saying „ostáva 0 z 7". The wording
    must say what actually happened — and must not leak into the review tab, which shares
    the same box."""
    console = _console_watch(page)
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    _toggle(page).click()
    # with the filter on, a group whose every line is now ordered disappears → always
    # work the FIRST remaining group and wait for the header count to drop
    while page.locator(".tosup-bulk").count():
        n = page.locator(".tosup-bulk").count()
        page.locator(".tosup-bulk").first.click()
        page.wait_for_function(
            "(n) => document.querySelectorAll('.tosup-bulk').length < n", n)
    page.wait_for_function(
        "() => document.querySelectorAll('.toorder-row').length === 0")

    empty = page.locator("#empty")
    assert empty.is_visible()
    assert empty.inner_text() == "Všetko vybavené — poriešené riadky sú skryté"
    assert "Ostáva vybaviť 0 položiek z 7" in page.locator("#toToolbar").inner_text()

    # the review tab shares `#empty` → it must get its own wording back
    page.get_by_role("button", name="Kontrola párovania").click()
    page.wait_for_function("() => !document.getElementById('toToolbar')"
                           " || document.getElementById('toToolbar').hidden")
    assert page.locator("#empty").inner_text() == "Žiadne produkty v tomto filtri."

    # …and switching back restores the success wording
    page.get_by_role("button", name="Na objednanie").click()
    page.wait_for_function("() => !document.getElementById('toToolbar').hidden")
    assert page.locator("#empty").inner_text() == "Všetko vybavené — poriešené riadky sú skryté"

    assert console == [], f"console not clean: {console}"


def test_the_empty_box_keeps_the_default_wording_while_work_remains(page, toorder_server):
    """The success wording is ONLY for „hidden because handled". A supplier filter that
    genuinely matches nothing, or the toggle switched off, keeps the neutral text."""
    console = _console_watch(page)
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    txt = page.evaluate("""() => {
      const e = document.getElementById('empty');
      const out = {};
      HIDE_HANDLED = false; ORDERS = []; renderToOrder(); out.noOrders = e.textContent;
      HIDE_HANDLED = true; renderToOrder(); out.noOrdersHidden = e.textContent;
      return out;
    }""")

    # no orders at all is NOT „všetko vybavené" — there was nothing to do
    assert txt["noOrders"] == "Žiadne produkty v tomto filtri."
    assert txt["noOrdersHidden"] == "Žiadne produkty v tomto filtri."

    assert console == [], f"console not clean: {console}"
