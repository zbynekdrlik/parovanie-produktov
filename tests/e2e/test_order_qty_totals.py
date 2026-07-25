"""E2E of #206 — súčet ks za rovnaký produkt naprieč objednávkami.

`build_to_order_rows` emits ONE row per order line and never dedups, so a product three
customers ordered is three rows and the manager had to add the quantities up in his head
before ordering from the supplier. The total is VISUAL only — the row model, its keys and
its per-line flags are untouched.

SCOPE (review of the #205-#208 batch): the chip counts the supplier's OUTSTANDING lines
— the same set „📋 Kopírovať objednávku" (#207) pastes — so the number on screen always
equals the number in the e-mail, whatever the „skryť poriešené" (#205) toggle says. The
FULL demand (handled lines included) stays readable in the chip's tooltip.

`toorder_server` has exactly this shape: ORBIS's two lines share itemCode S1 with
quantities 1 and 2 (→ spolu 3 ks), while CITRADE's four lines are four distinct codes.
"""

_SPY = """
window.__copied = [];
Object.defineProperty(navigator, 'clipboard', {
  configurable: true,
  value: { writeText: (t) => { window.__copied.push(String(t)); return Promise.resolve(); } },
});
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
    # nothing is handled yet → outstanding IS the whole demand, and the tooltip says both
    assert page.locator(".toorder-row[data-code='S1'] .to-total").first.get_attribute(
        "title") == "Spolu vo všetkých objednávkach: 3 ks · nevybavené: 3 ks"

    # a product on a single line gets no chip — it would only repeat the qty beside it
    assert page.locator(".toorder-row[data-code='C1'] .to-total").count() == 0
    assert page.locator(".to-total").count() == 2

    assert console == [], f"console not clean: {console}"


def test_total_drops_a_handled_line_and_keeps_it_in_the_tooltip(page, toorder_server):
    """The chip is the number the manager types into the supplier e-mail, so it counts the
    OUTSTANDING lines only: the moment he flags one of the two S1 lines „skladom", the
    supplier is owed the rest — not the original 3 ks. Independent of the #205 toggle: the
    same number with it on and off, and the full demand one hover away."""
    console = _console_watch(page)
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    # the qty-1 line (newest order first) → 2 ks stay outstanding on the other line
    page.locator(".toorder-row[data-code='S1']").first.locator(".to-instock").click()
    page.wait_for_function(
        "() => /6 položiek z 7/.test(document.getElementById('toToolbar').textContent)")
    page.reload()
    page.wait_for_selector(".toorder-row")

    assert page.locator(".toorder-row[data-code='S1']").count() == 2, \
        "with the filter off both lines stay on screen"
    # one outstanding line left → the chip would only repeat its own „2 ks" → no chip
    assert page.locator(".to-total").count() == 0

    # …and with „skryť poriešené" on, the surviving row still shows no second number
    page.locator("#toToolbar .to-hidehandled").click()
    page.wait_for_function(
        "() => document.querySelectorAll(\".toorder-row[data-code='S1']\").length === 1")
    assert page.locator(".to-total").count() == 0

    assert console == [], f"console not clean: {console}"


def test_the_screen_number_equals_the_copied_number(page, toorder_server):
    """The invariant behind both #206 and #207: whatever „Σ spolu" says for a product, the
    copied order asks for exactly that many pieces — in BOTH toggle states. Two different
    numbers for one supplier order is how the wrong quantity gets ordered."""
    console = _console_watch(page)
    page.add_init_script(_SPY)
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    def _copied_qty(idx):
        page.locator(".toorder-supplier").filter(has_text="ORBIS").locator(
            ".tosup-copy").click()
        page.wait_for_function(f"() => window.__copied.length === {idx + 1}")
        copied = page.evaluate(f"() => window.__copied[{idx}]")
        qty = sum(int(ln.split("|")[-2].strip().split(" ")[0])
                  for ln in copied.split("\n")[1:] if ln.startswith("S1 |"))
        chips = page.locator(".toorder-row[data-code='S1'] .to-total").all_inner_texts()
        if chips:                       # a chip exists → it must state that same number
            assert chips == [f"Σ spolu {qty} ks"] * len(chips), (chips, copied)
        return qty

    assert _copied_qty(0) == 3          # nothing handled: 1 ks + 2 ks

    page.locator(".toorder-row[data-code='S1']").first.locator(".to-instock").click()
    page.wait_for_function(
        "() => /6 položiek z 7/.test(document.getElementById('toToolbar').textContent)")
    page.reload()
    page.wait_for_selector(".toorder-row")
    assert _copied_qty(1) == 2          # the qty-1 line is handled → 2 ks left to order

    page.locator("#toToolbar .to-hidehandled").click()      # same again, filter ON
    page.wait_for_function("() => document.querySelectorAll('.toorder-row').length === 6")
    assert _copied_qty(2) == 2

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


def test_outstanding_scope_reads_the_live_flag_maps(page, toorder_server):
    """`outstandingOf` is the ONE scope both the chip and the copy use, and it reads the
    LIVE flag maps (like isHandled) — never the o.* snapshot, never the rendered set."""
    console = _console_watch(page)
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    out = page.evaluate("""() => {
      const items = [{key: 'a', itemCode: 'A', qty: '1'}, {key: 'b', itemCode: 'A', qty: '2'},
                     {key: 'c', itemCode: 'A', qty: '4'}];
      ORDERED = {a: true}; WAITING = {}; INSTOCK = {}; UNAVAIL = {c: true};
      return { keys: outstandingOf(items).map(o => o.key),
               totals: groupQtyTotals(outstandingOf(items)),
               empty: outstandingOf([]).length, nullish: outstandingOf(null).length };
    }""")

    assert out["keys"] == ["b"]
    assert out["totals"] == {"A": {"qty": 2, "lines": 1}}
    assert out["empty"] == 0 and out["nullish"] == 0

    assert console == [], f"console not clean: {console}"
