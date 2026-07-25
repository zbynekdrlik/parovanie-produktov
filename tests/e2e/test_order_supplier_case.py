"""#203 — the same supplier typed with different capitalisation must NOT split into
two chips / two groups on the „Na objednanie" tab.

The manager fills the supplier in by hand, so the very same supplier arrives as
'CITRADE', 'Citrade' and 'citrade'. Before the fix `effSup(o)` was used raw as the
grouping key, so each spelling got its OWN chip with its own count and its own
(contradictory) red/green colour, and its own group header — the manager could not
tell how many lines that supplier actually has.

Fixture (`toorder_server`): 4 CITRADE-ish lines (CITRADE ×2, Citrade ×1, citrade ×1)
and 2 ORBIS lines. Expected after the fix: ONE chip „CITRADE (4)" (the most-used
spelling wins the label) and ONE group header, and selecting it shows all 4 lines.
"""


def _chips(page):
    return page.locator("#filters button")


def _chip_texts(page):
    return [t.strip() for t in _chips(page).all_inner_texts()]


def test_supkey_normalises_case_and_whitespace(page, toorder_server):
    """Pure-logic unit test of the normalising key, in the browser realm — `supKey` is
    a top-level const in the classic (non-module) app.js, so it is reachable by bare
    name from page.evaluate (same trick as test_order_effsup.py)."""
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")
    r = page.evaluate("""() => [
      supKey('CITRADE') === supKey('Citrade'),      // case-insensitive
      supKey('  Citrade  ') === supKey('Citrade'),  // trimmed
      supKey('Citrade  s.r.o.') === supKey('Citrade s.r.o.'),  // inner runs collapsed
      supKey('CITRADE') === supKey('ORBIS'),        // different suppliers stay different
      supKey(null),                                 // null-safe
      supKey(undefined),
    ]""")
    assert r == [True, True, True, False, "", ""], r


def test_case_variants_are_one_chip_labelled_with_the_most_used_spelling(page, toorder_server):
    console = []
    page.on("console", lambda m: console.append(f"[{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    texts = _chip_texts(page)
    citrade = [t for t in texts if "citrade" in t.lower()]
    assert len(citrade) == 1, f"case variants must collapse into ONE chip, got {texts}"
    # label = the spelling the manager used most often (CITRADE 2× vs Citrade/citrade 1×),
    # count = ALL lines of that supplier (4), not the 2 of the winning spelling
    assert citrade[0].startswith("CITRADE (4)"), citrade[0]
    # 'Všetci' + CITRADE + ORBIS + '—' (the supplier-less line) — nothing else
    assert len(texts) == 4, texts

    assert console == [], f"console not clean: {console}"


def test_case_variants_share_one_group_and_one_filter(page, toorder_server):
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    heads = [t.strip() for t in page.locator(".toorder-supplier .tosup-label").all_inner_texts()]
    citrade_heads = [h for h in heads if "citrade" in h.lower()]
    assert len(citrade_heads) == 1, f"one group per supplier, got {heads}"
    assert citrade_heads[0].startswith("CITRADE — 4 položiek"), citrade_heads[0]

    # selecting the chip must filter to ALL 4 lines, whatever spelling they carry
    _chips(page).filter(has_text="CITRADE").first.click()
    page.wait_for_function(
        "() => document.querySelectorAll('.toorder-row').length === 4", timeout=3000)
    codes = sorted(page.locator(".toorder-row").evaluate_all(
        "els => els.map(e => e.dataset.code)"))
    assert codes == ["C1", "C2", "C3", "C4"], codes


def test_assigned_supplier_is_stored_and_shown_whitespace_normalised(page, toorder_server):
    """The client must send (and then show) the SAME spelling the endpoint stores —
    otherwise the row and the reopened ✏️ editor keep a three-space name while the store,
    and the eshop `supplier` column it feeds, hold the collapsed one."""
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    row = page.locator(".toorder-row[data-code='N1']")
    row.locator(".to-supinput").fill("  Citrade   s.r.o.  ")
    with page.expect_response("**/api/order-supplier") as sent:
        row.locator(".to-supsave").click()
    assert sent.value.request.post_data_json["supplier"] == "Citrade s.r.o."

    page.wait_for_selector(".toorder-row[data-code='N1'] .to-suptag")
    tag = page.locator(".toorder-row[data-code='N1'] .to-suptag").inner_text()
    assert tag.strip() == "🏷️ Citrade s.r.o.", tag
    # what the server actually holds
    stored = page.request.get(toorder_server + "/api/orders").json()["orders"]
    assert [o["assignedSupplier"] for o in stored if o["itemCode"] == "N1"] == ["Citrade s.r.o."]


def test_supplier_index_survives_javascript_reserved_names(page, toorder_server):
    """The grouping maps are keyed by the manager's FREE TEXT. On a plain object literal a
    supplier named '__proto__' swallows the write (the chip would render its raw internal
    key and disappear from the datalist) and 'constructor' writes the counter onto the
    global Object. Both maps are null-prototype for exactly this."""
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")
    r = page.evaluate("""() => {
      const idx = supplierSpellingIndex([
        {supplier: '__proto__'}, {supplier: '__proto__'}, {supplier: 'Proto'},
        {supplier: 'constructor'}, {supplier: 'toString'},
      ]);
      return {
        proto: idx.canon['s:__proto__'],
        ctor: idx.canon['s:constructor'],
        tostr: idx.canon['s:tostring'],
        known: idx.known,
        objectIntact: typeof Object.constructor === 'function'
                      && typeof {}.toString === 'function',
      };
    }""")
    assert r["proto"] == "__proto__", r
    assert r["ctor"] == "constructor", r
    assert r["tostr"] == "toString", r
    assert sorted(r["known"]) == ["Proto", "__proto__", "constructor", "toString"], r
    assert r["objectIntact"] is True, "the global Object must not be written through"


def test_whitespace_only_supplier_joins_the_placeholder_group(page, toorder_server):
    """A Shoptet itemSupplier of only spaces is truthy but is not a supplier — it must not
    form its own invisible chip labelled '   '."""
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")
    r = page.evaluate("""() => [
      effSup({supplier: '   ', assignedSupplier: ''}),
      effSup({supplier: '   ', assignedSupplier: 'ORBIS'}),
      supplierSpellingIndex([{supplier: '  '}, {supplier: ''}]).known,
    ]""")
    assert r == ["—", "ORBIS", []], r


def test_an_old_raw_supplier_selection_is_migrated_and_persisted(page, toorder_server):
    """Chip keys became 's:'+normalised (#203). A manager upgrading mid-work has the OLD
    raw name in localStorage — it must keep selecting the same supplier, and be rewritten
    once rather than re-migrated on every load."""
    page.goto(toorder_server + "/")
    page.evaluate("() => localStorage.setItem('orderSupplier', 'Citrade')")
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    page.wait_for_function(
        "() => document.querySelectorAll('.toorder-row').length === 4", timeout=3000)
    active = page.locator("#filters button.active").first
    assert active.inner_text().strip().startswith("CITRADE (4)"), active.inner_text()
    assert page.evaluate("() => localStorage.getItem('orderSupplier')") == "s:citrade"


def test_a_selection_matching_no_current_supplier_falls_back_to_all(page, toorder_server):
    """Yesterday's supplier may have no open orders today — the manager must not be left
    staring at an empty list with no active chip."""
    page.goto(toorder_server + "/")
    page.evaluate("() => localStorage.setItem('orderSupplier', 's:uzneexistuje')")
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    assert page.locator(".toorder-row").count() == 7, "every line must be shown again"
    assert page.evaluate("() => localStorage.getItem('orderSupplier')") == "all"
    assert page.locator("#filters button.active").inner_text().strip().startswith("Všetci")


def test_known_supplier_datalist_is_deduped_case_insensitively(page, toorder_server):
    """The autocomplete list exists to stop typo/case fragmentation at the source — it
    must offer ONE entry per real supplier (the canonical spelling), not one per variant."""
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")
    opts = page.locator("#known-suppliers option").evaluate_all("els => els.map(e => e.value)")
    assert sorted(opts) == ["CITRADE", "ORBIS"], opts
