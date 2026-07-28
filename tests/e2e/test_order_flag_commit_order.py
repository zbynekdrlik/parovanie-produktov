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
