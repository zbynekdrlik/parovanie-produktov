"""E2E of #207 — „📋 Kopírovať objednávku" in the supplier group header.

GRUBE (and most other suppliers here) has no B2B auto-ordering: the manager writes the
order by hand, and until now there was no way to get the whole supplier list out of the
tab. The button puts it into the clipboard as plain text (kód | veľkosť | ks | odkaz).

SCOPE (review of the #205-#208 batch): the copied text is the supplier's OUTSTANDING
lines, and that set does NOT depend on the „skryť poriešené" (#205) toggle. The old rule
was WYSIWYG („copy what the group shows"), which with the toggle OFF (the default) pasted
lines the manager had already ticked „objednané" straight into a supplier e-mail, and made
the on-screen „Σ spolu" chip contradict the copied quantities.

SCOPE (review pass 2): „outstanding" means NOT SETTLED — objednané / skladom / nedostupné.
„čaká sa" is a scheduling flag on a line that still has to be ordered, so it stays IN the
copy. And a group whose every line is settled copies NOTHING: an „Objednávka (0 položiek)"
in a supplier's inbox is an order for nothing.

The clipboard itself is asserted through a SPY installed before the page loads
(`navigator.clipboard.writeText`): it pins the exact text the code hands over — which is
the contract — without depending on a clipboard permission grant in headless CI. Two
further spies drive the LEGACY path (`copyPlainText`'s `execCommand` fallback and its
failure label), which a resolving clipboard would otherwise never execute.

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

# Clipboard API present but REFUSING (no permission / insecure context) → the legacy
# selection + execCommand path must carry the text. The stub reads it back off the
# textarea the fallback selected, which is exactly what a real `copy` command would take.
_SPY_FALLBACK = """
window.__copied = [];
window.__exec = 0;
Object.defineProperty(navigator, 'clipboard', {
  configurable: true,
  value: { writeText: () => Promise.reject(new Error('denied')) },
});
document.execCommand = (cmd) => {
  window.__exec += 1;
  window.__copied.push(String(document.activeElement && document.activeElement.value));
  return true;
};
"""

# Both paths dead → the button must SAY so (and must not leave the order text lying in
# a stray, tab-focusable textarea).
_SPY_DEAD = """
window.__exec = 0;
Object.defineProperty(navigator, 'clipboard', {
  configurable: true,
  value: { writeText: () => Promise.reject(new Error('denied')) },
});
document.execCommand = () => { window.__exec += 1; throw new Error('no copy here'); };
"""


def _console_watch(page):
    errs = []
    page.on("console", lambda m: errs.append(f"[{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)
    return errs


def _group(page, name):
    return page.locator(".toorder-supplier").filter(has_text=name)


def _stray_textareas(page):
    """Textareas parked directly on <body> — the fallback's scratch element. The comment
    editors live inside `#list`, so they never show up here."""
    return page.evaluate("() => document.querySelectorAll('body > textarea').length")


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
    assert text == "Objednávka — ORBIS (1 položka)\nS1 | Veľkosť: M | 3 ks"

    # the manager gets visible feedback that the click did something
    page.wait_for_function(
        "() => [...document.querySelectorAll('.toorder-supplier')]"
        ".some(h => /ORBIS/.test(h.textContent)"
        " && /Skopírované/.test(h.querySelector('.tosup-copy').textContent))")

    # the supplier label stays FIRST in the header (the startswith(sup) E2E contract)
    assert _group(page, "ORBIS").first.inner_text().startswith("ORBIS")

    assert console == [], f"console not clean: {console}"


def test_copy_is_the_outstanding_work_in_both_toggle_states(page, toorder_server):
    """A line the manager already flagged is DONE work: it must never be pasted into an
    order again — not with „skryť poriešené" on (where he cannot see it), and not with it
    off (where it is on screen, dimmed, and reads as part of the list). The copied text is
    therefore identical in both toggle states."""
    console = _console_watch(page)
    page.add_init_script(_SPY)
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    page.locator(".toorder-row[data-code='C1'] .to-instock").click()
    page.wait_for_function(
        "() => /6 položiek z 7/.test(document.getElementById('toToolbar').textContent)")
    page.reload()                       # the repaint that renders C1 as handled
    page.wait_for_selector(".toorder-row")

    # toggle OFF (the default): C1 is ON SCREEN, dimmed — and still must not be copied
    assert page.locator(".toorder-row[data-code='C1']").count() == 1
    _group(page, "CITRADE").locator(".tosup-copy").click()
    page.wait_for_function("() => window.__copied.length === 1")
    shown_text = page.evaluate("() => window.__copied[0]")

    assert "C1 |" not in shown_text, "an already-handled line must not be re-ordered"
    for code in ("C2", "C3", "C4"):
        assert f"{code} | Veľkosť: M | 1 ks" in shown_text, shown_text
    assert shown_text.startswith("Objednávka — CITRADE (3 položky)")

    # toggle ON: C1 is gone from the list — the copied text does not change
    page.locator("#toToolbar .to-hidehandled").click()
    page.wait_for_function("() => !document.querySelector(\".toorder-row[data-code='C1']\")")
    _group(page, "CITRADE").locator(".tosup-copy").click()
    page.wait_for_function("() => window.__copied.length === 2")
    assert page.evaluate("() => window.__copied[1]") == shown_text

    assert console == [], f"console not clean: {console}"


def test_a_waiting_line_still_goes_into_the_order(page, toorder_server):
    """„čaká sa" is a SCHEDULING flag, not a done flag: `/api/orders` defines it as an
    ACTIVE line that cannot be stocked yet — waiting on the supplier, BATCHING MORE ITEMS,
    or deferred by agreement with the customer — and the row button's own tooltip repeats
    „zbierame viac položiek". A line the manager parks until the order is worth placing is
    therefore still work to order: dropping it from the pasted e-mail orders less than the
    shop needs, while the row sits visibly on screen implying it went out. Only the three
    SETTLED flags (objednané / skladom / nedostupné) are held back."""
    console = _console_watch(page)
    page.add_init_script(_SPY)
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    page.locator(".toorder-row[data-code='C2'] .to-wait").click()      # parked, not done
    page.locator(".toorder-row[data-code='C3'] .to-instock").click()   # genuinely settled
    page.wait_for_function(
        "() => /čaká sa 1/.test(document.getElementById('toToolbar').textContent)"
        " && /skladom 1/.test(document.getElementById('toToolbar').textContent)")

    _group(page, "CITRADE").locator(".tosup-copy").click()
    page.wait_for_function("() => window.__copied.length === 1")
    text = page.evaluate("() => window.__copied[0]")

    assert "C2 | Veľkosť: M | 1 ks" in text, "a parked line is still to be ordered"
    assert "C3 |" not in text, "a stocked line must not be re-ordered"
    assert text.startswith("Objednávka — CITRADE (3 položky)"), text

    # the button says which lines it takes, so the scope is not folklore
    title = _group(page, "CITRADE").locator(".tosup-copy").get_attribute("title")
    assert "objednan" in title and "skladov" in title and "nedostupn" in title, title

    assert console == [], f"console not clean: {console}"


def test_copying_a_fully_handled_group_pastes_nothing_and_says_so(page, toorder_server):
    """With the hide filter OFF (the default) a supplier group whose every line is settled
    stays on screen, copy button and all. Handing „Objednávka — ORBIS (0 položiek)" to the
    clipboard and reporting „✓ Skopírované" produces an e-mail that orders nothing — the
    button must refuse and say so instead."""
    console = _console_watch(page)
    page.add_init_script(_SPY)
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    for i in range(2):
        page.locator(".toorder-row[data-code='S1']").nth(i).locator(".to-instock").click()
    page.wait_for_function(
        "() => /5 položiek z 7/.test(document.getElementById('toToolbar').textContent)")
    assert page.locator(".toorder-row[data-code='S1']").count() == 2, \
        "the filter is off — the settled rows (and their copy button) stay on screen"

    copy = _group(page, "ORBIS").locator(".tosup-copy")
    copy.click()
    page.wait_for_function(
        "() => /Nič na objednanie/.test("
        "[...document.querySelectorAll('.toorder-supplier')]"
        ".find(h => /ORBIS/.test(h.textContent)).querySelector('.tosup-copy').textContent)")
    assert page.evaluate("() => window.__copied.length") == 0, \
        "an empty order must never reach the clipboard"
    assert "ok" not in (copy.get_attribute("class") or "")

    # …and the label goes back on the same timer as every other outcome
    page.wait_for_function(
        "(lbl) => [...document.querySelectorAll('.toorder-supplier')]"
        ".find(h => /ORBIS/.test(h.textContent)).querySelector('.tosup-copy')"
        ".textContent === lbl", arg=COPY_LABEL, timeout=6000)

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
        "Objednávka — GRUBE (4 položky)",
        "A | Veľkosť: M | 3 ks | https://x.sk/a",     # same size → summed
        "A | Veľkosť: L | 1 ks | https://x.sk/a",     # other size → own line
        "B | grube 99887 | 1 ks | https://grube.de/b",
        "C | 1 ks",                                   # refused URL + no size → dropped
    ]

    assert console == [], f"console not clean: {console}"


def test_item_count_is_declined_the_slovak_way(page, toorder_server):
    """1 položka / 2-4 položky / 5+ položiek. The header line goes into an e-mail to the
    supplier, so „(1 položiek)" is not a cosmetic slip."""
    console = _console_watch(page)
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    out = page.evaluate("""() => {
      const mk = (n) => Array.from({length: n}, (_, i) => ({itemCode: 'K' + i, qty: '1'}));
      return {
        nom: [0, 1, 2, 4, 5, 11].map(n => itemsWord(n)),   // NOT .map(itemsWord) — the
                                                           // index would land in `acc`
        acc: [0, 1, 2, 4, 5, 11].map(n => itemsWord(n, true)),
        heads: [1, 2, 5].map(n => orderCopyText('X', mk(n)).split('\\n')[0]),
      };
    }""")

    assert out["nom"] == ["položiek", "položka", "položky", "položky",
                          "položiek", "položiek"]
    # accusative — „vybaviť 1 položku"; only the singular differs from the nominative
    assert out["acc"] == ["položiek", "položku", "položky", "položky",
                          "položiek", "položiek"]
    assert out["heads"] == ["Objednávka — X (1 položka)",
                            "Objednávka — X (2 položky)",
                            "Objednávka — X (5 položiek)"]

    assert console == [], f"console not clean: {console}"


def test_copy_falls_back_to_execcommand_when_the_clipboard_api_refuses(page, toorder_server):
    """The Clipboard API needs a secure context AND a permission; when it refuses, the
    legacy selection path must still deliver the text — and must clean up after itself
    and give the caret back to whatever the manager was typing in."""
    console = _console_watch(page)
    page.add_init_script(_SPY_FALLBACK)
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    # the manager is mid-typing in an inline pair editor when he hits copy
    page.evaluate("""() => {
      const inp = document.querySelector(".toorder-row[data-code='C2'] .to-pairurl");
      inp.value = 'https://a.sk/x'; inp.focus(); inp.setSelectionRange(3, 3);
      document.querySelector('.toorder-supplier .tosup-copy').click();
    }""")
    page.wait_for_function("() => window.__copied.length === 1")

    assert page.evaluate("() => window.__exec") == 1, "the legacy path must have run"
    assert page.evaluate("() => window.__copied[0]").startswith("Objednávka — ")
    # …and the button reports success, because the text DID reach the clipboard
    page.wait_for_function(
        "() => /Skopírované/.test(document.querySelector('.tosup-copy').textContent)")

    assert _stray_textareas(page) == 0, "the scratch textarea must not survive the copy"
    state = page.evaluate("""() => ({
      cls: document.activeElement.className, val: document.activeElement.value,
      sel: document.activeElement.selectionStart,
    })""")
    assert state["cls"] == "to-pairurl" and state["val"] == "https://a.sk/x"
    assert state["sel"] == 3, "the caret must come back where it was"

    assert console == [], f"console not clean: {console}"


def test_a_dead_clipboard_says_so_and_leaves_nothing_behind(page, toorder_server):
    """Both paths refused: the button must say it plainly, and the order text must not be
    left in an invisible, tab-focusable textarea on <body> (one per click, forever)."""
    console = _console_watch(page)
    page.add_init_script(_SPY_DEAD)
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    page.evaluate("""() => {
      const inp = document.querySelector(".toorder-row[data-code='C2'] .to-pairurl");
      inp.value = 'https://a.sk/x'; inp.focus(); inp.setSelectionRange(3, 3);
      document.querySelector('.toorder-supplier .tosup-copy').click();
      document.querySelector('.toorder-supplier .tosup-copy').click();
    }""")
    page.wait_for_function(
        "() => /Schránka nedostupná/.test(document.querySelector('.tosup-copy').textContent)")

    assert page.evaluate("() => window.__exec") == 2
    assert _stray_textareas(page) == 0, "a thrown copy must still remove the textarea"
    state = page.evaluate("""() => ({
      cls: document.activeElement.className, sel: document.activeElement.selectionStart,
    })""")
    assert state["cls"] == "to-pairurl" and state["sel"] == 3

    assert console == [], f"console not clean: {console}"
