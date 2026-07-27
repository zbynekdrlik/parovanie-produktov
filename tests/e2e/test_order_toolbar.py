"""E2E of the „Na objednanie" toolbar above the list.

#208 — súhrn „ostáva X z Y nevybavených": the supplier chips only ever counted lines
PER SUPPLIER, so the manager had no single number telling him how much of today's work
is left. The tally lives in the top bar (outside `#list`, so the list repaint — and the
inline-editor capture/restore that rides on it — is untouched) and is recomputed by
renderOrderFilters, which every per-line toggle already calls: the number must move the
moment he flags a line, without a reload (the #86 stale-chip lesson).

Two review follow-ups are pinned here as well: the summary follows the SELECTED supplier
chip (a global „7 z 7" above two visible ORBIS rows is a lie he acts on), and the
„všetko vybavené" colour must actually be readable on the light background (the old
#6CAB68 measured 2.56:1, well under the 4.5:1 the WCAG AA text minimum asks for).

The `toorder_server` fixture serves 7 order lines (4 CITRADE case-variants, 2 ORBIS
sharing itemCode S1, 1 supplier-less N1).
"""

TOTAL_LINES = 7

# Contrast of the rendered text colour against the first opaque background behind it —
# the same computation the WCAG AA 4.5:1 minimum is defined on.
_CONTRAST = """
(sel) => {
  const px = (c) => c.match(/[\\d.]+/g).slice(0, 3).map(Number);
  const lum = (rgb) => {
    const [r, g, b] = rgb.map(v => { v /= 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const node = document.querySelector(sel);
  const fg = px(getComputedStyle(node).color);
  let bg = null;
  for (let n = node; n; n = n.parentElement) {
    const c = getComputedStyle(n).backgroundColor;
    if (c && !/rgba\\(0, 0, 0, 0\\)|transparent/.test(c)) { bg = px(c); break; }
  }
  if (!bg) bg = [255, 255, 255];
  const [a, b] = [lum(fg), lum(bg)].sort((x, y) => y - x);
  return { ratio: (a + 0.05) / (b + 0.05), color: getComputedStyle(node).color };
}
"""


def _console_watch(page):
    errs = []
    page.on("console", lambda m: errs.append(f"[{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)
    return errs


def test_summary_shows_remaining_and_recomputes_live(page, toorder_server):
    console = _console_watch(page)
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    bar = page.locator("#toToolbar")
    assert bar.is_visible(), "toolbar must be visible on the Na objednanie tab"
    assert f"Ostáva vybaviť {TOTAL_LINES} položiek z {TOTAL_LINES}" in bar.inner_text()

    # The manager's real action: flag one line IN-SESSION. The tally must follow at once
    # — a per-line toggle deliberately does NOT repaint the list, so a summary that only
    # rebuilt with the list would sit there lying until the next reload.
    # The write must be OBSERVED before the reload below: `saveOrderFlag` mutates the
    # map and repaints SYNCHRONOUSLY and only then awaits the POST, so waiting on the
    # toolbar text proves the CLIENT updated — not that the server stored anything. A
    # reload in that window re-reads the old server state and the „still 6 of 7 after a
    # reload" assertion reads 7 of 7 (CI failure 2026-07-27: the POST /api/instock
    # landed in the test's TEARDOWN log). Same rule as the /api/decision races.
    with page.expect_response(lambda r: r.request.method == "POST"
                              and "/api/instock" in r.url):
        page.locator(".toorder-row[data-code='C1'] .to-instock").click()
    page.wait_for_function(
        "() => /Ostáva vybaviť 6 položiek z 7/.test("
        "document.getElementById('toToolbar').textContent)")
    assert "skladom 1" in bar.inner_text()

    # …and it is the SERVER's state, not a client-side illusion: after a reload the
    # summary still reads 6 of 7.
    page.reload()
    page.wait_for_selector(".toorder-row")
    assert "Ostáva vybaviť 6 položiek z 7" in page.locator("#toToolbar").inner_text()
    assert "skladom 1" in page.locator("#toToolbar").inner_text()

    assert console == [], f"console not clean: {console}"


def test_summary_follows_the_selected_supplier_chip(page, toorder_server):
    """Filtered to one supplier, the manager sees that supplier's rows — a GLOBAL tally
    above them („7 z 7" above 2 ORBIS rows) is a number he cannot act on. Selected chip →
    the summary counts that supplier and says whose it is; „Všetci" → the global text."""
    console = _console_watch(page)
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    page.get_by_role("button", name="ORBIS (2)").click()
    page.wait_for_function("() => document.querySelectorAll('.toorder-row').length === 2")
    bar = page.locator("#toToolbar")
    assert "ORBIS: ostáva vybaviť 2 položky z 2" in bar.inner_text(), bar.inner_text()

    # a flag inside the selected supplier moves the scoped number…
    page.locator(".toorder-row[data-code='S1']").first.locator(".to-instock").click()
    page.wait_for_function(
        "() => /ORBIS: ostáva vybaviť 1 položku z 2/.test("
        "document.getElementById('toToolbar').textContent)")
    assert "skladom 1" in bar.inner_text()

    # …and back on „Všetci" the wording (and the count) is the global one again
    page.get_by_role("button", name="Všetci (7)").click()
    page.wait_for_function(
        "() => /^📋 Ostáva vybaviť 6 položiek z 7/.test("
        "document.getElementById('toToolbar').textContent)")
    assert "ORBIS:" not in page.locator("#toToolbar").inner_text()

    assert console == [], f"console not clean: {console}"


def test_summary_is_hidden_outside_the_to_order_tab(page, toorder_server):
    console = _console_watch(page)
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")
    assert page.locator("#toToolbar").is_visible()

    page.get_by_role("button", name="Hľadať / opraviť").click()
    page.wait_for_selector("#tab-search:not([hidden])")
    assert page.locator("#toToolbar").is_hidden(), "toolbar belongs to the to-order tab only"

    assert console == [], f"console not clean: {console}"


def test_summary_text_is_not_a_partition_of_the_total(page, toorder_server):
    """A line can carry SEVERAL flags at once (objednané AND čaká sa), so the breakdown
    numbers must never be presented as parts summing to the total — and an empty bucket
    is dropped instead of rendering a row of zeros. The count is declined the Slovak way
    (1 položku / 2-4 položky / 5+ položiek). Pure helper, driven in the browser realm
    (app.js is a plain script → its functions and globals are reachable)."""
    console = _console_watch(page)
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    out = page.evaluate("""() => {
      ORDERS = [{key: 'a'}, {key: 'b'}, {key: 'c'}];
      ORDERED = {a: true}; WAITING = {a: true}; INSTOCK = {}; UNAVAIL = {b: true};
      const s = toOrderSummary(ORDERS);
      return { s, text: toOrderSummaryText(s), named: toOrderSummaryText(s, 'ORBIS') };
    }""")

    assert out["s"] == {"total": 3, "remaining": 1, "ordered": 1, "waiting": 1,
                        "instock": 0, "unavail": 1}
    # 'a' is double-flagged and 'c' is untouched → 1+1+1 != 3 on purpose
    assert out["text"] == ("📋 Ostáva vybaviť 1 položku z 3"
                           " · objednané 1 · čaká sa 1 · nedostupné 1")
    assert out["named"].startswith("📋 ORBIS: ostáva vybaviť 1 položku z 3 · ")
    assert "skladom" not in out["text"], "empty bucket must be dropped"

    # everything resolved → no breakdown tail at all
    counts = page.evaluate("""() => {
      const mk = (n) => Array.from({length: n}, (_, i) => ({key: 'k' + i}));
      ORDERED = {k0: true}; WAITING = {}; INSTOCK = {}; UNAVAIL = {};
      const one = toOrderSummaryText(toOrderSummary(mk(1)));
      ORDERED = {};
      return [one, ...[2, 5].map(n => toOrderSummaryText(toOrderSummary(mk(n))))];
    }""")
    assert counts == ["📋 Ostáva vybaviť 0 položiek z 1 · objednané 1",
                      "📋 Ostáva vybaviť 2 položky z 2",
                      "📋 Ostáva vybaviť 5 položiek z 5"]

    assert console == [], f"console not clean: {console}"


def test_all_done_summary_is_readable_on_the_light_background(page, toorder_server):
    """„Všetko vybavené" is the line the manager most wants to read, and it renders as
    small bold text on the light top bar — the old #6CAB68 measured 2.56:1 there. The
    chip palette (#143) is untouched; only this one text colour is the derived darker
    shade, which also flips correctly in dark mode."""
    console = _console_watch(page)
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    for i in range(page.locator(".tosup-bulk").count()):
        page.locator(".tosup-bulk").nth(i).click()
        page.wait_for_function(
            "(i) => /Zrušiť/.test(document.querySelectorAll('.tosup-bulk')[i].textContent)",
            arg=i)
    page.wait_for_function(
        "() => /Ostáva vybaviť 0 položiek z 7/.test("
        "document.getElementById('toToolbar').textContent)")
    assert page.locator("#toToolbar .to-sum.alldone").count() == 1

    out = page.evaluate(_CONTRAST, "#toToolbar .to-sum.alldone")
    assert out["color"] == "rgb(63, 127, 59)", out          # var(--accent), light theme
    assert out["ratio"] >= 4.5, f"AA needs 4.5:1, got {out['ratio']:.2f} ({out['color']})"

    assert console == [], f"console not clean: {console}"
