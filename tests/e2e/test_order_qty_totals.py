"""E2E of #206 — súčet ks za rovnaký produkt naprieč objednávkami.

`build_to_order_rows` emits ONE row per order line and never dedups, so a product three
customers ordered is three rows and the manager had to add the quantities up in his head
before ordering from the supplier. The total is VISUAL only — the row model, its keys and
its per-line flags are untouched.

`toorder_server` has exactly this shape: ORBIS's two lines share itemCode S1 with
quantities 1 and 2 (→ spolu 3 ks), while CITRADE's four lines are four distinct codes.
"""


def _console_watch(page):
    errs = []
    page.on("console", lambda m: errs.append(f"[{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)
    return errs


def test_repeated_product_shows_the_summed_quantity(page, toorder_server):
    console = _console_watch(page)
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    # both S1 lines carry the group total (1 ks + 2 ks)
    totals = page.locator(".toorder-row[data-code='S1'] .to-total").all_inner_texts()
    assert totals == ["Σ spolu 3 ks", "Σ spolu 3 ks"], totals

    # a product on a single line gets no chip — it would only repeat the qty beside it
    assert page.locator(".toorder-row[data-code='C1'] .to-total").count() == 0
    assert page.locator(".to-total").count() == 2

    assert console == [], f"console not clean: {console}"


def test_total_counts_lines_hidden_by_the_handled_filter(page, toorder_server):
    """The manager orders the PRODUCT, not the visible rows: a sibling hidden by
    „skryť poriešené" (#205) must still count towards „spolu X ks", or he would order
    3 ks as 1 ks the moment he ticks one of the two lines."""
    console = _console_watch(page)
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    page.locator("#toToolbar .to-hidehandled").click()
    page.locator(".toorder-row[data-code='S1']").first.locator(".to-instock").click()
    page.wait_for_function(
        "() => /Ostáva vybaviť 6 z 7/.test(document.getElementById('toToolbar').textContent)")

    page.reload()                       # the repaint that actually drops the hidden line
    page.wait_for_selector(".toorder-row")
    rows = page.locator(".toorder-row[data-code='S1']")
    assert rows.count() == 1, "one S1 line is handled → hidden"
    assert rows.first.locator(".to-total").inner_text() == "Σ spolu 3 ks"

    assert console == [], f"console not clean: {console}"


def test_quantity_parsing_matches_what_the_row_displays(page, toorder_server):
    """The export gives qty as a string and the row falls back to '1' when it is missing,
    so the arithmetic must fall back the same way — a line shown as „1 ks" may never count
    as 0. Pure helpers, driven in the browser realm."""
    console = _console_watch(page)
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    out = page.evaluate("""() => {
      const items = [
        {itemCode: 'A', qty: '2'}, {itemCode: 'A', qty: '3'},
        {itemCode: 'B', qty: ''},          // missing → the row shows „1 ks"
        {itemCode: 'B', qty: 'x'},         // unparseable → same fallback
        {itemCode: 'C', qty: '5'},
      ];
      return { totals: groupQtyTotals(items), qtyMissing: orderQty({}), qtyStr: orderQty({qty: '7'}) };
    }""")

    assert out["totals"] == {"A": {"qty": 5, "lines": 2},
                             "B": {"qty": 2, "lines": 2},
                             "C": {"qty": 5, "lines": 1}}
    assert out["qtyMissing"] == 1 and out["qtyStr"] == 7

    assert console == [], f"console not clean: {console}"
