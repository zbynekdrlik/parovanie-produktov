"""#212 — pruning the per-line flag keys whose ORDER has left the open set.

`ordered_items` / `waiting_items` / `instock_items` / `unavailable_items` are keyed
`<orderCode>|<itemCode>` and were never pruned, so every order the manager ever touched
left its keys behind for good. 141 of the 217 keys in his live stores were orphans when
this was measured.

The whole risk of this ticket is in the DELETING, so what is pinned here is mostly what
the prune must NOT do. Two independent guards, and both get their own test:

  1. POSITIVE evidence only — a key goes only when its order is IN the export AND none of
     its rows say „Vybavuje sa". An order the export does not mention at all is not
     „closed", it is UNSEEN: the export is a 90-day window (`ORDERS_EXPORT_WINDOW_DAYS`),
     and a truncated download drops rows rather than changing them. That single rule is
     what makes a short export able to prune FEWER keys and never more.
  2. A fail-closed floor on the source, the same shape as the catalogue's
     `EXPORT_MIN_CODES`: an export carrying implausibly few orders prunes NOTHING at all.

Stores are seeded through the app's own savers so the `protect=True` machinery (#261/#265)
is exercised exactly as in production, not bypassed by writing JSON by hand.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webreview"))
import app as webapp  # noqa: E402

_HEAD = "code;date;statusName;itemCode;itemName\r\n"

# The three keys the tests reason about, one per fate.
_OPEN = "99002001|A1"        # its order is still „Vybavuje sa" -> must stay
_CLOSED = "99002002|B1"      # its order is in the export and Vybavená -> must go
_UNSEEN = "99001500|C1"      # its order is not in the export at all -> must stay


def _export(rows, filler=60):
    """A plausible export: `rows` plus enough unrelated orders to clear the floor."""
    body = "".join(rows)
    body += "".join(f"99003{i:03d};2026-07-01 09:00:00;Vybavená;Z{i};Nieco\r\n"
                    for i in range(filler))
    return (_HEAD + body).encode("cp1250")


_OPEN_ROW = "99002001;2026-07-20 09:00:00;Vybavuje sa;A1;Bunda\r\n"
_CLOSED_ROW = "99002002;2026-07-02 09:00:00;Vybavená;B1;Ciapka\r\n"


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
    for blob in (b"", _HEAD.encode("cp1250"), b"not a csv at all"):
        res = webapp._prune_orphan_line_flags(blob)
        assert res["pruned"] == 0 and res["skipped"] == "implausible-source", (blob, res)
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
