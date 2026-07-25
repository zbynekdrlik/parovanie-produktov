"""E2E of #207 — „📋 Kopírovať objednávku" in the supplier group header.

GRUBE (and most other suppliers here) has no B2B auto-ordering: the manager writes the
order by hand, and until now there was no way to get the whole supplier list out of the
tab. The button puts it into the clipboard as plain text (kód | veľkosť | ks | odkaz).

The clipboard itself is asserted through a SPY installed before the page loads
(`navigator.clipboard.writeText`): it pins the exact text the code hands over — which is
the contract — without depending on a clipboard permission grant in headless CI.

`toorder_server`: CITRADE = C1..C4 (one line each), ORBIS = two lines sharing itemCode S1
with quantities 1 and 2, all unpaired (no URLs).
"""

COPY_LABEL = "📋 Kopírovať objednávku"

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


def _group(page, name):
    return page.locator(".toorder-supplier").filter(has_text=name)


def test_copy_button_hands_the_supplier_list_to_the_clipboard(page, toorder_server):
    console = _console_watch(page)
    page.add_init_script(_SPY)
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    _group(page, "ORBIS").locator(".tosup-copy").click()
    page.wait_for_function("() => window.__copied.length === 1")

    text = page.evaluate("() => window.__copied[0]")
    # ORBIS's two lines are the SAME product+size → ONE line asking for the summed 3 ks,
    # which is what an order to a supplier means (never the customer lines behind it)
    assert text == "Objednávka — ORBIS (1 položiek)\nS1 | Veľkosť: M | 3 ks"

    # the manager gets visible feedback that the click did something
    page.wait_for_function(
        "() => [...document.querySelectorAll('.toorder-supplier')]"
        ".some(h => /ORBIS/.test(h.textContent)"
        " && /Skopírované/.test(h.querySelector('.tosup-copy').textContent))")

    # the supplier label stays FIRST in the header (the startswith(sup) E2E contract)
    assert _group(page, "ORBIS").first.inner_text().startswith("ORBIS")

    assert console == [], f"console not clean: {console}"


def test_copy_takes_what_the_group_shows(page, toorder_server):
    """WYSIWYG: with „skryť poriešené" (#205) on, the copied list is the OUTSTANDING work
    — the manager must never paste a set of rows he cannot see before sending it."""
    console = _console_watch(page)
    page.add_init_script(_SPY)
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    page.locator("#toToolbar .to-hidehandled").click()
    page.locator(".toorder-row[data-code='C1'] .to-instock").click()
    page.wait_for_function(
        "() => /Ostáva vybaviť 6 z 7/.test(document.getElementById('toToolbar').textContent)")
    page.reload()                       # the repaint that drops the handled line
    page.wait_for_selector(".toorder-row")

    _group(page, "CITRADE").locator(".tosup-copy").click()
    page.wait_for_function("() => window.__copied.length === 1")
    text = page.evaluate("() => window.__copied[0]")

    assert "C1 |" not in text, "the already-handled line must not be re-ordered"
    for code in ("C2", "C3", "C4"):
        assert f"{code} | Veľkosť: M | 1 ks" in text, text
    assert text.startswith("Objednávka — CITRADE (3 položiek)")

    assert console == [], f"console not clean: {console}"


def test_copy_text_aggregates_sizes_and_drops_empty_columns(page, toorder_server):
    """Pure helper: per product+SIZE (two sizes of one product are two order lines), the
    GRUBE per-size code rides along when present, only http(s) links are pasted, and an
    empty column is dropped rather than padded with a placeholder that would read like an
    instruction to the supplier."""
    console = _console_watch(page)
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    out = page.evaluate("""() => orderCopyText('GRUBE', [
      {itemCode: 'A', size: 'Veľkosť: M', qty: '1', supplierUrl: 'https://x.sk/a'},
      {itemCode: 'A', size: 'Veľkosť: M', qty: '2', supplierUrl: 'https://x.sk/a'},
      {itemCode: 'A', size: 'Veľkosť: L', qty: '1', supplierUrl: 'https://x.sk/a'},
      {itemCode: 'B', size: '', qty: '1', grubeItemId: '99887', grubeDeUrl: 'https://grube.de/b'},
      {itemCode: 'C', size: '', qty: '1', pairUrl: 'javascript:alert(1)'},
    ])""")

    assert out.split("\n") == [
        "Objednávka — GRUBE (4 položiek)",
        "A | Veľkosť: M | 3 ks | https://x.sk/a",     # same size → summed
        "A | Veľkosť: L | 1 ks | https://x.sk/a",     # other size → own line
        "B | grube 99887 | 1 ks | https://grube.de/b",
        "C | 1 ks",                                   # refused URL + no size → dropped
    ]

    assert console == [], f"console not clean: {console}"
