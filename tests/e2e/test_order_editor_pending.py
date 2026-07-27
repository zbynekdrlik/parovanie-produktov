"""#235 — unsaved typing must survive a repaint that FILTERS ITS ROW OUT.

PR #233 taught the „Na objednanie" tab to carry an open inline editor across a whole-tab
`renderToOrder()` (`captureOpenEditors` / `restoreOpenEditors`). A snapshot whose row is
not in the rebuilt list — the manager switched the supplier chip, or the row jumped into
another group — was DROPPED on the floor:

    const row = rows.find(r => r.dataset.key === s.key);   // filtered out / gone → drop it

Narrow, but the same class of silent loss the carry-over exists to remove: he clicks a
chip to check something, comes back, and the half-typed comment / supplier / URL is gone
with no message at all. Parked snapshots now wait for the first repaint that shows their
row again — and die on their own the moment they stop being unsaved work.

Every test drives the REAL chips, so what is exercised is the manager's own navigation,
not a synthetic re-render.
"""
import pytest


def _open(page, base):
    page.goto(base + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")


def _chip(page, label):
    return page.locator("#filters button").filter(has_text=label).first


def _switch_away_and_back(page, away, back, gone_code, back_code):
    """Click the `away` supplier chip (which filters `gone_code` out of the list), then
    the `back` one. Waits on the DOM, never on a timeout."""
    _chip(page, away).click()
    page.wait_for_selector(f".toorder-row[data-code='{gone_code}']", state="detached")
    _chip(page, back).click()
    page.wait_for_selector(f".toorder-row[data-code='{back_code}']")


def test_a_half_typed_comment_survives_a_chip_switch_away_and_back(page, toorder_server):
    """The reported case: C1 belongs to CITRADE, so clicking the „—" chip removes its row
    from the list entirely. The snapshot has nowhere to land — and used to be discarded."""
    _open(page, toorder_server)
    c1 = page.locator(".toorder-row[data-code='C1']")
    c1.locator(".to-comadd").click()
    c1.locator(".to-cominput").fill("rozpisany komentar manazera")

    _switch_away_and_back(page, "—", "CITRADE", "C1", "C1")

    box = page.locator(".toorder-row[data-code='C1'] .to-cominput")
    assert box.count() == 1, "the parked editor never came back"
    assert box.input_value() == "rozpisany komentar manazera"


def test_a_half_typed_supplier_assignment_survives_it_too(page, toorder_server):
    """Not only comments: N1 is the supplier-less line (group „—"), and its inline
    supplier-assign box is the one place that row can be repaired at all."""
    _open(page, toorder_server)
    page.locator(".toorder-row[data-code='N1'] .to-supinput").fill("Nedopisany Dodava")

    _switch_away_and_back(page, "CITRADE", "—", "N1", "N1")

    box = page.locator(".toorder-row[data-code='N1'] .to-supinput")
    assert box.count() == 1
    assert box.input_value() == "Nedopisany Dodava"


def test_a_half_typed_pair_url_survives_it_too(page, toorder_server):
    _open(page, toorder_server)
    page.locator(".toorder-row[data-code='C2'] .to-pairurl").fill("https://dodavatel.test/rozp")

    _switch_away_and_back(page, "—", "CITRADE", "C2", "C2")

    box = page.locator(".toorder-row[data-code='C2'] .to-pairurl")
    assert box.count() == 1
    assert box.input_value() == "https://dodavatel.test/rozp"


def test_two_parked_editors_on_different_rows_both_come_back(page, toorder_server):
    """Parking is per (editor, row), so a whole group's worth of half-typed work returns
    together — one shared slot would have the second row silently eat the first."""
    _open(page, toorder_server)
    page.locator(".toorder-row[data-code='C1'] .to-comadd").click()
    page.locator(".toorder-row[data-code='C1'] .to-cominput").fill("poznamka k C1")
    page.locator(".toorder-row[data-code='C3'] .to-pairurl").fill("https://dodavatel.test/c3")

    _switch_away_and_back(page, "—", "CITRADE", "C1", "C1")

    assert page.locator(".toorder-row[data-code='C1'] .to-cominput").input_value() \
        == "poznamka k C1"
    assert page.locator(".toorder-row[data-code='C3'] .to-pairurl").input_value() \
        == "https://dodavatel.test/c3"


def test_a_parked_snapshot_is_dropped_once_that_value_is_actually_STORED(page, toorder_server):
    """The park must not become a resurrection machine. While the row is off screen the
    same text can land in the store (another window, the per-product propagation of #204).
    Coming back must then show the SAVED chip — not re-open an editor holding a value that
    is no longer unsaved work. `editorSnapHasWork` is the one predicate that decides it,
    for parked snapshots exactly as for live ones."""
    _open(page, toorder_server)
    c1 = page.locator(".toorder-row[data-code='C1']")
    c1.locator(".to-comadd").click()
    c1.locator(".to-cominput").fill("uz ulozena poznamka")

    _chip(page, "—").click()
    page.wait_for_selector(".toorder-row[data-code='C1']", state="detached")

    # the very same text becomes the STORED comment while the row is off screen
    page.evaluate("""() => fetch('/api/order-comment', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({orderCode: '20260910', comment: 'uz ulozena poznamka'})
    }).then(() => loadOrders()).then(() => render())""")

    _chip(page, "CITRADE").click()
    page.wait_for_selector(".toorder-row[data-code='C1']")

    assert page.locator(".toorder-row[data-code='C1'] .to-cominput").count() == 0, \
        "a value that is now STORED was re-opened as unsaved typing"
    assert page.locator(".toorder-row[data-code='C1'] .to-comment").count() == 1


def test_parking_does_not_leak_onto_a_row_that_never_had_an_editor(page, toorder_server):
    """A parked snapshot belongs to ONE row: C4 must not inherit C1's text just because
    both rows come back in the same repaint."""
    _open(page, toorder_server)
    c1 = page.locator(".toorder-row[data-code='C1']")
    c1.locator(".to-comadd").click()
    c1.locator(".to-cominput").fill("iba pre C1")

    _switch_away_and_back(page, "—", "CITRADE", "C1", "C1")

    assert page.locator(".toorder-row[data-code='C4'] .to-cominput").count() == 0
    assert page.locator(".toorder-row[data-code='C4'] .to-comadd").count() == 1


@pytest.mark.parametrize("code", ["C1"])
def test_the_console_stays_clean_while_editors_are_parked(page, toorder_server, code):
    msgs = []
    page.on("console", lambda m: msgs.append(f"[{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)
    _open(page, toorder_server)
    page.locator(f".toorder-row[data-code='{code}'] .to-comadd").click()
    page.locator(f".toorder-row[data-code='{code}'] .to-cominput").fill("text")
    _switch_away_and_back(page, "—", "CITRADE", code, code)
    assert msgs == [], msgs
