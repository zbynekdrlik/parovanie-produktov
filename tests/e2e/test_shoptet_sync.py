"""E2E of the automations tab „Sync zo Shoptetu" (#119) — real Chromium.

Against the seeded automations_server (see conftest — Shoptet creds point at a
nonexistent file, so no code path can reach the live shop): clicking ⚡ Spustiť
teraz hits the missing-credentials RuntimeError immediately — a hermetic,
network-free proof that the automation DEGRADES (❌ CHYBA shown, no crash, no
console error) instead of blowing up when creds are absent.
"""


def _console(page):
    msgs = []
    page.on("console", lambda m: msgs.append(f"[{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)
    return msgs


def _open_tab(page, base):
    page.goto(base)
    page.wait_for_selector('[data-testid="version"]')
    page.get_by_role("button", name="Sync zo Shoptetu").click()
    page.wait_for_selector('[data-testid="shoptet-sync-status"]')


def test_tab_renders_default_stopped(page, automations_server):
    console = _console(page)
    _open_tab(page, automations_server)

    # SAFETY: fresh deploy (no automations.json) = Zastavené, not running (#93 contract)
    assert page.locator('[data-testid="shoptet-sync-status"]').evaluate(
        "el => el.textContent") == "Zastavené"
    assert page.locator('[data-testid="shoptet-sync-toggle"]').inner_text().strip() == "▶ Štart"
    assert "každú hodinu" in page.locator(".autometa").inner_text()
    assert "zatiaľ nikdy" in page.locator(".autometa").inner_text()

    assert console == [], f"console not clean: {console}"


def test_start_stop_toggle_persists_across_reload(page, automations_server):
    console = _console(page)
    _open_tab(page, automations_server)

    with page.expect_response("**/api/automations/shoptet_sync/toggle"):
        page.locator('[data-testid="shoptet-sync-toggle"]').click()
    page.wait_for_function(
        "() => document.querySelector('[data-testid=shoptet-sync-status]')"
        ".textContent === 'Beží'")

    page.reload()
    page.wait_for_selector('[data-testid="version"]')
    page.get_by_role("button", name="Sync zo Shoptetu").click()
    page.wait_for_selector('[data-testid="shoptet-sync-status"]')
    assert page.locator('[data-testid="shoptet-sync-status"]').evaluate(
        "el => el.textContent") == "Beží"

    # Stop again — leave the fixture in its original state
    with page.expect_response("**/api/automations/shoptet_sync/toggle"):
        page.locator('[data-testid="shoptet-sync-toggle"]').click()
    page.wait_for_function(
        "() => document.querySelector('[data-testid=shoptet-sync-status]')"
        ".textContent === 'Zastavené'")

    assert console == [], f"console not clean: {console}"


def test_run_now_missing_creds_degrades_gracefully(page, automations_server):
    console = _console(page)
    _open_tab(page, automations_server)

    with page.expect_response("**/api/automations/shoptet_sync/run"):
        page.locator('[data-testid="shoptet-sync-run"]').click()
    page.wait_for_function(
        "() => document.querySelector('.autometa') && "
        "document.querySelector('.autometa').textContent.includes('CHYBA')",
        timeout=15000)
    meta = page.locator(".autometa").inner_text()
    assert "Posledný beh" in meta and "CHYBA" in meta
    err = page.locator(".autoerr").inner_text()
    assert "SHOPTET_ORDERS_URL" in err

    # the app itself survived (no crash) — other tabs stay usable
    page.get_by_role("button", name="Nevyzdvihnuté zásielky").click()
    page.wait_for_selector('[data-testid="posta-status"]')

    assert console == [], f"console not clean: {console}"


# ── #280 review: a NON-FATAL degradation must be visible on the card ──────────
def test_a_degraded_sync_announces_it_instead_of_reading_as_a_clean_run(
        page, automations_server):
    """`export_error` (a refused catalogue download, #280 review MUST FIX 2) and the
    pre-existing `customers_error` are both surfaced in the run result and were both
    rendered NOWHERE — the run showed last_status ok with a normal counts line, i.e.
    indistinguishable from a healthy hour. That is the „quietly dead automation" the
    playbook warns about: fail-soft on the server is only half a fix unless the tab
    says so.

    Driven through the real page globals (the playbook's pure-JS pattern)."""
    console = _console(page)
    _open_tab(page, automations_server)

    def render(extra):
        return page.evaluate(
            """(extra) => {
                 const saved = AUTOMATIONS;
                 AUTOMATIONS = [{key: 'shoptet_sync', name: 'Sync zo Shoptetu',
                   enabled: true, running: false, schedule: 'každú hodinu',
                   last_run: '2026-07-27T20:00:00+00:00', last_status: 'ok',
                   last_result: Object.assign({orders_bytes: 1000, catalog_products: 4378,
                     catalog_codes: 14066, review_synced: 12, review_stale: 0,
                     customers_bytes: 500}, extra)}];
                 renderShoptetSync();
                 const txt = document.getElementById('tab-shoptet_sync').innerText;
                 AUTOMATIONS = saved; renderShoptetSync();
                 return txt;
               }""", extra)

    exp = render({"export_error": "stiahnutý export katalógu je nepravdepodobne malý "
                                  "(10 B oproti 8585 B na disku, limit 4292 B)"})
    assert "nepravdepodobne malý" in exp, "a refused export download renders nowhere"
    assert "katalóg" in exp.lower()

    cus = render({"customers_error": "stiahnutie exportu zákazníkov zlyhalo: ConnectionError"})
    assert "ConnectionError" in cus, "a failed customer export renders nowhere"

    # …and a healthy run says nothing of the sort
    ok = render({})
    assert "nepravdepodobne" not in ok and "zlyhalo" not in ok

    assert console == [], f"console not clean: {console}"


# ── #293: a permanently refusing prune must not be SILENT ─────────────────────

def _open_sync_tab(page, base):
    page.goto(base)
    page.wait_for_selector('[data-testid="version"]')
    page.get_by_role("button", name="Sync zo Shoptetu").click()
    page.wait_for_selector('[data-testid="shoptet-sync-status"]')


def test_a_refused_prune_shows_a_banner_and_does_not_read_as_a_healthy_run(
        page, sync_prune_blocked_server):
    """The whole point of refusing to prune is safety, and the whole cost of it is silence:
    `no-open-orders` is a permanent state, so until the export is fixed the stores grow
    exactly as they did before #212 while the card stays green.

    So the card must say three things: that the run was DEGRADED (not „✅ OK"), what the
    prune refused on WITH the numbers it fired on, and what the manager should go and look
    at (`.claude/rules/automation-health.md` §3, steps 3+4)."""
    console = _console(page)
    _open_sync_tab(page, sync_prune_blocked_server)

    meta = page.locator(".autometa").inner_text()
    assert "⚠️ DEGRADOVANÝ" in meta, meta
    assert "✅ OK" not in meta, meta

    banner = page.locator(".autostatus .autoerr").inner_text()
    assert "Upratovanie starých značiek" in banner, banner
    assert "521" in banner, banner            # the number the refusal fired on
    assert "Vybavuje sa" in banner, banner    # what to go and check in Shoptet

    assert console == [], f"console not clean: {console}"


def test_a_refused_prune_lights_the_sidebar_warning_badge(page, sync_prune_blocked_server):
    """Step 4 of the same rule: without the ⚠ in the sidebar the manager only learns about it
    if he happens to open this one tab — which is exactly why #153 exists."""
    console = _console(page)
    _open_sync_tab(page, sync_prune_blocked_server)

    badge = page.locator('.tabs .navrow:has-text("Sync zo Shoptetu") .navwarn')
    assert badge.count() == 1, page.locator(".tabs").inner_text()

    assert console == [], f"console not clean: {console}"


def test_a_healthy_run_reports_how_many_marks_the_prune_removed(page, automations_server):
    """The prune is the one thing in this automation that DELETES the manager's markings, so
    the count belongs in front of him and not only in the log. A run that removed nothing is
    still reported — „0" is the normal, reassuring answer, and its absence is what let the
    refusal hide."""
    console = _console(page)
    _open_sync_tab(page, automations_server)

    page.evaluate("""() => {
      const a = AUTOMATIONS.find(x => x.key === 'shoptet_sync');
      a.last_run = '2026-07-28T09:00:05+02:00';
      a.last_status = 'ok';
      a.last_result = {orders_bytes: 1234567, catalog_products: 4321, catalog_codes: 8765,
                       review_synced: 120, review_stale: 0, flags_pruned: 22,
                       flags_orders_seen: 521, flags_orders_open: 57,
                       flags_unknown_statuses: ['Osob. odber', 'Vratený tovar']};
      renderShoptetSync();
    }""")

    txt = page.locator(".autostatus").inner_text()
    assert "vyčistené osirelé značky: 22" in txt, txt
    # the honest cost of the allow-list: a status nobody taught it about silently stops
    # being pruned, so it is named rather than hidden
    assert "Osob. odber" in txt and "Vratený tovar" in txt, txt
    assert page.locator(".autostatus .autoerr").count() == 0, txt

    assert console == [], f"console not clean: {console}"
