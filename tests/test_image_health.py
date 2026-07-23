"""Pure-logic tests for the our_images dead-URL detector (#135).

Hermetic: no network, no Flask — the checker function itself is injected by
the caller (webreview/app.py), this module only holds the cache/decision
logic. Proves: a live URL stays, a dead one drops ONLY after the configured
number of CONSECUTIVE failures (a single transient blip never nukes a
previously-good image), a fresh cache entry is not re-checked, and a failing
entry is ALWAYS re-checked regardless of freshness.
"""
from datetime import datetime, timedelta, timezone

from parovanie import image_health as ih

NOW = datetime(2026, 7, 23, 8, 0, 0, tzinfo=timezone.utc)


def _iso(dt):
    return dt.isoformat(timespec="seconds")


# ── collect_image_urls ──────────────────────────────────────────────────────
def test_collect_image_urls_dedupes_across_products_preserving_order():
    products = [
        {"our_images": ["https://cdn/a.jpg", "https://cdn/b.jpg"]},
        {"our_images": ["https://cdn/b.jpg", "https://cdn/c.jpg"]},
        {"our_images": []},
        {},
    ]
    assert ih.collect_image_urls(products) == [
        "https://cdn/a.jpg", "https://cdn/b.jpg", "https://cdn/c.jpg"]


def test_collect_image_urls_skips_empty_strings():
    products = [{"our_images": ["", "https://cdn/a.jpg", None]}]
    assert ih.collect_image_urls(products) == ["https://cdn/a.jpg"]


# ── needs_check ──────────────────────────────────────────────────────────────
def test_needs_check_never_seen_url():
    assert ih.needs_check("https://cdn/a.jpg", {}, NOW) is True


def test_needs_check_false_for_recently_confirmed_ok():
    cache = {"https://cdn/a.jpg": {"ok": True, "fails": 0, "checked_at": _iso(NOW - timedelta(hours=1))}}
    assert ih.needs_check("https://cdn/a.jpg", cache, NOW) is False


def test_needs_check_true_once_stale_beyond_fresh_window():
    old = NOW - timedelta(hours=ih.FRESH_HOURS + 1)
    cache = {"https://cdn/a.jpg": {"ok": True, "fails": 0, "checked_at": _iso(old)}}
    assert ih.needs_check("https://cdn/a.jpg", cache, NOW) is True


def test_needs_check_always_true_when_last_result_was_a_failure():
    # even though it was JUST checked (well inside the freshness window)
    cache = {"https://cdn/a.jpg": {"ok": False, "fails": 1, "checked_at": _iso(NOW - timedelta(minutes=1))}}
    assert ih.needs_check("https://cdn/a.jpg", cache, NOW) is True


def test_needs_check_true_on_unparsable_checked_at():
    cache = {"https://cdn/a.jpg": {"ok": True, "fails": 0, "checked_at": "not-a-date"}}
    assert ih.needs_check("https://cdn/a.jpg", cache, NOW) is True


# ── record_result ────────────────────────────────────────────────────────────
def test_record_result_success_resets_fail_streak():
    cache = {"https://cdn/a.jpg": {"ok": False, "fails": 1, "checked_at": ""}}
    entry = ih.record_result(cache, "https://cdn/a.jpg", True, NOW)
    assert entry == {"ok": True, "fails": 0, "checked_at": _iso(NOW)}
    assert cache["https://cdn/a.jpg"] == entry


def test_record_result_failure_increments_streak():
    cache = {}
    ih.record_result(cache, "https://cdn/a.jpg", False, NOW)
    entry = ih.record_result(cache, "https://cdn/a.jpg", False, NOW + timedelta(hours=1))
    assert entry["fails"] == 2
    assert entry["ok"] is False


# ── is_dead / filter_dead ────────────────────────────────────────────────────
def test_is_dead_false_when_never_checked():
    assert ih.is_dead(None) is False


def test_is_dead_false_below_threshold():
    assert ih.is_dead({"ok": False, "fails": 1}) is False


def test_is_dead_true_at_threshold():
    assert ih.is_dead({"ok": False, "fails": 2}) is True


def test_transient_single_failure_does_not_drop_previously_good_image():
    cache = {}
    ih.record_result(cache, "https://cdn/a.jpg", True, NOW)               # was good
    ih.record_result(cache, "https://cdn/a.jpg", False, NOW + timedelta(days=1))  # 1 blip
    urls = ih.filter_dead(["https://cdn/a.jpg"], cache)
    assert urls == ["https://cdn/a.jpg"]     # still kept — only 1 consecutive failure


def test_dead_url_dropped_after_two_consecutive_failures():
    cache = {}
    ih.record_result(cache, "https://cdn/a.jpg", False, NOW)
    ih.record_result(cache, "https://cdn/a.jpg", False, NOW + timedelta(days=1))
    urls = ih.filter_dead(["https://cdn/a.jpg", "https://cdn/b.jpg"], cache)
    assert urls == ["https://cdn/b.jpg"]


def test_live_url_never_checked_is_kept():
    urls = ih.filter_dead(["https://cdn/a.jpg"], {})
    assert urls == ["https://cdn/a.jpg"]


# ── clean_products (serve-time view) ─────────────────────────────────────────
def test_clean_products_drops_dead_and_keeps_live_untouched_object():
    cache = {"https://cdn/dead.jpg": {"ok": False, "fails": 2, "checked_at": _iso(NOW)}}
    live_product = {"key": "P1", "our_images": ["https://cdn/live.jpg"]}
    dead_product = {"key": "P2", "our_images": ["https://cdn/dead.jpg", "https://cdn/live.jpg"]}
    no_images = {"key": "P3"}
    products = [live_product, dead_product, no_images]

    out, dropped = ih.clean_products(products, cache)

    assert dropped == 1
    assert out[0] is live_product                       # unchanged -> same object
    assert out[2] is no_images                           # unchanged -> same object
    assert out[1] is not dead_product                    # changed -> shallow copy
    assert out[1]["our_images"] == ["https://cdn/live.jpg"]
    assert dead_product["our_images"] == ["https://cdn/dead.jpg", "https://cdn/live.jpg"]  # source untouched


def test_clean_products_all_dead_yields_empty_list_not_missing_key():
    cache = {"https://cdn/dead.jpg": {"ok": False, "fails": 2, "checked_at": _iso(NOW)}}
    products = [{"key": "P1", "our_images": ["https://cdn/dead.jpg"]}]
    out, dropped = ih.clean_products(products, cache)
    assert out[0]["our_images"] == []
    assert dropped == 1


def test_clean_products_no_cache_entries_never_drops_anything():
    products = [{"key": "P1", "our_images": ["https://cdn/a.jpg", "https://cdn/b.jpg"]}]
    out, dropped = ih.clean_products(products, {})
    assert dropped == 0
    assert out[0] is products[0]
