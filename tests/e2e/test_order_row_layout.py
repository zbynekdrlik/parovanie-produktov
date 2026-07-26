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
