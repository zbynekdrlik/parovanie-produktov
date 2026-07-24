"""BUG 1 (frontend): the order's OWN supplier wins over a manual assignment.

`effSup(o)` groups a to-order row by supplier. A per-product manual assignment
(`supplier_assignments.json`) is meant to FILL IN a supplier for a line that
arrived WITHOUT one — it must NEVER override the order's real Shoptet supplier
(`o.supplier`). Otherwise a long-stale assignment prebihuje reálneho dodávateľa
in the grouping AND the nightly write-back would clobber the eshop.

`effSup` is a top-level `const` in the classic app.js script, so it is reachable
by bare name from page.evaluate (same realm) — a pure-logic unit test, no DOM.
"""


def test_effsup_prefers_order_supplier_over_assignment(page, live_server):
    page.goto(live_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")
    r = page.evaluate("""() => [
      effSup({supplier: 'REAL',  assignedSupplier: 'STALE'}),   // order supplier wins
      effSup({supplier: '',      assignedSupplier: 'ASSIGNED'}),// no order supplier → assignment fills in
      effSup({supplier: 'REAL',  assignedSupplier: ''}),        // only order supplier
      effSup({supplier: '',      assignedSupplier: ''}),        // neither → placeholder
    ]""")
    assert r == ["REAL", "ASSIGNED", "REAL", "—"], r
