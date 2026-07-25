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


def test_repeated_identical_failures_do_not_stack_modals(page, toorder_server):
    """alert() blocks the thread — during an outage a manager rapid-firing toggles would
    otherwise queue one modal per click."""
    _open(page, toorder_server)
    _fail(page, "/api/instock")

    for code in ("C1", "C2", "C3"):
        page.locator(f".toorder-row[data-code='{code}'] .to-instock").click()
    _wait_alert(page)
    page.wait_for_timeout(400)
    assert page.evaluate("() => window.__alerts.length") == 1
    # every one of them still rolled back — suppressing the MESSAGE never suppresses the fix
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
