"""#212 — pruning the per-line flag keys whose ORDER has left the open set.

`ordered_items` / `waiting_items` / `instock_items` / `unavailable_items` are keyed
`<orderCode>|<itemCode>` and were never pruned, so every order the manager ever touched
left its keys behind for good. 141 of the 217 keys in his live stores were orphans when
this was measured.

The whole risk of this ticket is in the DELETING, so what is pinned here is mostly what
the prune must NOT do. Three independent guards, and each gets its own test:

  1. POSITIVE evidence of PRESENCE — a key goes only when its order is IN the export. An
     order the export does not mention at all is not „closed", it is UNSEEN: the export is
     a 90-day window (`ORDERS_EXPORT_WINDOW_DAYS`), and a truncated download drops rows
     rather than changing them. That rule is what makes a short export able to prune FEWER
     keys and never more.
  2. POSITIVE evidence of STATE — every row of that order carries a status that MEANS
     finished (`ORDERS_TERMINAL_STATUSES`). „Anything that is not the one open literal" was
     the same negative evidence applied to the status, and this shop uses NINE statuses:
     an unknown or newly added one used to read as closed and take live work with it.
  3. A fail-closed floor on the source, the same shape as the catalogue's
     `EXPORT_MIN_CODES`: an export carrying implausibly few orders prunes NOTHING at all.

Stores are seeded through the app's own savers so the `protect=True` machinery (#261/#265)
is exercised exactly as in production, not bypassed by writing JSON by hand.
"""
import json
import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webreview"))
import app as webapp  # noqa: E402

_HEAD = "code;date;statusName;itemCode;itemName\r\n"

# The three keys the tests reason about, one per fate.
_OPEN = "99002001|A1"        # its order is still „Vybavuje sa" -> must stay
_CLOSED = "99002002|B1"      # its order is in the export and Vybavená -> must go
_UNSEEN = "99001500|C1"      # its order is not in the export at all -> must stay
_CUT = "99002003|D1"         # its order appears ONLY in a row the download cut -> must stay


def _export(rows, filler=60):
    """A plausible export: `rows` plus enough unrelated orders to clear the floor."""
    body = "".join(rows)
    body += "".join(f"99003{i:03d};2026-01-01 09:00:00;Vybavená;Z{i};Nieco\r\n"
                    for i in range(filler))
    return (_HEAD + body).encode("cp1250")


_OPEN_ROW = "99002001;{};Vybavuje sa;A1;Bunda\r\n".format(
    (date.today() - timedelta(days=2)).isoformat() + " 09:00:00")
# Old enough to clear the order-age floor (`ORDERS_PRUNE_MIN_ORDER_AGE_DAYS`) on any run day.
_CLOSED_ROW = "99002002;{};Vybavená;B1;Ciapka\r\n".format(
    (date.today() - timedelta(days=120)).isoformat() + " 09:00:00")


@pytest.fixture
def stores(tmp_path, monkeypatch):
    """All four flag stores in tmp, each seeded with the same three keys."""
    paths = {}
    for attr, fname in (("ORDERED", "ordered_items.json"),
                        ("WAITING", "waiting_items.json"),
                        ("INSTOCK", "instock_items.json"),
                        ("UNAVAIL", "unavailable_items.json")):
        p = tmp_path / fname
        monkeypatch.setattr(webapp, attr, str(p))
        paths[fname] = p
    for save in (webapp._save_ordered, webapp._save_waiting,
                 webapp._save_instock, webapp._save_unavailable):
        save({_OPEN: True, _CLOSED: True, _UNSEEN: True})
    return paths


def _keys(path):
    return sorted(json.loads(path.read_text(encoding="utf-8")))


def test_prunes_only_the_keys_whose_order_the_export_shows_as_CLOSED(stores):
    """The one case the ticket is about — and the two it must not touch, in the same run.

    „Not among the open orders" is deliberately NOT the rule: it would sweep away
    `_UNSEEN` too, whose order is merely outside the 90-day window (or missing from a
    truncated download). That order may still be open; we simply cannot see it.
    """
    res = webapp._prune_orphan_line_flags(_export([_OPEN_ROW, _CLOSED_ROW]))

    assert res["pruned"] == 4, res            # the closed key, once per store
    assert res["skipped"] == "", res
    for path in stores.values():
        assert _keys(path) == sorted([_OPEN, _UNSEEN]), path.name


def test_an_implausibly_small_export_prunes_NOTHING(stores):
    """The fail-closed floor. The export here says the same thing about `_CLOSED` as the
    healthy one above — it is only SMALL — and that alone must disarm the prune."""
    res = webapp._prune_orphan_line_flags(_export([_OPEN_ROW, _CLOSED_ROW], filler=3))

    assert res["pruned"] == 0, res
    assert res["skipped"] == "implausible-source", res
    for path in stores.values():
        assert _keys(path) == sorted([_OPEN, _CLOSED, _UNSEEN]), path.name


def test_an_empty_or_unparsable_export_prunes_NOTHING(stores):
    """Degenerate sources take the same door as a small one — never „nothing is open, so
    everything can go", which is the shape that turns one bad fetch into a wipe."""
    # each names its OWN reason — „there is no statusName column" and „the body would not
    # parse" send an operator to different places, so they are not collapsed into one label
    for blob, why in ((b"", "no-status-column"),
                      (_HEAD.encode("cp1250"), "implausible-source"),
                      (b"not a csv at all", "no-status-column")):
        res = webapp._prune_orphan_line_flags(blob)
        assert res["pruned"] == 0 and res["skipped"] == why, (blob, res)
    for path in stores.values():
        assert _keys(path) == sorted([_OPEN, _CLOSED, _UNSEEN]), path.name


def test_losing_rows_from_the_export_can_only_prune_FEWER_keys(stores):
    """Truncation safety, asserted rather than argued. `statusName` is the whole ORDER's
    status and sits on every one of its rows, so a cut export can only make an order
    DISAPPEAR — and a disappeared order is UNSEEN, which is never pruned. The same run
    against the full export removes the key (test above), so this is a real difference."""
    res = webapp._prune_orphan_line_flags(_export([_OPEN_ROW]))    # _CLOSED_ROW cut out

    assert res["pruned"] == 0, res
    assert res["skipped"] == "", res           # the source was plausible, just incomplete
    for path in stores.values():
        assert _keys(path) == sorted([_OPEN, _CLOSED, _UNSEEN]), path.name


def test_a_key_that_is_not_per_line_is_never_judged(stores, tmp_path):
    """A key with no `<order>|<item>` shape cannot be attributed to an order, so it cannot
    be shown to be orphaned either. It stays — even when it happens to read like a closed
    order code."""
    webapp._save_ordered({"99002002": True, _CLOSED: True, "|B1": True})

    webapp._prune_orphan_line_flags(_export([_OPEN_ROW, _CLOSED_ROW]))

    assert _keys(tmp_path / "ordered_items.json") == sorted(["99002002", "|B1"])


def test_a_store_with_nothing_to_prune_is_not_REWRITTEN(stores):
    """These stores are `protect=True` — the manager's irreplaceable work. A no-op write
    is not harmless there: it burns the read receipt the shrink guard depends on and
    rewrites a file nothing asked to change. Same rule `_write_status_flag` follows."""
    before = {name: (p.read_bytes(), p.stat().st_mtime_ns) for name, p in stores.items()}

    res = webapp._prune_orphan_line_flags(_export([_OPEN_ROW]))    # nothing is prunable

    assert res["pruned"] == 0, res
    for name, p in stores.items():
        assert (p.read_bytes(), p.stat().st_mtime_ns) == before[name], name


def test_the_prune_reports_what_it_removed(stores, caplog):
    """It deletes the manager's markings, so it must say exactly which ones — a count
    alone leaves nothing to recover an argument from three weeks later."""
    with caplog.at_level("INFO", logger="webreview"):
        webapp._prune_orphan_line_flags(_export([_OPEN_ROW, _CLOSED_ROW]))

    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert _CLOSED in logged, logged
    assert "ordered_items.json" in logged, logged
    assert _OPEN not in logged and _UNSEEN not in logged, (
        "a key that was KEPT was reported as removed", logged)


# ── the ways a plausible-LOOKING export can still be lying (PR #292 review) ────

def test_an_export_with_NO_open_orders_at_all_prunes_nothing(stores):
    """The reviewers' 🔴: „closed" is computed as `seen - still_open`, so anything that
    empties `still_open` while `seen` stays large makes EVERY in-window order look closed
    and deletes the manager's marks wholesale — the floor does not fire, and the
    `protect=True` guard cannot fire either, because the prune is a legitimate
    read-modify-write.

    Two real triggers, both cheap: Shoptet's status names are shop-configurable TEXT and
    the literal is hard-coded, and the export template can change. On the live feed 57 of
    521 orders are open, so „a plausible export in which nothing at all is open" is not a
    quiet week — it is a signal that we are reading something else than we think."""
    rows = [r.replace("Vybavuje sa", "Vybavuje sa (nový názov)") for r in
            (_OPEN_ROW, _CLOSED_ROW)]
    res = webapp._prune_orphan_line_flags(_export(rows))

    assert res["pruned"] == 0, res
    assert res["skipped"] == "no-open-orders", res
    for path in stores.values():
        assert _keys(path) == sorted([_OPEN, _CLOSED, _UNSEEN]), path.name


def test_an_export_without_the_statusName_column_prunes_nothing(stores):
    """Same 🔴 through the other door: no `statusName` column at all (a changed export
    template, or the creds URL repointed at an export that also has a `code` column).
    Every `r.get("statusName")` is then `None` and nothing is open. Caught on the COLUMN,
    before a single row is judged, so the failure is named rather than inferred."""
    head = "code;date;itemCode;itemName\r\n"
    body = "".join(f"99003{i:03d};2026-07-01 09:00:00;Z{i};Nieco\r\n" for i in range(60))
    body += "99002002;2026-07-02 09:00:00;B1;Ciapka\r\n"

    res = webapp._prune_orphan_line_flags((head + body).encode("cp1250"))

    assert res["pruned"] == 0, res
    assert res["skipped"] == "no-status-column", res
    for path in stores.values():
        assert _keys(path) == sorted([_OPEN, _CLOSED, _UNSEEN]), path.name


def test_a_body_cut_mid_row_does_not_judge_that_row(stores):
    """The truncation argument („a cut export can only make an order DISAPPEAR") is true
    for every row EXCEPT the one the cut lands in: `csv.DictReader` hands that one back
    with a truncated or missing `statusName`, so a genuinely OPEN order reads as closed.
    A body that does not end in a newline is incomplete by definition — its last row is
    dropped rather than believed."""
    # `_OPEN_ROW` intact (so the run is not refused for having no open orders at all) plus
    # `_CLOSED_ROW` (which must still be pruned — the cut is not an excuse to stop working)
    # and, cut in half, a SECOND open order whose key must survive.
    webapp._save_ordered({_OPEN: True, _CLOSED: True, _UNSEEN: True, _CUT: True})
    full = _export([_OPEN_ROW, _CLOSED_ROW]).decode("cp1250") + \
        "99002003;{} 09:00:00;Vybavu".format(
            (date.today() - timedelta(days=90)).isoformat())

    res = webapp._prune_orphan_line_flags(full.encode("cp1250"))

    assert res["skipped"] == "", res
    assert _keys(stores["ordered_items.json"]) == sorted([_OPEN, _UNSEEN, _CUT]), (
        "the order in the cut-off row was judged from a half-read status", res)


def test_an_unparsable_body_prunes_nothing_and_does_not_escape(stores):
    """`csv.Error` is neither `ValueError` nor `OSError`, so it would sail through the
    caller's housekeeping `except` and take the whole hourly sync down with it — while the
    comment there promises it cannot. A body carrying a bare CR in an unquoted field does
    exactly that, and `errors="replace"` guarantees such a body always reaches the parser."""
    blob = (_HEAD + "99002002;2026-07-02 09:00:00;Vybav\rená;B1;X\r\n").encode("cp1250")

    res = webapp._prune_orphan_line_flags(blob)     # must NOT raise

    assert res["pruned"] == 0 and res["skipped"] == "unparsable-source", res
    for path in stores.values():
        assert _keys(path) == sorted([_OPEN, _CLOSED, _UNSEEN]), path.name


def test_a_freshly_PLACED_order_keeps_its_marks_for_a_while(stores):
    """The order-age floor: a young ORDER keeps its keys even once the export shows it
    finished.

    What it was reached for: a closed order can be REOPENED — the repo says so itself where
    the reminder dedup store explains why it keeps records — and deleting its marks the same
    hour means the line comes back with the manager's „objednané u dodávateľa" silently gone,
    so he orders it a second time.

    What it covers is the FLOOR only. The real reopen grace — measured from the day this app
    first saw the order closed, since the export has no status-change column — arrived with
    #294 and is pinned in `test_webreview_prune_grace.py`; the floor stays underneath it
    because an unreadable date is an unknown age, and that must never read as „old enough"."""
    fresh = "99002002;{};Vybavená;B1;Ciapka\r\n".format(
        (date.today() - timedelta(days=3)).isoformat() + " 09:00:00")

    res = webapp._prune_orphan_line_flags(_export([_OPEN_ROW, fresh]))

    assert res["pruned"] == 0, res
    assert res["skipped"] == "", res            # the source was fine; the order is young
    for path in stores.values():
        assert _keys(path) == sorted([_OPEN, _CLOSED, _UNSEEN]), path.name


# ── closure is decided by an ALLOW-LIST of finished statuses (PR #292 review #2) ──

# Every status the live 90-day export actually carries (2026-07-28), with the verdict the
# prune must reach for it. Measured counts, so the table is the shop's reality and not a
# guess: Vybavená 387 · Stornovaná 63 · Vybavuje sa 57 · Vybavená výmena 4 · Osob. odber 3
# · Vybavený Dobropis 3 · Kompletná 2 · Vratený tovar 1 · Výmena tovaru 1.
#
# The four that prune are the ones this shop marks as FINISHED, and two independent
# signals agree on exactly them: the naming convention (a „Vybavená/Vybavený" prefix marks
# the completed form — „Vybavená výmena" against „Výmena tovaru", „Vybavený Dobropis"
# against „Vratený tovar") and the tracking number, i.e. the goods physically left:
# Vybavená 250/387, Vybavená výmena 4/4, Vybavený Dobropis 3/3 — against Kompletná 0/2,
# Vratený tovar 0/1, Výmena tovaru 0/1, Osob. odber 0/3. „Stornovaná" is the exception that
# proves it (1/63 — a cancelled order has nothing to dispatch) and is unambiguously over.
_LIVE_STATUSES = {
    "Vybavená": True,
    "Stornovaná": True,
    "Vybavená výmena": True,
    "Vybavený Dobropis": True,
    "Vybavuje sa": False,        # the open literal itself
    "Osob. odber": False,        # waiting for the customer to collect — live
    "Výmena tovaru": False,      # an exchange still being handled
    "Vratený tovar": False,      # goods came back, the credit note is not issued yet
    "Kompletná": False,          # both live ones are 2 and 5 days old with no tracking
    # not a status the shop uses, but the shape a shifted or half-written export produces —
    # and „unknown" must include „unreadable", or a blank column would prune everything
    "": False,
    "   ": False,
}


@pytest.mark.parametrize("status,prunes", sorted(_LIVE_STATUSES.items()))
def test_only_a_status_that_MEANS_finished_prunes_its_keys(stores, status, prunes):
    """Closure must be POSITIVE membership in the finished set, never „anything that is not
    the one open literal". Every status this shop actually uses, one verdict each."""
    row = "99002002;{};{};B1;Ciapka\r\n".format(
        (date.today() - timedelta(days=120)).isoformat() + " 09:00:00", status)

    res = webapp._prune_orphan_line_flags(_export([_OPEN_ROW, row]))

    assert res["skipped"] == "", (status, res)
    assert res["pruned"] == (4 if prunes else 0), (status, res)
    kept = sorted([_OPEN, _UNSEEN]) if prunes else sorted([_OPEN, _CLOSED, _UNSEEN])
    for path in stores.values():
        assert _keys(path) == kept, (status, path.name)


def test_a_status_the_shop_ADDS_or_renames_into_is_never_pruned(stores):
    """The blocker. `closed = seen - still_open` reads every unknown status as finished, and
    the „no open orders at all" guard only fires on a TOTAL rename — a PARTIAL one walks
    straight through it.

    Shoptet's status names are shop-configurable TEXT. Rename the open status while a single
    order is left on the old literal (or simply add a new open status like „Čaká na
    dodávateľa"), and `still_open` is non-empty, the floor counts only `seen`, and the
    `protect=True` shrink guard cannot fire because the prune IS a legitimate
    read-modify-write. Measured on the manager's live stores, that scenario deleted 94 keys
    from orders that are still open (176/12/13/16 -> 99/6/9/9).
    """
    renamed = "99002002;{};Čaká na dodávateľa;B1;Ciapka\r\n".format(
        (date.today() - timedelta(days=45)).isoformat() + " 09:00:00")

    res = webapp._prune_orphan_line_flags(_export([_OPEN_ROW, renamed]))

    assert res["skipped"] == "", res      # the source is fine — the status is just not over
    assert res["pruned"] == 0, res
    for path in stores.values():
        assert _keys(path) == sorted([_OPEN, _CLOSED, _UNSEEN]), path.name


@pytest.mark.parametrize("unfinished_first", [False, True])
def test_one_unfinished_row_keeps_the_WHOLE_order(stores, unfinished_first):
    """`statusName` is the whole order's status repeated on every line, so a mixed order
    should not exist — but if one ever does, the answer must be the conservative one: a
    single row that is not a finished status keeps every key of that order.

    BOTH row orders, because one order alone does not pin it: with the finished row first, a
    „later row overrides" bug is invisible (verified by mutation — a terminal row cancelling
    an earlier unfinished one left the whole suite green)."""
    old = (date.today() - timedelta(days=120)).isoformat() + " 09:00:00"
    mixed = ["99002002;{};Vybavená;B1;Ciapka\r\n".format(old),
             "99002002;{};Osob. odber;B2;Rukavice\r\n".format(old)]
    if unfinished_first:
        mixed.reverse()

    res = webapp._prune_orphan_line_flags(_export([_OPEN_ROW] + mixed))

    assert res["pruned"] == 0, res
    for path in stores.values():
        assert _keys(path) == sorted([_OPEN, _CLOSED, _UNSEEN]), path.name


def test_the_run_reports_the_statuses_it_did_not_RECOGNISE(stores):
    """An added status silently narrows what gets pruned, which is the failure this
    allow-list trades for the old one. It must not be invisible: the run names the statuses
    it has no verdict for, so a human can decide whether one belongs on the list."""
    row = "99002002;{};Čaká na dodávateľa;B1;Ciapka\r\n".format(
        (date.today() - timedelta(days=45)).isoformat() + " 09:00:00")

    res = webapp._prune_orphan_line_flags(_export([_OPEN_ROW, row]))

    assert res["unknown_statuses"] == ["Čaká na dodávateľa"], res


def test_a_status_we_deliberately_classified_is_NOT_reported_as_unknown(stores):
    """The signal only works while it is quiet. Every status in `_LIVE_STATUSES` was weighed
    and deliberately left off the terminal list — reporting those four as „unknown" would put
    a permanent four-item line on the card and in every hourly log, and the ONE thing the
    signal exists for (a genuinely new status) would hide inside that noise."""
    old = (date.today() - timedelta(days=120)).isoformat() + " 09:00:00"
    rows = ["99002002;{};Osob. odber;B1;Ciapka\r\n".format(old),
            "99002004;{};Výmena tovaru;E1;Vesta\r\n".format(old),
            "99002005;{};Vratený tovar;F1;Nohavice\r\n".format(old),
            "99002006;{};Kompletná;G1;Cepica\r\n".format(old)]

    res = webapp._prune_orphan_line_flags(_export([_OPEN_ROW] + rows))

    assert res["unknown_statuses"] == [], res
    assert res["pruned"] == 0, res      # still not pruned — known does not mean finished


def test_an_EMPTY_status_is_reported_rather_than_silently_narrowing_the_prune(stores):
    """A blank `statusName` is not a status we know, so it keeps its keys — correct. But it
    must not do so INVISIBLY: a partially empty status column would then narrow the prune
    with no trace anywhere, which is exactly the silence the allow-list is supposed to end.
    It gets a named sentinel instead of being dropped for being falsy."""
    row = "99002002;{};;B1;Ciapka\r\n".format(
        (date.today() - timedelta(days=120)).isoformat() + " 09:00:00")

    res = webapp._prune_orphan_line_flags(_export([_OPEN_ROW, row]))

    assert res["pruned"] == 0, res
    assert res["unknown_statuses"] == ["(prázdny stav)"], res


def test_the_unknown_status_list_is_BOUNDED_in_count_and_in_length(stores):
    """`statusName` is an untrusted CSV cell, and a shifted or corrupt export puts arbitrary
    row content in it — which this list then logs, persists into `automations.json` and
    renders on the card. A corrupt export must not be able to dump its contents (possibly
    customer data) into the manager's automation store for ever."""
    old = (date.today() - timedelta(days=120)).isoformat() + " 09:00:00"
    rows = ["9900{:04d};{};Neznamy {} {};H{};X\r\n".format(4000 + i, old, i, "x" * 200, i)
            for i in range(40)]

    res = webapp._prune_orphan_line_flags(_export([_OPEN_ROW] + rows))

    assert len(res["unknown_statuses"]) <= 20, res["unknown_statuses"]
    assert all(len(s) <= 90 for s in res["unknown_statuses"]), res["unknown_statuses"]


def test_an_order_with_no_usable_date_is_never_pruned(stores):
    """Age is the grace period, so a row whose `date` cannot be read is a row whose age we
    do not know — and an unknown age must not be treated as „old enough"."""
    undated = "99002002;;Vybavená;B1;Ciapka\r\n"
    junk = "99002002;neplatný dátum;Vybavená;B1;Ciapka\r\n"

    for row in (undated, junk):
        res = webapp._prune_orphan_line_flags(_export([_OPEN_ROW, row]))
        assert res["pruned"] == 0, (row, res)
    assert _keys(stores["ordered_items.json"]) == sorted([_OPEN, _CLOSED, _UNSEEN])
