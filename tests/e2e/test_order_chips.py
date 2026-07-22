"""Real-browser E2E of the 'Na objednanie' supplier-chip colour coding (#86).

A supplier chip is RED (class `done`) once EVERY one of its order lines carries a
flag (objednané/počkať/skladom/nedostupné), GREEN (class `todo`) while any line is
still un-flagged, and ORANGE (class `active`) when it is the selected filter. The
fixture has ORBIS with a single line (77/X) and BETALOV with two (1/M, 2/M); flagging
ORBIS's only line as in-stock makes ORBIS fully resolved → red, while BETALOV stays
green. Colours are asserted via getComputedStyle so the scoped CSS is really applied,
not just the class. The seeded flag is removed at the end (session-scoped fixture).

Colours softened for #143 (2026-07-22, exact hex from the boss):
green #6CAB68 = rgb(108,171,104), red #D14D3B = rgb(209,77,59),
orange (kept distinct, gently muted) #DDA43C = rgb(221,164,60)."""

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
        assert red == "rgb(209, 77, 59)", f"ORBIS chip not red: {red}"
        assert green == "rgb(108, 171, 104)", f"BETALOV chip not green: {green}"

        # Clicking a chip makes it the active (orange) filter.
        betalov.click()
        page.wait_for_selector(".toorder-row")
        betalov = _chip(page, "BETALOV")
        assert "active" in (betalov.get_attribute("class") or "")
        orange = betalov.evaluate("e => getComputedStyle(e).backgroundColor")
        assert orange == "rgb(221, 164, 60)", f"active chip not orange: {orange}"

        assert console == [], f"console not clean: {console}"
    finally:
        # Restore the shared session store — never leave the fixture line flagged.
        page.request.post(live_server + "/api/instock",
                          data={"key": ORBIS_KEY, "instock": False})


def test_order_chip_recolours_live_when_manager_flags_a_line(page, live_server):
    """The manager's real action: click a line's flag button IN-SESSION (no reload) →
    the supplier chip must recolour immediately. This guards the #86 core promise; the
    seed-before-load test above cannot (it never drives the UI toggle)."""
    console = []
    page.on("console", lambda m: console.append(f"[{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)
    try:
        page.goto(live_server + "/?tab=toorder")
        page.wait_for_selector(".toorder-row")

        orbis = _chip(page, "ORBIS")
        assert "todo" in (orbis.get_attribute("class") or ""), "ORBIS starts un-flagged (green)"

        # Click ORBIS's only line's '✓ Skladom' button — the exact manager action.
        page.locator(".toorder-row[data-code='77/X'] .to-instock").click()

        # The chip must flip to done (red) WITHOUT any page reload.
        page.wait_for_function(
            "() => { const b=[...document.querySelectorAll('#filters button')]"
            ".find(x=>/ORBIS/.test(x.textContent)); return b && b.className.includes('done'); }",
            timeout=3000)
        orbis = _chip(page, "ORBIS")
        assert "done" in (orbis.get_attribute("class") or ""), "ORBIS chip should recolour to done live"

        assert console == [], f"console not clean: {console}"
    finally:
        page.request.post(live_server + "/api/instock",
                          data={"key": ORBIS_KEY, "instock": False})
