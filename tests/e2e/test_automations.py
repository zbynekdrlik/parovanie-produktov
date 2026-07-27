"""E2E of the automations tab „Nevyzdvihnuté zásielky" (#93) — real Chromium.

Against the seeded automations_server (see conftest): no network, no SMTP —
the manual run finds 0 shipments (orders fixture has no packageNumber), so
clicking ⚡ Spustiť teraz is a hermetic green run.
"""


def _console(page):
    msgs = []
    page.on("console", lambda m: msgs.append(f"[{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)
    return msgs


def _open_tab(page, base):
    page.goto(base)
    page.wait_for_selector('[data-testid="version"]')
    page.get_by_role("button", name="Nevyzdvihnuté zásielky").click()
    page.wait_for_selector('[data-testid="posta-status"]')


def test_tab_renders_seeded_data_default_stopped(page, automations_server):
    console = _console(page)
    _open_tab(page, automations_server)

    # SAFETY: fresh deploy (no automations.json) = Zastavené, not running
    # (.pill renders uppercase via CSS — compare the raw DOM text)
    assert page.locator('[data-testid="posta-status"]').evaluate(
        "el => el.textContent") == "Zastavené"
    assert page.locator('[data-testid="posta-toggle"]').inner_text().strip() == "▶ Štart"

    # the uncollected shipment from the last run is listed with its details
    table = page.locator('[data-testid="posta-table"]')
    row = table.locator("tbody tr").first
    assert "EF000000002SK" in row.inner_text()
    assert "Ján Vzor" in row.inner_text()
    assert "2026-08-03" in row.inner_text()          # vyzdvihnúť do
    assert "2/4" in row.inner_text()                 # escalation progress
    # tracking + admin links point where they should
    assert row.locator('a[href*="posta.sk/sledovanie-zasielok"]').count() == 1
    assert row.locator('a[href*="vyhladavanie"]').count() == 1

    # the invalid-format package (the class that broke n8n) is flagged, not hidden
    inv = page.locator('[data-testid="posta-invalid"]')
    assert "06565700348274" in inv.inner_text()
    assert "nesledovateľným" in inv.inner_text()

    # sidebar nav badge counts the uncollected shipments
    badge = page.locator("#autoTabs .navcount")
    assert badge.inner_text() == "1"

    assert console == [], f"console not clean: {console}"


def test_start_stop_toggle_persists_across_reload(page, automations_server):
    console = _console(page)
    _open_tab(page, automations_server)

    # Štart → Beží (persisted enabled=true + next run shown)
    with page.expect_response("**/api/automations/posta_uncollected/toggle"):
        page.locator('[data-testid="posta-toggle"]').click()
    page.wait_for_function(
        "() => document.querySelector('[data-testid=posta-status]')"
        ".textContent === 'Beží'")
    assert "Ďalší beh" in page.locator(".autometa").inner_text()

    # survives a full reload (state file, not just the page)
    page.reload()
    page.wait_for_selector('[data-testid="version"]')
    page.get_by_role("button", name="Nevyzdvihnuté zásielky").click()
    page.wait_for_selector('[data-testid="posta-status"]')
    assert page.locator('[data-testid="posta-status"]').evaluate(
        "el => el.textContent") == "Beží"

    # Stop → Zastavené again (leave the fixture in its original state)
    with page.expect_response("**/api/automations/posta_uncollected/toggle"):
        page.locator('[data-testid="posta-toggle"]').click()
    page.wait_for_function(
        "() => document.querySelector('[data-testid=posta-status]')"
        ".textContent === 'Zastavené'")

    assert console == [], f"console not clean: {console}"


# ── visible failure indicator (#153) ─────────────────────────────────────────────
def test_failed_automation_shows_nav_warning_badge_without_opening_its_tab(page, automations_server):
    """The fixture seeds orders_reminder's LAST run as failed. The manager must see that from
    ANY page — this test deliberately opens 'Nevyzdvihnuté zásielky' (a DIFFERENT, healthy
    automation), never the orders_reminder tab itself, and still expects the ⚠ badge in the
    sidebar. This is exactly the #156-class silent failure #153 exists to surface."""
    console = _console(page)
    _open_tab(page, automations_server)   # 'Nevyzdvihnuté zásielky' — healthy, no ⚠ expected

    ord_btn = page.get_by_role("button", name="Pripomienky objednávok")
    assert ord_btn.locator(".navwarn").count() == 1
    posta_btn = page.get_by_role("button", name="Nevyzdvihnuté zásielky")
    assert posta_btn.locator(".navwarn").count() == 0

    assert console == [], f"console not clean: {console}"


def test_run_now_executes_and_reports_result(page, automations_server):
    console = _console(page)
    _open_tab(page, automations_server)

    # hermetic manual run: 0 shipments in the fixture orders cache → instant OK
    with page.expect_response("**/api/automations/posta_uncollected/run"):
        page.locator('[data-testid="posta-run"]').click()
    page.wait_for_function(
        "() => document.querySelector('.autometa') && "
        "document.querySelector('.autometa').textContent.includes('OK')",
        timeout=15000)
    meta = page.locator(".autometa").inner_text()
    assert "Posledný beh" in meta and "OK" in meta
    assert "Skontrolovaných zásielok: 0" in page.locator(".autostatus").inner_text()

    assert console == [], f"console not clean: {console}"


# ── #217 — náhľad eskalačného e-mailu PRED odoslaním ──────────────────────────────
def test_posta_preview_button_shows_the_escalation_mail_without_sending(page, automations_server):
    """Read-only preview of the NEXT escalation mail for an uncollected shipment. The endpoint
    writes nothing and makes no Pošta SK call, and the dialog has no „Odoslať" button — so
    opening it can never mail a customer."""
    console = _console(page)
    _open_tab(page, automations_server)

    row = page.locator('[data-testid="posta-table"] tr[data-pkg="EF000000002SK"]')
    assert row.count() == 1
    with page.expect_response("**/api/posta-uncollected/preview") as resp:
        row.locator('[data-testid="posta-preview-EF000000002SK"]').click()
    assert resp.value.status == 200

    modal = page.locator("#emModal")
    modal.wait_for(state="visible")
    assert "jan@example.com" in page.locator("#emRecipient").inner_text()
    # the seeded shipment already had 2 of the 4 mails → the preview is #3, the „last warning"
    assert "Posledné upozornenie" in page.locator("#emRecipient").inner_text()
    body = page.frame_locator("#emPreview").locator("body").inner_text()
    assert "EF000000002SK" in body and "Skalica 1" in body

    assert modal.locator("button").count() == 1          # no way to send from here
    modal.locator("#emClose").click()
    page.wait_for_selector("#emModal", state="hidden")
    assert console == [], f"console not clean: {console}"


def test_a_preview_instance_says_plainly_that_nothing_will_run_on_a_timer(
        page, automations_server):
    """Every e2e fixture server boots with WEBREVIEW_NO_SCHEDULER=1 — exactly the
    throwaway-preview shape from #262. The tab must SAY so: „Ďalší beh" is read from the
    persisted state file, so without this banner a blocked or switched-off instance shows
    healthy future run times while nothing will ever fire (review PR #265)."""
    console = _console(page)
    _open_tab(page, automations_server)

    warn = page.locator('[data-testid="sched-warn"]')
    assert warn.is_visible(), "no scheduler banner on an instance with no scheduler"
    assert "nespustia" in warn.inner_text()

    # …and it is not in the way on an ordinary tab
    page.get_by_role("button", name="Poznámky").first.click()
    page.wait_for_timeout(200)
    assert not warn.is_visible()

    assert console == [], f"console not clean: {console}"


def test_a_fail_closed_automation_state_shows_the_repair_message_not_a_healthy_tab(
        page, automations_server):
    """C4 (#265) makes the SERVER refuse to pretend a corrupt `automations.json` means
    „no automations configured": /api/automations answers 503 with a Slovak repair
    message. The tab used to render that as the clean first-run state anyway —
    `loadAutomations` ignored the status and `j.scheduler || 'running'` HID the banner,
    so the manager saw a healthy scheduler while every reminder mail, pošta escalation
    and hourly sync was off. Driven here through a real 503 on the wire (third review).
    """
    import json as _json
    page.route("**/api/automations", lambda route: route.fulfill(
        status=503, content_type="application/json",
        body=_json.dumps({"ok": False, "error": (
            "Stav automatizácií (automations.json) sa nedá prečítať "
            "(neúplný/poškodený zápis). Súbor NEMAŽ.")}, ensure_ascii=False)))

    page.goto(automations_server)
    page.wait_for_selector('[data-testid="version"]')
    page.get_by_role("button", name="Nevyzdvihnuté zásielky").click()

    warn = page.locator('[data-testid="sched-warn"]')
    warn.wait_for(state="visible")
    assert "NEMAŽ" in warn.inner_text(), warn.inner_text()


# ── #282 — a dead shipment source must be impossible to miss ─────────────────────
def test_degraded_source_run_is_not_shown_as_a_healthy_ok_run(page, posta_degraded_server):
    """The failure this exists for: for five days the card said „✅ OK" and „0 nevyzdvihnutých"
    while the automation could no longer see a single shipment, and a real parcel ran out its
    pickup deadline. The run did not crash, so `last_status` is legitimately 'ok' — what failed is
    the input, and THAT is what the manager has to see."""
    console = _console(page)
    _open_tab(page, posta_degraded_server)

    meta = page.locator(".autometa").inner_text()
    assert "DEGRADOVANÝ" in meta                     # …not the „✅ OK" that hid the outage
    assert "✅ OK" not in meta

    # the banner names the real numbers and what to go and check — no adjectives
    banner = page.locator(".autoerr").first.inner_text()
    assert "87 z 91" in banner
    assert "podacie číslo" in banner
    assert "spred 26 dní" in banner                  # „pred" + instrumental would be ungrammatical

    assert console == [], f"console not clean: {console}"


def test_degraded_source_lights_the_nav_badge_from_any_page(page, posta_degraded_server):
    """Same reasoning as the #153 badge: the manager must learn about it WITHOUT opening the
    automation's own tab. A run that cannot see its own input has failed, whether or not it threw
    — and this automation has no other push channel at all (#285)."""
    console = _console(page)
    page.goto(posta_degraded_server)
    page.wait_for_selector('[data-testid="version"]')

    posta_btn = page.get_by_role("button", name="Nevyzdvihnuté zásielky")
    assert posta_btn.locator(".navwarn").count() == 1

    assert console == [], f"console not clean: {console}"
