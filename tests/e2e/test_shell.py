"""E2E of the new sidebar-dashboard shell (redesign): left sidebar hosts the nav,
the top bar shows a per-page title, and the dark-mode toggle persists across reloads.
These assert the NEW shell behavior — written RED before the redesign existed."""


def _console(page):
    msgs = []
    page.on("console", lambda m: msgs.append(f"[{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)
    return msgs


def test_sidebar_hosts_nav_and_pagetitle_updates(page, live_server):
    console = _console(page)
    page.goto(live_server)
    page.wait_for_selector('[data-testid="version"]')

    # Nav lives INSIDE the left sidebar now (not a top tab bar).
    assert page.locator(".sidebar").count() == 1
    assert page.locator(".sidebar #tabs").count() == 1

    # Top bar carries a per-page title; 'Na objednanie' is the default page
    # (#117 — review moved to last-used, so it's no longer the landing tab).
    # #pageTitle ships a static 'Kontrola párovania' in the HTML that init()
    # async-overwrites with the default page; the version span is present from
    # first paint so it does NOT gate init completion. Wait for the rendered nav
    # (renderTabs runs inside the same render() as setPageHead) so the title
    # assertion can't race the static markup (flaky before this fix).
    page.wait_for_selector(".sidebar #tabs button")
    page.wait_for_function(
        "() => document.getElementById('pageTitle')"
        ".textContent.trim() === 'Na objednanie'")
    assert page.locator("#pageTitle").inner_text().strip() == "Na objednanie"

    # Switching pages via the sidebar nav updates the top-bar title.
    page.get_by_role("button", name="Kontrola párovania").click()
    page.wait_for_function(
        "() => document.getElementById('pageTitle')"
        ".textContent.trim() === 'Kontrola párovania'")

    assert console == [], f"console not clean: {console}"


def test_nav_order_has_review_last(page, live_server):
    """#117 — 'Kontrola párovania' becomes the least-used page, so it moves to
    the BOTTOM of the sidebar nav (below 'Na objednanie'/'Hľadať / opraviť'/
    'Poznámky'), not the top."""
    console = _console(page)
    page.goto(live_server)
    page.wait_for_selector('[data-testid="version"]')
    page.wait_for_selector(".sidebar #tabs button")

    # The 'Eshop' folder (#tabs) holds ONLY the work tabs now — 'Užívatelia'
    # was moved OUT to a standalone item at the bottom (#118 refinement), so
    # 'Kontrola párovania' is the LAST item inside the folder.
    labels = page.locator(".sidebar #tabs button .tlabel").all_inner_texts()
    assert labels == ["Na objednanie", "Nedostupné tovary", "Poľovnícke výstavy",
                       "Hľadať / opraviť", "Poznámky", "Kontrola párovania"], labels

    assert console == [], f"console not clean: {console}"


def test_nav_badges_show_counts_on_initial_load(page, live_server):
    """#112 — the 'Na objednanie' sidebar count badge must be populated on the
    VERY FIRST paint, before the tab is ever opened. It used to stay empty
    (the count read from data that only loaded lazily on first visit)."""
    console = _console(page)
    page.goto(live_server)
    page.wait_for_selector('[data-testid="version"]')

    # Do NOT click any tab — the badge must already carry a real count.
    badge = page.get_by_role("button", name="Na objednanie").locator(".navcount")
    badge.wait_for(state="visible")
    assert int(badge.inner_text()) >= 1

    assert console == [], f"console not clean: {console}"


def test_dark_mode_toggle_persists_across_reload(page, live_server):
    console = _console(page)
    page.goto(live_server)
    page.wait_for_selector("#themeBtn")

    # Default = light: no dark marker on <body>.
    assert page.locator('body[data-theme="dark"]').count() == 0

    # Toggle → dark, persisted to localStorage.
    page.locator("#themeBtn").click()
    page.wait_for_selector('body[data-theme="dark"]')
    assert page.evaluate("() => localStorage.getItem('theme')") == "dark"

    # Survives a full reload (the store, not just the in-page toggle).
    page.reload()
    page.wait_for_selector('body[data-theme="dark"]')

    # Toggle back → light again, persisted.
    page.locator("#themeBtn").click()
    page.wait_for_function(
        "() => !document.body.hasAttribute('data-theme') "
        "|| document.body.getAttribute('data-theme') !== 'dark'")
    assert page.evaluate("() => localStorage.getItem('theme')") == "light"

    assert console == [], f"console not clean: {console}"


def test_eshop_folder_groups_nav_and_collapses(page, live_server):
    """#118 — the sidebar nav is grouped under a collapsible 'Eshop' folder:
    the folder header exists, the work-tab nav lives NESTED under its body, and
    clicking the header collapses/expands the items with the state persisted in
    localStorage across a full reload (default = expanded)."""
    console = _console(page)
    page.goto(live_server)
    page.wait_for_selector('[data-testid="version"]')
    page.wait_for_selector(".sidebar #tabs button")

    # The 'Eshop' folder header exists in the sidebar.
    head = page.locator(".folder-head", has_text="Eshop")
    assert head.count() == 1

    # The work-tab nav (#tabs) is NESTED under the Eshop folder body.
    assert page.locator(".nav-folder #folder-eshop-body #tabs").count() == 1
    naobj = page.locator("#tabs button").filter(has_text="Na objednanie")
    body_disp = ("() => getComputedStyle(document.getElementById("
                 "'folder-eshop-body')).display")

    # Default = expanded: the folder body and its items are visible.
    assert page.evaluate(body_disp) != "none"
    assert naobj.first.is_visible()

    # Collapse: click the folder header → body hidden, state persisted.
    head.click()
    page.wait_for_function(f"{body_disp} === 'none'")
    assert not naobj.first.is_visible()
    assert page.evaluate("() => localStorage.getItem('folder:eshop')") == "collapsed"

    # Persists across a full reload (the store, not just the in-page toggle).
    # #tabs buttons are collapsed (display:none) now, so wait for them ATTACHED.
    page.reload()
    page.wait_for_selector('[data-testid="version"]')
    page.wait_for_selector(".sidebar #tabs button", state="attached")
    assert page.evaluate(body_disp) == "none"
    assert not page.locator("#tabs button").filter(
        has_text="Na objednanie").first.is_visible()

    # Expand again → items visible + 'open' persisted.
    page.locator(".folder-head", has_text="Eshop").click()
    page.wait_for_function(f"{body_disp} !== 'none'")
    assert page.locator("#tabs button").filter(
        has_text="Na objednanie").first.is_visible()
    assert page.evaluate("() => localStorage.getItem('folder:eshop')") == "open"

    assert console == [], f"console not clean: {console}"


def test_users_standalone_at_bottom_and_no_soon_section(page, live_server):
    """#118 refinement (Marek 2026-07-22):
      1. 'Užívatelia' (admin-only) is OUT of the 'Eshop' folder — a standalone
         nav item at the very bottom of the sidebar, above the theme/version
         footer (E2E runs as bootstrap admin, so it is present).
      2. The whole 'Čoskoro — rozšírime' section (heading + the four 'soon'
         placeholders) is GONE."""
    console = _console(page)
    page.goto(live_server)
    page.wait_for_selector('[data-testid="version"]')
    page.wait_for_selector(".sidebar #tabs button")

    # 'Užívatelia' exists (admin) but is NOT nested under the Eshop folder — it
    # lives in the dedicated standalone container at the sidebar bottom.
    assert page.get_by_role("button", name="Užívatelia").count() == 1
    assert page.locator("#usersNav button", has_text="Užívatelia").count() == 1
    assert page.locator(
        "#folder-eshop-body button", has_text="Užívatelia").count() == 0

    # The standalone users nav sits AFTER the folder and ABOVE the theme/version
    # footer (bottom of the sidebar).
    order_ok = page.evaluate(
        "() => {"
        " const sb = document.querySelector('.sidebar');"
        " const kids = [...sb.children];"
        " const folder = document.getElementById('folder-eshop');"
        " const un = document.getElementById('usersNav');"
        " const foot = document.querySelector('.sidefoot');"
        " const pos = n => kids.indexOf(n.closest('.sidebar > *')) ;"
        " return sb.contains(un) && pos(un) > pos(folder) && pos(un) < pos(foot);"
        "}")
    assert order_ok, "usersNav must be after the folder and before the footer"

    # No 'Čoskoro' heading and no 'soon' placeholder items remain anywhere.
    assert page.locator(".sidebar", has_text="Čoskoro").count() == 0
    assert page.locator(".soon").count() == 0
    assert page.locator(".soon-nav").count() == 0

    assert console == [], f"console not clean: {console}"


def test_favicon_present_and_generic_title(page, live_server):
    """#175 — the browser tab now carries a favicon (inline SVG data-URI from the
    brand shield) and a GENERIC title 'Forestshop' (not the per-view 'Kontrola
    párovania …') — the tool grew into a whole system, not just the pairing view."""
    console = _console(page)
    page.goto(live_server)
    page.wait_for_selector('[data-testid="version"]')

    # A favicon <link rel=icon> exists in the head, as an inline SVG data-URI.
    icon_href = page.evaluate(
        "() => { const l = document.querySelector(\"link[rel~='icon']\");"
        " return l ? l.getAttribute('href') : null; }")
    assert icon_href, "no <link rel='icon'> favicon in the head"
    assert icon_href.startswith("data:image/svg+xml"), icon_href

    # Browser-tab title is the generic app name, not the old per-view title.
    assert page.title() == "Forestshop", page.title()

    assert console == [], f"console not clean: {console}"


def test_hidden_tab_sections_are_display_none_on_review(page, live_server):
    """[hidden] must actually hide: #tab-search/#tab-notes carry display:flex in
    CSS, and an author display rule OVERRIDES the UA [hidden]{display:none} —
    so the catalog-search box bled into EVERY tab (review/notes/users). Locks
    the global [hidden] guard in place."""
    console = _console(page)
    page.goto(live_server)
    page.wait_for_selector('[data-testid="version"]')
    page.get_by_role("button", name="Kontrola párovania").click()
    for sec in ("tab-search", "tab-notes"):
        disp = page.evaluate(
            f"() => getComputedStyle(document.getElementById('{sec}')).display")
        assert disp == "none", f"#{sec} visible on the review tab (display={disp})"
    assert not page.locator("#searchBox").is_visible()
    assert console == [], f"console not clean: {console}"


def test_sidebar_resizer_drags_and_persists(page, live_server):
    """The left sidebar carries a drag grip on its right edge: dragging widens/narrows
    it, the chosen width persists across reloads (localStorage 'sideW'), and a
    double-click resets to the default width (clears the stored value)."""
    console = _console(page)
    page.set_viewport_size({"width": 1400, "height": 900})
    page.goto(live_server)
    page.wait_for_selector('[data-testid="version"]')

    grip = page.locator(".side-resizer")
    assert grip.count() == 1, "sidebar resize grip missing"

    def width():
        return page.evaluate(
            "() => parseInt(getComputedStyle(document.querySelector('.sidebar')).width, 10)")

    start = width()
    box = grip.bounding_box()
    # drag the grip right → the sidebar gets wider
    page.mouse.move(box["x"] + 4, box["y"] + 200)
    page.mouse.down()
    page.mouse.move(box["x"] + 4 + 120, box["y"] + 200, steps=10)
    page.mouse.up()
    wider = width()
    assert wider >= start + 80, f"drag did not widen the sidebar ({start} -> {wider})"
    assert page.evaluate("() => localStorage.getItem('sideW')") is not None

    # the width survives a reload
    page.reload()
    page.wait_for_selector('[data-testid="version"]')
    assert abs(width() - wider) <= 3, "resized width did not persist across reload"

    # double-click the grip resets to the default width (clears the stored width)
    box2 = page.locator(".side-resizer").bounding_box()
    page.mouse.dblclick(box2["x"] + 4, box2["y"] + 200)
    assert page.evaluate("() => localStorage.getItem('sideW')") is None
    assert console == [], f"console not clean: {console}"
