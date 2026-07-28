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
import unicodedata
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
    manager has not classified anywhere is the one case the signal exists for.

    `known_open` is written NON-empty on purpose (PR #295 review): this test used to send
    `[]`, which the loader silently replaced with the four built-in defaults — so it passed
    only because its probe status happened not to be one of them, and it proved nothing
    about the third set at all. What `[]` really means now has its own test
    (`test_an_emptied_known_open_makes_its_statuses_UNKNOWN_again`)."""
    _write(iso, {"to_order": ["Vybavuje sa"], "terminal": ["Vybavená"],
                 "known_open": ["Osob. odber"]})
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


def test_a_config_file_that_EXISTS_but_cannot_be_used_DISARMS_the_prune(iso, tmp_path,
                                                                        monkeypatch):
    """The asymmetry that matters, and the one a plain „fall back to the defaults" gets
    backwards. A manager who NARROWS the finished list is told by the card itself to do it
    only when he is sure, because deleted marks cannot be brought back. If that file then
    becomes unreadable, silently restoring the four built-ins RE-ARMS the prune on exactly
    the statuses he excluded — a fail-OPEN on the one operation this whole area exists to
    make safe (store-prune §1: a missed status costs a few keys, a wrongly included one
    costs irreplaceable work).

    So: no file at all = a fresh install = the defaults. A file that IS there and cannot be
    used = we do not know what he decided → the prune refuses under its own named reason,
    which the card renders as a red banner instead of quietly deleting."""
    for attr, fname in (("ORDERED", "ordered_items.json"), ("WAITING", "waiting_items.json"),
                        ("INSTOCK", "instock_items.json"),
                        ("UNAVAIL", "unavailable_items.json"),
                        ("ORDERS_CLOSED_SEEN", "orders_closed_seen.json")):
        monkeypatch.setattr(webapp, attr, str(tmp_path / fname))
    webapp._save_ordered({"99002002|B1": True})
    (iso / "order_statuses.json").write_text("{ toto nie je json", encoding="utf-8")
    body = (_order_row("Vybavuje sa")
            + "".join(_order_row("Vybavená", code=f"99003{i:03d}", days_old=120,
                                 item=f"Z{i}") for i in range(60))
            + _order_row("Vybavená", code="99002002", days_old=120, item="B1"))

    res = webapp._prune_orphan_line_flags((_HEAD + body).encode("cp1250"))

    assert res["skipped"] == "bad-status-config", res
    assert res["pruned"] == 0, res
    assert sorted(webapp._load_ordered()) == ["99002002|B1"]


def test_no_config_file_at_all_is_a_fresh_install_not_a_broken_one(iso):
    """The other half: an app that was never configured must work, so „missing" and
    „present but broken" cannot share an answer."""
    assert not (iso / "order_statuses.json").exists()
    st = webapp._order_statuses()

    assert st["to_order"] == frozenset({"Vybavuje sa"}), st
    assert webapp._order_statuses_state()[1] == "", webapp._order_statuses_state()


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
    ({"to_order": ["Kompletná"], "terminal": ["Vybavená", "Kompletná"]}, "naraz"),
    # the likeliest mis-edit of three copy-pasteable boxes: moved to „known open" but left
    # in „finished". Its consequence is deletion, while the box's own help text promises
    # the marks stay — so it is refused exactly like the to_order/terminal clash.
    ({"terminal": ["Vybavená", "Kompletná"], "known_open": ["Kompletná"]}, "naraz"),
    ({"to_order": ["Vybavuje sa", "X"], "known_open": ["X"]}, "naraz"),
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


@pytest.mark.parametrize("body", ["\"hello\"", "17", "null", "[1,2]"])
def test_a_non_dict_BODY_is_refused_not_a_500(iso, body):
    """`get_json(silent=True) or {}` neutralises null and [], but a JSON string or number
    sails through and blows up on `.get`. A malformed request must never be a 500."""
    r = _client_as(ADMIN).post("/api/order-statuses", data=body,
                               content_type="application/json")

    assert r.status_code in (200, 400), (body, r.status_code)


def test_an_OVER_CAP_list_is_refused_rather_than_silently_truncated(iso):
    """Saving 60 statuses and answering „✅ Uložené" while keeping 50 is a lie the manager
    cannot see — and a status that was silently dropped simply never matches the export
    again. The endpoint refuses the other two bad shapes; this is the third."""
    r = _client_as(ADMIN).post("/api/order-statuses", json={
        "known_open": [f"S{i}" for i in range(webapp.ORDER_STATUS_MAX + 10)]})

    assert r.status_code == 400, r.get_json()
    assert str(webapp.ORDER_STATUS_MAX) in (r.get_json().get("error") or "")


def test_an_OVER_LONG_status_is_refused_rather_than_silently_cut(iso):
    r = _client_as(ADMIN).post("/api/order-statuses", json={
        "known_open": ["x" * (webapp.ORDERS_UNKNOWN_STATUS_MAXLEN + 1)]})

    assert r.status_code == 400, r.get_json()


def test_a_PARTIAL_payload_keeps_the_sets_it_does_not_mention(iso):
    """The endpoint's central promise — the card can send one box without wiping the other
    two, and a future screen that edits only one set stays safe."""
    _write(iso, {"to_order": ["Spracúva sa"], "terminal": ["Hotová"],
                 "known_open": ["Čaká na dodávateľa"]})

    r = _client_as(ADMIN).post("/api/order-statuses", json={"known_open": ["Iné"]})

    assert r.status_code == 200, r.get_json()
    st = webapp._order_statuses()
    assert st["to_order"] == frozenset({"Spracúva sa"}), st
    assert st["terminal"] == frozenset({"Hotová"}), st
    assert st["known_open"] == frozenset({"Iné"}), st


def test_build_to_order_rows_honours_an_EXPLICIT_status_set(iso):
    """The seam the web app uses is also the seam a script or a test uses — pin it, or the
    parameter silently rots into „whatever the store says"."""
    export = (_HEAD + _order_row("Čokoľvek")).encode("cp1250")

    rows = webapp.build_to_order_rows(export, [], {}, {}, {}, statuses={"Čokoľvek"})

    assert [r["key"] for r in rows] == ["99002001|A1"], rows


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


# ── 6. PR #295 review — what the endpoint ACCEPTS, the loader must then USE ─────
#    B3: the POST validates the payload as written; the loader then re-reads the file and
#    substitutes DEFAULTS for a set it finds unusable — including one the manager
#    deliberately emptied. Those defaults can clash with the sets he DID write, and a clash
#    discards the whole configuration. The card answers „✅ Uložené. Platí to hneď pre celú
#    appku." while the rename reverts, the mails go to nobody, and the prune is disarmed
#    with a banner naming a „contradictory list" the panel renders as EMPTY — a state he
#    cannot fix from the screen at all.

@pytest.mark.parametrize("payload", [
    # moved „Kompletná" from the default `known_open` into `terminal` and emptied the box
    # it came from — the exact shape the panel produces from three textareas
    {"to_order": ["Vybavuje sa"], "terminal": ["Vybavená", "Kompletná"], "known_open": []},
    # …and the same with the third box merely omitted from the payload
    {"to_order": ["Vybavuje sa"], "terminal": ["Vybavená", "Vratený tovar"]},
])
def test_a_configuration_the_endpoint_ACCEPTS_is_the_one_the_app_then_USES(iso, payload):
    """The invariant, whichever way it is satisfied: either the POST refuses with a
    sentence the manager can act on, or what he saved is exactly what the app runs on.
    „Accepted, then silently discarded by the loader" is the one answer that must not
    exist."""
    r = _client_as(ADMIN).post("/api/order-statuses", json=payload)

    if r.status_code == 400:
        assert (r.get_json().get("error") or "").strip(), r.get_json()
        return
    assert r.status_code == 200, r.get_json()
    saved = r.get_json()["statuses"]
    sets, reason = webapp._order_statuses_state()

    assert reason == "", (reason, saved, {k: sorted(v) for k, v in sets.items()})
    for key, values in saved.items():
        assert sets[key] == frozenset(values), (key, saved, sets)


def test_the_card_never_reports_saved_for_a_configuration_that_reverts(iso):
    """The reported symptom in one assertion: the panel's own GET must agree with what the
    save answered, or „✅ Uložené. Platí to hneď pre celú appku." is false."""
    c = _client_as(ADMIN)
    r = c.post("/api/order-statuses", json={"to_order": ["Vybavuje sa"],
                                            "terminal": ["Vybavená", "Kompletná"],
                                            "known_open": []})
    if r.status_code == 400:
        return                                    # refused at the door is also correct
    assert c.get("/api/order-statuses").get_json()["statuses"] == r.get_json()["statuses"]


# ── 7. B4 — „absent" and „deliberately empty" are different answers ─────────────
#    `_clean_status_list` returns None for `[]`, so the loader cannot tell an unusable set
#    from one the manager emptied on purpose — and `known_open: []` is an explicitly
#    supported POST outcome the loader never honoured.

def test_an_explicitly_EMPTIED_known_open_stays_empty(iso):
    """He cleared the box because he wants every unclassified status reported. Restoring
    the four built-ins instead makes the „unknown" signal permanently quiet about them —
    the exact opposite of what he asked for."""
    r = _client_as(ADMIN).post("/api/order-statuses", json={"known_open": []})

    assert r.status_code == 200, r.get_json()
    assert webapp._order_statuses()["known_open"] == frozenset(), \
        webapp._order_statuses()["known_open"]
    # …and it survives the round trip through the file, which is where it was lost
    assert _client_as(USER).get("/api/order-statuses").get_json()["statuses"]["known_open"] \
        == []


def test_an_emptied_known_open_makes_its_statuses_UNKNOWN_again(iso):
    """The observable consequence, so the test cannot pass on a default that merely
    happens not to contain the probe status (the flaw in
    test_a_status_in_NONE_of_the_three_sets_is_reported_as_unknown)."""
    _write(iso, {"to_order": ["Vybavuje sa"], "terminal": ["Vybavená"], "known_open": []})
    body = _order_row("Vybavuje sa") + _order_row("Kompletná", code="99002002", item="B1")

    *_rest, unknown, reason = webapp._orders_by_openness((_HEAD + body).encode("cp1250"))

    assert reason == "", reason
    # „Kompletná" IS one of the built-in known_open statuses — with the box emptied it must
    # be reported, or the emptying did nothing
    assert unknown == {"Kompletná"}, unknown


@pytest.mark.parametrize("key", ["to_order", "terminal"])
def test_the_two_LOAD_BEARING_sets_still_refuse_to_be_emptied(iso, key):
    """B4 must not become „empty means empty" for the two sets that cannot be empty: an
    empty `to_order` blanks the tab and the customer mails, an empty `terminal` disarms the
    prune."""
    assert _client_as(ADMIN).post("/api/order-statuses",
                                  json={key: []}).status_code == 400
    _write(iso, {key: []})
    assert webapp._order_statuses()[key] == frozenset(webapp.ORDER_STATUS_DEFAULTS[key])
    assert webapp._order_statuses_state()[1] == "bad-status-config"


def test_an_ABSENT_set_still_means_the_default(iso):
    """The other half of the distinction — a file that never mentions a set is not a file
    that emptied it."""
    _write(iso, {"to_order": ["Vybavuje sa"]})

    assert webapp._order_statuses()["known_open"] \
        == frozenset(webapp.ORDER_STATUS_DEFAULTS["known_open"])


# ── 8. B5 — the same status name must COMPARE equal to the export's ────────────
#    `_clean_status_list` strips but does not normalise, so an NFD status name is stored
#    and echoed back looking identical while matching nothing. For `to_order` NOTHING
#    surfaces it: the tab, „Nedostupné" and the mails simply go empty.

def test_a_status_typed_in_NFD_still_matches_the_export(iso):
    """„Vybavuje sa" pasted from a source that decomposes its diacritics is byte-different
    and looks identical on screen. Normalise both sides, or the tab empties in silence."""
    nfd = unicodedata.normalize("NFD", "Vybavuje sa čaká")
    assert nfd != "Vybavuje sa čaká"
    r = _client_as(ADMIN).post("/api/order-statuses", json={"to_order": [nfd]})
    assert r.status_code == 200, r.get_json()

    export = (_HEAD + _order_row("Vybavuje sa čaká")).encode("cp1250")

    assert [x["key"] for x in webapp.build_to_order_rows(export, [], {}, {}, {})] \
        == ["99002001|A1"]
    seen, still_open, *_rest = webapp._orders_by_openness(export)
    assert still_open == {"99002001"}, still_open


def test_an_export_status_in_NFD_still_matches_the_configuration(iso):
    """…and the same from the other side: the EXPORT is the untrusted input here."""
    _write(iso, {"to_order": ["Vybavuje sa čaká"]})
    export = (_HEAD + _order_row(
        unicodedata.normalize("NFD", "Vybavuje sa čaká"))).encode("cp1250")

    assert len(webapp.build_to_order_rows(export, [], {}, {}, {})) == 1
    picked = orders_reminder.select_orders(
        export, now=datetime.now() + timedelta(days=30),
        statuses=webapp._order_statuses()["to_order"])
    assert [o["code"] for o in picked] == ["99002001"], picked


def test_the_API_reports_which_statuses_the_EXPORT_actually_carries(iso, monkeypatch):
    """A name that matches nothing is otherwise invisible. The panel can only flag „this
    status matches 0 orders" if it is told what the export contains."""
    body = (_order_row("Vybavuje sa") + _order_row("Vybavená", code="99002002", item="B1")
            + _order_row("Osob. odber", code="99002003", item="C1"))
    monkeypatch.setattr(webapp, "_orders_csv_cached",
                        lambda: (_HEAD + body).encode("cp1250"))

    got = _client_as(USER).get("/api/order-statuses").get_json()

    assert got["export_statuses"] == ["Osob. odber", "Vybavená", "Vybavuje sa"], got
    saved = _client_as(ADMIN).post("/api/order-statuses",
                                   json={"known_open": ["Osob. odber"]}).get_json()
    assert saved["export_statuses"] == got["export_statuses"], saved


def test_an_unreadable_export_leaves_the_endpoint_working(iso, monkeypatch):
    """The extra field is a convenience — it must never be able to break the card the
    manager goes to when things are already broken."""
    def boom():
        raise OSError("no export here")
    monkeypatch.setattr(webapp, "_orders_csv_cached", boom)

    r = _client_as(USER).get("/api/order-statuses")

    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["export_statuses"] == []


# ── 9. B6 — one unreadable file must not 500 four tabs ─────────────────────────

def test_an_UNREADABLE_config_file_degrades_instead_of_500ing_every_tab(iso, monkeypatch):
    """`_read_json_store` propagates an I/O error on purpose, and `_order_statuses()`
    catches nothing — so a permissions problem on THIS one file 500s /api/orders,
    /api/nedostupne and /api/nedostupne/<code>. Every other store breaks one tab."""
    p = iso / "order_statuses.json"
    p.write_text(json.dumps({"to_order": ["Vybavuje sa"]}), encoding="utf-8")
    p.chmod(0o000)
    monkeypatch.setattr(webapp, "_orders_csv_cached",
                        lambda: (_HEAD + _order_row("Vybavuje sa")).encode("cp1250"))
    try:
        sets, reason = webapp._order_statuses_state()
        c = _client_as(USER)
        assert c.get("/api/orders").status_code == 200
        assert c.get("/api/nedostupne").status_code == 200
    finally:
        p.chmod(0o600)

    # …and it is the „present but unusable" answer, not a silent fresh-install fallback
    assert reason == "bad-status-config", reason
    assert sets["to_order"] == frozenset(webapp.ORDER_STATUS_DEFAULTS["to_order"]), sets


# ── 10. B7 — a status name is untrusted text that reaches the log ──────────────

@pytest.mark.parametrize("bad", ["Vybavená\nINFO fake log line", "Vybavuje\rsa", "a\x00b",
                                 "x\x1b[31m"])
def test_a_status_name_with_CONTROL_characters_is_refused(iso, bad):
    """`_clean_status_list` only `.strip()`s, so an interior newline survives the POST and
    lands unescaped in `log.info(...)` and in the prune's `", ".join(open_statuses)` — log
    forgery through an authenticated API call or a hand edit."""
    r = _client_as(ADMIN).post("/api/order-statuses", json={"known_open": [bad]})

    assert r.status_code == 400, r.get_json()
    assert (r.get_json().get("error") or "").strip(), r.get_json()


def test_a_hand_edited_control_character_is_DROPPED_by_the_loader(iso):
    """The endpoint can refuse; the file can be edited by hand, so the loader has to
    defend the log too."""
    _write(iso, {"known_open": ["Dobrý stav", "zlý\nstav"]})

    assert webapp._order_statuses()["known_open"] == frozenset({"Dobrý stav"}), \
        webapp._order_statuses()["known_open"]
