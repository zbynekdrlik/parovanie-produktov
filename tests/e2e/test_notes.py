"""E2E for the '📝 Poznámky' tab (#83) — free-form notes, a Discord replacement for
ad-hoc reminders. Runs against the shared session `live_server`; the note this test
adds is deleted at the end so the shared fixture's notes.json is left empty for any
later test."""


def test_notes_add_persist_done_delete(page, live_server):
    console = []
    page.on("console", lambda m: console.append(f"[{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)
    page.on("dialog", lambda d: d.accept())   # accept the delete-confirm dialog

    page.goto(live_server + "/?tab=notes")
    page.wait_for_selector(".note-add textarea")

    ta = page.locator(".note-add textarea")
    ta.fill("objednať na výmenu betelavo")
    with page.expect_response(
            lambda r: "/api/notes" in r.url and r.request.method == "POST"):
        page.locator(".note-add button").click()

    page.wait_for_selector(".note")
    note = page.locator(".note").first
    assert "objednať na výmenu betelavo" in note.locator(".note-text").inner_text()
    assert "done" not in (note.get_attribute("class") or "")

    # Persists across reload (tab restored from localStorage via ?tab=notes URL).
    page.reload()
    page.wait_for_selector(".note-add textarea")
    page.wait_for_selector(".note")
    note = page.locator(".note").first
    assert "objednať na výmenu betelavo" in note.locator(".note-text").inner_text()

    # Toggle done → strikethrough class + persists.
    with page.expect_response(
            lambda r: "/api/note" in r.url and r.request.method == "POST"):
        note.locator(".note-done").click()
    assert "done" in (note.get_attribute("class") or "")

    # Delete it — leaves the shared fixture's notes.json empty for later tests.
    with page.expect_response(
            lambda r: "/api/note" in r.url and r.request.method == "POST"):
        note.locator(".note-del").click()
    assert page.locator(".note").count() == 0

    assert console == [], f"console not clean: {console}"
