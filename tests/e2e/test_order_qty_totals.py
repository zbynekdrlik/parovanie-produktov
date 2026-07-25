"""E2E of #206 — súčet ks za rovnaký produkt naprieč objednávkami.

`build_to_order_rows` emits ONE row per order line and never dedups, so a product three
customers ordered is three rows and the manager had to add the quantities up in his head
before ordering from the supplier. The total is VISUAL only — the row model, its keys and
its per-line flags are untouched.

SCOPE (review of the #205-#208 batch): the chip counts the supplier's OUTSTANDING lines
— the same set „📋 Kopírovať objednávku" (#207) pastes — so the number on screen always
equals the number in the e-mail, whatever the „skryť poriešené" (#205) toggle says. The
FULL demand (settled lines included) stays readable in the chip's tooltip.

SCOPE (review pass 2): that equality has to hold WITHOUT a reload too. A per-row flag
toggle deliberately does not repaint `#list`, so the chips are rewritten in place on every
toggle — otherwise the screen keeps the number from the last full paint while the copy
button (narrowed at click time) already pastes the smaller one. And the chip belongs to a
product that spans SEVERAL order lines, so it survives a sibling being flagged: it then
shows what is left, with the whole demand in the tooltip.

SCOPE (review pass 3): „outstanding" is ONE predicate — `!isHandled` — behind ALL of it:
the #205 hide filter, the supplier chip colour, the #208 toolbar tally, this chip, the
copy and the empty-list wording. Pass 2's second, narrower scope (settled flags only, so
„čaká sa" stayed in the order) made those surfaces contradict each other on screen; the
last test here pins that they cannot again.

`toorder_server` has exactly this shape: ORBIS's two lines share itemCode S1 with
quantities 1 and 2 (→ spolu 3 ks), while CITRADE's four lines are four distinct codes.
"""

import re

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


def test_a_handled_line_leaves_the_chip_showing_what_is_left(page, toorder_server):
    """The chip is the number the manager types into the supplier e-mail, so it counts the
    OUTSTANDING lines only: the moment he flags one of the two S1 lines „skladom", the
    supplier is owed the rest — not the original 3 ks. It must NOT disappear at that point:
    the product genuinely spans two orders, and „the full demand is one hover away" is only
    true while the chip carrying that tooltip is on screen. Independent of the #205 toggle:
    the same number with it on and off."""
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
    # the product still spans two orders → both rows keep the chip, now stating the work
    # LEFT, and the whole demand stays readable in the tooltip
    assert page.locator(".to-total").all_inner_texts() == ["Σ spolu 2 ks"] * 2
    assert page.locator(".to-total").first.get_attribute("title") == \
        "Spolu vo všetkých objednávkach: 3 ks · nevybavené: 2 ks"

    # …and with „skryť poriešené" on, the surviving row says exactly the same thing
    page.locator("#toToolbar .to-hidehandled").click()
    page.wait_for_function(
        "() => document.querySelectorAll(\".toorder-row[data-code='S1']\").length === 1")
    assert page.locator(".to-total").all_inner_texts() == ["Σ spolu 2 ks"]
    assert page.locator(".to-total").first.get_attribute("title") == \
        "Spolu vo všetkých objednávkach: 3 ks · nevybavené: 2 ks"

    assert console == [], f"console not clean: {console}"


def test_the_chip_follows_a_flag_toggle_without_a_repaint(page, toorder_server):
    """NO `reload()` between the flag click and the copy click — that window IS the bug.
    A per-row toggle deliberately does not repaint `#list` (#205/#233: a row the manager is
    typing in must never vanish under him), so the chip has to be rewritten IN PLACE. Left
    stale it kept the number from the last full paint — „Σ spolu 3 ks" on screen, tooltip
    positively claiming „nevybavené: 3 ks" — while „Kopírovať objednávku", narrowed at
    click time, already pasted 2 ks. Two numbers for one supplier order, again."""
    console = _console_watch(page)
    page.add_init_script(_SPY)
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")
    assert page.locator(".to-total").all_inner_texts() == ["Σ spolu 3 ks"] * 2

    page.locator(".toorder-row[data-code='S1']").first.locator(".to-instock").click()
    page.wait_for_function(
        "() => /6 položiek z 7/.test(document.getElementById('toToolbar').textContent)")

    # the list was NOT repainted — both rows are still there, and both chips now say 2 ks
    assert page.locator(".toorder-row[data-code='S1']").count() == 2
    assert page.locator(".to-total").all_inner_texts() == ["Σ spolu 2 ks"] * 2
    assert page.locator(".to-total").first.get_attribute("title") == \
        "Spolu vo všetkých objednávkach: 3 ks · nevybavené: 2 ks"

    # …and the clipboard agrees with what the screen says, in the SAME session
    page.locator(".toorder-supplier").filter(has_text="ORBIS").locator(".tosup-copy").click()
    page.wait_for_function("() => window.__copied.length === 1")
    assert page.evaluate("() => window.__copied[0]") == \
        "Objednávka — ORBIS (1 položka)\nS1 | Veľkosť: M | 2 ks"

    assert console == [], f"console not clean: {console}"


def test_the_screen_number_equals_the_copied_number(page, toorder_server):
    """The invariant behind both #206 and #207: whatever „Σ spolu" says for a product, the
    copied order asks for exactly that many pieces — in BOTH toggle states. Two different
    numbers for one supplier order is how the wrong quantity gets ordered."""
    console = _console_watch(page)
    page.add_init_script(_SPY)
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    def _copied_qty():
        # the spy is re-installed on every navigation, so the buffer is emptied per call
        # instead of being indexed across a reload
        page.evaluate("() => { window.__copied = []; }")
        page.locator(".toorder-supplier").filter(has_text="ORBIS").locator(
            ".tosup-copy").click()
        page.wait_for_function("() => window.__copied.length === 1")
        copied = page.evaluate("() => window.__copied[0]")
        # the „N ks" column, wherever it sits (empty columns are dropped, so its index
        # depends on whether the line carries a grube code / a link)
        qty = sum(int(re.fullmatch(r"(\d+) ks", part.strip()).group(1))
                  for ln in copied.split("\n")[1:] if ln.startswith("S1 |")
                  for part in ln.split("|") if re.fullmatch(r"\d+ ks", part.strip()))
        chips = page.locator(".toorder-row[data-code='S1'] .to-total").all_inner_texts()
        if chips:                       # a chip exists → it must state that same number
            assert chips == [f"Σ spolu {qty} ks"] * len(chips), (chips, copied)
        return qty

    assert _copied_qty() == 3          # nothing handled: 1 ks + 2 ks

    page.locator(".toorder-row[data-code='S1']").first.locator(".to-instock").click()
    page.wait_for_function(
        "() => /6 položiek z 7/.test(document.getElementById('toToolbar').textContent)")
    page.reload()
    page.wait_for_selector(".toorder-row")
    assert _copied_qty() == 2          # the qty-1 line is handled → 2 ks left to order

    page.locator("#toToolbar .to-hidehandled").click()      # same again, filter ON
    page.wait_for_function("() => document.querySelectorAll('.toorder-row').length === 6")
    assert _copied_qty() == 2

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


def test_outstanding_scope_is_exactly_the_one_handled_predicate(page, toorder_server):
    """`outstandingOf` is the ONE scope every surface of the tab uses, and it is exactly
    `!isHandled` — no second, narrower predicate beside it. It reads the LIVE flag maps
    (never the o.* snapshot, never the rendered set), and „čaká sa" counts as handled like
    every other flag: it means „not today's work", so the line leaves the order until the
    manager switches the flag back off."""
    console = _console_watch(page)
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    out = page.evaluate("""() => {
      const items = [{key: 'a', itemCode: 'A', qty: '1'}, {key: 'b', itemCode: 'A', qty: '2'},
                     {key: 'c', itemCode: 'A', qty: '4'}, {key: 'd', itemCode: 'A', qty: '8'}];
      ORDERED = {a: true}; WAITING = {d: true}; INSTOCK = {}; UNAVAIL = {c: true};
      return { keys: outstandingOf(items).map(o => o.key),
               totals: groupQtyTotals(outstandingOf(items)),
               handled: items.map(o => isHandled(o)),
               // the invariant itself: the two can never be defined apart again
               agrees: items.every(o => outstandingOf([o]).length === (isHandled(o) ? 0 : 1)),
               empty: outstandingOf([]).length, nullish: outstandingOf(null).length };
    }""")

    assert out["keys"] == ["b"], 'a parked („čaká sa") line is not today\'s work'
    assert out["totals"] == {"A": {"qty": 2, "lines": 1}}
    assert out["handled"] == [True, False, True, True]
    assert out["agrees"] is True, "outstandingOf must be exactly !isHandled"
    assert out["empty"] == 0 and out["nullish"] == 0

    assert console == [], f"console not clean: {console}"


def _sum_text(page):
    return page.locator("#toToolbar .to-sum").inner_text()


def _remaining(page):
    """the „ostáva vybaviť N" number of the #208 toolbar, in whatever scope it is showing"""
    # „Ostáva" / „ORBIS: ostáva", and the noun is declined (1 položku / 2-4 položky / 5+
    # položiek), so the prefix stops before the ending
    m = re.search(r"stáva vybaviť (\d+) polož", _sum_text(page))
    assert m, _sum_text(page)
    return int(m.group(1))


def _chip_class(page, label):
    return page.evaluate(
        "(l) => { const b = [...document.querySelectorAll('#filters button')]"
        ".find(x => x.textContent.startsWith(l)); return b ? b.className : null; }", label)


def _totals(page, code="S1"):
    """the „Σ spolu" chips of a product: their text and the „nevybavené" number they carry"""
    return page.evaluate(
        "(c) => [...document.querySelectorAll('.toorder-row[data-code=\"' + c + '\"] "
        ".to-total')].map(t => ({text: t.textContent, title: t.title}))", code)


def _chip_open_qty(page, code="S1"):
    tips = {t["title"] for t in _totals(page, code)}
    if not tips:
        return None                      # the product has no chip in this view
    assert len(tips) == 1, tips           # …and every row of it must say the same thing
    return int(re.search(r"nevybavené: (\d+) ks", tips.pop()).group(1))


def _copied_qty(page, sup):
    """the pieces the copy button actually hands over for `sup` — 0 when it refuses, and
    0 when the group is not even on screen (nothing to reach, nothing owed)."""
    grp = page.locator(".toorder-supplier").filter(has_text=sup)
    if grp.count() == 0:
        return 0
    page.evaluate("() => { window.__copied = []; }")
    grp.locator(".tosup-copy").click()
    page.wait_for_function(
        "() => window.__copied.length === 1 || [...document.querySelectorAll('.tosup-copy')]"
        ".some(b => /Nič na objednanie/.test(b.textContent))")
    copied = page.evaluate("() => window.__copied")
    return sum(int(m.group(1)) for m in re.finditer(r"\|\s*(\d+) ks", copied[0])) if copied else 0


def test_every_surface_agrees_when_a_group_is_parked(page, toorder_server):
    """THE invariant of the tab, and the one nothing pinned before: the #208 toolbar tally,
    the „Σ spolu" chip's „nevybavené" number, the supplier chip's colour and the number of
    pieces the copy emits must all describe the SAME set of work — with the #205 filter on
    and off, and under „Všetci" as well as the supplier's own chip.

    A second, narrower scope for the copy/chip broke exactly this: with both ORBIS lines
    parked („⏳ Počkať") the toolbar read „ostáva vybaviť 0 z 2" while the chip beside it
    insisted „nevybavené: 3 ks", the chip was RED (done) over 3 ks the app still counted as
    work, and with „skryť poriešené" on the whole group — copy button included — vanished
    while those pieces were still supposedly outstanding.

    NO `page.reload()` between a mutating click and an asserting one: a per-row toggle
    deliberately does not repaint `#list`, and that window is where the surfaces drift."""
    console = _console_watch(page)
    page.add_init_script(_SPY)
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    # ── park BOTH ORBIS lines (3 ks of one product across two orders)
    for i in range(2):
        page.locator(".toorder-row[data-code='S1']").nth(i).locator(".to-wait").click()
    page.wait_for_function(
        "() => /čaká sa 2/.test(document.querySelector('#toToolbar .to-sum').textContent)")

    # „Všetci", filter OFF — same session, no repaint of `#list`
    assert _remaining(page) == 5                    # 7 lines − the 2 parked
    assert "done" in _chip_class(page, "ORBIS"), "nothing left to deal with → RED"
    assert [t["text"] for t in _totals(page)] == ["Σ spolu 0 ks"] * 2
    assert _chip_open_qty(page) == 0
    assert "Spolu vo všetkých objednávkach: 3 ks" in _totals(page)[0]["title"]
    assert _copied_qty(page, "ORBIS") == 0

    # ORBIS's own chip — the tally narrows to what he is looking at, nothing else moves
    page.get_by_role("button", name="ORBIS (2)").click()
    page.wait_for_function("() => document.querySelectorAll('.toorder-row').length === 2")
    assert _sum_text(page).startswith("📋 ORBIS: ostáva vybaviť 0 položiek z 2")
    assert "čaká sa 2" in _sum_text(page)
    assert _remaining(page) == 0 and _chip_open_qty(page) == 0
    assert _copied_qty(page, "ORBIS") == 0

    # …and with „skryť poriešené" on, the group disappearing is now HONEST: it owes nothing
    page.locator("#toToolbar .to-hidehandled").click()
    page.wait_for_function("() => !document.querySelector('.toorder-row')")
    assert _remaining(page) == 0
    assert page.locator("#empty").inner_text() == \
        "Tento dodávateľ je vybavený — poriešené riadky sú skryté"
    assert _copied_qty(page, "ORBIS") == 0

    page.get_by_role("button", name="Všetci (7)").click()
    page.wait_for_function("() => document.querySelectorAll('.toorder-row').length === 5")
    assert _remaining(page) == 5
    assert page.locator(".toorder-supplier").filter(has_text="ORBIS").count() == 0
    assert _copied_qty(page, "ORBIS") == 0     # hidden AND owing nothing — not unreachable

    # ── mixed group: un-park ONE line → it is today's work again, on every surface at once
    page.locator("#toToolbar .to-hidehandled").click()
    page.wait_for_function("() => document.querySelectorAll('.toorder-row').length === 7")
    page.locator(".toorder-row[data-code='S1']").first.locator(".to-wait").click()
    page.wait_for_function(
        "() => /čaká sa 1/.test(document.querySelector('#toToolbar .to-sum').textContent)")

    assert _remaining(page) == 6
    assert "todo" in _chip_class(page, "ORBIS"), "work is back → GREEN"
    # the un-parked line is the newest ORBIS order (1 ks); the whole demand stays in the tooltip
    assert [t["text"] for t in _totals(page)] == ["Σ spolu 1 ks"] * 2
    assert _chip_open_qty(page) == 1
    assert _copied_qty(page, "ORBIS") == 1

    page.get_by_role("button", name="ORBIS (2)").click()
    page.wait_for_function("() => document.querySelectorAll('.toorder-row').length === 2")
    assert _sum_text(page).startswith("📋 ORBIS: ostáva vybaviť 1 položku z 2")
    assert _remaining(page) == 1 and _chip_open_qty(page) == 1
    assert _copied_qty(page, "ORBIS") == 1

    page.locator("#toToolbar .to-hidehandled").click()   # the parked sibling goes away…
    page.wait_for_function("() => document.querySelectorAll('.toorder-row').length === 1")
    assert _remaining(page) == 1 and _chip_open_qty(page) == 1
    assert _copied_qty(page, "ORBIS") == 1               # …and it owed nothing anyway

    assert console == [], f"console not clean: {console}"
