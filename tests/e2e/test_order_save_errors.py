"""#214 — a failed save on the „Na objednanie" tab must never look like a success.

Every write on this tab used to swallow its own failure: `markGroupOrdered` did a bare
`if (!r.ok) return;` and a network throw was unhandled, so the manager who marked a
15-line supplier group as ordered saw absolutely nothing happen and no error — he
either clicked again or assumed it landed. The per-line flag toggles were worse: they
flip the flag map + the row's colour SYNCHRONOUSLY and fire the POST afterwards, so a
rejected write left the tab showing a flag the server never stored (silently lost the
manager's work until the next reload).

Both halves are pinned here with Playwright request interception (500 for a rejected
write, abort for a dead network): the manager is told, and an optimistic flag is rolled
back so what he sees is what the server holds.

`window.alert` is replaced by a SPY rather than driven through `page.on("dialog")`, for
two reasons: a real alert() blocks the page's JS thread, so nothing can inspect the DOM
while it is up — and the DOM *at alert time* is exactly what pins the "roll the tab back
BEFORE telling him" ordering (an error message over a row that still shows the refused
flag is the half-fix). The spy records the message together with that snapshot.
"""
import json

import pytest

# Records every alert together with the DOM state AT THE MOMENT it fired.
_ALERT_SPY = """
window.__alerts = [];
window.alert = (m) => {
  const cls = (sel) => { const e = document.querySelector(sel); return e ? e.className : null; };
  window.__alerts.push({
    msg: String(m),
    C1: cls("[data-code='C1']"),
    N1: cls("[data-code='N1']"),
    chips: [...document.querySelectorAll('#filters button')]
      .map(b => b.textContent + '||' + b.className),
  });
};
"""


def _open(page, base):
    page.add_init_script(_ALERT_SPY)
    page.goto(base + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")


def _fail(page, path, status=500, body='{"ok": false}'):
    page.route(f"**{path}", lambda route: route.fulfill(
        status=status, content_type="application/json", body=body))


def _wait_alert(page):
    page.wait_for_function("() => window.__alerts.length > 0", timeout=3000)
    return page.evaluate("() => window.__alerts")


def _chip(alert, label):
    return next(c for c in alert["chips"] if c.startswith(label))


# Holds every POST to `path` open until the test releases it — with the status IT picks —
# so two writes for the SAME row are genuinely in flight at once (a stubbed route answers
# far too fast to race). Release with `window.__held[i](status, passthrough)`:
# `(200, true)` replays the request against the real server so its state really changes,
# `(200)` fakes the success without touching it (client bookkeeping in isolation), any
# other status fakes that failure. `__realFetch` stays available to read the state back.
_HOLD_TEMPLATE = """
window.__held = [];
window.__settled = [];
window.__realFetch = window.fetch.bind(window);
window.fetch = (url, opts) => {
  const p = new URL(String(url), location.href).pathname;
  if (p === '__PATH__' && opts && opts.method === 'POST') {
    return new Promise((resolve, reject) => window.__held.push((status, passthrough) => {
      if (status === 200 && passthrough) {
        window.__realFetch(url, opts).then(
          (r) => { window.__settled.push(r.status); resolve(r); },
          (e) => { window.__settled.push('err'); reject(e); });
        return;
      }
      window.__settled.push(status || 500);
      resolve(new Response(status === 200 ? '{"ok": true}' : '{"ok": false}',
                           {status: status || 500,
                            headers: {'Content-Type': 'application/json'}}));
    }));
  }
  return window.__realFetch(url, opts);
};
"""


def _hold(path):
    return _HOLD_TEMPLATE.replace("__PATH__", path)


_HOLD_INSTOCK = _hold("/api/instock")


def test_failed_bulk_mark_tells_the_manager_and_marks_nothing(page, toorder_server):
    _open(page, toorder_server)
    _fail(page, "/api/ordered/bulk")

    page.locator(".toorder-supplier").filter(has_text="CITRADE").locator(".tosup-bulk").click()

    alerts = _wait_alert(page)
    assert len(alerts) == 1, alerts
    assert "nepodarilo" in alerts[0]["msg"].lower(), alerts[0]["msg"]
    assert "chyba 500" in alerts[0]["msg"], alerts[0]["msg"]
    # nothing may look ordered — the write never landed
    assert page.locator(".toorder-row.done").count() == 0
    assert page.locator(".toorder-row input[type=checkbox]:checked").count() == 0


def test_failed_bulk_mark_survives_a_dead_network(page, toorder_server):
    _open(page, toorder_server)
    page.route("**/api/ordered/bulk", lambda route: route.abort())

    page.locator(".toorder-supplier").filter(has_text="CITRADE").locator(".tosup-bulk").click()

    alerts = _wait_alert(page)
    assert "neodpoved" in alerts[0]["msg"].lower(), alerts[0]["msg"]
    assert page.locator(".toorder-row.done").count() == 0


@pytest.mark.parametrize(
    "path, button, row_class",
    [("/api/instock", ".to-instock", "instock"),
     ("/api/waiting", ".to-wait", "waiting"),
     ("/api/unavailable", ".to-unavail", "unavail")])
def test_failed_row_flag_is_reported_and_rolled_back(page, toorder_server, path, button, row_class):
    """N1 is the ONLY line of its ('—') supplier group, so flagging it would turn that
    chip from todo (green) to done (red) — which makes both assertions below able to
    fail: without the rollback the row keeps the class AND the chip claims 'resolved'."""
    _open(page, toorder_server)
    _fail(page, path)

    page.locator(".toorder-row[data-code='N1']").locator(button).click()

    alerts = _wait_alert(page)
    assert "nepodarilo" in alerts[0]["msg"].lower(), alerts[0]["msg"]
    # the tab was already telling the truth WHEN the message fired, not only afterwards
    assert row_class not in (alerts[0]["N1"] or "").split(), alerts[0]["N1"]
    assert "todo" in _chip(alerts[0], "—").split("||")[1], _chip(alerts[0], "—")

    row = page.locator(".toorder-row[data-code='N1']")
    assert row_class not in (row.get_attribute("class") or "").split()
    assert "on" not in (row.locator(button).get_attribute("class") or "").split()
    chip = page.locator("#filters button").filter(has_text="—").first
    assert "todo" in (chip.get_attribute("class") or "").split(), "chip must stay un-resolved"


def test_failed_ordered_checkbox_is_reported_and_rolled_back(page, toorder_server):
    _open(page, toorder_server)
    _fail(page, "/api/ordered")

    # .click(), not .check() — check() re-asserts the box afterwards, and the rollback
    # replaces the node under it, which would make Playwright retry and POST twice
    page.locator(".toorder-row[data-code='N1'] input[type=checkbox]").click()

    alerts = _wait_alert(page)
    assert "nepodarilo" in alerts[0]["msg"].lower(), alerts[0]["msg"]
    assert "done" not in (alerts[0]["N1"] or "").split(), alerts[0]["N1"]
    assert page.locator(".toorder-row[data-code='N1'] input[type=checkbox]").is_checked() is False


def test_every_failing_row_is_reported_not_just_the_first(page, toorder_server):
    """Working DOWN a supplier group is the whole point of this tab, so three rows failing
    within a couple of seconds is the NORMAL shape of a partial outage — not a repeat of
    one event. The dedup used to key on the message prose alone, which carries no row
    identity, so every row produced a byte-identical string: the manager was told once,
    the other flags quietly flipped back, and he could not tell which of his writes
    landed. Each failed WRITE now reports itself, and names the row it lost."""
    _open(page, toorder_server)
    _fail(page, "/api/instock")

    for code in ("C1", "C2", "C3"):
        page.locator(f".toorder-row[data-code='{code}'] .to-instock").click()
        page.wait_for_function(
            "n => window.__alerts.length === n", arg=("C1", "C2", "C3").index(code) + 1,
            timeout=3000)

    msgs = page.evaluate("() => window.__alerts.map(a => a.msg)")
    assert len(msgs) == 3, msgs
    for code in ("C1", "C2", "C3"):
        assert sum(1 for m in msgs if code in m) == 1, (code, msgs)
    # every one of them still rolled back — reporting never replaces the fix
    assert page.locator(".toorder-row.instock").count() == 0


def test_failed_pair_url_save_is_reported(page, toorder_server):
    _open(page, toorder_server)
    _fail(page, "/api/order-pair")

    row = page.locator(".toorder-row[data-code='C1']")
    row.locator(".to-pairurl").fill("https://dodavatel.test/c1")
    row.locator(".to-pairsave").click()

    alerts = _wait_alert(page)
    assert "nepodarilo" in alerts[0]["msg"].lower(), alerts[0]["msg"]
    assert page.locator(".toorder-row[data-code='C1'] .to-link").count() == 0


def test_server_reason_is_shown_not_just_the_status(page, toorder_server):
    """'comment too long' / 'invalid supplier' / 'unauthorized' are DETERMINISTIC — a bare
    „chyba 400" would have the manager retrying a write that can never succeed."""
    _open(page, toorder_server)
    _fail(page, "/api/order-comment", status=400, body='{"ok": false, "error": "comment too long"}')

    row = page.locator(".toorder-row[data-code='C1']")
    row.locator(".to-comadd").click()
    row.locator(".to-cominput").fill("príliš dlhý komentár")
    row.locator(".to-comsave").click()

    alerts = _wait_alert(page)
    assert "comment too long" in alerts[0]["msg"], alerts[0]["msg"]
    assert page.locator(".toorder-row[data-code='C1'] .to-comment").count() == 0


def test_non_url_pair_input_is_reported_instead_of_silently_ignored(page, toorder_server):
    """A typo'd URL used to be dropped on the floor by the client-side guard — the
    manager saw his text stay in the box with no explanation."""
    _open(page, toorder_server)

    row = page.locator(".toorder-row[data-code='C1']")
    row.locator(".to-pairurl").fill("www.dodavatel.test/c1")   # no scheme
    row.locator(".to-pairsave").click()

    alerts = _wait_alert(page)
    assert "http" in alerts[0]["msg"].lower(), alerts[0]["msg"]
    assert page.locator(".toorder-row[data-code='C1'] .to-link").count() == 0


def test_failed_supplier_assign_is_reported(page, toorder_server):
    _open(page, toorder_server)
    _fail(page, "/api/order-supplier")

    row = page.locator(".toorder-row[data-code='N1']")   # the line that has no supplier
    row.locator(".to-supinput").fill("CITRADE")
    row.locator(".to-supsave").click()

    alerts = _wait_alert(page)
    assert "nepodarilo" in alerts[0]["msg"].lower(), alerts[0]["msg"]
    assert page.locator(".toorder-row[data-code='N1'] .to-suptag").count() == 0


# ── PR #233 review: two in-flight writes for one row ─────────────────────────

def test_double_click_never_leaves_a_flag_the_server_refused(page, toorder_server):
    """A plain double-click on „✓ Skladom" during an outage fires TWO writes for the same
    row. Each used to roll back to its OWN captured snapshot, and the second one's
    snapshot was the OPTIMISTIC value the first had just written — so the later rollback
    restored a flag the server had explicitly refused. The manager was told the save
    failed and then looked at a row (and a RED „everything resolved" chip) claiming it
    succeeded — the exact silent loss #214 exists to remove.

    N1 is the only line of its '—' group, so its chip really does flip colour when the
    flag survives; without the fix the assertions below fail on the client map, the
    server map, the row, the button and the chip alike."""
    _open(page, toorder_server)
    page.evaluate(_HOLD_INSTOCK)

    btn = page.locator(".toorder-row[data-code='N1'] .to-instock")
    btn.click()                      # write #1: on  — POST held open
    btn.click()                      # write #2: off — fired before #1 resolved
    page.wait_for_function("() => window.__held.length === 2", timeout=3000)

    page.evaluate("() => window.__held[0](500)")   # #1 is refused first …
    page.evaluate("() => window.__held[1](500)")   # … then #2
    alerts = _wait_alert(page)
    page.wait_for_timeout(300)                     # let both continuations settle

    assert page.evaluate("() => Object.keys(INSTOCK)") == [], "client kept a refused flag"
    server = page.evaluate(
        "() => window.__realFetch('/api/instock').then(r => r.json())")
    assert server["instock"] == {}, server
    row = page.locator(".toorder-row[data-code='N1']")
    assert "instock" not in (row.get_attribute("class") or "").split()
    assert "on" not in (row.locator(".to-instock").get_attribute("class") or "").split()
    chip = page.locator("#filters button").filter(has_text="—").first
    assert "todo" in (chip.get_attribute("class") or "").split(), "chip must stay un-resolved"
    # one refused row, one message — the superseded write is not a second outage
    assert len(alerts) == 1, [a["msg"] for a in alerts]


def test_a_refused_second_write_rolls_back_to_what_the_server_ACCEPTED(page, toorder_server):
    """The other half of the same race, and the trap in the obvious fix: if the rollback
    target is the state captured before the FIRST in-flight write, then a first write the
    server ACCEPTED is thrown off the tab when a second one is refused. The manager's
    successful click would silently disappear until the next reload — the same class of
    loss, mirrored. The rollback target is therefore the last value the server took.

    This half was never demonstrated red on the pre-fix code (its per-call snapshot
    happened to give the right answer for THIS ordering) — it is a mutation guard, and it
    does kill the mutant that drops saveOrderFlag's `confirmed` update."""
    _open(page, toorder_server)
    page.evaluate(_HOLD_INSTOCK)

    btn = page.locator(".toorder-row[data-code='N1'] .to-instock")
    btn.click()                      # write #1: on  — will be ACCEPTED
    btn.click()                      # write #2: off — will be refused
    page.wait_for_function("() => window.__held.length === 2", timeout=3000)

    page.evaluate("() => window.__held[0](200, true)")   # #1 really lands on the server
    # gate on the round-trip ACTUALLY finishing: a fixed sleep would flip the release
    # order on a loaded runner and fail this test against correct code
    page.wait_for_function("() => window.__settled.length === 1", timeout=5000)
    page.evaluate("() => window.__held[1](500)")
    _wait_alert(page)
    page.wait_for_timeout(300)

    server = page.evaluate(
        "() => window.__realFetch('/api/instock').then(r => r.json())")
    assert list(server["instock"]) == ["20260001|N1"], server
    assert page.evaluate("() => Object.keys(INSTOCK)") == ["20260001|N1"], "the accepted write was lost"
    row = page.locator(".toorder-row[data-code='N1']")
    assert "instock" in (row.get_attribute("class") or "").split()
    assert "on" in (row.locator(".to-instock").get_attribute("class") or "").split()


def test_an_accepted_write_survives_a_refusal_that_answers_FIRST(page, toorder_server):
    """The same double-click, with the responses arriving in the OTHER order — which is
    exactly what a partial outage produces: a write that 500s/401s fast overtakes one that
    succeeds slowly behind the app's `with _lock:` + atomic file replace.

    #1 (on) is ACCEPTED but slow, #2 (off) is REFUSED and answers first. The refusal owns
    the row, so it rolls back to `confirmed` (still false) and alerts. When #1's success
    finally lands it updates `confirmed` to true — and then, being superseded, returned
    without touching the map. The manager was told „nepodarilo sa uložiť", the row showed
    the flag OFF, and the server held it ON: his accepted click was silently gone until
    the next reload. The client must reconcile with what the server confirmed once the
    last response for that row has settled."""
    _open(page, toorder_server)
    page.evaluate(_HOLD_INSTOCK)

    btn = page.locator(".toorder-row[data-code='N1'] .to-instock")
    btn.click()                      # write #1: on  — ACCEPTED, answers LAST
    btn.click()                      # write #2: off — REFUSED, answers FIRST
    page.wait_for_function("() => window.__held.length === 2", timeout=3000)

    page.evaluate("() => window.__held[1](500)")          # the refusal overtakes …
    _wait_alert(page)
    page.evaluate("() => window.__held[0](200, true)")    # … and #1 really lands after it
    page.wait_for_function("() => window.__settled.length === 2", timeout=5000)
    page.wait_for_timeout(300)                            # let the continuation settle

    server = page.evaluate(
        "() => window.__realFetch('/api/instock').then(r => r.json())")
    assert list(server["instock"]) == ["20260001|N1"], server
    assert page.evaluate("() => Object.keys(INSTOCK)") == ["20260001|N1"], \
        "the accepted write was dropped because its success landed last"
    row = page.locator(".toorder-row[data-code='N1']")
    assert "instock" in (row.get_attribute("class") or "").split()
    assert "on" in (row.locator(".to-instock").get_attribute("class") or "").split()


def test_a_straggler_from_an_older_burst_cannot_poison_a_later_click(page, toorder_server):
    """Sequencing that DELETES its bookkeeping when a burst settles leaves a generation
    hole. Click on / click off (both in flight) → the OFF write is accepted and the entry
    is dropped → the manager clicks on again → the first burst's ACCEPTED reply finally
    lands and writes its value onto the FRESH entry → the third write is refused and rolls
    back to that stale value. The phantom flag is right back, with a „save failed" over it.

    Every response here is faked (the server is never touched), because that is the point:
    what is under test is the CLIENT's own bookkeeping, not the inherently unresolvable
    question of which POST reached the server last."""
    _open(page, toorder_server)
    page.evaluate(_HOLD_INSTOCK)

    btn = page.locator(".toorder-row[data-code='N1'] .to-instock")
    btn.click()                      # burst 1, write #1: on
    btn.click()                      # burst 1, write #2: off
    page.wait_for_function("() => window.__held.length === 2", timeout=3000)
    page.evaluate("() => window.__held[1](200)")          # #2 accepted → burst 1 settles
    page.wait_for_function("() => window.__settled.length === 1", timeout=3000)
    page.wait_for_timeout(200)                            # let the continuation run

    # The never-delete rule, asserted DIRECTLY: everything below still passes with
    # delete-on-settle put back (the `live` identity check neutralises the straggler on
    # its own), so without this the rule the code and the playbook both call load-bearing
    # would be unpinned prose. One row, one flag → exactly one entry, and it SURVIVES the
    # burst settling; only `loadOrders()` may ever clear the bookkeeping.
    assert page.evaluate("() => Object.keys(_flagWrites).length") == 1, \
        "the (flag, row) entry was dropped when its burst settled"

    page.locator(".toorder-row[data-code='N1'] .to-instock").click()   # burst 2, write #3
    page.wait_for_function("() => window.__held.length === 3", timeout=3000)
    page.evaluate("() => window.__held[0](200)")          # #1's late reply, ACCEPTED
    page.wait_for_function("() => window.__settled.length === 2", timeout=3000)
    page.evaluate("() => window.__held[2](500)")          # #3 refused
    _wait_alert(page)

    # #2 (off) is the newest write the client ever saw accepted — that is the baseline
    assert page.evaluate("() => Object.keys(INSTOCK)") == [], "a stale burst set the baseline"
    row = page.locator(".toorder-row[data-code='N1']")
    assert "instock" not in (row.get_attribute("class") or "").split()


def test_a_bulk_write_supersedes_an_in_flight_per_row_write(page, toorder_server):
    """The bulk „označiť skupinu objednané" writes the SAME rows as the per-row checkbox,
    so it has to join the same bookkeeping. Otherwise: the manager ticks a row (POST slow),
    then marks the whole group — the group write lands — and the row's own write then
    fails and rolls the row back to un-ordered, with an alert, while the server holds it
    as ordered. N1 is alone in its '—' group, so the bulk write covers exactly that row."""
    _open(page, toorder_server)
    page.evaluate(_hold("/api/ordered"))      # only the PER-ROW endpoint is held

    page.locator(".toorder-row[data-code='N1'] input[type=checkbox]").click()
    page.wait_for_function("() => window.__held.length === 1", timeout=3000)

    page.evaluate("""() => {
      const h = [...document.querySelectorAll('.toorder-supplier')]
        .find(x => x.querySelector('.tosup-label').textContent.trim().startsWith('—'));
      h.querySelector('.tosup-bulk').click();
    }""")
    page.wait_for_function("""() => {
      const h = [...document.querySelectorAll('.toorder-supplier')]
        .find(x => x.querySelector('.tosup-label').textContent.trim().startsWith('—'));
      return h && h.querySelector('.tosup-bulk').textContent.includes('Zrušiť');
    }""", timeout=3000)     # the group write landed and the tab repainted

    page.evaluate("() => window.__held[0](500)")          # the per-row write is refused
    page.wait_for_function("() => window.__settled.length === 1", timeout=3000)
    page.wait_for_timeout(400)     # give the (wrong) rollback every chance to happen

    assert page.evaluate("() => Object.keys(ORDERED)") == ["20260001|N1"]
    server = page.evaluate("() => window.__realFetch('/api/ordered').then(r => r.json())")
    assert list(server["ordered"]) == ["20260001|N1"], server
    assert "done" in (page.locator(".toorder-row[data-code='N1']")
                      .get_attribute("class") or "").split()
    # nothing failed from the manager's point of view: the group write carried the row
    assert page.evaluate("() => window.__alerts.length") == 0, \
        page.evaluate("() => window.__alerts.map(a => a.msg)")


# ── PR #233 review: a repaint must not eat unsaved typing ────────────────────

def test_a_failed_flag_save_keeps_another_rows_unsaved_comment(page, toorder_server):
    """The rollback repaints the WHOLE tab, which silently threw away whatever the
    manager had typed into any open inline editor — and the message he got talked only
    about the flag, so the lost note was invisible."""
    _open(page, toorder_server)
    c1 = page.locator(".toorder-row[data-code='C1']")
    c1.locator(".to-comadd").click()
    c1.locator(".to-cominput").fill("rozpisany komentar manazera")

    _fail(page, "/api/instock")
    page.locator(".toorder-row[data-code='N1'] .to-instock").click()
    _wait_alert(page)

    box = page.locator(".toorder-row[data-code='C1'] .to-cominput")
    assert box.count() == 1, "the open comment editor was destroyed by the rollback repaint"
    assert box.input_value() == "rozpisany komentar manazera"


def test_a_successful_pair_save_keeps_another_rows_unsaved_comment(page, toorder_server):
    """#204 turned savePairUrl's `row.replaceWith(...)` into a whole-tab renderToOrder(),
    so even a SUCCESSFUL save now wipes an editor open on a different row."""
    _open(page, toorder_server)
    c2 = page.locator(".toorder-row[data-code='C2']")
    c2.locator(".to-comadd").click()
    c2.locator(".to-cominput").fill("nedokoncena poznamka")

    c1 = page.locator(".toorder-row[data-code='C1']")
    c1.locator(".to-pairurl").fill("https://dodavatel.test/c1")
    c1.locator(".to-pairsave").click()
    page.wait_for_selector(".toorder-row[data-code='C1'] .to-link")

    box = page.locator(".toorder-row[data-code='C2'] .to-cominput")
    assert box.count() == 1, "the open comment editor was destroyed by the save repaint"
    assert box.input_value() == "nedokoncena poznamka"
    # the row that WAS saved shows its link — a saved value is not "unsaved typing"
    assert page.locator(".toorder-row[data-code='C1'] .to-pairurl").count() == 0


def test_an_unsaved_supplier_and_pair_edit_survive_a_repaint_too(page, toorder_server):
    """Not just comments: the inline supplier and pair-URL editors carry the same
    half-typed work, and each is restored with the text the manager left in it."""
    _open(page, toorder_server)
    page.locator(".toorder-row[data-code='N1'] .to-supinput").fill("Nedopisany Dodava")
    page.locator(".toorder-row[data-code='C1'] .to-pairurl").fill("https://dodavatel.test/roz")

    _fail(page, "/api/waiting")
    page.locator(".toorder-row[data-code='C4'] .to-wait").click()
    _wait_alert(page)

    assert page.locator(".toorder-row[data-code='N1'] .to-supinput").input_value() \
        == "Nedopisany Dodava"
    assert page.locator(".toorder-row[data-code='C1'] .to-pairurl").input_value() \
        == "https://dodavatel.test/roz"


def test_an_editor_opened_on_a_row_with_NOTHING_stored_survives_a_repaint(page, toorder_server):
    """💬 Komentár on a row that has no stored comment: the box is empty and so is the
    stored value, so the „value equals what is stored" skip fired BEFORE the „the manager
    opened this one himself" exception ever got a chance — and the repaint silently closed
    the box under him while he was about to type into it."""
    _open(page, toorder_server)
    c1 = page.locator(".toorder-row[data-code='C1']")
    c1.locator(".to-comadd").click()
    c1.locator(".to-cominput").wait_for(timeout=3000)

    _fail(page, "/api/instock")
    page.locator(".toorder-row[data-code='N1'] .to-instock").click()
    _wait_alert(page)
    page.wait_for_timeout(300)

    assert page.locator(".toorder-row[data-code='C1'] .to-cominput").count() == 1, \
        "the empty box the manager opened himself was destroyed by the repaint"


# ── PR #233 review: a poisoned stored URL must not render a clickable link ────

def test_a_non_http_stored_url_never_renders_a_self_navigating_link(page, toorder_server):
    """`safeHttpUrl` correctly refuses a non-http(s) value — but the caller still built an
    <a> around the '' it returns, and `href=""` resolves to the PAGE ITSELF: one click
    reloads the tab and takes every open editor (and the unsaved typing in it) with it.
    The poisoned value was echoed back into the link's tooltip on top of that."""
    _open(page, toorder_server)
    page.evaluate("""() => {
      for (const o of ORDERS) {
        if (o.itemCode === 'C1') o.pairUrl = 'javascript:alert(1)';
        if (o.itemCode === 'C2') o.supplierUrl = 'javascript:alert(2)';
      }
      renderToOrder();
    }""")
    page.wait_for_selector(".toorder-row[data-code='C1']")

    for code in ("C1", "C2"):
        hrefs = page.evaluate(
            "(c) => [...document.querySelectorAll(`.toorder-row[data-code='${c}'] a`)]"
            ".map(a => a.getAttribute('href'))", code)
        assert "" not in hrefs, (code, hrefs)
        assert page.evaluate(
            "(c) => [...document.querySelectorAll(`.toorder-row[data-code='${c}'] *`)]"
            ".some(e => String(e.title || '').includes('javascript:'))", code) is False, code

    # the manager can still repair the bad pairing — the row falls back to the paste box
    assert page.locator(".toorder-row[data-code='C1'] .to-pairurl").count() == 1


# ── PR #233 review: one message per failed WRITE, not per prose ──────────────

def test_the_same_failing_write_is_reported_only_once(page, toorder_server):
    """alert() blocks the thread, so re-clicking the same refused toggle during one
    outage must not queue a second modal. The POST counter is what makes the assertion
    able to fail at all: without it „no second alert yet" and „the second write has not
    finished yet" look identical."""
    _open(page, toorder_server)
    posts = []

    def refuse(route):
        posts.append(1)
        route.fulfill(status=500, content_type="application/json", body='{"ok": false}')

    page.route("**/api/instock", refuse)

    page.locator(".toorder-row[data-code='C1'] .to-instock").click()
    _wait_alert(page)
    page.locator(".toorder-row[data-code='C1'] .to-instock").click()   # same write again
    # the second write really happened AND really rolled back — only the MESSAGE was
    # suppressed (an un-flagged row after two refused clicks is the observable proof)
    page.wait_for_function(
        "() => document.querySelectorAll('.toorder-row.instock').length === 0",
        timeout=3000)
    assert len(posts) == 2, posts
    assert page.evaluate("() => window.__alerts.length") == 1


def test_a_comment_SAVED_empty_collapses_instead_of_sticking_open(page, toorder_server):
    """The other side of the „opened box is his" exception: once he has COMMITTED the
    value, the repaint that follows must be free to collapse the editor back to the
    💬 Komentár button — including when he deliberately saved it EMPTY to delete the note.
    Otherwise the box he just finished with reopens itself and, being re-stamped as
    „opened", never closes on any later repaint either."""
    _open(page, toorder_server)
    c1 = page.locator(".toorder-row[data-code='C1']")
    c1.locator(".to-comadd").click()
    c1.locator(".to-cominput").fill("docasna poznamka")
    with page.expect_response("**/api/order-comment"):
        c1.locator(".to-comsave").click()
    page.wait_for_selector(".toorder-row[data-code='C1'] .to-comment", timeout=3000)

    # now clear it and save again — the note is deleted
    page.locator(".toorder-row[data-code='C1'] .to-comedit").click()
    page.locator(".toorder-row[data-code='C1'] .to-cominput").fill("")
    with page.expect_response("**/api/order-comment"):
        page.locator(".toorder-row[data-code='C1'] .to-comsave").click()

    page.wait_for_selector(".toorder-row[data-code='C1'] .to-comadd", timeout=3000)
    assert page.locator(".toorder-row[data-code='C1'] .to-cominput").count() == 0, \
        'a SAVED-empty editor reopened itself instead of collapsing'

    # …and it stays collapsed across a later, unrelated repaint
    _fail(page, "/api/instock")
    page.locator(".toorder-row[data-code='N1'] .to-instock").click()
    _wait_alert(page)
    page.wait_for_timeout(300)
    assert page.locator(".toorder-row[data-code='C1'] .to-cominput").count() == 0, \
        'the consumed „opened“ claim came back on a later repaint'


# ── PR #233 final verdict: the bulk write must not steal a LATER click's row ──

# Holds POSTs to SEVERAL paths in ONE shared queue, so the order in which the test
# releases them is exactly the order the server itself would answer in. Entries carry
# their path: release with `window.__held[i].go(status, passthrough)` (same semantics as
# `_hold`: `(200, true)` replays against the real server, `(200)` only fakes success).
_HOLD_MULTI_TEMPLATE = """
window.__held = [];
window.__settled = [];
window.__realFetch = window.fetch.bind(window);
window.fetch = (url, opts) => {
  const p = new URL(String(url), location.href).pathname;
  if (__PATHS__.indexOf(p) >= 0 && opts && opts.method === 'POST') {
    return new Promise((resolve, reject) => window.__held.push({path: p, go: (status, passthrough) => {
      if (status === 200 && passthrough) {
        window.__realFetch(url, opts).then(
          (r) => { window.__settled.push(p + ':' + r.status); resolve(r); },
          (e) => { window.__settled.push(p + ':err'); reject(e); });
        return;
      }
      window.__settled.push(p + ':' + status);
      resolve(new Response(status === 200 ? '{"ok": true}' : '{"ok": false}',
                           {status: status || 500,
                            headers: {'Content-Type': 'application/json'}}));
    }}));
  }
  return window.__realFetch(url, opts);
};
"""


def _hold_multi(*paths):
    return _HOLD_MULTI_TEMPLATE.replace("__PATHS__", json.dumps(list(paths)))


def _click_group_bulk(page):
    """Click „✔ Označiť skupinu objednané" on the '—' group (N1 is alone in it)."""
    page.evaluate("""() => {
      const h = [...document.querySelectorAll('.toorder-supplier')]
        .find(x => x.querySelector('.tosup-label').textContent.trim().startsWith('—'));
      h.querySelector('.tosup-bulk').click();
    }""")


def test_a_per_row_write_issued_DURING_the_bulk_flight_is_not_inverted(page, toorder_server):
    """The mirror of `test_a_bulk_write_supersedes_an_in_flight_per_row_write`, and the
    direction that inverts the manager's click: he marks the group, then — while that POST
    is still flying — ticks a row twice, ending on OFF. Those two writes are NEWER intent
    and commit LATER on the server (they queue behind the bulk's `with _lock:`), so the
    server ends up holding the row un-ordered. Crowning the bulk at RESPONSE time made it
    supersede them anyway: the tab painted the row ordered, `_reconcileFlag` forced the map
    to the bulk's value on settle, and not one alert said anything happened."""
    page.add_init_script(_ALERT_SPY)
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")
    page.evaluate(_hold_multi("/api/ordered", "/api/ordered/bulk"))

    _click_group_bulk(page)                      # bulk: ordered = true  (held)
    page.wait_for_function("() => window.__held.length === 1", timeout=3000)

    cb = page.locator(".toorder-row[data-code='N1'] input[type=checkbox]")
    cb.click()      # per-row write A: ordered = true   (held)
    cb.click()      # per-row write B: ordered = false  (held) ← his FINAL intent
    page.wait_for_function("() => window.__held.length === 3", timeout=3000)

    # released in REQUEST order — exactly how the server serialises them
    for i in range(3):
        page.evaluate(f"() => window.__held[{i}].go(200, true)")
        page.wait_for_function(f"() => window.__settled.length === {i + 1}", timeout=5000)
    page.wait_for_timeout(400)      # let every continuation (incl. reconcile) settle

    server = page.evaluate("() => window.__realFetch('/api/ordered').then(r => r.json())")
    assert "20260001|N1" not in server["ordered"], server      # his last click won
    assert page.evaluate("() => Object.keys(ORDERED)") == [], \
        "the client map crowned the bulk over a per-row write issued AFTER it"
    assert "done" not in (page.locator(".toorder-row[data-code='N1']")
                          .get_attribute("class") or "").split(), \
        "the row is painted ordered while the server holds it un-ordered"
    assert page.evaluate("() => window.__alerts.length") == 0, \
        page.evaluate("() => window.__alerts.map(a => a.msg)")


def test_a_bulk_that_LANDED_still_owns_the_row_when_the_later_writes_all_fail(page, toorder_server):
    """The other half of the same ordering rule, and the one a naive „snapshot the seq
    before the POST and skip any row that moved" fix would break: the bulk is ACCEPTED and
    both per-row writes that followed are REFUSED, so the server holds the row ordered.
    The bulk's acceptance is the newest server truth for that row, so the rollback of the
    last refused write must land on it — not on the pre-bulk baseline."""
    page.add_init_script(_ALERT_SPY)
    page.goto(toorder_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")
    page.evaluate(_hold_multi("/api/ordered", "/api/ordered/bulk"))

    _click_group_bulk(page)
    page.wait_for_function("() => window.__held.length === 1", timeout=3000)
    cb = page.locator(".toorder-row[data-code='N1'] input[type=checkbox]")
    cb.click()
    cb.click()
    page.wait_for_function("() => window.__held.length === 3", timeout=3000)

    page.evaluate("() => window.__held[0].go(200, true)")     # bulk lands for real
    page.wait_for_function("() => window.__settled.length === 1", timeout=5000)
    page.evaluate("() => window.__held[1].go(500)")           # A refused
    page.wait_for_function("() => window.__settled.length === 2", timeout=5000)
    page.evaluate("() => window.__held[2].go(500)")           # B refused
    page.wait_for_function("() => window.__settled.length === 3", timeout=5000)
    _wait_alert(page)
    page.wait_for_timeout(300)

    server = page.evaluate("() => window.__realFetch('/api/ordered').then(r => r.json())")
    assert list(server["ordered"]) == ["20260001|N1"], server
    assert page.evaluate("() => Object.keys(ORDERED)") == ["20260001|N1"], \
        "the rollback discarded the bulk write the server had already accepted"
    assert "done" in (page.locator(".toorder-row[data-code='N1']")
                      .get_attribute("class") or "").split()


# ── PR #233 final verdict: a write a reload disowned must still report itself ──

def test_a_write_whose_bookkeeping_a_reload_dropped_still_reports_its_failure(page, toorder_server):
    """`loadOrders()` voids every in-flight write's bookkeeping, so such a write has no
    map left to roll back — but it still FAILED, and returning `false` in silence is the
    exact silent-lost-write #214 exists to kill."""
    _open(page, toorder_server)
    page.evaluate(_hold("/api/instock"))
    page.locator(".toorder-row[data-code='C1'] .to-instock").click()
    page.wait_for_function("() => window.__held.length === 1", timeout=3000)

    page.evaluate("() => loadOrders()")          # disowns the write in flight
    assert page.evaluate("() => Object.keys(_flagWrites).length") == 0

    page.evaluate("() => window.__held[0](500)")
    alerts = _wait_alert(page)
    assert "nepodarilo" in alerts[0]["msg"].lower(), alerts[0]["msg"]


def test_a_click_during_a_reload_cannot_bind_its_write_to_the_replaced_map(page, toorder_server):
    """`loadOrders()` cleared `_flagWrites` five awaits BEFORE it replaced the map objects.
    A click landing in that window seeded its baseline from a still-optimistic value and
    bound its write to a map the reload was about to throw away — a rollback into thin
    air. The clear belongs in the SAME synchronous block as the assignment."""
    _open(page, toorder_server)
    # hold the reload's own GET of the instock map (GET calls pass no `opts`)
    page.evaluate("""() => {
      window.__held = [];
      window.__realFetch = window.fetch.bind(window);
      window.fetch = (url, opts) => {
        const p = new URL(String(url), location.href).pathname;
        if (p === '/api/instock' && !(opts && opts.method === 'POST')) {
          return new Promise((resolve) => window.__held.push(
            () => window.__realFetch(url, opts).then(resolve)));
        }
        return window.__realFetch(url, opts);
      };
    }""")
    page.evaluate("() => { window.__load = loadOrders(); window.__before = INSTOCK; }")
    page.wait_for_function("() => window.__held.length === 1", timeout=3000)

    page.locator(".toorder-row[data-code='C1'] .to-instock").click()   # lands INSIDE the window
    page.evaluate("() => window.__held[0]()")
    page.evaluate("() => window.__load")

    assert page.evaluate("() => INSTOCK !== window.__before"), "the reload never replaced the map"
    assert page.evaluate("() => Object.keys(_flagWrites).length") == 0, \
        "a write seeded during the reload survived it, bound to the map that was replaced"


# ── PR #233 final verdict: a failed EMPTY commit must give the editor back ────

def test_a_failed_EMPTY_comment_save_gives_the_open_editor_back(page, toorder_server):
    """Saving CONSUMES the „he opened it himself" claim, so a repaint during an in-flight
    EMPTY commit captures `{value:'', opened:false}` and restores nothing — the box is
    gone. When that commit then FAILS, `delete wrap.dataset.editorSaving` hands the claim
    back to a DETACHED node and the manager is told his deletion failed while the box he
    was working in has vanished."""
    _open(page, toorder_server)
    c1 = page.locator(".toorder-row[data-code='C1']")
    c1.locator(".to-comadd").click()
    c1.locator(".to-cominput").fill("poznamka na zmazanie")
    with page.expect_response("**/api/order-comment"):
        c1.locator(".to-comsave").click()
    page.wait_for_selector(".toorder-row[data-code='C1'] .to-comment", timeout=3000)

    page.evaluate(_hold("/api/order-comment"))
    page.locator(".toorder-row[data-code='C1'] .to-comedit").click()
    page.locator(".toorder-row[data-code='C1'] .to-cominput").fill("")
    page.locator(".toorder-row[data-code='C1'] .to-comsave").click()   # EMPTY commit, held
    page.wait_for_function("() => window.__held.length === 1", timeout=3000)

    _fail(page, "/api/instock")                    # an unrelated write fails…
    page.locator(".toorder-row[data-code='N1'] .to-instock").click()
    _wait_alert(page)                              # …and its rollback repaints the tab

    page.evaluate("() => window.__held[0](500)")   # now the empty commit is refused
    page.wait_for_function("() => window.__alerts.length === 2", timeout=3000)
    page.wait_for_timeout(200)

    box = page.locator(".toorder-row[data-code='C1'] .to-cominput")
    assert box.count() == 1, "the editor the manager was working in was never given back"
    assert box.input_value() == "", "his cleared box came back with the old text in it"
