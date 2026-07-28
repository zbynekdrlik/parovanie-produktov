"""#209 — the shop's order statuses are CONFIGURABLE text, so this app must not carry
them as baked-in literals.

Until now „the order is being processed" was the literal `"Vybavuje sa"` written out in
four places (`build_to_order_rows`, `_orders_by_openness`, `nedostupne.ORDER_STATUS`,
`orders_reminder.ORDER_STATUS`) and „the order is finished" was a module-level frozenset.
Rename the status in the Shoptet admin — it is a text field the shop owner edits — and the
„Na objednanie" tab, „Nedostupné" and the customer reminders all go silently EMPTY, while
the prune stops recognising the finished statuses it was taught.

What is pinned here:

  1. the three sets come from ONE store (`order_statuses.json`) whose defaults are exactly
     the measured live sets, so an app with no config file behaves as it did before;
  2. a renamed / newly added status can be CLASSIFIED by the manager, and every consumer
     follows the same set — there is no second, competing notion of „open";
  3. the validation is fail-SAFE in both directions: a nonsense file falls back to the
     measured defaults (never to an empty set, which would empty the tab), and the write
     endpoint refuses the two states that would break the prune's own invariant;
  4. a status in NONE of the three sets is reported as unknown — the signal that tells the
     manager a new status appeared instead of letting it be mis-bucketed in silence.
"""
import json
import os
import sys
from datetime import date, datetime, timedelta

import pytest
from werkzeug.security import generate_password_hash

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webreview"))
import app as webapp  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from parovanie import orders_reminder  # noqa: E402

ADMIN = "admin@test.sk"
USER = "clen@test.sk"

_HEAD = "code;date;statusName;itemCode;itemName;itemAmount;shopRemark\r\n"

# The nine statuses the live 90-day export actually carries (measured 2026-07-28:
# Vybavená 1474 · Vybavuje sa 225 · Stornovaná 209 · Vybavená výmena 14 · Vybavený
# Dobropis 10 · Osob. odber 9 · Kompletná 6 · Vratený tovar 4 · Výmena tovaru 3 rows),
# and the bucket the DEFAULT configuration must put each of them in.
_LIVE = {
    "Vybavená": "terminal",
    "Stornovaná": "terminal",
    "Vybavená výmena": "terminal",
    "Vybavený Dobropis": "terminal",
    "Vybavuje sa": "to_order",
    "Osob. odber": "known_open",
    "Výmena tovaru": "known_open",
    "Vratený tovar": "known_open",
    "Kompletná": "known_open",
}


@pytest.fixture
def iso(tmp_path, monkeypatch):
    """Isolated status config + users store — never touches the real data/out."""
    monkeypatch.setattr(webapp, "ORDER_STATUSES", str(tmp_path / "order_statuses.json"))
    users = {
        ADMIN: {"pw_hash": generate_password_hash("x"), "is_admin": True,
                "created_at": "2026-01-01T00:00:00+00:00"},
        USER: {"pw_hash": generate_password_hash("x"), "is_admin": False,
               "created_at": "2026-01-01T00:00:00+00:00"},
    }
    p = tmp_path / "users.json"
    p.write_text(json.dumps(users), encoding="utf-8")
    monkeypatch.setattr(webapp, "USERS", str(p))
    return tmp_path


def _client_as(email):
    c = webapp.app.test_client()
    with c.session_transaction() as s:
        s["user"] = email
    return c


def _write(iso, cfg):
    """Put a configuration on disk the way a hand edit / an older version would."""
    (iso / "order_statuses.json").write_text(json.dumps(cfg), encoding="utf-8")


def _order_row(status, code="99002001", days_old=2, item="A1"):
    return "{};{};{};{};Bunda;1;\r\n".format(
        code, (date.today() - timedelta(days=days_old)).isoformat() + " 09:00:00",
        status, item)


# ── 1. the defaults ARE today's measured sets ───────────────────────────────────

def test_with_no_config_file_the_nine_live_statuses_land_where_they_do_today(iso):
    """A fresh install must behave exactly as v0.102.0 did — the config is an override,
    not a new source of truth that has to be filled in before the app works."""
    st = webapp._order_statuses()

    for status, bucket in _LIVE.items():
        assert status in st[bucket], (status, bucket, st)
    assert st["to_order"] == frozenset({"Vybavuje sa"}), st["to_order"]
    assert st["terminal"] == frozenset({"Vybavená", "Vybavená výmena",
                                        "Vybavený Dobropis", "Stornovaná"}), st["terminal"]


# ── 2. a renamed status can be configured, and EVERY consumer follows ───────────

def test_a_RENAMED_open_status_is_configurable_and_the_to_order_tab_follows(iso):
    """The whole ticket in one test. Today the literal is baked into
    `build_to_order_rows`, so the renamed status yields nothing at all."""
    export = (_HEAD + _order_row("Spracúva sa")).encode("cp1250")

    assert webapp.build_to_order_rows(export, [], {}, {}, {}) == []

    _write(iso, {"to_order": ["Spracúva sa"]})
    rows = webapp.build_to_order_rows(export, [], {}, {}, {})

    assert [r["key"] for r in rows] == ["99002001|A1"], rows


def test_the_SAME_configured_set_drives_nedostupne_and_the_customer_reminders(iso):
    """One notion of „the order is being processed", not four. A rename that empties the
    to-order tab must not leave the two customer-facing paths reading a different set."""
    _write(iso, {"to_order": ["Spracúva sa"]})
    old = (date.today() - timedelta(days=30)).isoformat() + " 09:00:00"
    export = (_HEAD + "99002002;{};Spracúva sa;B1;Ciapka;1;\r\n".format(old)).encode("cp1250")

    view = webapp._nedostupne_view(export, {"99002002|B1": True}, {})
    picked = orders_reminder.select_orders(export, now=datetime.now(),
                                           statuses=webapp._order_statuses()["to_order"])

    assert [o["code"] for o in picked] == ["99002002"], picked
    (entry,) = [r for r in view if r["code"] == "B1"]
    assert entry["order_count"] == 1, entry      # the renamed status still reaches the tab


def test_the_openness_signal_and_the_prune_read_the_SAME_configured_sets(iso):
    """`_orders_by_openness` is what decides which keys may be deleted, so it must move
    with the configuration too — otherwise a rename turns every open order into an
    unknown status and the prune's own „nothing is open" alarm fires for ever."""
    _write(iso, {"to_order": ["Spracúva sa"], "terminal": ["Hotová"]})
    body = (_order_row("Spracúva sa")
            + _order_row("Hotová", code="99002002", days_old=120, item="B1"))

    seen, still_open, finished, _dates, unknown, reason = webapp._orders_by_openness(
        (_HEAD + body).encode("cp1250"))

    assert reason == "", reason
    assert still_open == {"99002001"}, still_open
    assert finished == {"99002002"}, finished
    assert unknown == set(), unknown


# ── 3. an UNCLASSIFIED status stays visible ────────────────────────────────────

def test_a_status_in_NONE_of_the_three_sets_is_reported_as_unknown(iso):
    """The point of the third set: „unknown" must mean genuinely UNJUDGED. A status the
    manager has not classified anywhere is the one case the signal exists for."""
    _write(iso, {"to_order": ["Vybavuje sa"], "terminal": ["Vybavená"], "known_open": []})
    body = (_order_row("Vybavuje sa")
            + _order_row("Čaká na dodávateľa", code="99002002", item="B1"))

    *_rest, unknown, reason = webapp._orders_by_openness((_HEAD + body).encode("cp1250"))

    assert reason == "", reason
    assert unknown == {"Čaká na dodávateľa"}, unknown


def test_a_status_the_manager_HAS_classified_is_no_longer_reported(iso):
    """…and once he files it, the signal goes quiet. A permanent report of an expected
    value is noise that hides the one case it was built for (store-prune §1a)."""
    _write(iso, {"known_open": ["Čaká na dodávateľa"]})
    body = (_order_row("Vybavuje sa")
            + _order_row("Čaká na dodávateľa", code="99002002", item="B1"))

    *_rest, unknown, reason = webapp._orders_by_openness((_HEAD + body).encode("cp1250"))

    assert reason == "" and unknown == set(), (reason, unknown)


# ── 4. fail-SAFE reading of a broken configuration ─────────────────────────────

@pytest.mark.parametrize("cfg", [
    {"to_order": []},                              # emptied — would blank the whole tab
    {"to_order": ["   ", ""]},                      # blank-only entries are not statuses
    {"to_order": "Vybavuje sa"},                    # a string, not a list
    {"to_order": [{"a": 1}]},                       # wrong element type
    {"terminal": []},                               # nothing would ever be pruned
])
def test_an_UNUSABLE_set_falls_back_to_the_measured_defaults(iso, cfg):
    """Never to an empty set: an empty `to_order` empties the tab and the customer
    mails, an empty `terminal` silently disarms the prune. A store this app cannot read
    is not permission to invent behaviour."""
    _write(iso, cfg)
    st = webapp._order_statuses()

    assert st["to_order"] == frozenset({"Vybavuje sa"}), st
    assert "Vybavená" in st["terminal"], st


def test_a_status_claimed_by_BOTH_open_and_finished_falls_back_entirely(iso):
    """The one invariant the prune cannot survive: a status that means both „still being
    handled" and „over" would delete the marks of live orders. A hand-edited file that
    says so is rejected WHOLE — patching just one side would leave the manager with a
    configuration he never wrote."""
    _write(iso, {"to_order": ["Vybavuje sa", "Kompletná"],
                 "terminal": ["Vybavená", "Kompletná"]})
    st = webapp._order_statuses()

    assert st["to_order"] == frozenset({"Vybavuje sa"}), st
    assert "Kompletná" not in st["terminal"], st


def test_the_sets_are_BOUNDED_in_count_and_in_entry_length(iso):
    """Same reasoning as `ORDERS_UNKNOWN_STATUS_MAX`: a status is untrusted text that
    ends up in a log, in `automations.json` and on the card."""
    _write(iso, {"known_open": ["x" * 500] + [f"S{i}" for i in range(200)]})
    st = webapp._order_statuses()

    assert len(st["known_open"]) <= webapp.ORDER_STATUS_MAX, len(st["known_open"])
    assert all(len(s) <= webapp.ORDERS_UNKNOWN_STATUS_MAXLEN for s in st["known_open"])


# ── 5. the API the manager's screen talks to ───────────────────────────────────

def test_anon_gets_401_and_a_non_admin_may_read_but_not_write(iso):
    anon = webapp.app.test_client()
    assert anon.get("/api/order-statuses").status_code == 401
    assert anon.post("/api/order-statuses", json={}).status_code == 401

    c = _client_as(USER)
    assert c.get("/api/order-statuses").status_code == 200
    assert c.post("/api/order-statuses",
                  json={"to_order": ["Vybavuje sa"]}).status_code == 403


def test_GET_returns_the_effective_sets_AND_the_defaults(iso):
    """The screen has to be able to show „this is what the app is using" next to „this is
    what it falls back to", or a manager cannot tell whether his edit took effect."""
    _write(iso, {"to_order": ["Spracúva sa"]})
    r = _client_as(USER).get("/api/order-statuses")
    body = r.get_json()

    assert body["statuses"]["to_order"] == ["Spracúva sa"], body
    assert body["defaults"]["to_order"] == ["Vybavuje sa"], body
    assert body["statuses"]["terminal"] == sorted(webapp.ORDER_STATUS_DEFAULTS["terminal"])


def test_POST_saves_all_three_sets_and_they_take_effect_at_once(iso):
    c = _client_as(ADMIN)
    r = c.post("/api/order-statuses", json={"to_order": ["Spracúva sa"],
                                            "terminal": ["Hotová"],
                                            "known_open": ["Čaká na dodávateľa"]})

    assert r.status_code == 200, r.get_json()
    st = webapp._order_statuses()
    assert st["to_order"] == frozenset({"Spracúva sa"}), st
    assert st["terminal"] == frozenset({"Hotová"}), st
    assert st["known_open"] == frozenset({"Čaká na dodávateľa"}), st


@pytest.mark.parametrize("payload,why", [
    ({"to_order": [], "terminal": ["Vybavená"]}, "prázdny"),
    ({"to_order": ["Vybavuje sa"], "terminal": []}, "prázdny"),
    ({"to_order": ["Kompletná"], "terminal": ["Vybavená", "Kompletná"]}, "aj"),
])
def test_POST_REFUSES_a_configuration_that_would_break_the_prune(iso, payload, why):
    """Refused at the door with a sentence a human can act on — not silently corrected,
    and not accepted only to be ignored by the loader's fallback."""
    before = json.dumps(webapp._read_json_store(webapp.ORDER_STATUSES, {}), sort_keys=True)
    r = _client_as(ADMIN).post("/api/order-statuses", json=payload)

    assert r.status_code == 400, r.get_json()
    assert why in (r.get_json().get("error") or "").lower(), r.get_json()
    assert json.dumps(webapp._read_json_store(webapp.ORDER_STATUSES, {}),
                      sort_keys=True) == before


def test_the_prune_result_NAMES_the_configured_open_statuses(iso, tmp_path, monkeypatch):
    """#293's „nothing is open" banner used to name the hard-coded „Vybavuje sa" — after a
    rename that is exactly the wrong thing to send the manager looking for. The run reports
    what it was actually looking for."""
    for attr, fname in (("ORDERED", "ordered_items.json"), ("WAITING", "waiting_items.json"),
                        ("INSTOCK", "instock_items.json"),
                        ("UNAVAIL", "unavailable_items.json")):
        monkeypatch.setattr(webapp, attr, str(tmp_path / fname))
    _write(iso, {"to_order": ["Spracúva sa"]})
    body = "".join(_order_row("Vybavená", code=f"99003{i:03d}", days_old=120, item=f"Z{i}")
                   for i in range(60))

    res = webapp._prune_orphan_line_flags((_HEAD + body).encode("cp1250"))

    assert res["skipped"] == "no-open-orders", res
    assert res["open_statuses"] == ["Spracúva sa"], res
