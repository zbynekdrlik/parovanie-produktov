"""#211 — a per-(flag, row) sequence number may only ever be stamped by the flag itself.

`_flagWrites` keeps an INDEPENDENT `seq` counter per (flag, row): „čaká sa" and „skladom"
are separate writes on the same line and must not supersede each other. `confirmedSeq` is
the guard built on that counter — a write's acceptance only counts if no LATER-issued
write on the SAME (flag, row) has already been accepted.

`_mirrorServerFlags` adopts the server's own account of all four flags. It was handed ONE
number — the CLICKED flag's `seq` — and stamped it onto the `confirmedSeq` of every flag
in the answer, including flags that write never claimed. Because the counters are
independent, a flag whose own counter is behind got a FOREIGN, higher number written into
its guard; from then on the guard rejected that flag's OWN accepted writes, `confirmed`
(the last known SERVER value) froze on stale data, and the next REFUSED write "rolled
back" to that stale value — i.e. did not roll back at all.

Both directions are reachable right after the manager has been told the save failed, which
is exactly when he trusts the row least:

  S5  ordered stays OFF on screen while the server HOLDS it   -> the line gets ordered twice
  S7  ordered stays ON on screen while the server refused it  -> the line is never ordered

The divergence needs TWO clicks of one status button (that is what pushes the foreign
`confirmedSeq` past the untouched `ordered.seq`), so the single-click
`test_a_refused_exclusive_write_restores_BOTH_flags` cannot reach this path. S6 is the
one-click control: it must agree both before and after the fix, so a change that merely
papers over the symptom is not mistaken for the fix.

Every assertion compares the SCREEN against the SERVER read back through `fetch` — a
client that repaints prettily while the store disagrees still fails.
"""
import pytest

_KEY = "99000910|C1"
_ROW = ".toorder-row[data-code='C1']"
_CHECK = _ROW + " input[type=checkbox]"


def _open(page, base):
    page.goto(base + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")


def _click_status(page):
    """One click of the „čaká sa" button, round-trip awaited."""
    with page.expect_response("**/api/waiting"):
        page.locator(_ROW + " .to-wait").click()


def _click_ordered(page):
    with page.expect_response("**/api/ordered"):
        page.locator(_CHECK).click()


def _refuse_ordered(page):
    """Refuse the WRITE only — the assertions read the same path back through GET, and a
    blanket route would answer that read with the 500 too and prove nothing."""
    page.route("**/api/ordered", lambda r: r.fulfill(
        status=500, content_type="application/json", body='{"ok": false}')
        if r.request.method == "POST" else r.continue_())


def _click_ordered_refused(page):
    page.locator(_CHECK).click()
    # the cumulative failure banner (#234) is raised AFTER the rollback repaint, so it is
    # the settle signal for the whole refused write
    page.wait_for_selector("#toFails .tofail", timeout=3000)


def _server_ordered(page):
    """Does the SERVER hold „objednané" for the row?"""
    return page.evaluate(
        """key => fetch('/api/ordered').then(r => r.json())
                    .then(j => Object.keys(j.ordered).includes(key))""", _KEY)


def _screen_ordered(page):
    """Does the SCREEN say the row is ordered? Row class and checkbox must agree — the
    click handler paints the row by hand, the repaint rebuilds it from the flag map."""
    cls = set((page.locator(_ROW).get_attribute("class") or "").split())
    checked = page.locator(_CHECK).is_checked()
    assert ("done" in cls) == checked, ("row class and checkbox disagree", cls, checked)
    return checked


@pytest.mark.parametrize("status_clicks", [2, 1], ids=["S5-two-clicks", "S6-control"])
def test_a_refused_ordered_OFF_cannot_leave_the_row_looking_un_ordered(
        page, toorder_server, status_clicks):
    """S5 — the row IS ordered on the server; a refused „un-order" must not blank it.

    Two clicks of „čaká sa" stamped `ordered.confirmedSeq = 2` while `ordered.seq` was
    still 0, so the accepted „objednané" ZAP that followed was rejected by its own guard
    and `confirmed` stayed `false`. The refused „objednané" VYP then rolled back to that
    stale `false` — which is where the map already was, so nothing moved and the manager
    saw an un-ordered row for a line the server holds as ordered.

    S6 (one click) is the control: the same sequence, one stamp short of the hole.
    """
    _open(page, toorder_server)
    for _ in range(status_clicks):
        _click_status(page)

    _click_ordered(page)                       # accepted — the server now holds it
    assert _server_ordered(page) is True, "precondition: the accepted write must have landed"

    _refuse_ordered(page)
    _click_ordered_refused(page)

    assert _server_ordered(page) is True, "the refused write must not have reached the store"
    assert _screen_ordered(page) is True, (
        "the screen shows the line as NOT ordered while the server HOLDS it — the manager "
        "orders it a second time")


@pytest.mark.parametrize("status_clicks", [2, 1], ids=["S7-two-clicks", "S6-control"])
def test_a_refused_ordered_ON_cannot_leave_the_row_looking_ordered(
        page, toorder_server, status_clicks):
    """S7 — the mirror image: the server does NOT hold „objednané" and a refused
    „objednané" ZAP must not leave the tick standing.

    The row is seeded ordered through the API (no client click, so `ordered.seq` stays 0
    — the state a manager reaching a freshly loaded tab is in). Two „čaká sa" clicks stamp
    `ordered.confirmedSeq = 2`; the accepted „objednané" VYP is then rejected by its own
    guard and `confirmed` freezes on `true`; the refused „objednané" ZAP rolls back to
    that stale `true`, leaving the tick on a line nobody ordered.
    """
    page.request.post(toorder_server + "/api/ordered",
                      data={"key": _KEY, "ordered": True})
    _open(page, toorder_server)
    assert _screen_ordered(page) is True, "precondition: the seeded flag must be on screen"

    for _ in range(status_clicks):
        _click_status(page)

    _click_ordered(page)                       # accepted — un-orders it on the server
    assert _server_ordered(page) is False, "precondition: the accepted write must have landed"

    _refuse_ordered(page)
    _click_ordered_refused(page)

    assert _server_ordered(page) is False, "the refused write must not have reached the store"
    assert _screen_ordered(page) is False, (
        "the screen shows the line as ordered while the server refused it — the line is "
        "never ordered at all")


def test_a_status_write_leaves_the_ordered_sequence_untouched(page, toorder_server):
    """The invariant behind both regressions, asserted directly on the bookkeeping: a
    write may only stamp the guard of the flags it CLAIMED. „čaká sa" claims all three
    axis-B flags (turning one on clears the other two, #211) and never „objednané" — so
    `ordered` must come out of a status write with a virgin guard.

    Kept alongside the two browser sequences on purpose: they prove the CONSEQUENCE, this
    pins the CAUSE, so a future refactor that reintroduces the foreign stamp fails here
    with a readable reason instead of only as a mysterious flag divergence.

    #291 moved the guard's NUMBER: it used to be the client's own per-(flag, row) ISSUE
    counter (`confirmedSeq`), which is not the order the server committed in, so it is now
    the server's own commit number (`confirmedCommit`). The invariant this test exists for
    is untouched — a write may still only stamp what it claimed — so only the field name
    moves with it. The second assertion changes shape because the two numbers no longer
    live in the same space: an issue counter could be compared against `seq`, a global
    commit clock cannot, so what it pins now is that the claimed flag carries a REAL
    server number (a stamp of 0/undefined would mean the answer was never adopted at all).
    """
    _open(page, toorder_server)
    _click_status(page)
    _click_status(page)

    stamped = page.evaluate("""key => {
      const out = {};
      for (const f of ['ordered', 'waiting', 'instock', 'unavailable']) {
        const e = _flagWrites[f + '\\u0000' + key];
        out[f] = e ? {seq: e.seq, confirmedCommit: e.confirmedCommit} : null;
      }
      return out;
    }""", _KEY)

    assert stamped["ordered"] is None or stamped["ordered"]["confirmedCommit"] == 0, (
        "a 'caka sa' write stamped the 'objednane' guard with a commit number that flag "
        "never claimed", stamped)
    assert stamped["waiting"]["confirmedCommit"] > 0, (
        "the clicked flag's guard was never stamped with the server's commit number — its "
        "accepted write was not adopted at all", stamped)
