"""E2E of the „Nedostupné tovary" tab (#100) — real Chromium.

Against the seeded nedostupne_server (a flagged product with two open orders + two relatedProduct
alternatives). Verifies the tab shows the flagged product with its affected customers + alternatives,
the two-state checkboxes persist across a reload, and the e-mail PREVIEW modal opens and lists the
recipients + renders the e-mail — WITHOUT sending anything (MAIL_HOST="" in the fixture is a further
safety net). Console must stay clean and the checkbox is toggled back at the end (leave-no-trace).
"""


def _console(page):
    msgs = []
    page.on("console", lambda m: msgs.append(f"[{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)
    return msgs


def _open_tab(page, base):
    page.goto(base)
    page.wait_for_selector('[data-testid="version"]')
    page.get_by_role("button", name="Nedostupné tovary").click()
    page.wait_for_selector('[data-testid="nd-card-ND1/M"]')


def test_tab_lists_flagged_product_customers_and_alternatives(page, nedostupne_server):
    console = _console(page)
    _open_tab(page, nedostupne_server)

    card = page.locator('[data-testid="nd-card-ND1/M"]')
    body = card.inner_text()
    assert "Nedostupný Kabát Test" in body
    # both customers with an OPEN order for the flagged variant are listed
    assert "ada@example.com" in body and "bob@example.com" in body
    assert "2 zákazníkov" in body
    # the assigned alternatives (relatedProduct*) resolved to their names
    assert "Alternatíva Bunda Test" in body and "Alternatíva Vesta Test" in body

    assert console == [], f"console not clean: {console}"


def test_two_state_checkbox_persists_across_reload(page, nedostupne_server):
    console = _console(page)
    _open_tab(page, nedostupne_server)

    cb = page.locator('[data-testid="nd-cb-nedostupne-ND1/M"]')
    assert cb.is_checked() is False
    with page.expect_response("**/api/nedostupne/state"):
        cb.check()
    # reload → the intent persisted server-side
    page.reload()
    page.get_by_role("button", name="Nedostupné tovary").click()
    page.wait_for_selector('[data-testid="nd-card-ND1/M"]')
    cb = page.locator('[data-testid="nd-cb-nedostupne-ND1/M"]')
    assert cb.is_checked() is True

    # leave no trace: toggle it back off
    with page.expect_response("**/api/nedostupne/state"):
        cb.uncheck()
    assert console == [], f"console not clean: {console}"


def test_preview_modal_lists_recipients_without_sending(page, nedostupne_server):
    console = _console(page)
    _open_tab(page, nedostupne_server)

    with page.expect_response("**/api/nedostupne/preview"):
        page.locator('[data-testid="nd-preview-nedostupne-ND1/M"]').click()
    page.wait_for_selector("#ndModal:not([hidden])")
    recips = page.locator("#ndRecipients")
    recips.wait_for()
    assert "ada@example.com" in recips.inner_text()
    assert "bob@example.com" in recips.inner_text()
    # the send button offers to send to BOTH (nothing sent yet — this is preview only)
    assert "(2)" in page.locator("#ndSend").inner_text()

    # close without sending
    page.locator("#ndCancel").click()
    page.wait_for_selector("#ndModal", state="hidden")
    assert console == [], f"console not clean: {console}"
