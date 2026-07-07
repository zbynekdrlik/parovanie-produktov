"""Real-browser E2E of the 'Na objednanie' supplier-chip colour coding (#86).

A supplier chip is RED (class `done`) once EVERY one of its order lines carries a
flag (objednané/počkať/skladom/nedostupné), GREEN (class `todo`) while any line is
still un-flagged, and ORANGE (class `active`) when it is the selected filter. The
fixture has ORBIS with a single line (77/X) and BETALOV with two (1/M, 2/M); flagging
ORBIS's only line as in-stock makes ORBIS fully resolved → red, while BETALOV stays
green. Colours are asserted via getComputedStyle so the scoped CSS is really applied,
not just the class. The seeded flag is removed at the end (session-scoped fixture)."""

ORBIS_KEY = "20260700|77/X"   # <orderCode>|<itemCode> of ORBIS's single fixture line


def _chip(page, name):
    return page.locator("#filters button").filter(has_text=name).first


def test_order_supplier_chips_colour_by_resolved_state(page, live_server):
    console = []
    page.on("console", lambda m: console.append(f"[{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)

    # Flag ORBIS's only line as 'skladom' → ORBIS becomes fully resolved (red).
    page.request.post(live_server + "/api/instock",
                      data={"key": ORBIS_KEY, "instock": True})
    try:
        page.goto(live_server + "/?tab=toorder")
        page.wait_for_selector(".toorder-row")

        orbis, betalov = _chip(page, "ORBIS"), _chip(page, "BETALOV")

        # ORBIS: every line flagged → done (red); BETALOV: still un-flagged → todo (green)
        assert "done" in (orbis.get_attribute("class") or ""), "ORBIS should be done (red)"
        assert "todo" in (betalov.get_attribute("class") or ""), "BETALOV should be todo (green)"

        red = orbis.evaluate("e => getComputedStyle(e).backgroundColor")
        green = betalov.evaluate("e => getComputedStyle(e).backgroundColor")
        assert red == "rgb(220, 38, 38)", f"ORBIS chip not red: {red}"
        assert green == "rgb(22, 163, 74)", f"BETALOV chip not green: {green}"

        # Clicking a chip makes it the active (orange) filter.
        betalov.click()
        page.wait_for_selector(".toorder-row")
        betalov = _chip(page, "BETALOV")
        assert "active" in (betalov.get_attribute("class") or "")
        orange = betalov.evaluate("e => getComputedStyle(e).backgroundColor")
        assert orange == "rgb(245, 158, 11)", f"active chip not orange: {orange}"

        assert console == [], f"console not clean: {console}"
    finally:
        # Restore the shared session store — never leave the fixture line flagged.
        page.request.post(live_server + "/api/instock",
                          data={"key": ORBIS_KEY, "instock": False})
