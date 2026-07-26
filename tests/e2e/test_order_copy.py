"""E2E of #207 — „📋 Kopírovať objednávku" in the supplier group header.

GRUBE (and most other suppliers here) has no B2B auto-ordering: the manager writes the
order by hand, and until now there was no way to get the whole supplier list out of the
tab. The button puts it into the clipboard as plain text (kód | veľkosť | ks | odkaz).

SCOPE (review of the #205-#208 batch): the copied text is the supplier's OUTSTANDING
lines, and that set does NOT depend on the „skryť poriešené" (#205) toggle. The old rule
was WYSIWYG („copy what the group shows"), which with the toggle OFF (the default) pasted
lines the manager had already ticked „objednané" straight into a supplier e-mail, and made
the on-screen „Σ spolu" chip contradict the copied quantities.

SCOPE (review pass 3 — REVERSES pass 2): there is ONE predicate, `isHandled`. „Outstanding"
= not flagged at all, and „⏳ Čaká sa" is a flag like any other: it means „this line is not
today's work" in all three meanings its own tooltip lists (already at the supplier, parked
to collect more items, deferred by agreement). Pass 2 narrowed the copy to the three
SETTLED flags only, which made the tab contradict itself — the toolbar said „ostáva
vybaviť 0", the chip beside it said „nevybavené: 3 ks", and with „skryť poriešené" on the
group (and its copy button) vanished while the app still counted those pieces as work.
When the manager decides to order a parked line he switches „⏳ Čaká sa" off and it
re-enters the order — that toggle IS the workflow. A group whose every line is flagged
copies NOTHING: an „Objednávka (0 položiek)" in a supplier's inbox is an order for nothing.

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

# The first write lands, the SECOND is refused (and the legacy path throws) → two clicks
# in one 2,5 s window end on DIFFERENT labels, which is what pins the reset timer. Only
# that one write is refused: the same test then copies two DIFFERENT suppliers, and both
# of those have to succeed for their „✓ Skopírované" labels to be worth timing.
_SPY_SECOND_CLICK_FAILS = """
window.__copied = [];
window.__n = 0;
Object.defineProperty(navigator, 'clipboard', {
  configurable: true,
  value: { writeText: (t) => {
    window.__n += 1;
    if (window.__n === 2) return Promise.reject(new Error('denied'));
    window.__copied.push(String(t));
    return Promise.resolve();
  } },
});
document.execCommand = () => { throw new Error('no copy here'); };
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


def test_a_parked_waiting_line_is_not_in_todays_order(page, toorder_server):
    """„⏳ Čaká sa" is NOT today's work — in every meaning its own tooltip lists: the line
    is already at the supplier, or deliberately parked until the order is worth placing, or
    deferred by agreement with the customer. None of those belong in the order the manager
    is placing right now, so the copy leaves them out — and when he decides to order a
    parked line he switches the flag off, which puts it straight back in. That toggle IS
    the workflow, and it keeps ONE predicate behind the hide filter, the chip colour, the
    toolbar tally, the „Σ spolu" chip, the copy and the empty-list wording: a second,
    narrower scope made the tab say „ostáva vybaviť 0" and „nevybavené: 3 ks" side by side
    (review pass 3, reversing pass 2)."""
    console = _console_watch(page)
    page.add_init_script(_SPY)
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    page.locator(".toorder-row[data-code='C2'] .to-wait").click()      # parked
    page.locator(".toorder-row[data-code='C3'] .to-instock").click()   # stocked
    page.wait_for_function(
        "() => /čaká sa 1/.test(document.getElementById('toToolbar').textContent)"
        " && /skladom 1/.test(document.getElementById('toToolbar').textContent)")

    _group(page, "CITRADE").locator(".tosup-copy").click()
    page.wait_for_function("() => window.__copied.length === 1")
    text = page.evaluate("() => window.__copied[0]")

    assert "C2 |" not in text, "a parked line is not part of today's order"
    assert "C3 |" not in text, "a stocked line must not be re-ordered"
    for code in ("C1", "C4"):
        assert f"{code} | Veľkosť: M | 1 ks" in text, text
    assert text.startswith("Objednávka — CITRADE (2 položky)"), text

    # the button says which lines it takes, so the scope is not folklore
    title = _group(page, "CITRADE").locator(".tosup-copy").get_attribute("title")
    assert "objednan" in title and "čakaj" in title, title
    assert "skladov" in title and "nedostupn" in title, title

    assert console == [], f"console not clean: {console}"


def test_a_later_copy_outcome_survives_the_earlier_click_timer(page, toorder_server):
    """Every outcome label („✓ Skopírované" / „⚠️ Schránka nedostupná" / „Nič na
    objednanie") goes back to the default after 2,5 s. A SECOND click inside that window
    used to inherit the FIRST click's remaining time, because the timer was fired and
    forgotten: the manager copied ORBIS, clicked again ~2,3 s later, that write failed, the
    warning showed for 200 ms and the stale timer wiped it — he read the default label,
    assumed the copy had landed, and pasted the PREVIOUS supplier's order (still in the
    clipboard) into this supplier's mail. Each click must own its own timer.

    Second leg: that timer is per BUTTON — `resetT` lives in the per-group closure, so
    copying a SECOND supplier cancels only ITS OWN pending reset. Hoisted out of the
    supplier loop it becomes one timer for the whole tab, and the same paste-the-wrong-
    order harm comes back through the other door: CITRADE's click kills ORBIS's pending
    reset, ORBIS keeps reading „✓ Skopírované" for good, and the manager trusts that label
    over a clipboard that now holds CITRADE's order."""
    console = _console_watch(page)
    page.add_init_script(_SPY_SECOND_CLICK_FAILS)
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    copy = _group(page, "CITRADE").locator(".tosup-copy")
    copy.click()
    page.wait_for_function(
        "() => /Skopírované/.test(document.querySelector('.tosup-copy').textContent)")

    # …well inside the first click's 2,5 s window, so its timer is still pending
    page.wait_for_timeout(1200)
    assert "Skopírované" in copy.inner_text(), "precondition: the first timer has not fired"
    copy.click()
    page.wait_for_function(
        "() => /Schránka nedostupná/.test(document.querySelector('.tosup-copy').textContent)")

    # past the FIRST click's deadline (~t0+2,5 s) but well inside the SECOND click's own
    page.wait_for_timeout(1500)
    assert "Schránka nedostupná" in copy.inner_text(), \
        "the earlier click's timer must not wipe a later outcome"
    assert page.evaluate("() => window.__copied.length") == 1, \
        "only the first write reached the clipboard — the second one was refused"

    # …and the label does come back on the SECOND click's own timer
    page.wait_for_function(
        "(lbl) => document.querySelector('.tosup-copy').textContent === lbl",
        arg=COPY_LABEL, timeout=6000)

    # ONE closure per supplier group: a copy of ANOTHER supplier must not cancel this
    # button's pending reset either. ORBIS goes first, CITRADE ~1 s later — ORBIS's label
    # has to come back on its own deadline (a shared timer never lets it) while CITRADE's
    # outcome, a second younger, is still on screen.
    _orbis_copy = ("[...document.querySelectorAll('.toorder-supplier')]"
                   ".find(h => /ORBIS/.test(h.textContent)).querySelector('.tosup-copy')")
    _group(page, "ORBIS").locator(".tosup-copy").click()
    page.wait_for_function(f"() => /Skopírované/.test({_orbis_copy}.textContent)")
    page.wait_for_timeout(1000)          # still well inside ORBIS's 2,5 s window
    copy.click()
    page.wait_for_function(
        "() => /Skopírované/.test(document.querySelector('.tosup-copy').textContent)")
    page.wait_for_function(f"(lbl) => {_orbis_copy}.textContent === lbl",
                           arg=COPY_LABEL, timeout=6000)
    assert "Skopírované" in copy.inner_text(), \
        "another group's click must not wipe this button's outcome"

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
