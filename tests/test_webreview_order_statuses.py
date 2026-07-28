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
    """…and the same from the other side: the EXPORT is the untrusted input here.

    Fed as TEXT, not cp1250 bytes — cp1250 has no combining marks, so the decomposed form
    cannot even be encoded in it. Both readers accept `str` (that is their documented
    seam), and normalising there is what makes the comparison independent of whatever
    encoding a future export template arrives in."""
    _write(iso, {"to_order": ["Vybavuje sa čaká"]})
    export = _HEAD + _order_row(unicodedata.normalize("NFD", "Vybavuje sa čaká"))

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


# ── #296: the Pošta automation's two remaining literals ────────────────────────
# `posta_uncollected.DISPATCHED_STATUS` („vybavená") and its `== "stornovaná"` filter stayed
# outside #209's configuration. They live on the TERMINAL side, which the `terminal` set owns
# — so a fourth set names the CANCELLED half of it and „dispatched" is what is left over.
# Deriving it is the load-bearing decision: a fourth EDITABLE box would make one rename need
# TWO edits, and the second one is not on the card where the manager is told about the new
# status — which is the silent death #209 removed.

def test_cancelled_defaults_to_the_measured_live_status(iso):
    """A fresh install keeps behaving exactly as v0.103.0 did: „Stornovaná" is the cancelled
    status, and it is one of the four the default `terminal` set already carries."""
    st = webapp._order_statuses()
    assert st["cancelled"] == frozenset({"Stornovaná"})
    assert st["cancelled"] <= st["terminal"]


def test_dispatched_is_DERIVED_as_terminal_minus_cancelled(iso):
    """No fourth editable box. Renaming a dispatched status is ONE edit — of `terminal`, the
    box the prune already forces the manager to keep correct."""
    cancelled, dispatched = webapp._posta_statuses()
    assert cancelled == frozenset({"Stornovaná"})
    assert dispatched == frozenset({"Vybavená", "Vybavená výmena", "Vybavený Dobropis"})
    c = _client_as(ADMIN).post("/api/order-statuses", json={
        "terminal": ["Expedovaná", "Zrušená"], "cancelled": ["Zrušená"]})
    assert c.status_code == 200, c.get_json()
    assert webapp._posta_statuses() == (frozenset({"Zrušená"}), frozenset({"Expedovaná"}))


def test_a_cancelled_status_OUTSIDE_terminal_is_REFUSED_by_the_endpoint(iso):
    """The invariant that stops the two boxes drifting apart. „Zrušená is cancelled but is not
    a finished status" is not a configuration anybody means — and left unchecked it is exactly
    the silent drift #296 is about, one box further along."""
    r = _client_as(ADMIN).post("/api/order-statuses", json={
        "terminal": ["Vybavená", "Stornovaná"], "cancelled": ["Zrušená"]})
    assert r.status_code == 400
    assert "Zrušená" in r.get_json()["error"]


def test_an_EMPTIED_cancelled_is_refused_like_the_other_load_bearing_sets(iso):
    """With nothing cancelled, `dispatched` becomes the whole `terminal` set — so cancelled
    orders land in the alarm's denominator (they never carry a package number) and, if one
    ever did, its customer gets chased with escalation mails."""
    r = _client_as(ADMIN).post("/api/order-statuses", json={"cancelled": []})
    assert r.status_code == 400
    assert "zrušen" in r.get_json()["error"].lower()


def test_a_config_written_BEFORE_this_set_existed_is_not_broken_by_the_upgrade(iso):
    """The invariant is an endpoint rule, NOT a loader rule, and this is why. Every stored
    configuration predating #296 has no `cancelled` key, so the default steps in — and if that
    default is not inside a `terminal` the manager had legitimately narrowed, a loader-level
    check would red-banner it and DISARM the prune on upgrade, for a state nobody caused.
    Nothing dangerous follows from the mismatch: `dispatched` is `terminal − cancelled`, so a
    name that is not in `terminal` simply subtracts nothing."""
    _write(iso, {"to_order": ["Vybavuje sa"], "terminal": ["Vybavená"], "known_open": []})
    sets, reason = webapp._order_statuses_state()
    assert reason == ""                                   # prune stays armed
    assert sets["cancelled"] == frozenset({"Stornovaná"})
    assert webapp._posta_statuses() == (frozenset({"Stornovaná"}), frozenset({"Vybavená"}))


def test_a_PARTIAL_payload_that_never_mentions_cancelled_is_not_refused(iso):
    """Same reason, on the write path: a caller editing only `to_order` must not be rejected
    over a set it did not touch. The panel always posts all four boxes, so the mis-edit the
    rule exists for is still fully covered."""
    r = _client_as(ADMIN).post("/api/order-statuses",
                               json={"to_order": ["Spracúva sa"], "terminal": ["Hotová"]})
    assert r.status_code == 200, r.get_json()


def test_an_ABSENT_cancelled_key_is_a_fresh_install_not_a_broken_config(iso):
    """Every config file written before this change has no `cancelled` key at all. That must
    read as „not configured" → the default, with no reason and no banner."""
    _write(iso, {"to_order": ["Vybavuje sa"], "terminal": ["Vybavená", "Stornovaná"],
                 "known_open": []})
    sets, reason = webapp._order_statuses_state()
    assert reason == ""
    assert sets["cancelled"] == frozenset({"Stornovaná"})


def test_the_posta_run_passes_the_CONFIGURED_sets_to_the_automation(iso, tmp_path,
                                                                   monkeypatch):
    """The wiring itself: without it every test above pins a helper nobody calls."""
    _write(iso, {"to_order": ["Vybavuje sa"], "terminal": ["Expedovaná", "Zrušená"],
                 "known_open": [], "cancelled": ["Zrušená"]})
    monkeypatch.setattr(webapp, "POSTA_STATE", str(tmp_path / "posta.json"))
    monkeypatch.setattr(webapp, "_orders_csv_cached", lambda: b"")
    seen = {}

    def _ship(csv_bytes, today=None, cancelled_statuses=None):
        seen["cancelled"] = cancelled_statuses
        return []

    def _cov(csv_bytes, today=None, cancelled_statuses=None, dispatched_statuses=None):
        seen["cov_cancelled"] = cancelled_statuses
        seen["dispatched"] = dispatched_statuses
        return {"eligible_orders": 0, "dispatched_orders": 0, "dispatched_with_package": 0,
                "dispatched_without_package": 0, "missing_package": 0,
                "days_since_last_package": None, "dispatched_status_unknown": False,
                "degraded": False}

    monkeypatch.setattr(webapp.posta_uncollected, "shipments_from_orders_csv", _ship)
    monkeypatch.setattr(webapp.posta_uncollected, "source_coverage", _cov)
    webapp.run_posta_uncollected()
    assert seen["cancelled"] == frozenset({"Zrušená"})
    assert seen["cov_cancelled"] == frozenset({"Zrušená"})
    assert seen["dispatched"] == frozenset({"Expedovaná"})


def test_the_blind_spot_ERROR_names_the_CONFIGURED_statuses_not_a_constant(iso, caplog):
    """store-prune §7 / automation-health §3: a refusal that names a literal the shop no
    longer uses sends the manager looking for something that does not exist. The old line
    told him to edit `DISPATCHED_STATUS` — a name only the source code has."""
    _write(iso, {"to_order": ["Vybavuje sa"], "terminal": ["Expedovaná", "Zrušená"],
                 "known_open": [], "cancelled": ["Zrušená"]})
    msg = webapp._dispatched_status_blind_message(12, webapp._posta_statuses()[1])
    assert "12" in msg and "Expedovaná" in msg
    assert "DISPATCHED_STATUS" not in msg


def test_the_blind_spot_ERROR_stops_claiming_ANI_JEDNA_when_a_few_survive(iso):
    """PR #298 review, A1: the signal now also fires with 1-4 recognised orders (a renamed main
    status whose rare siblings survive), so the line may not keep asserting that not a single
    order carries one of the names — it would be false about the only number the manager can
    act on, which is how the previous version of this message ended up saying „v okne je 0
    objednávok"."""
    _write(iso, {"to_order": ["Vybavuje sa"], "terminal": ["Expedovaná", "Zrušená"],
                 "known_open": [], "cancelled": ["Zrušená"]})
    msg = webapp._dispatched_status_blind_message(102, webapp._posta_statuses()[1],
                                                  dispatched=2)
    assert "102" in msg and "2" in msg and "Expedovaná" in msg
    assert "ANI JEDNA" not in msg
    # …and with a genuinely empty count it still says exactly that
    assert "ANI JEDNA" in webapp._dispatched_status_blind_message(
        102, webapp._posta_statuses()[1], dispatched=0)


# ── #297: widening `to_order` silently widens the customer mailing list ────────
# `to_order` drives three things at once — the „Na objednanie" tab, „Nedostupné" AND the
# reminder mails (deliberately: one notion of „open", not four). The sharp edge is that adding
# a status makes EVERY order in it older than 4 days instantly mail-eligible, with no preview,
# no count and no cap on the first run. Measured on the live export (28.7.2026): adding
# „Kompletná" reaches 2 more orders, „Osob. odber" 3 — and „Vybavená" 387 orders / 370
# distinct customers, in one wave, under a card that answers „✅ Uložené".
_IMPACT_HEAD = ("code;date;statusName;email;phone;billFullName;itemCode;itemName;"
                "itemAmount;totalPriceWithVat;shopRemark\r\n")


def _impact_row(status, code, note="volať zákazníka", email="k@example.com", days_old=9):
    d = (datetime.now() - timedelta(days=days_old)).strftime("%Y-%m-%d %H:%M:%S")
    return f"{code};{d};{status};{email};;Zákazník;A1;Bunda;1;10;{note}\r\n"


def _impact_export(monkeypatch, rows):
    monkeypatch.setattr(webapp, "_orders_csv_cached",
                        lambda: (_IMPACT_HEAD + "".join(rows)).encode("cp1250"))


def test_widening_to_order_REPORTS_how_many_customers_it_newly_reaches(iso, monkeypatch):
    """The whole ticket in one call: before saving, the manager must be able to see the size
    of the wave he is about to release."""
    _impact_export(monkeypatch, [
        _impact_row("Vybavuje sa", "99003001"),                       # already in scope
        _impact_row("Kompletná", "99003002", email="a@example.com"),  # newly reachable
        _impact_row("Kompletná", "99003003", email="b@example.com"),
        _impact_row("Kompletná", "99003004", email="a@example.com"),  # same customer twice
    ])
    r = _client_as(ADMIN).post("/api/order-statuses/impact",
                               json={"to_order": ["Vybavuje sa", "Kompletná"]})
    assert r.status_code == 200, r.get_json()
    j = r.get_json()
    assert j["added"] == ["Kompletná"]
    assert j["orders"] == 3
    assert j["mailable"] == 3
    assert j["customers"] == 2                 # DISTINCT e-mails, not orders
    assert j.get("unknown") is not True


def test_an_unchanged_or_NARROWED_set_reaches_nobody_new(iso, monkeypatch):
    """No added status → no wave → nothing to confirm. Narrowing is the safe direction and
    must never put a scary number in front of the manager."""
    _impact_export(monkeypatch, [_impact_row("Vybavuje sa", "99003005"),
                                 _impact_row("Kompletná", "99003006")])
    c = _client_as(ADMIN)
    for payload in ({"to_order": ["Vybavuje sa"]}, {"to_order": []}):
        j = c.post("/api/order-statuses/impact", json=payload).get_json()
        assert j["added"] == [] and j["orders"] == 0 and j["customers"] == 0, (payload, j)


def test_orders_that_could_never_be_mailed_are_counted_apart(iso, monkeypatch):
    """`orders` is what the app starts watching; `mailable` is the honest upper bound on the
    wave. An order with no internal note only ever surfaces as the red „nikto sa jej nedotkol"
    alert and one with no e-mail address can never be mailed at all — counting either as a
    pending mail would cry wolf, and a preview that exaggerates is one he stops reading."""
    _impact_export(monkeypatch, [
        _impact_row("Kompletná", "99003007"),                       # note + e-mail → mailable
        _impact_row("Kompletná", "99003008", note=""),              # no note → red alert only
        _impact_row("Kompletná", "99003009", email=""),             # nobody to mail
    ])
    j = _client_as(ADMIN).post("/api/order-statuses/impact",
                               json={"to_order": ["Kompletná"]}).get_json()
    assert j["orders"] == 3
    assert j["mailable"] == 1
    assert j["customers"] == 1


def test_an_order_already_resolved_is_not_counted_as_a_pending_mail(iso, tmp_path,
                                                                   monkeypatch):
    """The dedup store is what stops a second mail, so an order it already holds is not part
    of the wave. Without this the preview would over-count exactly the orders that are already
    safe — and a number he can prove wrong once is a number he ignores forever."""
    monkeypatch.setattr(webapp, "ORDERS_REMINDER_STATE", str(tmp_path / "rem.json"))
    (tmp_path / "rem.json").write_text(json.dumps({"orders": {
        "99003010": {"status": "emailed"}, "99003011": {"status": "skipped_contacted"}}}),
        encoding="utf-8")
    _impact_export(monkeypatch, [_impact_row("Kompletná", "99003010"),
                                 _impact_row("Kompletná", "99003011"),
                                 _impact_row("Kompletná", "99003012")])
    j = _client_as(ADMIN).post("/api/order-statuses/impact",
                               json={"to_order": ["Kompletná"]}).get_json()
    assert j["orders"] == 3
    assert j["mailable"] == 1


def test_an_unreadable_export_says_UNKNOWN_rather_than_a_reassuring_zero(iso, monkeypatch):
    """A preview that cannot be computed must SAY so. Answering 0 would be the worst of both
    worlds: the card would wave the change through silently, on no evidence at all."""
    def _boom():
        raise OSError("export not there")
    monkeypatch.setattr(webapp, "_orders_csv_cached", _boom)
    j = _client_as(ADMIN).post("/api/order-statuses/impact",
                               json={"to_order": ["Vybavuje sa", "Kompletná"]}).get_json()
    assert j["unknown"] is True
    assert j["added"] == ["Kompletná"]          # what he changed is still knowable


def test_the_preview_is_READ_ONLY_and_admin_only(iso, monkeypatch):
    """It runs on a candidate the manager has NOT saved yet, so it must not touch the stored
    configuration — and it reads the orders export, which is not public reading."""
    _impact_export(monkeypatch, [_impact_row("Kompletná", "99003013")])
    before = webapp._order_statuses()
    assert _client_as(ADMIN).post("/api/order-statuses/impact",
                                  json={"to_order": ["Kompletná"]}).status_code == 200
    assert webapp._order_statuses() == before
    assert not os.path.exists(os.fspath(webapp.ORDER_STATUSES))
    assert _client_as(USER).post("/api/order-statuses/impact",
                                 json={"to_order": ["Kompletná"]}).status_code == 403
    assert webapp.app.test_client().post("/api/order-statuses/impact",
                                         json={"to_order": ["X"]}).status_code == 401


# ── PR #298 review: the SECOND customer-mail path, and previews of a broken config ─────────
# `run_orders_reminder` was made fail-CLOSED on `bad-status-config` in PR #295's review
# (automation-health §3): an unusable file must not silently restore the built-in „Vybavuje sa"
# and re-arm mails to the very customers the manager excluded. The Pošta escalation is the same
# kind of mail on the same configuration — and it was left fail-OPEN, because `_posta_statuses`
# went through `_order_statuses()`, the wrapper that throws the reason away.
_BROKEN = {"to_order": ["Vybavuje sa"], "terminal": ["Vybavuje sa", "Vybavená"],
           "known_open": [], "cancelled": ["Stornovaná"]}       # one status in two lists


def _posta_shipment_run(iso, tmp_path, monkeypatch):
    """One shipment that WOULD be escalated, with the tracking round-trip stubbed out.
    Returns (stats, mails) — `mails` is every _send_mail_html call the run made."""
    monkeypatch.setattr(webapp, "POSTA_STATE", str(tmp_path / "posta.json"))
    monkeypatch.setattr(webapp, "_orders_csv_cached", lambda: b"")
    ship = {"code": "99004001", "packageNumber": "EF000000901SK"}
    monkeypatch.setattr(webapp.posta_uncollected, "shipments_from_orders_csv",
                        lambda *a, **k: [ship])
    monkeypatch.setattr(webapp.posta_uncollected, "source_coverage", lambda *a, **k: {
        "eligible_orders": 40, "dispatched_orders": 30, "dispatched_with_package": 30,
        "dispatched_without_package": 0, "missing_package": 0,
        "days_since_last_package": 1, "dispatched_status_unknown": False, "degraded": False})
    monkeypatch.setattr(webapp, "_fetch_tracking", lambda pkg: {})
    monkeypatch.setattr(webapp.posta_uncollected, "terminal_state", lambda tj: "")
    monkeypatch.setattr(webapp.posta_uncollected, "evaluate_shipment", lambda s, tj, esc: {
        "invalid": False, "send": True, "uncollected": True,
        "orderCode": s["code"], "packageNumber": s["packageNumber"],
        "email": "k@example.com", "email_subject": "Zásielka čaká", "email_body": "<p>x</p>",
        "new_state_value": "1|2026-07-27", "count": 1, "last_sent": "", "call_needed": False,
        "name": "Zákazník", "phone": "", "office_name": "Pošta 1", "office_addr": "Ulica 1",
        "retained_till": "2026-08-03", "notified_since": "2026-07-20", "days_at_post": 3,
        "tracking_link": "https://x/", "admin_link": "https://y/"})
    mails = []
    monkeypatch.setattr(webapp, "_send_mail_html",
                        lambda *a, **k: (mails.append(a[0]), True)[1])
    return webapp.run_posta_uncollected(), mails


def test_a_BROKEN_status_config_sends_no_posta_escalation_mail_either(iso, tmp_path,
                                                                     monkeypatch, caplog):
    """The whole finding: with an unusable configuration the reminders stop (PR #295 review)
    while Pošta went on mailing REAL CUSTOMERS off the built-in defaults. Both are the same
    escalation to the same person on the same file, so both get the prune's answer — render,
    do not act. Worse than a wasted mail: if the manager had correctly reconfigured a renamed
    cancelled status, the defaults put cancelled orders back into the chased set."""
    _write(iso, _BROKEN)
    stats, mails = _posta_shipment_run(iso, tmp_path, monkeypatch)
    assert mails == []                                  # not one customer heard from us
    assert stats["status_config_broken"] is True
    assert stats["source_degraded"] is True             # never a green ✅ over a blind run
    assert stats["uncollected"] == 1                    # …but the row stays on the tab
    assert any("nastavenie stavov" in r.getMessage() for r in caplog.records
               if r.levelname == "ERROR")


def test_a_HEALTHY_status_config_still_mails_the_customer(iso, tmp_path, monkeypatch):
    """The control the fix above must not break: fail-closed is only worth anything if the
    open path still works. A „never send" patch would be indistinguishable without it."""
    _write(iso, {"to_order": ["Vybavuje sa"], "terminal": ["Vybavená", "Stornovaná"],
                 "known_open": [], "cancelled": ["Stornovaná"]})
    stats, mails = _posta_shipment_run(iso, tmp_path, monkeypatch)
    assert mails == ["k@example.com"]
    assert stats["status_config_broken"] is False
    assert stats["source_degraded"] is False


def test_the_preview_measures_against_what_is_EFFECTIVE_not_what_the_loader_renders(
        iso, monkeypatch):
    """The preview's blind spot on the BIGGEST wave there is. With an unusable configuration
    the reminders send NOTHING, so the effective open set is empty — but the preview subtracted
    the DEFAULTS the loader renders, reported „nothing new" and let the whole backlog go out in
    one wave the moment the manager repaired the file. Measured by the reviewer on a corrupt
    store: preview 0, actually mail-eligible right after saving 37."""
    _write(iso, _BROKEN)
    _impact_export(monkeypatch, [_impact_row("Vybavuje sa", "99003020", email="a@example.com"),
                                 _impact_row("Vybavuje sa", "99003021", email="b@example.com")])
    j = _client_as(ADMIN).post("/api/order-statuses/impact",
                               json={"to_order": ["Vybavuje sa"]}).get_json()
    assert j["added"] == ["Vybavuje sa"]        # nothing is effective today, so all of it is new
    assert j["orders"] == 2
    assert j["customers"] == 2
    assert j["config_broken"] is True           # so the dialog can say WHY the number is large


def test_a_PARTIAL_payload_that_narrows_terminal_alone_is_still_cross_checked(iso):
    """The cross-check ran only `if "cancelled" in body`, so an API call editing `terminal`
    alone produced exactly the drift the check exists for: „Stornovaná" stays cancelled while
    dropping out of terminal, `terminal − cancelled` subtracts nothing, and cancelled orders
    return to the set the escalation chases."""
    _write(iso, {"to_order": ["Vybavuje sa"], "terminal": ["Vybavená", "Stornovaná"],
                 "known_open": [], "cancelled": ["Stornovaná"]})
    r = _client_as(ADMIN).post("/api/order-statuses", json={"terminal": ["Vybavená"]})
    assert r.status_code == 400, r.get_json()
    assert "Stornovaná" in r.get_json()["error"]
    # and the stored configuration is untouched by the refusal
    assert webapp._order_statuses()["terminal"] == frozenset({"Vybavená", "Stornovaná"})
