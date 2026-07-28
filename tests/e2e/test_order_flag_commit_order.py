"""#291 — the flag gate must order writes by the server's COMMIT order, not by the
client's ISSUE order.

`_flagWrites[...].seq` is taken in `saveOrderWrite` at the moment a write is ISSUED, but
what decides the state on disk is the order in which the server threads took
`with _lock:` — the order they COMMITTED. Two writes on the same (flag, row) issued inside
one round-trip travel on separate connections, so the two orders can diverge: the client
then stands on the answer that committed EARLIER while the store holds the other one.

The divergence needs the FIRST-ISSUED write to commit LAST, which no amount of clicking
produces on its own — the two orders agree by luck almost always. So the test MAKES them
diverge: a `window.fetch` wrapper installed before `app.js` loads holds the FIRST POST at
the network boundary until the SECOND one has completed. The client issues A then B; the
server commits B then A; the store therefore holds A's value, and the screen must too.

Everything is asserted SCREEN against SERVER read back through `fetch` — the same
convention as `test_order_flag_seq_guard.py`, because a client that repaints prettily
while the store disagrees is exactly the failure this is about.
"""
import pytest

_KEY = "99000910|C1"
_ROW = ".toorder-row[data-code='C1']"
_CHECK = _ROW + " input[type=checkbox]"

# Hold the FIRST POST to /api/ordered at the network boundary; everything else (the GETs
# `loadOrders` fires, the read-backs the assertions do) passes straight through. Installed
# through `add_init_script` so it is in place before app.js binds anything.
_HOLD_FIRST_ORDERED_POST = """
(() => {
  const _fetch = window.fetch;
  let _release;
  const _gate = new Promise(r => { _release = r; });
  window.__release = () => _release();
  window.__posts = { issued: 0, done: 0 };
  window.fetch = function (url, opts) {
    const isWrite = (typeof url === 'string') && url === '/api/ordered'
                    && opts && opts.method === 'POST';
    if (!isWrite) return _fetch.call(window, url, opts);
    window.__posts.issued += 1;
    const send = () => _fetch.call(window, url, opts).then(
      (r) => { window.__posts.done += 1; return r; },
      (e) => { window.__posts.done += 1; throw e; });
    return window.__posts.issued === 1 ? _gate.then(send) : send();
  };
})();
"""


def _server_ordered(page):
    """Does the SERVER hold „objednané" for the row?"""
    return page.evaluate(
        """key => fetch('/api/ordered').then(r => r.json())
                    .then(j => Object.keys(j.ordered).includes(key))""", _KEY)


def _screen_ordered(page):
    """Does the SCREEN say the row is ordered? Row class and checkbox must agree — the
    click handler paints the row by hand, a repaint rebuilds it from the flag map."""
    cls = set((page.locator(_ROW).get_attribute("class") or "").split())
    checked = page.locator(_CHECK).is_checked()
    assert ("done" in cls) == checked, ("row class and checkbox disagree", cls, checked)
    return checked


@pytest.fixture
def reordered_page(page, toorder_server):
    """The tab, with the first „objednané" write held at the network boundary — and the
    row left un-ordered again afterwards. `toorder_server` is session-scoped, so a test
    that walks away leaving the flag set poisons every later test on that fixture."""
    page.add_init_script(_HOLD_FIRST_ORDERED_POST)
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")
    yield page
    page.request.post(toorder_server + "/api/ordered",
                      data={"key": _KEY, "ordered": False})


def test_the_write_that_committed_LAST_is_what_the_row_shows(reordered_page):
    """A: „objednané" ON, issued first, committed LAST. B: „objednané" OFF, issued second,
    committed FIRST. The store ends up holding ON, so the row must show ON.

    Before the fix the client compared its own issue numbers: B carried seq 2, A carried
    seq 1, so A's answer — the one that actually committed last and IS the store — was
    dropped as stale and the row stayed blank on a line the server holds as ordered. That
    is the „order it a second time" half of the same family as #211/S5, reached without a
    single refused write.
    """
    page = reordered_page

    page.locator(_CHECK).click()                                    # A — held
    page.wait_for_function("() => window.__posts.issued === 1")
    page.locator(_CHECK).click()                                    # B — straight through
    page.wait_for_function("() => window.__posts.done === 1")       # B has COMMITTED

    page.evaluate("() => window.__release()")                       # …now let A commit
    page.wait_for_function("() => window.__posts.done === 2")
    # `__posts.done` fires in the RAW fetch's `then`, i.e. before postToOrder awaits the
    # body, before the bookkeeping and before renderToOrder — so it does NOT mean „the row
    # has been repainted". Wait for the DOM itself, or the assertions below pass or fail on
    # an accident of timing (previously: on the round-trip `_server_ordered` happens to add).
    page.wait_for_function(
        """() => { const r = document.querySelector(".toorder-row[data-code='C1']");
                   return r && r.classList.contains('done'); }""", timeout=3000)

    assert _server_ordered(page) is True, (
        "precondition: the write issued FIRST must be the one that committed LAST")
    assert _screen_ordered(page) is True, (
        "the row shows NOT ordered while the server holds it ordered — the client kept "
        "the answer that committed EARLIER because it was ISSUED later")


def test_every_flag_write_answers_with_a_commit_number(page, toorder_server):
    """The CAUSE, pinned directly on the wire: ordering by commits is only possible if the
    server says WHEN each write committed. Kept next to the browser sequence so a refactor
    that drops `commitSeq` fails here with a readable reason instead of as a mysterious
    flag divergence months later (the shape `.claude/rules/toorder-e2e.md` point 9 asks
    for).

    Strictly increasing, and comparable ACROSS flags and rows: that global ordering is the
    whole point — the per-(flag, row) issue counters are deliberately NOT comparable.

    Deliberately WITHOUT the holding wrapper: this test awaits its own writes, and a
    `page.evaluate` has no default timeout — awaiting a POST the wrapper is holding for
    ever hangs the run instead of failing it.
    """
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")
    seqs = page.evaluate("""async (key) => {
      const post = (path, body) => fetch(path, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(Object.assign({key}, body))
      }).then(r => r.json());
      const out = [];
      // deliberately across DIFFERENT flags, and one bulk — the number must order them all
      out.push(await post('/api/waiting', {waiting: true}));
      out.push(await post('/api/instock', {instock: true}));
      out.push(await post('/api/unavailable', {unavailable: false}));
      out.push(await post('/api/ordered', {ordered: true}));
      out.push(await fetch('/api/ordered/bulk', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({keys: [key], ordered: false})
      }).then(r => r.json()));
      // leave axis B as we found it
      await post('/api/instock', {instock: false});
      return out.map(j => j.commitSeq);
    }""", _KEY)

    assert all(isinstance(s, int) for s in seqs), (
        "a flag write answered without a commit number — the client has nothing to order "
        "answers by", seqs)
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs), (
        "commit numbers must be strictly increasing across every flag write", seqs)


# The answer that carries NO commit number — the corner the first cut of #291 got wrong.
# The write really reaches the server; only the number is stripped from the 200 it answers
# with, which is exactly what a truncated body (this app sits behind a tunnel) or a NEW
# app.js talking to an OLD server produces.
_STRIP_COMMIT_SEQ = """
(() => {
  const _fetch = window.fetch;
  window.fetch = function (url, opts) {
    const p = _fetch.call(window, url, opts);
    if (!(typeof url === 'string' && url === '/api/instock'
          && opts && opts.method === 'POST')) return p;
    return p.then(async (r) => {
      const j = await r.json();
      delete j.commitSeq;
      return new Response(JSON.stringify(j),
                          {status: r.status, headers: {'Content-Type': 'application/json'}});
    });
  };
})();
"""

_N1 = ".toorder-row[data-code='N1']"


def test_an_accepted_write_without_a_commit_number_still_matches_the_server(
        page, toorder_server):
    """An accepted write whose answer carries no number cannot be ORDERED against others —
    but when it is the only write out for that (flag, row), there is nothing to order it
    against, and refusing to adopt it freezes `confirmed` on a value the server has moved
    past. The next REFUSED write then „rolls back" onto that stale value, i.e. does not
    roll back — the screen shows a flag the server does not hold, right after telling the
    manager the save failed. That is the #290 failure shape, and the first cut of #291
    re-opened it while trying to close a different one.

    So: accepted + nothing else in flight for that (flag, row) => adopt, without moving the
    commit clock. Ambiguity only exists when there IS something to be ambiguous with.
    """
    page.add_init_script(_STRIP_COMMIT_SEQ)
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    with page.expect_response("**/api/instock"):
        page.locator(_N1 + " .to-instock").click()          # accepted, number stripped
    page.wait_for_selector(_N1 + ".instock")

    # …now refuse the next write on the same flag: the rollback must land on the value the
    # server really holds (on), not on a baseline frozen before the accepted write
    page.route("**/api/instock", lambda r: r.fulfill(
        status=500, content_type="application/json", body='{"ok": false}')
        if r.request.method == "POST" else r.continue_())
    page.locator(_N1 + " .to-instock").click()
    page.wait_for_selector("#toFails .tofail", timeout=3000)

    server = page.evaluate(
        """() => fetch('/api/instock').then(r => r.json())
                   .then(j => Object.keys(j.instock))""")
    cls = set((page.locator(_N1).get_attribute("class") or "").split())
    assert server, "precondition: the accepted write must have reached the store"
    assert "instock" in cls, (
        "the row shows the flag OFF while the server holds it ON — the accepted write was "
        "never adopted, so the refused one rolled back onto a stale baseline", server, cls)


# Both at once: the first POST is HELD at the network boundary (so the server commits the
# writes in the REVERSE of the issue order) AND every answer loses its commit number.
_HOLD_AND_STRIP_INSTOCK = """
(() => {
  const _fetch = window.fetch;
  let _release;
  const _gate = new Promise(r => { _release = r; });
  window.__release = () => _release();
  window.__posts = { issued: 0, done: 0 };
  const strip = async (r) => {
    const j = await r.json();
    delete j.commitSeq;
    return new Response(JSON.stringify(j),
                        {status: r.status, headers: {'Content-Type': 'application/json'}});
  };
  window.fetch = function (url, opts) {
    const isWrite = (typeof url === 'string') && url === '/api/instock'
                    && opts && opts.method === 'POST';
    if (!isWrite) return _fetch.call(window, url, opts);
    window.__posts.issued += 1;
    const send = () => _fetch.call(window, url, opts).then(strip).then(
      (r) => { window.__posts.done += 1; return r; },
      (e) => { window.__posts.done += 1; throw e; });
    return window.__posts.issued === 1 ? _gate.then(send) : send();
  };
})();
"""


def test_two_unnumbered_writes_still_leave_the_row_matching_the_server(page, toorder_server):
    """The residual the first `_mayAdopt` fallback left open. With NO number there is
    nothing to order two answers by, and the fallback („adopt when I am still the latest
    issued and nothing else is out") is issue-order reasoning wearing a different hat: when
    both answers are unnumbered and the writes commit in the REVERSE of the issue order it
    adopts NEITHER — the one that is still latest-issued is rejected because the other is
    in flight, and the one that settles last is rejected because it is no longer latest.
    `confirmed` stays frozen at the pre-burst value and the map is forced back onto it,
    while the server holds the write that committed last.

    So an unorderable answer is not guessed at at all: the client re-reads the row's flags
    from the server, which is the only thing that actually knows.
    """
    page.add_init_script(_HOLD_AND_STRIP_INSTOCK)
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")

    btn = page.locator(_N1 + " .to-instock")
    btn.click()                                                  # A — held
    page.wait_for_function("() => window.__posts.issued === 1")
    btn.click()                                                  # B — commits FIRST
    page.wait_for_function("() => window.__posts.done === 1")
    page.evaluate("() => window.__release()")                     # …A commits LAST
    page.wait_for_function("() => window.__posts.done === 2")

    # the resync settles asynchronously — wait for screen and server to agree
    page.wait_for_function("""() => fetch('/api/instock').then(r => r.json()).then(j => {
        const row = document.querySelector(".toorder-row[data-code='N1']");
        return row && (Object.keys(j.instock).length > 0) === row.classList.contains('instock');
      })""", timeout=5000)

    server = page.evaluate(
        """() => fetch('/api/instock').then(r => r.json())
                   .then(j => Object.keys(j.instock))""")
    cls = set((page.locator(_N1).get_attribute("class") or "").split())
    assert ("instock" in cls) == bool(server), (
        "screen and server disagree after a burst of unnumbered answers", server, cls)
