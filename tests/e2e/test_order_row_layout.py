"""#241 + #242 — the „Na objednanie" row must stay ON the screen, and a wrong pairing
link must be fixable right where the manager sees it.

Measured on the live app (v0.93.0, viewport 1280): `document.scrollWidth` 1778 — the row
is `display:flex` with NO `flex-wrap` above 760 px and most cells are `flex:0 0 auto` +
`white-space:nowrap`, so once the rigid cells no longer fit they simply march off the
right edge. On the worst row FIVE cells sat outside the viewport (`.to-shopnote` 1386 …
`.to-unavail` 1778) and `.to-name` was squeezed to width 0 — the product name entirely
invisible. Nine „💬 Komentár" buttons across the tab were unreachable.

And 55 of 89 live rows rendered the reviewed pairing 🔗 read-only, with NO ✏️ at all, so
the manager could not correct a wrong link without going back to the review tab and
pairing the product again („musím to znova nájsť a načítať").

Width is asserted as „no row cell may end past the viewport" rather than against a magic
pixel number, so the rule survives a palette/label change. Widths cover the sizes from
the ticket (1280/1440/1600/1780) plus 1920, which was already fine and must stay fine.
"""
import pytest


def _console(page):
    msgs = []
    page.on("console", lambda m: msgs.append(f"[{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)
    return msgs


def _offscreen_cells(page):
    """Every direct child of every row whose right edge is past the viewport."""
    return page.evaluate("""() => {
      const vw = document.documentElement.clientWidth;
      const out = [];
      document.querySelectorAll('.toorder-row').forEach(r => {
        [...r.children].forEach(c => {
          const b = c.getBoundingClientRect();
          if (b.width > 0 && b.right > vw + 1) out.push({
            key: r.dataset.key, cls: String(c.className),
            right: Math.round(b.right), txt: (c.textContent || '').trim().slice(0, 24)});
        });
      });
      return out;
    }""")


@pytest.mark.parametrize("width", [1280, 1440, 1600, 1780, 1920])
def test_no_row_cell_is_pushed_off_the_screen(page, toorder_wide_server, width):
    console = _console(page)
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(toorder_wide_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    off = _offscreen_cells(page)
    assert off == [], f"@{width}px these row cells are unreachable: {off}"
    # the page itself must not gain a horizontal scrollbar either
    doc = page.evaluate("() => [document.documentElement.scrollWidth,"
                        " document.documentElement.clientWidth]")
    assert doc[0] <= doc[1] + 1, f"@{width}px page scrolls horizontally: {doc}"

    assert console == [], f"console not clean: {console}"


def test_the_product_name_stays_readable_when_the_row_is_crowded(page, toorder_wide_server):
    """`.to-name` was collapsed to 0 px on a crowded row — the manager saw a line with a
    code and buttons but no idea WHICH product it was."""
    console = _console(page)
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(toorder_wide_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    names = page.evaluate("""() => [...document.querySelectorAll('.toorder-row .to-name')]
      .map(n => ({w: Math.round(n.getBoundingClientRect().width), txt: n.textContent.trim()}))""")
    assert names, "fixture must render rows with a product name"
    tiny = [n for n in names if n["w"] < 60]
    assert tiny == [], f"product name squeezed to nothing: {tiny}"

    assert console == [], f"console not clean: {console}"


# --- #242: the reviewed link must be editable ON this tab ------------------- #
def test_a_reviewed_pairing_link_offers_an_edit_button(page, toorder_wide_server):
    """This is the whole complaint: the row shows 🔗 from a reviewed decision and,
    before the fix, nothing to click to correct it."""
    console = _console(page)
    page.goto(toorder_wide_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    row = page.locator('.toorder-row[data-key="20261217|61247/L"]')
    assert row.locator("a.to-link").count() == 1, "row must render the reviewed link"
    assert row.locator(".to-pairedit").count() == 1, \
        "a reviewed pairing must be correctable from this tab (#242)"

    assert console == [], f"console not clean: {console}"


def test_fixing_a_reviewed_link_sticks_and_reaches_the_import(page, toorder_wide_server):
    """The failure was not only 'no button' — an inline paste on such a row was
    outranked by the decision in BOTH the render and the eshop write-back, so the save
    looked accepted and changed nothing. Assert the new link survives a reload AND is
    what /api/import ships for the product."""
    console = _console(page)
    page.goto(toorder_wide_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    new_url = "https://www.huntingshop.eu/polokosela-forest-OPRAVENA/"
    row = page.locator('.toorder-row[data-key="20261217|61247/L"]')
    row.locator(".to-pairedit").click()
    row.locator(".to-pairurl").fill(new_url)
    with page.expect_response("**/api/order-decision-url") as resp:
        row.locator(".to-pairsave").click()
    assert resp.value.status == 200

    # back to a link, carrying the corrected address — without a reload, because the
    # repaint is what the manager actually sees (a reload would hide a stale-DOM bug)
    page.wait_for_selector('.toorder-row[data-key="20261217|61247/L"] a.to-link')
    assert row.locator("a.to-link").get_attribute("href") == new_url

    # the SIBLING size is the same review product → one decision covers both (#204 shape)
    sib = page.locator('.toorder-row[data-key="20261218|61247/XL"]')
    assert sib.locator("a.to-link").get_attribute("href") == new_url

    page.reload()
    page.wait_for_selector(".toorder-row")
    assert row.locator("a.to-link").get_attribute("href") == new_url

    # and it is what the eshop import would receive, for EVERY variant of the product
    served = page.evaluate("""async () => {
      const j = await (await fetch('/api/orders')).json();
      return (j.orders || []).filter(o => o.itemCode.startsWith('61247/'))
                             .map(o => o.supplierUrl);
    }""")
    assert served == [new_url, new_url], served

    assert console == [], f"console not clean: {console}"


def test_the_url_box_never_collapses_below_a_readable_width(page, toorder_wide_server):
    """`.to-pair{min-width:260px}` was the only unpinned hunk of #241 — mutating it back
    to 230px left all nine layout tests green.

    It cannot be pinned by a RENDERED width: the cell's flex-basis is 320px, so on every
    viewport this fixture can reach the floor never binds. What the floor actually
    guarantees is what happens WHEN it binds — `.to-pairurl` has its own 110px floor, so
    a cell floor too small to hold the save button, the gaps AND a readable address
    field lets the field's own floor win and the URL becomes an unreadable 110px stub
    (the reported „pole na dodávateľskú URL sa scvrklo"). So assert that relationship on
    the DECLARED floors, with every component measured off the live page — it survives a
    label or palette change, unlike the pixel number itself."""
    console = _console(page)
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(toorder_wide_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row .to-pairurl")

    m = page.evaluate("""() => {
      const p = document.querySelector('.toorder-row .to-pair');
      const cs = getComputedStyle(p);
      const save = p.querySelector('.to-pairsave').getBoundingClientRect().width;
      return {cellFloor: parseFloat(cs.minWidth),
              inputFloor: parseFloat(getComputedStyle(p.querySelector('.to-pairurl')).minWidth),
              save: Math.round(save), gap: parseFloat(cs.columnGap || cs.gap || 0)};
    }""")
    room = m["cellFloor"] - m["save"] - 2 * m["gap"]      # left for the address field
    assert room >= m["inputFloor"] + 35, (
        f"at its narrowest the cell leaves the URL box only {room}px — barely its own "
        f"{m['inputFloor']}px floor, i.e. an unreadable stub: {m}")

    assert console == [], f"console not clean: {console}"


# --- the .to-badlink branch: an unusable reviewed link is the row that most --- #
# --- needs repairing, and it had ZERO coverage.                             --- #
_BAD = '.toorder-row[data-key="20261221|77777/S"]'


def test_an_unusable_reviewed_link_is_inert_and_still_repairable(page, toorder_wide_server):
    """`/api/decision` does not validate the scheme, so a scheme-less URL really does
    reach the store. It must NOT become an `<a href="">` (that navigates to the page
    itself and takes every open editor with it), and the ✏️ must still be there —
    deleting `row.appendChild(pairPencil(bad))` left the whole suite green."""
    console = _console(page)
    page.goto(toorder_wide_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    row = page.locator(_BAD)
    assert row.locator(".to-badlink").count() == 1, "unusable value must render inert"
    assert row.locator("a.to-link").count() == 0, "must never be a link"
    assert row.locator(".to-badlink").get_attribute("title") is not None
    assert row.locator(".to-pairedit").count() == 1, \
        "the row that most needs fixing must carry the ✏️"

    assert console == [], f"console not clean: {console}"


def test_the_editor_on_an_unusable_link_prefills_with_the_stored_value(page, toorder_wide_server):
    """He has to SEE the broken address to spot the typo — an empty box would make him
    retype the whole URL."""
    console = _console(page)
    page.goto(toorder_wide_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    row = page.locator(_BAD)
    row.locator(".to-pairedit").click()
    assert row.locator(".to-pairurl").input_value() == "www.citrade.sk/rukavice-zimne"

    assert console == [], f"console not clean: {console}"


def test_typing_on_an_unusable_link_row_survives_a_repaint(page, toorder_wide_server):
    """A successful save on ANOTHER row repaints the whole tab (#233 — `saveSupplier`
    calls `renderToOrder()`), and `_EDITORS.pair.open` is what puts each half-typed
    editor back. It finds the node to replace through `a.to-link, .to-badlink`;
    narrowing that selector back to `a.to-link` drops the correction on exactly this
    row — the one the manager most needs to fix — and left the whole suite green.

    NOTE: a per-line FLAG toggle is deliberately NOT a repaint (it only re-styles the
    row + the chips), so it cannot be used to provoke this."""
    console = _console(page)
    page.goto(toorder_wide_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    row = page.locator(_BAD)
    row.locator(".to-pairedit").click()
    row.locator(".to-pairurl").fill("https://www.citrade.sk/rukavice-ZATIAL-NEULOZENE")

    # assign a supplier on the supplier-less row → saveSupplier() → renderToOrder()
    n1 = page.locator('.toorder-row[data-key="20261220|N1"]')
    n1.locator(".to-supinput").fill("CITRADE")
    with page.expect_response("**/api/order-supplier") as resp:
        n1.locator(".to-supsave").click()
    assert resp.value.status == 200
    page.wait_for_selector('.toorder-row[data-key="20261220|N1"] .to-suptag')

    assert row.locator(".to-pairurl").count() == 1, "the editor was thrown away by the repaint"
    assert row.locator(".to-pairurl").input_value() == \
        "https://www.citrade.sk/rukavice-ZATIAL-NEULOZENE"

    assert console == [], f"console not clean: {console}"


# --- a split product is not a blind spot ------------------------------------ #
_SPLIT = '.toorder-row[data-key="20261222|55555/M"]'


def test_a_split_product_shows_its_per_size_link(page, toorder_wide_server):
    """Without variant_links the split branch of `link_row_specs` yields nothing, so the
    row rendered an EMPTY paste box for a product that IS paired per size — and the save
    went to order_pairings, which the zip discards while the nightly ships it,
    permanently clobbering the already-uploaded per-size link."""
    console = _console(page)
    page.goto(toorder_wide_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    row = page.locator(_SPLIT)
    assert row.locator("a.to-link").get_attribute("href") == \
        "https://www.orbis.sk/termopodvlecenie-velkost-M"
    assert row.locator(".to-pair .to-pairurl").count() == 0, \
        "a per-size paired row must not offer an empty paste box"

    assert console == [], f"console not clean: {console}"


def test_a_split_row_sends_the_edit_to_the_per_size_panel(page, toorder_wide_server):
    """A product-wide save on a split row can only 409 (the endpoint refuses to discard
    the per-size links) or, on the inline path, corrupt one. So the button opens the
    per-size editor in the review tab instead of a paste box."""
    console = _console(page)
    page.goto(toorder_wide_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    row = page.locator(_SPLIT)
    assert row.locator(".to-pairedit").count() == 0, "no product-wide ✏️ on a split row"
    assert row.locator(".to-splitedit").count() == 1

    row.locator(".to-splitedit").click()
    page.wait_for_selector('#list .card[data-key="ORBIS|555"] .splitrow')
    assert page.locator("#pageTitle").inner_text() == "Kontrola párovania"

    assert console == [], f"console not clean: {console}"


def test_the_split_button_warns_before_discarding_unsaved_typing(page, toorder_wide_server):
    """✂️ leaves the tab, and leaving repaints `#list` from scratch — so every open
    inline editor on EVERY other row, and the half-typed text in it, is gone with no
    message at all. That is the silent-loss class #205/#233 exist to remove, and this
    button hides it especially well: it sits IN the row, right where ✏️ edits in
    place, so it does not read as navigation.

    Dismissing the warning must leave everything exactly as it was — same tab, same
    text, and no per-size panel opened behind his back."""
    console = _console(page)
    dialogs = []
    page.goto(toorder_wide_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    row = page.locator(_BAD)
    row.locator(".to-pairedit").click()
    row.locator(".to-pairurl").fill("https://www.citrade.sk/rukavice-ROZPISANE")

    page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
    page.locator(_SPLIT + " .to-splitedit").click()
    page.wait_for_timeout(300)

    assert len(dialogs) == 1, f"no warning before discarding typed text: {dialogs}"
    assert page.locator("#pageTitle").inner_text() == "Na objednanie", "navigated anyway"
    assert row.locator(".to-pairurl").input_value() == \
        "https://www.citrade.sk/rukavice-ROZPISANE"
    assert console == [], f"console not clean: {console}"


def test_the_split_button_keeps_the_saved_filter_preference(page, toorder_wide_server):
    """Getting to the per-size panel needs the review tab on a filter that shows a
    `split` card, but that is a NAVIGATION detail — it was also WRITTEN to
    localStorage, so one ✂️ silently replaced whichever review filter the manager had
    chosen, for good. The in-memory switch is enough; his stored preference is his."""
    console = _console(page)
    dialogs = []
    page.on("dialog", lambda d: (dialogs.append(d.message), d.accept()))
    page.add_init_script("localStorage.setItem('filter', 'bad');")
    page.goto(toorder_wide_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    page.locator(_SPLIT + " .to-splitedit").click()
    page.wait_for_selector('#list .card[data-key="ORBIS|555"] .splitrow')

    assert dialogs == [], "warned with nothing unsaved to lose"
    assert page.evaluate("() => localStorage.getItem('filter')") == "bad"
    assert console == [], f"console not clean: {console}"


def test_a_row_outside_the_review_set_keeps_its_inline_paste_box(page, toorder_wide_server):
    """The existing inline-pairing path must be untouched — it writes to order_pairings,
    which is still the right store for a code that has no reviewed decision."""
    console = _console(page)
    page.goto(toorder_wide_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    row = page.locator('.toorder-row[data-key="20261219|99999/M"]')
    assert row.locator(".to-pair .to-pairurl").count() == 1
    row.locator(".to-pairurl").fill("https://www.orbis.sk/nohavice-trophy/")
    with page.expect_response("**/api/order-pair") as resp:
        row.locator(".to-pairsave").click()
    assert resp.value.status == 200
    page.wait_for_selector('.toorder-row[data-key="20261219|99999/M"] a.to-link')

    assert console == [], f"console not clean: {console}"
