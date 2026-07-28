"""#211 — the „Na objednanie" status flags are mutually exclusive, in the browser too.

Four flags were four independent ticks, so a row could hold done + čaká sa + skladom +
nedostupné at once: the CSS states then fought each other (rule ORDER decides the
background and the coloured bar, not the row's state) and `isHandled` took ANY of them.

The rule (decided from the manager's own live stores — see the design note on #211):

  axis A  „objednané"                              — independent, coexists with anything
  axis B  „čaká sa" ⊕ „skladom" ⊕ „nedostupné"     — mutually exclusive

The SERVER enforces it (one atomic write); the client only reflects it. These tests drive
the real buttons and then read the SERVER back through `fetch`, so a client that merely
repaints prettily while the stores disagree still fails.
"""
import itertools

import pytest

_STATUS = {"waiting": (".to-wait", "/api/waiting"),
           "instock": (".to-instock", "/api/instock"),
           "unavail": (".to-unavail", "/api/unavailable")}
_FIELD = {"waiting": "waiting", "instock": "instock", "unavail": "unavailable"}
_KEY = "99000910|C1"


def _open(page, base):
    page.goto(base + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")


def _click(page, code, state):
    with page.expect_response("**" + _STATUS[state][1]):
        page.locator(f".toorder-row[data-code='{code}'] {_STATUS[state][0]}").click()


def _server(page):
    """The state of all four stores, as the SERVER holds it."""
    return page.evaluate("""() => Promise.all(
      ['/api/ordered', '/api/waiting', '/api/instock', '/api/unavailable']
        .map(u => fetch(u).then(r => r.json())))
      .then(([o, w, i, u]) => ({ordered: Object.keys(o.ordered),
                                waiting: Object.keys(w.waiting),
                                instock: Object.keys(i.instock),
                                unavailable: Object.keys(u.unavailable)}))""")


def _classes(page, code):
    return set((page.locator(f".toorder-row[data-code='{code}']")
                .get_attribute("class") or "").split())


@pytest.mark.parametrize("first, second", [p for p in itertools.permutations(_STATUS, 2)])
def test_a_status_flag_switches_off_the_conflicting_one(page, toorder_server, first, second):
    """Every ordered pair of the three contradictory statuses: the second click wins and
    the first is switched off — on the row, on its buttons, and in the store."""
    _open(page, toorder_server)
    _click(page, "C1", first)
    _click(page, "C1", second)
    page.wait_for_function(
        "s => !document.querySelector(`.toorder-row[data-code='C1']`).classList.contains(s)",
        arg=first, timeout=3000)

    cls = _classes(page, "C1")
    assert second in cls and first not in cls, cls
    on = page.locator(f".toorder-row[data-code='C1'] {_STATUS[first][0]}")
    assert "on" not in (on.get_attribute("class") or "").split()

    srv = _server(page)
    assert srv[_FIELD[second]] == [_KEY], srv
    assert srv[_FIELD[first]] == [], srv


def test_objednane_survives_a_status_flag_and_vice_versa(page, toorder_server):
    """„objednané" is the other axis — objednané + čaká sa na dodávateľa is exactly what
    the ⏳ button's own tooltip describes, and 27 rows in the live stores use it."""
    _open(page, toorder_server)
    with page.expect_response("**/api/ordered"):
        page.locator(".toorder-row[data-code='C1'] input[type=checkbox]").click()
    _click(page, "C1", "waiting")
    page.wait_for_timeout(200)

    cls = _classes(page, "C1")
    assert "done" in cls and "waiting" in cls, cls
    assert page.locator(".toorder-row[data-code='C1'] input[type=checkbox]").is_checked()
    srv = _server(page)
    assert srv["ordered"] == [_KEY] and srv["waiting"] == [_KEY], srv


def test_marking_a_group_ordered_leaves_the_status_flags_alone(page, toorder_server):
    """The bulk „✔ Označiť skupinu objednané" is axis A for a whole supplier group."""
    _open(page, toorder_server)
    _click(page, "C1", "instock")
    page.locator(".toorder-supplier").filter(has_text="CITRADE").locator(".tosup-bulk").click()
    page.wait_for_function(
        "() => document.querySelector(`.toorder-row[data-code='C1']`).classList.contains('done')",
        timeout=3000)

    assert "instock" in _classes(page, "C1")
    srv = _server(page)
    assert srv["instock"] == [_KEY], srv
    assert _KEY in srv["ordered"], srv


def test_switching_a_status_flag_OFF_leaves_the_row_plain(page, toorder_server):
    """Turning one off is not a statement about the other two — the line simply becomes
    unhandled again."""
    _open(page, toorder_server)
    _click(page, "C1", "instock")
    _click(page, "C1", "instock")
    page.wait_for_function(
        "() => !document.querySelector(`.toorder-row[data-code='C1']`).classList.contains('instock')",
        timeout=3000)
    srv = _server(page)
    assert srv["instock"] == [] and srv["waiting"] == [] and srv["unavailable"] == [], srv


def test_the_conflicting_flag_disappears_from_the_row_IMMEDIATELY(page, toorder_server):
    """Reflecting the server means reflecting it at CLICK time, not when the POST answers:
    a row painted „čaká sa + skladom" for the length of a round-trip is the very state
    this issue is about, and on a slow link that is not a blink."""
    _open(page, toorder_server)
    _click(page, "C1", "waiting")
    page.evaluate("""() => {
      window.__realFetch = window.fetch.bind(window);
      window.__pending = [];
      window.fetch = (u, o) => (o && o.method === 'POST')
        ? new Promise(res => window.__pending.push(() => res(window.__realFetch(u, o))))
        : window.__realFetch(u, o);
    }""")
    page.locator(".toorder-row[data-code='C1'] .to-instock").click()
    page.wait_for_function("() => window.__pending.length === 1", timeout=3000)

    cls = _classes(page, "C1")
    assert "waiting" not in cls, ("the conflicting flag lingered until the POST answered", cls)
    assert "instock" in cls, cls
    page.evaluate("() => window.__pending[0]()")


def test_a_refused_exclusive_write_restores_BOTH_flags(page, toorder_server):
    """The rollback must undo the whole write, not half of it: the refused „✓ Skladom"
    goes back off AND the „⏳ Čaká sa" it optimistically cleared comes back — otherwise a
    failed click silently ERASES a flag the server still holds."""
    _open(page, toorder_server)
    _click(page, "C1", "waiting")
    # Refuse the WRITE only — `_server()` reads the same path back through GET, and a
    # blanket route would answer that read with the 500 too and prove nothing.
    page.route("**/api/instock", lambda r: r.fulfill(
        status=500, content_type="application/json", body='{"ok": false}')
        if r.request.method == "POST" else r.continue_())
    page.locator(".toorder-row[data-code='C1'] .to-instock").click()
    page.wait_for_function(
        "() => document.querySelector(`.toorder-row[data-code='C1']`).classList.contains('waiting')",
        timeout=3000)

    cls = _classes(page, "C1")
    assert "waiting" in cls and "instock" not in cls, cls
    assert "on" in (page.locator(".toorder-row[data-code='C1'] .to-wait")
                    .get_attribute("class") or "").split()
    srv = _server(page)
    assert srv["waiting"] == [_KEY] and srv["instock"] == [], srv


def test_the_exclusive_repaint_keeps_another_rows_unsaved_typing(page, toorder_server):
    """Whatever the clear does to the screen — repaint the tab or rewrite the row in
    place — a half-typed comment on an UNRELATED row must not be the price of ticking
    „✓ Skladom". An implementation reaching for `renderToOrder()` here still has to go
    through the editor carry-over of #233/#235; this holds it to that either way."""
    _open(page, toorder_server)
    c2 = page.locator(".toorder-row[data-code='C2']")
    c2.locator(".to-comadd").click()
    c2.locator(".to-cominput").fill("rozpisana poznamka")

    _click(page, "C1", "waiting")
    _click(page, "C1", "instock")
    page.wait_for_timeout(200)

    box = page.locator(".toorder-row[data-code='C2'] .to-cominput")
    assert box.count() == 1
    assert box.input_value() == "rozpisana poznamka"


def test_the_console_stays_clean_through_an_exclusive_switch(page, toorder_server):
    msgs = []
    page.on("console", lambda m: msgs.append(f"[{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)
    _open(page, toorder_server)
    _click(page, "C1", "waiting")
    _click(page, "C1", "unavail")
    page.wait_for_timeout(200)
    assert msgs == [], msgs
