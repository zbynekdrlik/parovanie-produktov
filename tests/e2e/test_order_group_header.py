"""#238 + #240 — the supplier GROUP HEADER on „Na objednanie" is the one counter on the
tab that never declined its noun.

`renderToOrder` hard-coded the genitive plural into the template
(`${items.length} položiek`), so the header read „CITRADE — 4 položiek" (#238) and
„ORBIS — 1 položiek" (#240) — the same single string, reported twice from two different
counts. Every other counter on the tab (the copied order's header, the toolbar tally)
already went through `itemsWord(n, acc)`; this one was left behind because an E2E
contract pinned the wrong form.

Slovak declines after a numeral: 1 → položka, 2–4 → položky, 0 and 5+ → položiek. The
group header is a nominative label, so it takes `itemsWord(n)` — the accusative
(`položku`, after „vybaviť") belongs to the toolbar summary alone.

ONE helper carries the rule for all four callers; a second implementation beside it is
exactly how this header drifted out of step in the first place.
"""


def _console_watch(page):
    seen = []
    page.on("console", lambda m: seen.append(f"[{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)
    return seen


def test_items_word_declines_every_slovak_case(page, toorder_server):
    """Table-driven over the whole rule, including the counts that only differ under a
    naive `n > 1 → plural` reading (11 / 21 / 101 stay genitive in Slovak). Pure helper,
    driven in the browser realm — app.js is a classic script, so `itemsWord` is reachable
    by bare name (same trick as test_order_toolbar.py)."""
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    out = page.evaluate("""() => {
      const ns = [0, 1, 2, 4, 5, 11, 21, 101];
      return {
        nom: ns.map(n => n + ' ' + itemsWord(n)),
        acc: ns.map(n => n + ' ' + itemsWord(n, true)),
      };
    }""")

    assert out["nom"] == ["0 položiek", "1 položka", "2 položky", "4 položky",
                          "5 položiek", "11 položiek", "21 položiek", "101 položiek"]
    # the accusative differs in the SINGULAR only („vybaviť 1 položku")
    assert out["acc"] == ["0 položiek", "1 položku", "2 položky", "4 položky",
                          "5 položiek", "11 položiek", "21 položiek", "101 položiek"]


def test_group_headers_decline_the_rendered_count(page, toorder_server):
    """The rendered DOM, not the helper: the fixture serves a 4-line group (CITRADE), a
    2-line group (ORBIS) and a 1-line group (the supplier-less „—"), which is exactly the
    2–4 case and the singular #240 reported."""
    console = _console_watch(page)
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    heads = {t.strip() for t in
             page.locator(".toorder-supplier .tosup-label").all_inner_texts()}
    assert heads == {"CITRADE — 4 položky", "ORBIS — 2 položky",
                     "— — 1 položka"}, heads

    assert console == [], f"console not clean: {console}"


def test_group_header_declines_the_plural_counts_too(page, toorder_server):
    """5+ / 11 / 21 / 101 through the real `renderToOrder`, by cloning a rendered order
    line into a group of the wanted size — the header must read whatever `itemsWord`
    says, never a template-baked „položiek"."""
    console = _console_watch(page)
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    heads = page.evaluate("""() => {
      const base = ORDERS.find(o => o.itemCode === 'C1');
      const out = {};
      for (const n of [1, 2, 5, 11, 21, 101]) {
        ORDERS = Array.from({length: n}, (_, i) =>
          Object.assign({}, base, {key: 'k' + i, itemCode: 'K' + i}));
        renderToOrder();
        out[n] = document.querySelector('.toorder-supplier .tosup-label').textContent.trim();
      }
      return out;
    }""")

    assert heads == {"1": "CITRADE — 1 položka", "2": "CITRADE — 2 položky",
                     "5": "CITRADE — 5 položiek", "11": "CITRADE — 11 položiek",
                     "21": "CITRADE — 21 položiek", "101": "CITRADE — 101 položiek"}, heads

    assert console == [], f"console not clean: {console}"
