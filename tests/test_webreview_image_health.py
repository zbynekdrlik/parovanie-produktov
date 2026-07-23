"""our_images dead-URL validation automation (#135) — Flask wiring: run function,
store, registration, wired through the generic automation runner (#93), and the
/api/products serve-time filter that actually stops the browser ever requesting a
confirmed-dead URL.

Hermetic: the network check (_check_image_url) is monkeypatched — NO real HTTP.
PRODUCTS is set in-memory; the image_health + automations stores and the 5 manager
decision stores are redirected to tmp. Mirrors test_webreview_riziko_vypadku.py's
isolation pattern.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webreview"))
import app as webapp  # noqa: E402

from tests.conftest import authed_client  # noqa: E402

_MANAGER_STORES = (("DECISIONS", "decisions.json"), ("ORDERED", "ordered_items.json"),
                   ("WAITING", "waiting_items.json"),
                   ("ORDER_PAIRINGS", "order_pairings.json"),
                   ("SUPPLIER_ASSIGN", "supplier_assignments.json"))


def _product(key, images):
    return {"key": key, "supplier": "TESTSUP", "name": key, "our_images": images}


@pytest.fixture
def iso(tmp_path, monkeypatch):
    """Isolate every store this automation touches + the network edge."""
    monkeypatch.setattr(webapp.RUNNER, "state_path", str(tmp_path / "automations.json"))
    monkeypatch.setattr(webapp, "IMAGE_HEALTH_STATE", str(tmp_path / "image_health.json"))
    dec_path = tmp_path / "decisions.json"
    dec_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(webapp, "DECISIONS", str(dec_path))

    sentinels = {}
    for name, fname in _MANAGER_STORES:
        if name == "DECISIONS":
            continue
        p = tmp_path / fname
        p.write_text('{"sentinel": true}', encoding="utf-8")
        monkeypatch.setattr(webapp, name, str(p))
        sentinels[name] = p
    return {"tmp": tmp_path, "manager_stores": sentinels}


def _fake_checker(results):
    """results: {url: bool} — default True (live) for any url not listed."""
    calls = []

    def check(url):
        calls.append(url)
        return results.get(url, True)
    return check, calls


# ── registration + status ──────────────────────────────────────────────────────
def test_registered_disabled_daily_0430(iso):
    c = authed_client()
    (a,) = [x for x in c.get("/api/automations").get_json()["automations"]
            if x["key"] == "image_health"]
    assert a["name"] == "Kontrola obrázkov"
    assert a["enabled"] is False            # SAFETY: deploy starts stopped (#93 contract)
    assert a["schedule"] == "denne o 04:30"
    assert a["running"] is False


def test_disabled_automation_does_not_run(iso):
    webapp.RUNNER.tick_once()
    (a,) = [x for x in webapp.RUNNER.status() if x["key"] == "image_health"]
    assert a["last_run"] == ""
    assert not os.path.exists(webapp.IMAGE_HEALTH_STATE)


# ── the run: check + cache ───────────────────────────────────────────────────────
def test_run_checks_every_url_and_persists_cache(iso, monkeypatch):
    check, calls = _fake_checker({"https://cdn/dead.jpg": False})
    monkeypatch.setattr(webapp, "_check_image_url", check)
    monkeypatch.setattr(webapp, "PRODUCTS",
                        [_product("P1", ["https://cdn/live.jpg", "https://cdn/dead.jpg"])])

    stats = webapp.run_image_health()
    assert stats["total_urls"] == 2 and stats["checked"] == 2
    assert stats["ok"] == 1 and stats["failed"] == 1
    assert set(calls) == {"https://cdn/live.jpg", "https://cdn/dead.jpg"}

    st = json.loads(open(webapp.IMAGE_HEALTH_STATE, encoding="utf-8").read())
    assert st["cache"]["https://cdn/live.jpg"]["ok"] is True
    assert st["cache"]["https://cdn/dead.jpg"]["ok"] is False
    assert st["cache"]["https://cdn/dead.jpg"]["fails"] == 1
    assert st["stats"]["dead_urls"] == 0    # only 1 consecutive failure so far — not dead yet


def test_second_consecutive_failure_marks_dead(iso, monkeypatch):
    check, _ = _fake_checker({"https://cdn/dead.jpg": False})
    monkeypatch.setattr(webapp, "_check_image_url", check)
    monkeypatch.setattr(webapp, "PRODUCTS", [_product("P1", ["https://cdn/dead.jpg"])])

    webapp.run_image_health()
    stats2 = webapp.run_image_health()
    assert stats2["dead_urls"] == 1
    assert stats2["cleaned_images"] == 1


def test_recovery_between_runs_resets_fail_streak(iso, monkeypatch):
    monkeypatch.setattr(webapp, "PRODUCTS", [_product("P1", ["https://cdn/x.jpg"])])
    check1, _ = _fake_checker({"https://cdn/x.jpg": False})
    monkeypatch.setattr(webapp, "_check_image_url", check1)
    webapp.run_image_health()                       # 1st failure

    check2, _ = _fake_checker({})                    # now healthy again
    monkeypatch.setattr(webapp, "_check_image_url", check2)
    webapp.run_image_health()                       # success resets streak

    check3, _ = _fake_checker({"https://cdn/x.jpg": False})
    monkeypatch.setattr(webapp, "_check_image_url", check3)
    stats = webapp.run_image_health()                # only 1 consecutive failure again
    assert stats["dead_urls"] == 0


def test_freshness_window_skips_recently_confirmed_ok(iso, monkeypatch):
    check, calls = _fake_checker({})
    monkeypatch.setattr(webapp, "_check_image_url", check)
    monkeypatch.setattr(webapp, "PRODUCTS", [_product("P1", ["https://cdn/x.jpg"])])

    webapp.run_image_health()
    assert calls == ["https://cdn/x.jpg"]
    stats2 = webapp.run_image_health()               # same run, right away — still fresh
    assert stats2["checked"] == 0 and stats2["skipped"] == 1
    assert len(calls) == 1                            # no second HEAD


def test_cache_pruned_for_urls_no_longer_referenced(iso, monkeypatch):
    monkeypatch.setattr(webapp, "PRODUCTS", [_product("P1", ["https://cdn/old.jpg"])])
    check, _ = _fake_checker({})
    monkeypatch.setattr(webapp, "_check_image_url", check)
    webapp.run_image_health()

    monkeypatch.setattr(webapp, "PRODUCTS", [_product("P1", ["https://cdn/new.jpg"])])
    webapp.run_image_health()
    st = json.loads(open(webapp.IMAGE_HEALTH_STATE, encoding="utf-8").read())
    assert "https://cdn/old.jpg" not in st["cache"]
    assert "https://cdn/new.jpg" in st["cache"]


def test_run_via_runner_records_ok_status(iso, monkeypatch):
    check, _ = _fake_checker({})
    monkeypatch.setattr(webapp, "_check_image_url", check)
    monkeypatch.setattr(webapp, "PRODUCTS", [_product("P1", ["https://cdn/x.jpg"])])
    c = authed_client()
    r = c.post("/api/automations/image_health/run")
    assert r.get_json()["started"] is True
    webapp.RUNNER._threads["image_health"].join(timeout=15)
    (st,) = [x for x in webapp.RUNNER.status() if x["key"] == "image_health"]
    assert st["last_status"] == "ok"
    assert st["last_result"]["checked"] == 1
    assert st["enabled"] is False           # run-now must not enable the schedule


# ── isolation: never touches the manager's live decision stores ────────────────
def test_run_never_touches_manager_stores(iso, monkeypatch):
    check, _ = _fake_checker({})
    monkeypatch.setattr(webapp, "_check_image_url", check)
    monkeypatch.setattr(webapp, "PRODUCTS", [_product("P1", ["https://cdn/x.jpg"])])
    webapp.run_image_health()
    for _name, path in iso["manager_stores"].items():
        assert path.read_text(encoding="utf-8") == '{"sentinel": true}'
    assert webapp.PRODUCTS[0]["our_images"] == ["https://cdn/x.jpg"]   # PRODUCTS untouched


# ── /api/products: the actual fix — a confirmed-dead URL is never served ────────
def test_api_products_drops_confirmed_dead_image_after_threshold(iso, monkeypatch):
    monkeypatch.setattr(webapp, "PRODUCTS",
                        [_product("P1", ["https://cdn/live.jpg", "https://cdn/dead.jpg"])])
    check, _ = _fake_checker({"https://cdn/dead.jpg": False})
    monkeypatch.setattr(webapp, "_check_image_url", check)
    webapp.run_image_health()
    webapp.run_image_health()                        # 2nd consecutive fail -> dead

    j = authed_client().get("/api/products").get_json()
    served = j["products"][0]["our_images"]
    assert served == ["https://cdn/live.jpg"]
    # storage itself (in-memory PRODUCTS) is untouched — display-only filter
    assert webapp.PRODUCTS[0]["our_images"] == ["https://cdn/live.jpg", "https://cdn/dead.jpg"]


def test_api_products_keeps_image_after_single_failure(iso, monkeypatch):
    monkeypatch.setattr(webapp, "PRODUCTS", [_product("P1", ["https://cdn/dead.jpg"])])
    check, _ = _fake_checker({"https://cdn/dead.jpg": False})
    monkeypatch.setattr(webapp, "_check_image_url", check)
    webapp.run_image_health()                        # only 1 failure so far

    j = authed_client().get("/api/products").get_json()
    assert j["products"][0]["our_images"] == ["https://cdn/dead.jpg"]


def test_api_products_before_any_run_serves_unfiltered(iso, monkeypatch):
    monkeypatch.setattr(webapp, "PRODUCTS", [_product("P1", ["https://cdn/never-checked.jpg"])])
    j = authed_client().get("/api/products").get_json()
    assert j["products"][0]["our_images"] == ["https://cdn/never-checked.jpg"]
