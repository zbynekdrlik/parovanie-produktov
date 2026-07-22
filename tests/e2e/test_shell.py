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
    page.wait_for_selector("#pageTitle")
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

    # E2E runs as the bootstrap admin, so 'Užívatelia' (admin-only, appended
    # after TABS regardless) trails everything — 'Kontrola párovania' is last
    # among the real work tabs, right before it.
    labels = page.locator(".sidebar #tabs button .tlabel").all_inner_texts()
    assert labels == ["Na objednanie", "Hľadať / opraviť", "Poznámky",
                       "Kontrola párovania", "Užívatelia"], labels

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
