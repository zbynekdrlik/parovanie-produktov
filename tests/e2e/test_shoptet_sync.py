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
