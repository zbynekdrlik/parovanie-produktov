"""E2E of the automations tab „Párovania → eshop" (#109) — real Chromium.

Against the seeded automations_server (see conftest — no decisions.json and no
supplier_assignments.json, Shoptet creds point at a nonexistent file): the tab
defaults to Zastavené (#93 safety), Štart/Stop persists across a reload, and a
⚡ Spustiť teraz run finds 0 new pairings + 0 new suppliers, so it calls NO
Shoptet import at all (hermetic, network-free) and reports a clean OK — never
touching the live eshop.
"""


def _console(page):
    msgs = []
    page.on("console", lambda m: msgs.append(f"[{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)
    return msgs


def _open_tab(page, base):
    page.goto(base)
    page.wait_for_selector('[data-testid="version"]')
    page.get_by_role("button", name="Párovania → eshop").click()
    page.wait_for_selector('[data-testid="parovania-status"]')


def test_tab_renders_default_stopped(page, automations_server):
    console = _console(page)
    _open_tab(page, automations_server)

    # SAFETY: fresh deploy (no automations.json) = Zastavené, not running (#93 contract)
    assert page.locator('[data-testid="parovania-status"]').evaluate(
        "el => el.textContent") == "Zastavené"
    assert page.locator('[data-testid="parovania-toggle"]').inner_text().strip() == "▶ Štart"
    assert "denne o 21:00" in page.locator(".autometa").inner_text()
    assert "zatiaľ nikdy" in page.locator(".autometa").inner_text()

    assert console == [], f"console not clean: {console}"


def test_start_stop_toggle_persists_across_reload(page, automations_server):
    console = _console(page)
    _open_tab(page, automations_server)

    with page.expect_response("**/api/automations/parovania_eshop/toggle"):
        page.locator('[data-testid="parovania-toggle"]').click()
    page.wait_for_function(
        "() => document.querySelector('[data-testid=parovania-status]')"
        ".textContent === 'Beží'")

    page.reload()
    page.wait_for_selector('[data-testid="version"]')
    page.get_by_role("button", name="Párovania → eshop").click()
    page.wait_for_selector('[data-testid="parovania-status"]')
    assert page.locator('[data-testid="parovania-status"]').evaluate(
        "el => el.textContent") == "Beží"

    # Stop again — leave the fixture in its original state
    with page.expect_response("**/api/automations/parovania_eshop/toggle"):
        page.locator('[data-testid="parovania-toggle"]').click()
    page.wait_for_function(
        "() => document.querySelector('[data-testid=parovania-status]')"
        ".textContent === 'Zastavené'")

    assert console == [], f"console not clean: {console}"


def test_run_now_zero_new_reports_ok_without_touching_eshop(page, automations_server):
    console = _console(page)
    _open_tab(page, automations_server)

    with page.expect_response("**/api/automations/parovania_eshop/run"):
        page.locator('[data-testid="parovania-run"]').click()
    # no decisions/assignments seeded → 0 new to push → no Shoptet import → clean OK
    page.wait_for_function(
        "() => document.querySelector('.autometa') && "
        "document.querySelector('.autometa').textContent.includes('OK')",
        timeout=15000)
    meta = page.locator(".autometa").inner_text()
    assert "Posledný beh" in meta and "OK" in meta and "CHYBA" not in meta

    # #38: the result box also reports the inline order_pairings push (0 seeded here)
    result_text = page.locator(".autoresult").inner_text()
    assert "Inline páry" in result_text and "+0 nových" in result_text
    assert "zaradených do frontu" in result_text          # #299 Task 10 — queues, not uploads

    # PR #271 review: the ONE fact this producer can still honestly report — how many
    # rows the eshop already had exactly as we would write them, credited from its own
    # export without going through the queue. #299 opravné kolo 1 review I1 — the card
    # used to ALSO claim "Shoptet odmietol N riadkov" here, but since Task 10 this
    # producer only queues and never imports, so it cannot know what Shoptet rejects —
    # that verdict belongs to the hourly "Sync do Shoptetu" drain, not this card.
    assert "potvrdené z exportu" in result_text
    assert "Shoptet odmietol" not in result_text

    # the app itself survived (no crash) — other tabs stay usable
    page.get_by_role("button", name="Nevyzdvihnuté zásielky").click()
    page.wait_for_selector('[data-testid="posta-status"]')

    assert console == [], f"console not clean: {console}"


# ── #280 review, item 3: a blocked run must name the REAL cause ────────────────
def test_a_gate_blocked_run_names_the_export_not_missing_codes(page, automations_server):
    """Before the fix every gate block rendered „N zablokovaných (chýbajú kódy)", which
    is the wrong cause: nothing is missing, the export is simply not believable (too
    small, too old, or absent). #277 widens the blocking band from <1000 to <7033 codes,
    so the manager WILL meet this state and must be able to act on it.

    Driven through the real page globals — app.js is a plain <script>, so its functions
    and `let` globals are reachable from page.evaluate (the playbook's pure-JS pattern)."""
    console = _console(page)
    _open_tab(page, automations_server)

    def render(gate):
        return page.evaluate(
            """(gate) => {
                 const saved = AUTOMATIONS;
                 AUTOMATIONS = [{key: 'parovania_eshop', name: 'Párovania → eshop',
                   enabled: false, running: false, schedule: 'denne o 21:00',
                   last_run: '2026-07-27T21:00:00+00:00', last_status: 'ok',
                   last_result: {status: 'blocked',
                     pairings: {count: 0, queued: 0, total_uploaded: 0, total_products: 0,
                                remaining: 0, blocked: 0, order_count: 0,
                                order_blocked: 0, missing_count: 0, missing_in_eshop: [],
                                confirmed_in_export: 0,
                                ok: true, error: ''},
                     suppliers: {count: 0, queued: 0, total_uploaded: 0, total_assigned: 3,
                                 remaining: 3, blocked: 3, missing_count: 0,
                                 missing_in_eshop: [], gate_blocked: gate,
                                 ok: true, error: ''}}}];
                 renderParovaniaEshop();
                 const txt = document.getElementById('tab-parovania_eshop').innerText;
                 AUTOMATIONS = saved; renderParovaniaEshop();
                 return txt;
               }""", gate)

    stale = render({"reason": "stale", "codes": 14066, "min_codes": 7033,
                    "age_h": 31.5, "max_age_h": 6.0})
    assert "chýbajú kódy" not in stale, "still blames missing codes on a stale export"
    assert "3 zablokovaných" in stale
    assert "starý" in stale and "31.5" in stale       # says WHAT is wrong, with the number

    small = render({"reason": "small", "codes": 312, "min_codes": 7033,
                    "age_h": 1.0, "max_age_h": 6.0})
    assert "chýbajú kódy" not in small
    assert "312" in small and "7033" in small
    assert "neúplný" in small

    missing = render({"reason": "missing", "codes": 0, "min_codes": 7033,
                      "age_h": None, "max_age_h": 6.0})
    assert "chýbajú kódy" not in missing
    assert "chýba" in missing or "prázdny" in missing

    # …and a run blocked by genuinely missing CODES still says exactly that
    codes = render(None)
    assert "chýbajú kódy" in codes

    assert console == [], f"console not clean: {console}"
