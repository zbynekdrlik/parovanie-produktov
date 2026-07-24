"""E2E for the two Na-objednanie improvements.

VYLEPŠENIE 1 — bulk „označiť skupinu objednané": the supplier-group header carries
a button that marks EVERY line of that supplier ordered in one atomic write; it
persists after reload.

VYLEPŠENIE 2 — ⚠️ alert on old un-handled orders: an order older than 14 days that
the manager has NOT resolved shows a „⚠️ X dní" badge so it doesn't sink out of sight;
a handled or fresh order shows none. `orderAgeDays` is a top-level pure helper in the
classic app.js script → unit-tested by bare name via page.evaluate.

Both drive the session-scoped `live_server`; every seeded flag is cleaned up.
"""

BETALOV_KEYS = ["20260900|1/M", "20260750|2/M"]   # the two BETALOV fixture lines
ORBIS_KEY = "20260700|77/X"                        # ORBIS single line (2026-04-24, old)


def _clear_flags(page, base, key):
    for path, field in (("ordered", "ordered"), ("waiting", "waiting"),
                        ("instock", "instock"), ("unavailable", "unavailable")):
        page.request.post(f"{base}/api/{path}", data={"key": key, field: False})


# ── VYLEPŠENIE 1 ────────────────────────────────────────────────────────────────
def test_bulk_mark_supplier_group_ordered(page, live_server):
    console = []
    page.on("console", lambda m: console.append(f"[{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)
    for k in BETALOV_KEYS:
        _clear_flags(page, live_server, k)
    try:
        page.goto(live_server + "/?tab=toorder")
        page.wait_for_selector(".toorder-row")

        # BETALOV group header's bulk button (label span comes first → has_text matches)
        header = page.locator(".toorder-supplier").filter(has_text="BETALOV").first
        header.locator(".tosup-bulk").click()

        # both BETALOV lines become ordered (row .done)
        for k in BETALOV_KEYS:
            page.wait_for_selector(f".toorder-row[data-key='{k}'].done")

        # persists across a reload
        page.reload()
        page.wait_for_selector(".toorder-row")
        for k in BETALOV_KEYS:
            assert page.locator(f".toorder-row[data-key='{k}'].done").count() == 1, k

        assert console == [], f"console not clean: {console}"
    finally:
        for k in BETALOV_KEYS:
            _clear_flags(page, live_server, k)


# ── VYLEPŠENIE 2 ────────────────────────────────────────────────────────────────
def test_order_age_days_helper(page, live_server):
    page.goto(live_server + "/?tab=toorder")
    page.wait_for_selector(".toorder-row")
    r = page.evaluate("""() => {
      const now = Date.parse('2026-07-24T00:00:00');
      return [
        orderAgeDays('2026-07-01', now),   // 23 days → stale
        orderAgeDays('2026-07-20', now),   // 4 days → fresh
        orderAgeDays('2026-08-01', now),   // future → 0
        orderAgeDays('', now),             // empty → 0
        orderAgeDays('nonsense', now),     // invalid → 0
      ];
    }""")
    assert r == [23, 4, 0, 0, 0], r


def test_stale_badge_on_old_unhandled_order(page, live_server):
    console = []
    page.on("console", lambda m: console.append(f"[{m.type}] {m.text}")
            if m.type in ("error", "warning") else None)
    _clear_flags(page, live_server, ORBIS_KEY)   # ensure it starts un-handled
    try:
        page.goto(live_server + "/?tab=toorder")
        page.wait_for_selector(".toorder-row")

        # ORBIS 77/X (2026-04-24) is far older than 14 days & un-handled → ⚠️ badge
        row = page.locator(f".toorder-row[data-key='{ORBIS_KEY}']")
        badge = row.locator(".to-staleage")
        assert badge.count() == 1
        assert "dní" in badge.inner_text()
        assert "stale" in (row.get_attribute("class") or "")

        # resolving it (skladom) removes the badge — handled orders never nag
        page.request.post(live_server + "/api/instock",
                          data={"key": ORBIS_KEY, "instock": True})
        page.reload()
        page.wait_for_selector(".toorder-row")
        assert page.locator(f".toorder-row[data-key='{ORBIS_KEY}'] .to-staleage").count() == 0

        assert console == [], f"console not clean: {console}"
    finally:
        _clear_flags(page, live_server, ORBIS_KEY)
