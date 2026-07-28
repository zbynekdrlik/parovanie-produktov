"""#294 — the grace before the prune deletes an order's marks must run from when the order
CLOSED, not from when it was placed.

PR #292 was honest about not having this: the export carries 67 columns and its only date is
`date` = order creation, so the moment an order closed cannot be read from the source. The
consequence it named: an order **placed 40 days ago and closed today** is pruned on the very
next hourly run, with NO grace at all — and that is precisely the long supplier-wait order
these marks exist for (a live open flagged order runs up to 75 days).

The grace protects against a REOPEN: a „Vybavená" order can go back to „Vybavuje sa", and a
line that comes back without the manager's „objednané u dodávateľa" is a line he orders from
the supplier a second time.

So the app measures the grace itself — `orders_closed_seen.json`, „the day we FIRST saw this
order in a terminal status". What is pinned here:

  1. the grace is real: an old order that closes today keeps its marks;
  2. the record is written BEFORE the run decides, so a newly closed order gets the FULL
     grace on the very first run that sees it — deciding first would make every newly closed
     order „unknown", i.e. no grace at all, for ever (the bug itself);
  3. nothing is ever STRANDED, and nothing is deleted early to achieve that: the export
     window is 90 days from the ORDER date, so an order that closes late leaves it before a
     30-day grace elapses. The RECORD outlives the export, and it — not the order's absence
     — is what makes the key deletable afterwards (store-prune §1c);
  4. a reopen restarts the grace, and the store cannot grow without bound;
  5. a lost / corrupt store prunes NOTHING that run — it re-records everything as closed
     today, which is fail-CLOSED by construction.
"""
import json
import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webreview"))
import app as webapp  # noqa: E402

_HEAD = "code;date;statusName;itemCode;itemName\r\n"

_KEY = "99002002|B1"          # the key under test, one order, one line
_OTHER = "99002001|A1"        # an order that stays open — never touched


def _row(code, status, days_old, item):
    return "{};{};{};{};Ciapka\r\n".format(
        code, (date.today() - timedelta(days=days_old)).isoformat() + " 09:00:00",
        status, item)


def _export(rows, filler=60):
    """A plausible export: `rows` plus enough unrelated orders to clear the floor."""
    body = "".join(rows) + "".join(
        f"99003{i:03d};2026-01-01 09:00:00;Vybavená;Z{i};Nieco\r\n" for i in range(filler))
    return (_HEAD + body).encode("cp1250")


_OPEN_ROW = _row("99002001", "Vybavuje sa", 2, "A1")


@pytest.fixture
def stores(tmp_path, monkeypatch):
    """The four flag stores + the new grace store, all in tmp."""
    paths = {}
    for attr, fname in (("ORDERED", "ordered_items.json"),
                        ("WAITING", "waiting_items.json"),
                        ("INSTOCK", "instock_items.json"),
                        ("UNAVAIL", "unavailable_items.json"),
                        ("ORDERS_CLOSED_SEEN", "orders_closed_seen.json")):
        p = tmp_path / fname
        monkeypatch.setattr(webapp, attr, str(p))
        paths[fname] = p
    for save in (webapp._save_ordered, webapp._save_waiting,
                 webapp._save_instock, webapp._save_unavailable):
        save({_KEY: True, _OTHER: True})
    return paths


def _keys(path):
    return sorted(json.loads(path.read_text(encoding="utf-8")))


def _grace(paths):
    p = paths["orders_closed_seen.json"]
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _seed_grace(paths, mapping):
    paths["orders_closed_seen.json"].write_text(json.dumps(mapping), encoding="utf-8")


def _days_ago(n):
    return (date.today() - timedelta(days=n)).isoformat()


# ── 1. the grace is real ───────────────────────────────────────────────────────

def test_an_old_order_that_CLOSED_TODAY_keeps_its_marks(stores):
    """The ticket in one test. The order is 40 days old, so the order-age floor lets it
    through — but we are seeing it closed for the FIRST time, so the reopen grace has not
    started running yet. Before #294 this was deleted on the very next hourly run."""
    res = webapp._prune_orphan_line_flags(
        _export([_OPEN_ROW, _row("99002002", "Vybavená", 40, "B1")]))

    assert res["pruned"] == 0, res
    assert res["skipped"] == "", res
    for name, path in stores.items():
        if name != "orders_closed_seen.json":
            assert _keys(path) == sorted([_KEY, _OTHER]), name
    # …and the day it was first seen closed is now on record, so the grace can start
    assert _grace(stores) == {"99002002": date.today().isoformat()}, _grace(stores)


def test_once_the_grace_has_ELAPSED_the_same_order_is_pruned(stores):
    """The prune still does its job — #212 is not undone, only delayed by the grace."""
    _seed_grace(stores, {"99002002": _days_ago(webapp.ORDERS_PRUNE_REOPEN_GRACE_DAYS)})

    res = webapp._prune_orphan_line_flags(
        _export([_OPEN_ROW, _row("99002002", "Vybavená", 40, "B1")]))

    assert res["pruned"] == 4, res            # once per flag store
    for name, path in stores.items():
        if name != "orders_closed_seen.json":
            assert _keys(path) == [_OTHER], name


def test_one_day_short_of_the_grace_is_still_TOO_SOON(stores):
    """The boundary, from the safe side — an off-by-one here deletes a day early, and a
    deletion cannot be undone."""
    _seed_grace(stores, {"99002002": _days_ago(webapp.ORDERS_PRUNE_REOPEN_GRACE_DAYS - 1)})

    res = webapp._prune_orphan_line_flags(
        _export([_OPEN_ROW, _row("99002002", "Vybavená", 40, "B1")]))

    assert res["pruned"] == 0, res


def test_the_order_age_floor_still_applies_UNDER_the_grace(stores):
    """A young order is not pruned even when it has been sitting closed for a long time
    (a hand-edited or clock-skewed record must not become a shortcut past the floor that
    was already there)."""
    _seed_grace(stores, {"99002002": _days_ago(365)})

    res = webapp._prune_orphan_line_flags(
        _export([_OPEN_ROW, _row("99002002", "Vybavená", 3, "B1")]))

    assert res["pruned"] == 0, res


def test_an_order_with_no_usable_DATE_is_still_never_pruned(stores):
    """Unchanged from #292 and pinned again here: a row whose date cannot be read is a row
    whose age we do not know, and an unknown age is never „old enough" — not even with a
    record that says the grace is long over."""
    _seed_grace(stores, {"99002002": _days_ago(365)})

    res = webapp._prune_orphan_line_flags(
        _export([_OPEN_ROW, "99002002;neplatný dátum;Vybavená;B1;Ciapka\r\n"]))

    assert res["pruned"] == 0, res


# ── 2/3. never STRANDED — the RECORD is what carries the key past the window ───

def test_an_order_that_has_LEFT_the_export_is_pruned_once_its_grace_elapses(stores):
    """store-prune §1c: check that the grace and the source window do not leave a key
    stuck. The export is a 90-day window measured from the ORDER date, so an order that
    closes late leaves it before a 30-day grace can elapse — and if „still in the export"
    were required, that key could never be deleted again.

    It is deleted on OUR OWN RECORD, which is a different thing from the „I cannot see it,
    so it must be gone" that store-prune §1 forbids: we watched this order carry a terminal
    status and wrote the day down. Absence is not the evidence here; the record is."""
    _seed_grace(stores, {"99002002": _days_ago(webapp.ORDERS_PRUNE_REOPEN_GRACE_DAYS)})

    res = webapp._prune_orphan_line_flags(_export([_OPEN_ROW]))   # 99002002 is NOT in it

    assert res["pruned"] == 4, res
    assert _grace(stores) == {}, _grace(stores)          # …and the record goes with it


def test_an_order_that_has_left_the_export_still_serves_its_FULL_grace(stores):
    """The half that makes the branch above safe: leaving the window is not a shortcut.
    An order we saw closed yesterday keeps its marks even once the export forgets it."""
    _seed_grace(stores, {"99002002": _days_ago(1)})

    res = webapp._prune_orphan_line_flags(_export([_OPEN_ROW]))

    assert res["pruned"] == 0, res
    assert _grace(stores) == {"99002002": _days_ago(1)}, _grace(stores)


def test_an_order_we_never_saw_closed_is_NEVER_pruned_once_it_leaves(stores):
    """The rule that keeps the branch above from becoming „not in the export = gone": with
    no record there is no evidence, so the key stays for ever. That is the correct answer —
    the manager can still be waiting on it."""
    res = webapp._prune_orphan_line_flags(_export([_OPEN_ROW]))

    assert res["pruned"] == 0, res
    assert _grace(stores) == {}, _grace(stores)


# ── 4. reopen restarts the grace; the store stays bounded ─────────────────────

def test_a_REOPENED_order_loses_its_record_so_the_grace_starts_AGAIN(stores):
    """The whole reason the grace exists. If the record survived a reopen, the clock would
    keep running from the FIRST closure and the second closure would get no grace at all."""
    _seed_grace(stores, {"99002002": _days_ago(20)})

    res = webapp._prune_orphan_line_flags(
        _export([_OPEN_ROW, _row("99002002", "Vybavuje sa", 40, "B1")]))

    assert res["pruned"] == 0, res
    assert _grace(stores) == {}, _grace(stores)


def test_an_order_the_export_does_not_MENTION_keeps_its_record(stores):
    """Unseen is not reopened — the same positive-evidence rule the prune itself follows
    (store-prune §1). A truncated download must not silently restart everybody's grace."""
    _seed_grace(stores, {"99002002": _days_ago(20)})

    webapp._prune_orphan_line_flags(_export([_OPEN_ROW]))

    assert _grace(stores) == {"99002002": _days_ago(20)}, _grace(stores)


def test_only_orders_that_HAVE_marks_are_recorded(stores):
    """Bounded growth, half one: the store exists to time the deletion of flag keys, so an
    order with no key owns nothing here. The 60 filler orders are all finished."""
    webapp._prune_orphan_line_flags(
        _export([_OPEN_ROW, _row("99002002", "Vybavená", 40, "B1")]))

    assert sorted(_grace(stores)) == ["99002002"], _grace(stores)


def test_the_record_goes_with_the_LAST_key_it_was_timing(stores):
    """Bounded growth, half two: once the keys are gone the record has nothing left to
    time, so it must not linger for ever."""
    _seed_grace(stores, {"99002002": _days_ago(webapp.ORDERS_PRUNE_REOPEN_GRACE_DAYS)})

    res = webapp._prune_orphan_line_flags(
        _export([_OPEN_ROW, _row("99002002", "Vybavená", 40, "B1")]))

    assert res["pruned"] == 4, res
    assert _grace(stores) == {}, _grace(stores)


def test_the_grace_store_is_NOT_rewritten_when_nothing_changed(stores):
    """store-prune §3 — a no-op write burns a read receipt and rewrites a file nobody asked
    to change. Pinned over the bytes AND the mtime, like the flag stores."""
    webapp._prune_orphan_line_flags(
        _export([_OPEN_ROW, _row("99002002", "Vybavená", 40, "B1")]))
    p = stores["orders_closed_seen.json"]
    before, mtime = p.read_bytes(), p.stat().st_mtime_ns

    webapp._prune_orphan_line_flags(
        _export([_OPEN_ROW, _row("99002002", "Vybavená", 40, "B1")]))

    assert p.read_bytes() == before
    assert p.stat().st_mtime_ns == mtime


# ── 5. a lost store is fail-CLOSED ────────────────────────────────────────────

@pytest.mark.parametrize("blob", ["{ this is not json", '["a list"]', '{"99002002": 17}'])
@pytest.mark.parametrize("age", [40, 85])
def test_an_UNREADABLE_or_nonsense_grace_store_deletes_nothing_that_run(stores, blob, age):
    """Losing this store must never cost the manager a mark — that is the premise the whole
    `protect=False` / not-in-`backup_data.sh` decision rests on. It degrades to „we have no
    idea when anything closed", every finished order is recorded as closed TODAY, and the
    run therefore deletes nothing; the grace simply starts over.

    The 85-day case is the one that has to be tested EXPLICITLY: a first cut carried a
    „last chance" branch that fired on the ORDER's age alone, without consulting the record
    — so an order in the last days of the export window was deleted even with the store
    gone, i.e. the fail-closed claim was false in exactly the band it mattered."""
    stores["orders_closed_seen.json"].write_text(blob, encoding="utf-8")

    res = webapp._prune_orphan_line_flags(
        _export([_OPEN_ROW, _row("99002002", "Vybavená", age, "B1")]))

    assert res["pruned"] == 0, res
    assert _grace(stores) == {"99002002": date.today().isoformat()}, _grace(stores)


def test_a_run_whose_records_NET_OUT_does_not_create_the_file(stores):
    """The same §3 rule from the other side, and the case a boolean „something changed"
    flag gets wrong: a record added and then dropped again in the SAME run leaves the set
    exactly as it was, so there is nothing to write — not even an empty file."""
    _seed_grace(stores, {"99002002": _days_ago(webapp.ORDERS_PRUNE_REOPEN_GRACE_DAYS)})
    webapp._prune_orphan_line_flags(
        _export([_OPEN_ROW, _row("99002002", "Vybavená", 40, "B1")]))
    stores["orders_closed_seen.json"].unlink()          # the store is now legitimately empty

    res = webapp._prune_orphan_line_flags(_export([_OPEN_ROW]))

    assert res["pruned"] == 0, res
    assert not stores["orders_closed_seen.json"].exists()


def test_a_refused_run_never_touches_the_grace_store(stores):
    """An implausible export prunes nothing — and must not record anything either, or a
    broken feed would start everybody's grace over on a source we have just refused to
    believe."""
    res = webapp._prune_orphan_line_flags(
        _export([_OPEN_ROW, _row("99002002", "Vybavená", 40, "B1")], filler=3))

    assert res["skipped"] == "implausible-source", res
    assert not stores["orders_closed_seen.json"].exists()
