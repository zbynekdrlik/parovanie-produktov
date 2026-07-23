"""Real-browser E2E of the per-order comment on the 'Na objednanie' tab (#101).

The manager writes a free-text comment about a whole order (mirroring the Shoptet
admin's "Poznámka e-shopu"); it persists in data/out/order_comments.json and shows as
a 💬 chip on every line of that order. The ORBIS fixture order (77/X = 20260700) also
carries a shopRemark, so the read-only 🛈 Shoptet note is asserted too. live_server is
session-scoped and shared, so the comment is cleared at the end.
"""

ORBIS_CODE = "77/X"           # itemCode of ORBIS's single fixture line
ORBIS_ORDER = "20260700"      # its orderCode — the comment key


def test_order_comment_write_persist_and_shopnote(page, live_server):
    console = []
    page.on("console", lambda m: console.append(f"[{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)
    row = f".toorder-row[data-code='{ORBIS_CODE}']"
    try:
        page.goto(live_server + "/?tab=toorder")
        page.wait_for_selector(".toorder-row")

        # read-only Shoptet note is surfaced on the ORBIS row
        note = page.locator(f"{row} .to-shopnote")
        assert note.count() == 1, "ORBIS row should show the read-only Shoptet note"
        assert "chýba" in note.inner_text()

        # no comment yet → the add button is present, no chip
        assert page.locator(f"{row} .to-comment").count() == 0
        page.locator(f"{row} .to-comadd").click()

        # editor opens → type a comment and save
        page.locator(f"{row} .to-cominput").fill("Objednané u dodávateľa")
        with page.expect_response("**/api/order-comment"):
            page.locator(f"{row} .to-comsave").click()

        # chip shows the saved comment
        chip = page.locator(f"{row} .to-comment")
        chip.wait_for(state="visible", timeout=3000)
        assert "Objednané u dodávateľa" in chip.inner_text()

        # persists across a full reload (read back from the store)
        page.reload()
        page.wait_for_selector(".toorder-row")
        chip2 = page.locator(f"{row} .to-comment")
        chip2.wait_for(state="visible", timeout=3000)
        assert "Objednané u dodávateľa" in chip2.inner_text()

        assert console == [], f"console not clean: {console}"
    finally:
        # restore the shared session store — never leave the fixture order commented
        page.request.post(live_server + "/api/order-comment",
                          data={"orderCode": ORBIS_ORDER, "comment": ""})
