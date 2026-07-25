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
    # 'Všetci' + CITRADE + ORBIS — nothing else
    assert len(texts) == 3, texts

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


def test_known_supplier_datalist_is_deduped_case_insensitively(page, toorder_server):
    """The autocomplete list exists to stop typo/case fragmentation at the source — it
    must offer ONE entry per real supplier (the canonical spelling), not one per variant."""
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")
    opts = page.locator("#known-suppliers option").evaluate_all("els => els.map(e => e.value)")
    assert sorted(opts) == ["CITRADE", "ORBIS"], opts
