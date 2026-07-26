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
    # #devNav .tab — a bare "button" also matches the lightbulb's aria-label
    # substring AND (admin, #173) the ✏️ rename icon sitting beside the nav
    # button; .tab is the real nav-button class, always exactly one per key.
    assert page.locator(".sidebar #devNav .tab").count() == 1
    page.locator("#devNav .tab").click()

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
    # #243 — the dialog no longer just vanishes: it confirms the request landed (and
    # says which number it got), because the lightbulb is on every tab and a silent
    # close left the boss with nothing. Closing that panel closes the modal.
    page.wait_for_selector("#ideaDone:not([hidden])")
    page.locator("#ideaDoneClose").click()
    page.locator("#ideaModal").wait_for(state="hidden")

    # the new issue shows up in the Vývoj list (created issues are 'open').
    page.locator("#devNav .tab").click()
    page.wait_for_function(
        "() => [...document.querySelectorAll('#tab-dev .dev-row')]"
        ".some(r => r.textContent.includes('Napad z e2e testu'))")

    assert console == [], f"console not clean: {console}"


def test_issue_title_does_not_link_to_github(page, dev_server):
    """The boss never wants GitHub — the issue title is NOT a link out; clicking it
    opens the in-app detail box and stays on our app (no navigation to github.com)."""
    console = _console(page)
    page.goto(dev_server)
    page.wait_for_selector(".sidebar #tabs button")
    page.locator("#devNav .tab").click()
    page.wait_for_selector("#tab-dev .dev-row")

    # no anchor pointing at GitHub anywhere in the list
    assert page.locator('#tab-dev a[href*="github"]').count() == 0
    # the title is a plain (non-anchor) clickable element
    assert page.locator("#tab-dev .dev-row .dev-title").first.evaluate(
        "el => el.tagName.toLowerCase()") == "span"

    # clicking the open issue's title opens the in-app detail box, same URL
    before = page.url
    page.locator("#tab-dev .dev-row .dev-title.clickable").first.click()
    page.locator("#tab-dev .dev-row .dev-detail-box").first.wait_for(state="visible")
    assert page.url == before               # never navigated to GitHub

    assert console == [], f"console not clean: {console}"


def test_priority_soon_moves_issue_to_top_group(page, dev_server):
    """Boss marks an open issue „Čoskoro" → it lands in the „Riešiť čoskoro" group.
    GitHub stays hidden; the split is driven by the in-app priority."""
    console = _console(page)
    page.goto(dev_server)
    page.wait_for_selector(".sidebar #tabs button")
    page.locator("#devNav .tab").click()
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


def test_detail_shows_zadanie_and_existing_comments(page, dev_server):
    """Opening an issue's detail shows its text (zadanie) + ALL existing details —
    the boss reads everything in the app, GitHub hidden."""
    console = _console(page)
    page.goto(dev_server)
    page.wait_for_selector(".sidebar #tabs button")
    page.locator("#devNav .tab").click()
    page.wait_for_selector("#tab-dev .dev-row")

    row = page.locator("#tab-dev .dev-row").first          # #7, has 1 existing comment
    row.get_by_role("button", name="🔎 Detail / doplniť").click()
    box = row.locator(".dev-detail-box")
    box.wait_for(state="visible")
    page.wait_for_function(
        "() => document.querySelector('#tab-dev .dev-detail-box .dev-comment')")
    txt = box.inner_text()
    assert "Zadanie otvorenej úlohy" in txt                 # issue body shown
    assert "Prvý existujúci detail" in txt                  # existing comment shown

    assert console == [], f"console not clean: {console}"


def test_add_detail_note_appears_immediately(page, dev_server):
    """Boss writes a detail → it's saved (GitHub comment) AND immediately appears in
    the detail list (no more „it just vanished"), without leaving the app."""
    console = _console(page)
    page.goto(dev_server)
    page.wait_for_selector(".sidebar #tabs button")
    page.locator("#devNav .tab").click()
    page.wait_for_selector("#tab-dev .dev-row")

    row = page.locator("#tab-dev .dev-row").first
    row.get_by_role("button", name="🔎 Detail / doplniť").click()
    box = row.locator(".dev-detail-box")
    box.wait_for(state="visible")
    box.locator(".dev-note-ta").fill("Šéfov detail: toto treba spraviť takto.")
    with page.expect_response("**/api/dev/issue/7/note") as resp:
        box.get_by_role("button", name="Uložiť detail").click()
    assert resp.value.status == 201
    # the new detail SHOWS up in the list (re-fetched), not just a vanishing ✓
    page.wait_for_function(
        "() => [...document.querySelectorAll('#tab-dev .dev-comment-body')]"
        ".some(c => c.textContent.includes('toto treba spraviť takto'))")
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


# --- #243: correcting an already-submitted request -------------------------- #
# The boss could add a comment but never fix the request itself, so one that came out
# wrong was abandoned („chcel som mu ešte niečo dopísať – nedá sa, tak som sa na to
# vykašlal"), and the lightbulb closed with no confirmation at all.

def _open_dev_tab(page, dev_server):
    page.goto(dev_server)
    page.wait_for_selector(".sidebar #tabs button")
    page.locator("#devNav .tab").click()
    page.wait_for_selector("#tab-dev .dev-row")


def test_the_boss_can_correct_an_already_sent_request(page, dev_server):
    console = _console(page)
    _open_dev_tab(page, dev_server)

    row = page.locator('#tab-dev .dev-row[data-num="7"]')
    row.locator(".dev-title").click()                    # open the detail
    page.wait_for_selector('.dev-row[data-num="7"] .dev-detail-box .dev-detail-body')

    row.locator(".dev-edit-btn").click()
    page.wait_for_selector(".dev-edit-title")
    assert row.locator(".dev-edit-title").input_value() == "E2E otvorena uloha"
    assert "Zadanie otvorenej úlohy" in row.locator(".dev-edit-ta").input_value()

    row.locator(".dev-edit-title").fill("E2E opravena uloha")
    row.locator(".dev-edit-ta").fill("Opraveny text zadania, este som chcel doplnit toto.")
    with page.expect_response("**/api/dev/issue/7/edit") as resp:
        row.locator(".dev-note-save").click()
    assert resp.value.status == 200

    # the corrected NAME must show in the list (not only inside the detail), and the
    # detail must re-open showing the stored text — no reload, that is what he sees
    page.wait_for_function(
        "() => !!document.querySelector('.dev-row[data-num=\"7\"] .dev-title')"
        " && document.querySelector('.dev-row[data-num=\"7\"] .dev-title')"
        "        .textContent.includes('opravena')")
    body = page.locator('.dev-row[data-num="7"] .dev-detail-box .dev-detail-body')
    assert "este som chcel doplnit toto" in body.inner_text()

    page.reload()
    page.locator("#devNav .tab").click()
    page.wait_for_selector("#tab-dev .dev-row")
    assert "E2E opravena uloha" in page.locator('#tab-dev .dev-row[data-num="7"]').inner_text()

    assert console == [], f"console not clean: {console}"


def test_the_edit_form_never_asks_him_to_retype_the_apps_own_signature(page, dev_server):
    """A request created from the lightbulb carries `_Nápad cez appku (Vývoj) — …_`.
    That is bookkeeping, not his text: the form must not show it, and saving must not
    lose it either."""
    console = _console(page)
    page.goto(dev_server)
    page.wait_for_selector(".sidebar #tabs button")
    page.locator("#ideaBtn").click()
    page.locator("#ideaTitleInput").fill("Poziadavka na upravu")
    page.locator("#ideaDescInput").fill("Prvy text.")
    with page.expect_response("**/api/dev/idea") as resp:
        page.locator("#ideaSubmit").click()
    assert resp.value.status == 201
    num = resp.value.json()["issue"]["number"]

    page.locator("#ideaDoneClose").click()
    page.locator("#devNav .tab").click()
    page.wait_for_selector(f'#tab-dev .dev-row[data-num="{num}"]')
    row = page.locator(f'#tab-dev .dev-row[data-num="{num}"]')
    row.locator(".dev-title").click()
    page.wait_for_selector(f'.dev-row[data-num="{num}"] .dev-detail-body')
    # the stored body DOES carry the marker …
    assert "Nápad cez appku" in row.locator(".dev-detail-body").inner_text()

    row.locator(".dev-edit-btn").click()
    page.wait_for_selector(".dev-edit-ta")
    # … but what he edits is only his own text
    assert row.locator(".dev-edit-ta").input_value() == "Prvy text."

    row.locator(".dev-edit-ta").fill("Prvy text. A este toto.")
    with page.expect_response(f"**/api/dev/issue/{num}/edit") as resp:
        row.locator(".dev-note-save").click()
    assert resp.value.status == 200
    page.wait_for_selector(f'.dev-row[data-num="{num}"] .dev-detail-body')
    stored = row.locator(".dev-detail-body").inner_text()
    assert "A este toto." in stored
    assert "Nápad cez appku" in stored, "the author marker must survive an edit"

    assert console == [], f"console not clean: {console}"


def test_cancelling_an_edit_leaves_the_request_untouched(page, dev_server):
    console = _console(page)
    _open_dev_tab(page, dev_server)
    row = page.locator('#tab-dev .dev-row[data-num="7"]')
    row.locator(".dev-title").click()
    page.wait_for_selector('.dev-row[data-num="7"] .dev-detail-body')

    row.locator(".dev-edit-btn").click()
    page.wait_for_selector(".dev-edit-ta")
    row.locator(".dev-edit-ta").fill("nezmysel ktory nechcem ulozit")
    row.locator(".dev-edit-cancel").click()

    page.wait_for_selector('.dev-row[data-num="7"] .dev-detail-body')
    assert "Zadanie otvorenej úlohy" in row.locator(".dev-detail-body").inner_text()
    assert row.locator(".dev-edit-ta").count() == 0

    assert console == [], f"console not clean: {console}"


def test_a_closed_request_offers_no_edit_button(page, dev_server):
    """Rewriting a request somebody already acted on would be invisible to them."""
    console = _console(page)
    _open_dev_tab(page, dev_server)
    page.get_by_role("button", name="Hotové").click()
    page.wait_for_selector('#tab-dev .dev-row[data-num="6"]')
    row = page.locator('#tab-dev .dev-row[data-num="6"]')
    row.locator(".dev-title").click()
    page.wait_for_selector('.dev-row[data-num="6"] .dev-detail-body')
    assert row.locator(".dev-edit-btn").count() == 0

    assert console == [], f"console not clean: {console}"


def test_the_lightbulb_confirms_the_request_landed_and_opens_it(page, dev_server):
    """It used to just close, so from any tab other than „Vývoj" he got no number, no
    confirmation and no way back to what he had just sent."""
    console = _console(page)
    page.goto(dev_server + "/?tab=toorder")          # deliberately NOT the Vývoj tab
    page.wait_for_selector(".sidebar #tabs button")
    page.locator("#ideaBtn").click()
    page.locator("#ideaTitleInput").fill("Chcem to vidiet potvrdene")
    with page.expect_response("**/api/dev/idea") as resp:
        page.locator("#ideaSubmit").click()
    num = resp.value.json()["issue"]["number"]

    page.wait_for_selector("#ideaDone:not([hidden])")
    assert page.locator("#ideaForm").is_hidden()
    assert str(num) in page.locator("#ideaDoneText").inner_text()

    page.locator("#ideaDoneOpen").click()
    page.wait_for_selector(f'#tab-dev .dev-row[data-num="{num}"] .dev-detail-box')
    assert page.locator("#ideaModal").is_hidden()

    # re-opening the lightbulb must give the FORM back, not the confirmation panel
    page.locator("#ideaBtn").click()
    page.wait_for_selector("#ideaForm:not([hidden])")
    assert page.locator("#ideaDone").is_hidden()
    assert page.locator("#ideaTitleInput").input_value() == ""

    assert console == [], f"console not clean: {console}"
