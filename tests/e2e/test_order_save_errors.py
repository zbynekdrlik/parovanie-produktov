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
"""
import pytest


def _dialogs(page):
    seen = []
    page.on("dialog", lambda d: (seen.append(d.message), d.accept()))
    return seen


def _fail(page, path, status=500):
    page.route(f"**{path}", lambda route: route.fulfill(
        status=status, content_type="application/json", body='{"ok": false}'))


def _open(page, base):
    page.goto(base + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")


def test_failed_bulk_mark_tells_the_manager_and_marks_nothing(page, toorder_server):
    _open(page, toorder_server)
    seen = _dialogs(page)
    _fail(page, "/api/ordered/bulk")

    citrade = page.locator(".toorder-supplier").filter(has_text="CITRADE")
    citrade.locator(".tosup-bulk").click()

    page.wait_for_function("() => true")            # let the rejected POST settle
    page.wait_for_timeout(300)
    assert seen, "a failed bulk mark must be surfaced to the manager"
    assert "nepodarilo" in seen[0].lower(), seen
    # nothing may look ordered — the write never landed
    assert page.locator(".toorder-row.done").count() == 0
    assert page.locator(".toorder-row input[type=checkbox]:checked").count() == 0


def test_failed_bulk_mark_survives_a_dead_network(page, toorder_server):
    _open(page, toorder_server)
    seen = _dialogs(page)
    page.route("**/api/ordered/bulk", lambda route: route.abort())

    page.locator(".toorder-supplier").filter(has_text="CITRADE").locator(".tosup-bulk").click()
    page.wait_for_timeout(300)
    assert seen and "nepodarilo" in seen[0].lower(), seen
    assert page.locator(".toorder-row.done").count() == 0


@pytest.mark.parametrize(
    "path, button, row_class",
    [("/api/instock", ".to-instock", "instock"),
     ("/api/waiting", ".to-wait", "waiting"),
     ("/api/unavailable", ".to-unavail", "unavail")])
def test_failed_row_flag_is_reported_and_rolled_back(page, toorder_server, path, button, row_class):
    _open(page, toorder_server)
    seen = _dialogs(page)
    _fail(page, path)

    row = page.locator(".toorder-row[data-code='C1']")
    row.locator(button).click()

    page.wait_for_function(
        f"() => !document.querySelector(\"[data-code='C1']\").classList.contains('{row_class}')",
        timeout=3000)
    assert seen and "nepodarilo" in seen[0].lower(), seen
    row = page.locator(".toorder-row[data-code='C1']")
    assert row_class not in (row.get_attribute("class") or "")
    assert "on" not in (row.locator(button).get_attribute("class") or "")
    # the supplier chip must not claim the line is resolved either
    chip = page.locator("#filters button").filter(has_text="CITRADE").first
    assert "todo" in (chip.get_attribute("class") or ""), "chip must stay un-resolved (green)"


def test_failed_ordered_checkbox_is_reported_and_rolled_back(page, toorder_server):
    _open(page, toorder_server)
    seen = _dialogs(page)
    _fail(page, "/api/ordered")

    page.locator(".toorder-row[data-code='C1'] input[type=checkbox]").check()
    page.wait_for_function(
        "() => !document.querySelector(\"[data-code='C1']\").classList.contains('done')",
        timeout=3000)
    assert seen and "nepodarilo" in seen[0].lower(), seen
    assert page.locator(".toorder-row[data-code='C1'] input[type=checkbox]").is_checked() is False


def test_failed_pair_url_save_is_reported(page, toorder_server):
    _open(page, toorder_server)
    seen = _dialogs(page)
    _fail(page, "/api/order-pair")

    row = page.locator(".toorder-row[data-code='C1']")
    row.locator(".to-pairurl").fill("https://dodavatel.test/c1")
    row.locator(".to-pairsave").click()
    page.wait_for_timeout(300)
    assert seen and "nepodarilo" in seen[0].lower(), seen
    assert page.locator(".toorder-row[data-code='C1'] .to-link").count() == 0


def test_non_url_pair_input_is_reported_instead_of_silently_ignored(page, toorder_server):
    """A typo'd URL used to be dropped on the floor by the client-side guard — the
    manager saw his text stay in the box with no explanation."""
    _open(page, toorder_server)
    seen = _dialogs(page)

    row = page.locator(".toorder-row[data-code='C1']")
    row.locator(".to-pairurl").fill("www.dodavatel.test/c1")   # no scheme
    row.locator(".to-pairsave").click()
    page.wait_for_timeout(300)
    assert seen and "http" in seen[0].lower(), seen
    assert page.locator(".toorder-row[data-code='C1'] .to-link").count() == 0


def test_failed_supplier_assign_is_reported(page, toorder_server):
    _open(page, toorder_server)
    seen = _dialogs(page)
    _fail(page, "/api/order-supplier")

    row = page.locator(".toorder-row[data-code='N1']")   # the line that has no supplier
    row.locator(".to-supinput").fill("CITRADE")
    row.locator(".to-supsave").click()
    page.wait_for_timeout(300)
    assert seen and "nepodarilo" in seen[0].lower(), seen
    assert page.locator(".toorder-row[data-code='N1'] .to-suptag").count() == 0


def test_failed_order_comment_save_is_reported(page, toorder_server):
    _open(page, toorder_server)
    seen = _dialogs(page)
    _fail(page, "/api/order-comment")

    row = page.locator(".toorder-row[data-code='C1']")
    row.locator(".to-comadd").click()
    row.locator(".to-cominput").fill("skúšobný komentár")
    row.locator(".to-comsave").click()
    page.wait_for_timeout(300)
    assert seen and "nepodarilo" in seen[0].lower(), seen
    assert page.locator(".toorder-row[data-code='C1'] .to-comment").count() == 0
