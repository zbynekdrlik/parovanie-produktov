"""#260 — the ✂️ „prejsť na veľkosti" warning claimed unsaved TEXT for a box that was
only opened.

`openSplitSizes` counts what a repaint would carry over, and `editorSnapHasWork`
deliberately merges TWO states into that one count (see the comment there): text the
manager typed and has not saved, and a box he OPENED with ✏️/💬 and left empty (his,
whether he has typed into it yet or deliberately cleared it — the repaint keeps it).
Only the first can actually be LOST, so calling an empty open box „rozpísaný neuložený
text" warns him about work that does not exist.

The counting is unchanged — it must stay the same predicate the repaint uses, or „would
be lost" drifts from „is lost". Only the sentence separates the two states now.

`window.confirm` is a SPY (add_init_script) rather than a `page.on("dialog")` handler:
a real dialog blocks the page's JS thread, and the spy records the exact message while
answering Cancel, so the tab never actually leaves for the review tab.
"""

_CONFIRM_SPY = """
window.__confirms = [];
window.confirm = (m) => { window.__confirms.push(String(m)); return false; };
"""

# Turn the C2 line into a split row (that is what renders ✂️ instead of ✏️) and give it a
# review product to find, so openSplitSizes reaches the warning instead of the
# „nenašiel sa v revízii" alert.
_MAKE_SPLIT_ROW = """() => {
  const o = ORDERS.find(x => x.itemCode === 'C2');
  o.reviewStatus = 'split';
  o.reviewKey = 'SPLITKEY';
  // a paired row is what renders the pencil slot at all; a `split` one puts ✂️ in it
  o.supplierUrl = 'https://dodavatel.example/ciapka';
  PRODUCTS = [{key: 'SPLITKEY', supplier: 'CITRADE', name: 'Ciapka Cit Test'}];
  renderToOrder();
}"""


def _open(page, base):
    page.add_init_script(_CONFIRM_SPY)
    page.goto(base + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")
    page.evaluate(_MAKE_SPLIT_ROW)
    page.wait_for_selector(".toorder-row[data-code='C2'] .to-splitedit")


def _leave(page):
    page.locator(".toorder-row[data-code='C2'] .to-splitedit").click()
    page.wait_for_function("() => window.__confirms.length > 0", timeout=3000)
    return page.evaluate("() => window.__confirms")


def _type_pair_url(page, code, value):
    """Set the inline paste box's value the way the manager's typing leaves it — no
    submit, so nothing is saved and the snapshot sees unsaved text."""
    page.evaluate("""([code, v]) => {
      const inp = document.querySelector(
        ".toorder-row[data-code='" + code + "'] .to-pairurl");
      inp.value = v;
      inp.dispatchEvent(new Event('input', {bubbles: true}));
    }""", [code, value])


def test_warning_text_is_built_from_the_two_counts(page, toorder_server):
    """Pure helper, driven in the browser realm. The count keeps the established
    `(N×)` shape after a singular noun, so no second declension rule is introduced
    beside `itemsWord`."""
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    out = page.evaluate("""() => ({
      none: leaveEditorsWarning(0, 0),
      typed: leaveEditorsWarning(2, 0),
      opened: leaveEditorsWarning(0, 3),
      both: leaveEditorsWarning(2, 1),
    })""")

    assert out["none"] == ""          # nothing at stake → no dialog at all
    assert out["typed"] == ("⚠️ Máš rozpísaný neuložený text (2×) v objednávkach. "
                            "Prechodom na veľkosti sa zahodí. Pokračovať?")
    assert out["opened"] == ("⚠️ Máš otvorené prázdne políčko (3×) v objednávkach. "
                             "Prechodom na veľkosti sa zavrie. Pokračovať?")
    # both states at once → BOTH fates, or the sentence still tells him the merely
    # opened box is thrown away (review of this PR)
    assert out["both"] == ("⚠️ Máš rozpísaný neuložený text (2×) a otvorené prázdne "
                           "políčko (1×) v objednávkach. Prechodom na veľkosti sa "
                           "zahodí a zavrie. Pokračovať?")


def test_only_opened_editor_is_not_called_unsaved_text(page, toorder_server):
    """The ticket's case: 💬 opened on one row, nothing typed. The box IS carried over
    (so it is worth warning about — it closes on the way out), but there is no text to
    throw away, and the sentence must not claim there is."""
    _open(page, toorder_server)
    page.locator(".toorder-row[data-code='C1'] .to-comadd").click()
    page.wait_for_selector(".toorder-row[data-code='C1'] .to-cominput")

    # the count itself is unchanged — one editor is at stake, and none of it is text
    counts = page.evaluate("""() => {
      const busy = captureOpenEditors().filter(
        s => editorSnapHasWork(s, ORDERS.find(x => x.key === s.key)));
      return {at_stake: busy.length, typed: busy.filter(s => s.value.trim()).length};
    }""")
    assert counts == {"at_stake": 1, "typed": 0}, counts

    msgs = _leave(page)
    assert msgs == ["⚠️ Máš otvorené prázdne políčko (1×) v objednávkach. "
                    "Prechodom na veľkosti sa zavrie. Pokračovať?"], msgs
    # Cancel was answered → still on „Na objednanie", editor intact
    assert page.evaluate("() => ACTIVE_TAB") == "toorder"
    assert page.locator(".toorder-row[data-code='C1'] .to-cominput").count() == 1


def test_typed_text_still_says_unsaved_text(page, toorder_server):
    """The half that was always true keeps its wording — a half-typed pair URL really is
    thrown away by the trip to the sizes panel."""
    _open(page, toorder_server)
    _type_pair_url(page, "C1", "https://dodavatel.example/produkt-1")

    msgs = _leave(page)
    assert msgs == ["⚠️ Máš rozpísaný neuložený text (1×) v objednávkach. "
                    "Prechodom na veľkosti sa zahodí. Pokračovať?"], msgs


def test_both_states_are_named_separately(page, toorder_server):
    """One typed box and one merely-opened box → both counts, each under its own name."""
    _open(page, toorder_server)
    _type_pair_url(page, "C1", "https://dodavatel.example/produkt-1")
    page.locator(".toorder-row[data-code='C3'] .to-comadd").click()
    page.wait_for_selector(".toorder-row[data-code='C3'] .to-cominput")

    msgs = _leave(page)
    assert msgs == ["⚠️ Máš rozpísaný neuložený text (1×) a otvorené prázdne políčko "
                    "(1×) v objednávkach. Prechodom na veľkosti sa zahodí a zavrie. "
                    "Pokračovať?"], msgs


def test_nothing_at_stake_leaves_without_asking(page, toorder_server):
    """No editor of his open → no dialog. The inline paste box every unpaired row renders
    by default is NOT his open editor (it was never opened and holds nothing), so the
    trip to the sizes panel must be silent."""
    _open(page, toorder_server)

    # („Na objednanie" and „Kontrola párovania" share `#list`, so the tab is read from
    # ACTIVE_TAB — there is no per-tab section to check.)
    page.locator(".toorder-row[data-code='C2'] .to-splitedit").click()
    page.wait_for_function("() => ACTIVE_TAB === 'review'", timeout=5000)
    assert page.evaluate("() => window.__confirms") == []
