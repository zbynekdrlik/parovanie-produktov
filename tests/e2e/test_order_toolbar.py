"""E2E of the „Na objednanie" toolbar above the list.

#208 — súhrn „ostáva X z Y nevybavených": the supplier chips only ever counted lines
PER SUPPLIER, so the manager had no single number telling him how much of today's work
is left. The tally lives in the top bar (outside `#list`, so the list repaint — and the
inline-editor capture/restore that rides on it — is untouched) and is recomputed by
renderOrderFilters, which every per-line toggle already calls: the number must move the
moment he flags a line, without a reload (the #86 stale-chip lesson).

The `toorder_server` fixture serves 7 order lines (4 CITRADE case-variants, 2 ORBIS
sharing itemCode S1, 1 supplier-less N1).
"""

TOTAL_LINES = 7


def _console_watch(page):
    errs = []
    page.on("console", lambda m: errs.append(f"[{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)
    return errs


def test_summary_shows_remaining_and_recomputes_live(page, toorder_server):
    console = _console_watch(page)
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    bar = page.locator("#toToolbar")
    assert bar.is_visible(), "toolbar must be visible on the Na objednanie tab"
    assert f"Ostáva vybaviť {TOTAL_LINES} z {TOTAL_LINES} položiek" in bar.inner_text()

    # The manager's real action: flag one line IN-SESSION. The tally must follow at once
    # — a per-line toggle deliberately does NOT repaint the list, so a summary that only
    # rebuilt with the list would sit there lying until the next reload.
    page.locator(".toorder-row[data-code='C1'] .to-instock").click()
    page.wait_for_function(
        "() => /Ostáva vybaviť 6 z 7 položiek/.test("
        "document.getElementById('toToolbar').textContent)")
    assert "skladom 1" in bar.inner_text()

    # …and it is the SERVER's state, not a client-side illusion: after a reload the
    # summary still reads 6 of 7.
    page.reload()
    page.wait_for_selector(".toorder-row")
    assert "Ostáva vybaviť 6 z 7 položiek" in page.locator("#toToolbar").inner_text()
    assert "skladom 1" in page.locator("#toToolbar").inner_text()

    assert console == [], f"console not clean: {console}"


def test_summary_is_hidden_outside_the_to_order_tab(page, toorder_server):
    console = _console_watch(page)
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")
    assert page.locator("#toToolbar").is_visible()

    page.get_by_role("button", name="Hľadať / opraviť").click()
    page.wait_for_selector("#tab-search:not([hidden])")
    assert page.locator("#toToolbar").is_hidden(), "toolbar belongs to the to-order tab only"

    assert console == [], f"console not clean: {console}"


def test_summary_text_is_not_a_partition_of_the_total(page, toorder_server):
    """A line can carry SEVERAL flags at once (objednané AND čaká sa), so the breakdown
    numbers must never be presented as parts summing to the total — and an empty bucket
    is dropped instead of rendering a row of zeros. Pure helper, driven in the browser
    realm (app.js is a plain script → its functions and globals are reachable)."""
    console = _console_watch(page)
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    out = page.evaluate("""() => {
      ORDERS = [{key: 'a'}, {key: 'b'}, {key: 'c'}];
      ORDERED = {a: true}; WAITING = {a: true}; INSTOCK = {}; UNAVAIL = {b: true};
      const s = toOrderSummary(ORDERS);
      return { s, text: toOrderSummaryText(s) };
    }""")

    assert out["s"] == {"total": 3, "remaining": 1, "ordered": 1, "waiting": 1,
                        "instock": 0, "unavail": 1}
    # 'a' is double-flagged and 'c' is untouched → 1+1+1 != 3 on purpose
    assert out["text"] == ("📋 Ostáva vybaviť 1 z 3 položiek"
                           " · objednané 1 · čaká sa 1 · nedostupné 1")
    assert "skladom" not in out["text"], "empty bucket must be dropped"

    # everything resolved → no breakdown tail at all
    all_done = page.evaluate("""() => {
      ORDERS = [{key: 'a'}]; ORDERED = {a: true}; WAITING = {}; INSTOCK = {}; UNAVAIL = {};
      return toOrderSummaryText(toOrderSummary(ORDERS));
    }""")
    assert all_done == "📋 Ostáva vybaviť 0 z 1 položiek · objednané 1"

    assert console == [], f"console not clean: {console}"
