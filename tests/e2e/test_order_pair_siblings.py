"""#204 — pairing a URL on one order line must show up on its SIBLING lines at once.

`order_pairings.json` is keyed by forestshop itemCode (a PRODUCT property), so
/api/orders serves the same `pairUrl` on EVERY order line of that code. The client
used to update only the clicked row, leaving the sibling lines showing an empty
"vlož párovaciu URL" box even though the product WAS already paired on the server —
the manager pasted the URL again (and again) for each order.

Fixture (`toorder_server`): ORBIS has two lines with the same itemCode S1, in two
different orders (20260900, 20260890). The mirror behaviour already exists for the
per-product supplier assignment (saveSupplier); this pins the same for the pair URL.
"""

PAIR_URL = "https://dodavatel.test/produkt/s1"


def _rows(page, code):
    return page.locator(f".toorder-row[data-code='{code}']")


def test_pairing_one_line_shows_the_link_on_every_sibling_line(page, toorder_server):
    console = []
    page.on("console", lambda m: console.append(f"[{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    rows = _rows(page, "S1")
    assert rows.count() == 2, "fixture must have two sibling lines of S1"
    assert rows.locator(".to-link").count() == 0, "both siblings start unpaired"

    # paste + save the supplier URL on the FIRST sibling only
    first = rows.first
    first.locator(".to-pairurl").fill(PAIR_URL)
    with page.expect_response("**/api/order-pair"):
        first.locator(".to-pairsave").click()

    # BOTH lines must now render the 🔗 link — no reload, no second paste
    page.wait_for_function(
        "() => document.querySelectorAll(\"[data-code='S1'] .to-link\").length === 2",
        timeout=3000)
    hrefs = _rows(page, "S1").locator(".to-link").evaluate_all("els => els.map(e => e.href)")
    assert hrefs == [PAIR_URL, PAIR_URL], hrefs
    # and neither sibling still offers an empty paste box
    assert _rows(page, "S1").locator(".to-pairurl").count() == 0

    # the other suppliers' rows are untouched by the propagation
    assert _rows(page, "C1").locator(".to-link").count() == 0

    assert console == [], f"console not clean: {console}"


def test_clearing_the_pair_url_clears_every_sibling_line(page, toorder_server):
    """The inverse direction: emptying the URL on one line un-pairs the product, so no
    sibling may keep showing a stale link (the server already dropped it)."""
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    first = _rows(page, "S1").first
    first.locator(".to-pairurl").fill(PAIR_URL)
    with page.expect_response("**/api/order-pair"):
        first.locator(".to-pairsave").click()
    page.wait_for_function(
        "() => document.querySelectorAll(\"[data-code='S1'] .to-link\").length === 2",
        timeout=3000)

    # reopen the editor on the first line and save an empty value → clears the pairing
    _rows(page, "S1").first.locator(".to-pairedit").click()
    _rows(page, "S1").first.locator(".to-pairurl").fill("")
    with page.expect_response("**/api/order-pair"):
        _rows(page, "S1").first.locator(".to-pairsave").click()

    page.wait_for_function(
        "() => document.querySelectorAll(\"[data-code='S1'] .to-link\").length === 0",
        timeout=3000)
    assert _rows(page, "S1").locator(".to-pairurl").count() == 2


def test_a_non_http_stored_pair_url_never_renders_as_a_clickable_link(page, toorder_server):
    """PR #233 review — `/api/order-pair` validates the scheme on WRITE, but the row
    rendered `a.href = o.pairUrl` (and `o.supplierUrl`) straight from the store, so a
    `javascript:` value left there by any other path would render as a clickable link.
    The GRUBE .de link on the same row already carried this guard; now all three do."""
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")
    r = page.evaluate("""() => {
      const mk = (o) => renderOrderRow(Object.assign(
        {key: 'X|W1', itemCode: 'W1', orderCode: 'X', name: 'T', supplier: 'S',
         qty: '1', size: '', orderDate: '2026-05-20 09:00:00'}, o));
      const href = (o) => {
        const a = mk(o).querySelector('.to-link');
        return a ? a.getAttribute('href') : null;
      };
      return {
        evil_pair: href({pairUrl: 'javascript:alert(1)'}),
        evil_decision: href({supplierUrl: 'javascript:alert(1)'}),
        good_pair: href({pairUrl: 'https://dodavatel.test/x'}),
        good_decision: href({supplierUrl: 'https://dodavatel.test/y'}),
      };
    }""")
    assert r == {"evil_pair": "", "evil_decision": "",
                 "good_pair": "https://dodavatel.test/x",
                 "good_decision": "https://dodavatel.test/y"}, r
