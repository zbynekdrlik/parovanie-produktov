"""E2E of the „Vývoj" tab + idea lightbulb (#115). Hermetic: the app is pointed at
a local GitHub stub (dev_server fixture), so no real GitHub / token is involved.

- The standalone 'Vývoj' nav (bottom of the sidebar) lists this repo's issues,
  showing BOTH open and closed (so the boss sees what's already done); PRs are
  filtered out.
- The lightbulb (bottom-right) opens a form; submitting creates a GitHub issue that
  then appears in the list."""


def _console(page):
    msgs = []
    page.on("console", lambda m: msgs.append(f"[{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)
    return msgs


def test_vyvoj_tab_lists_issues_open_and_closed(page, dev_server):
    console = _console(page)
    page.goto(dev_server)
    page.wait_for_selector(".sidebar #tabs button")

    # 'Vývoj' is a standalone nav at the very bottom (like 'Užívatelia'). Scope to
    # #devNav — the label substring also matches the lightbulb's aria-label.
    assert page.locator(".sidebar #devNav button").count() == 1
    page.locator("#devNav button").click()

    # default filter is 'Otvorené' → the open issue is shown.
    page.wait_for_selector("#tab-dev .dev-row")
    # switch to 'Všetky' to see open + closed together.
    page.get_by_role("button", name="Všetky").click()
    page.wait_for_function(
        "() => document.querySelectorAll('#tab-dev .dev-row').length === 2")

    joined = "\n".join(page.locator("#tab-dev .dev-row").all_inner_texts())
    assert "E2E otvorena uloha" in joined       # open issue shown
    assert "E2E hotova uloha" in joined         # closed issue shown (already done)
    assert "pull request" not in joined.lower()  # PR #5 filtered out
    # one open + one closed state pill (class-based — robust vs CSS text-transform)
    assert page.locator("#tab-dev .dev-state.open").count() == 1
    assert page.locator("#tab-dev .dev-state.done").count() == 1

    assert console == [], f"console not clean: {console}"


def test_lightbulb_creates_idea_that_appears_in_list(page, dev_server):
    console = _console(page)
    page.goto(dev_server)
    page.wait_for_selector(".sidebar #tabs button")

    # The lightbulb (present on every tab) opens the idea form.
    page.locator("#ideaBtn").click()
    page.locator("#ideaModal").wait_for(state="visible")
    page.fill("#ideaTitleInput", "Napad z e2e testu")
    page.fill("#ideaDescInput", "podrobnosti")
    with page.expect_response("**/api/dev/idea") as resp:
        page.locator("#ideaSubmit").click()
    assert resp.value.status == 201
    # modal closes on success
    page.locator("#ideaModal").wait_for(state="hidden")

    # the new issue shows up in the Vývoj list (created issues are 'open').
    page.locator("#devNav button").click()
    page.wait_for_function(
        "() => [...document.querySelectorAll('#tab-dev .dev-row')]"
        ".some(r => r.textContent.includes('Napad z e2e testu'))")

    assert console == [], f"console not clean: {console}"


def test_priority_soon_moves_issue_to_top_group(page, dev_server):
    """Boss marks an open issue „Čoskoro" → it lands in the „Riešiť čoskoro" group.
    GitHub stays hidden; the split is driven by the in-app priority."""
    console = _console(page)
    page.goto(dev_server)
    page.wait_for_selector(".sidebar #tabs button")
    page.locator("#devNav button").click()
    page.wait_for_selector("#tab-dev .dev-row")

    # the open issue (#7) has a „🔴 Čoskoro" button; click it → POST priority
    row = page.locator("#tab-dev .dev-row").first
    with page.expect_response("**/api/dev/issue/7/priority") as resp:
        row.get_by_role("button", name="🔴 Čoskoro").click()
    assert resp.value.status == 200

    # after refresh the issue is under the „Riešiť čoskoro" group + carries prio-soon
    page.wait_for_selector("#tab-dev .dev-group.soon")
    page.wait_for_selector("#tab-dev .dev-row.prio-soon")
    # the raw label name is NEVER shown to the boss
    joined = "\n".join(page.locator("#tab-dev .dev-row").all_inner_texts())
    assert "prio:soon" not in joined

    assert console == [], f"console not clean: {console}"


def test_add_detail_note_saves_to_issue(page, dev_server):
    """Boss writes a detail under an issue → it's saved (GitHub comment) and the
    confirmation shows, without ever leaving the app or seeing GitHub."""
    console = _console(page)
    page.goto(dev_server)
    page.wait_for_selector(".sidebar #tabs button")
    page.locator("#devNav button").click()
    page.wait_for_selector("#tab-dev .dev-row")

    row = page.locator("#tab-dev .dev-row").first
    row.get_by_role("button", name="➕ Doplniť detail").click()
    box = row.locator(".dev-note-box")
    box.wait_for(state="visible")
    box.locator(".dev-note-ta").fill("Šéfov detail: toto treba spraviť takto.")
    with page.expect_response("**/api/dev/issue/7/note") as resp:
        box.get_by_role("button", name="Uložiť detail").click()
    assert resp.value.status == 201
    # inline confirmation, no navigation to GitHub
    page.wait_for_selector("#tab-dev .dev-note-msg.ok")
    assert "github" not in page.url.lower()

    assert console == [], f"console not clean: {console}"


def test_empty_title_shows_inline_error_no_issue(page, dev_server):
    console = _console(page)
    page.goto(dev_server)
    page.wait_for_selector(".sidebar #tabs button")

    page.locator("#ideaBtn").click()
    page.locator("#ideaModal").wait_for(state="visible")
    # submit with an empty title → client-side inline error, modal stays open
    page.locator("#ideaSubmit").click()
    page.wait_for_selector("#ideaMsg:not([hidden])")
    assert page.locator("#ideaModal").is_visible()
    # close without creating anything
    page.locator("#ideaCancel").click()
    page.locator("#ideaModal").wait_for(state="hidden")

    assert console == [], f"console not clean: {console}"
