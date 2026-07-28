"""#291 — the server-side half of the flag commit clock, pinned where the browser tests
cannot reach.

The client decides which of two answers is newer purely from `commitSeq`, so the number
carries the whole fix. Two properties make it true, and NEITHER is observable from a
browser test that issues its writes one after another (they come out increasing whatever
the code does — verified by moving all three call sites outside their locks and watching
the e2e suite stay green):

  1. The number is taken INSIDE the same `with _lock:` block that writes the store. Taken
     outside, two writes can take numbers in the opposite order to the one they committed
     in — which is exactly the bug `commitSeq` exists to remove, reintroduced one level
     down.
  2. It never goes BACKWARDS across a restart. A tab that is open across a deploy holds
     numbers from the old process; if the new process hands out lower ones, that tab
     rejects every answer for the rest of its life and its `confirmed` baseline freezes —
     the #290 failure shape through a different door.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webreview"))
import app as webapp  # noqa: E402

from tests.conftest import authed_client  # noqa: E402

_KEY = "99009001|X1"

_WRITES = [("/api/ordered", {"ordered": True}),
           ("/api/waiting", {"waiting": True}),
           ("/api/instock", {"instock": True}),
           ("/api/unavailable", {"unavailable": False})]


@pytest.fixture
def flag_stores(tmp_path, monkeypatch):
    for attr, fname in (("ORDERED", "ordered_items.json"), ("WAITING", "waiting_items.json"),
                        ("INSTOCK", "instock_items.json"),
                        ("UNAVAIL", "unavailable_items.json"),
                        ("COMMIT_SEQ", "flag_commit_seq.json")):
        monkeypatch.setattr(webapp, attr, str(tmp_path / fname))
    return tmp_path


def test_every_endpoint_takes_its_commit_number_while_holding_the_store_lock(
        flag_stores, monkeypatch):
    """Pins the CAUSE, not the symptom. `_lock` is depth-counted, so „the lock is held"
    is directly observable at the moment the number is taken — and a call moved out of its
    `with _lock:` block reads depth 0 and fails here with a readable reason."""
    depths = []
    real = webapp._next_commit_seq

    def spy():
        depths.append(webapp._lock._depth)
        return real()
    monkeypatch.setattr(webapp, "_next_commit_seq", spy)

    c = authed_client()
    for path, body in _WRITES:
        assert c.post(path, json=dict(body, key=_KEY)).get_json()["ok"] is True
    assert c.post("/api/ordered/bulk",
                  json={"keys": [_KEY], "ordered": False}).get_json()["ok"] is True

    assert len(depths) == 5, ("a flag write did not take a commit number at all", depths)
    assert all(d > 0 for d in depths), (
        "a commit number was taken OUTSIDE the store lock — two writes can then take "
        "numbers in the opposite order to the one they committed in", depths)


def test_the_numbers_are_strictly_increasing_across_flags_and_rows(flag_stores):
    """One global clock, so answers about DIFFERENT flags and DIFFERENT rows are
    comparable. The per-(flag, row) issue counters deliberately are not."""
    c = authed_client()
    seqs = []
    for key in (_KEY, "99009002|X2"):
        for path, body in _WRITES:
            seqs.append(c.post(path, json=dict(body, key=key)).get_json()["commitSeq"])
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs), seqs


def test_the_clock_never_goes_backwards_across_a_restart(flag_stores, monkeypatch):
    """The wall clock alone does not give this: `time.time()` is CLOCK_REALTIME and a
    restart is exactly when an NTP correction (or a VM snapshot, or a hand-set clock) can
    put it behind where the previous process had already got to. A process that has just
    restarted needs only a small step backwards to re-issue numbers a live tab has seen.

    So the highest number handed out is RESERVED on disk and the seed is
    `max(wall clock, reservation)`. Simulated here by seeding against a reservation far in
    the future — the same shape as „the clock jumped back", without moving the box's clock."""
    c = authed_client()
    first = c.post("/api/ordered", json={"key": _KEY, "ordered": True}).get_json()["commitSeq"]

    ahead = first + 10 ** 9
    webapp._reserve_commit_seq(ahead)          # what an earlier process had reached
    # …and the clock is now useless. Patched ONLY across the seed: a session cookie and
    # half the app read the same clock, so leaving it at epoch 1 would break the request
    # below for a reason that has nothing to do with what is being pinned.
    real_time = webapp.time.time
    monkeypatch.setattr(webapp.time, "time", lambda: 1.0)
    webapp._seed_commit_seq()                  # „restart"
    monkeypatch.setattr(webapp.time, "time", real_time)

    after = c.post("/api/ordered", json={"key": _KEY, "ordered": False}).get_json()["commitSeq"]
    assert after > ahead, (
        "the commit clock restarted BELOW a number already handed out — every answer to a "
        "tab holding the higher number is now rejected for the life of that tab",
        first, ahead, after)


def test_the_reservation_is_extended_before_it_is_overrun(flag_stores, monkeypatch):
    """The reservation is written in blocks so a click does not cost a disk write. That is
    only safe while the block is extended BEFORE the counter reaches it — otherwise a
    restart after the block is exhausted seeds below what was already issued."""
    monkeypatch.setattr(webapp, "COMMIT_SEQ_BLOCK", 4)
    webapp._seed_commit_seq()
    c = authed_client()

    issued = [c.post("/api/ordered", json={"key": _KEY, "ordered": bool(i % 2)})
              .get_json()["commitSeq"] for i in range(12)]

    with open(os.fspath(webapp.COMMIT_SEQ), encoding="utf-8") as fh:
        reserved = json.load(fh)["reserved"]
    assert reserved >= max(issued), (
        "the on-disk reservation fell behind the numbers actually handed out", reserved,
        max(issued))


def test_a_missing_or_corrupt_reservation_degrades_to_the_wall_clock(flag_stores):
    """The reservation is derived state, so an unreadable one must not stop the app from
    serving — it only loses the extra protection, exactly like the export watermark."""
    for blob in ("", "not json", '{"reserved": "nonsense"}', '{"reserved": -5}'):
        with open(os.fspath(webapp.COMMIT_SEQ), "w", encoding="utf-8") as fh:
            fh.write(blob)
        webapp._seed_commit_seq()
        c = authed_client()
        seq = c.post("/api/ordered", json={"key": _KEY, "ordered": True}).get_json()["commitSeq"]
        assert isinstance(seq, int) and seq > 0, (blob, seq)
