import copy
from datetime import datetime, timedelta, timezone

from parovanie import shoptet_outbox as ob


def test_queue_fields_stores_one_field_per_column_with_its_source():
    pending, n = ob.queue_fields(
        {}, source="parovania_eshop", header="code;pairCode;internalNote",
        rows=[["60648", "60648", "https://dodavatel.sk/x"]],
        now="2026-07-28T14:00:00+02:00")
    assert n == 1
    assert pending["60648"]["pairCode"] == "60648"
    f = pending["60648"]["fields"]["internalNote"]
    assert f["value"] == "https://dodavatel.sk/x"
    assert f["source"] == "parovania_eshop"
    assert f["queued_at"] == "2026-07-28T14:00:00+02:00"
    assert pending["60648"]["blocked"] is None
    assert pending["60648"]["attempts"] == 0


def test_queue_fields_never_queues_the_key_columns_as_fields():
    pending, _ = ob.queue_fields(
        {}, source="s", header="code;pairCode;internalNote",
        rows=[["A", "P", "u"]], now="T")
    assert set(pending["A"]["fields"]) == {"internalNote"}


def test_queue_fields_skips_empty_cells_so_a_blank_never_wipes_a_field():
    pending, n = ob.queue_fields(
        {}, source="s", header="code;pairCode;internalNote;supplier",
        rows=[["A", "P", "", "FOREST"]], now="T")
    assert n == 1
    assert set(pending["A"]["fields"]) == {"supplier"}


def test_a_second_source_adds_its_own_field_to_the_same_code():
    p, _ = ob.queue_fields({}, source="parovania_eshop",
                           header="code;pairCode;internalNote",
                           rows=[["A", "P", "u"]], now="T1")
    p, _ = ob.queue_fields(p, source="restock_skladom",
                           header="code;pairCode;availabilityInStock",
                           rows=[["A", "P", "Skladom"]], now="T2")
    assert set(p["A"]["fields"]) == {"internalNote", "availabilityInStock"}
    assert p["A"]["fields"]["availabilityInStock"]["source"] == "restock_skladom"


# ── #299 opravné kolo 1 review C2 (Critical) — `queued_at` is the time a ────── #
# ── field's CURRENT value was FIRST queued, never the time of the LATEST ───── #
# ── call that happened to queue the same value again (same discipline as ──── #
# ── settle()'s own `blocked.since`, further down this file). ───────────────── #
def test_requeueing_the_SAME_value_keeps_the_ORIGINAL_queued_at():
    p, _ = ob.queue_fields({}, source="parovania_eshop",
                           header="code;pairCode;internalNote",
                           rows=[["A", "P", "https://x"]], now="T1")
    p, _ = ob.queue_fields(p, source="parovania_eshop",
                           header="code;pairCode;internalNote",
                           rows=[["A", "P", "https://x"]], now="T2")   # SAME value
    f = p["A"]["fields"]["internalNote"]
    assert f["value"] == "https://x"
    assert f["queued_at"] == "T1", (
        "a re-queue of the SAME value must not push queued_at forward to T2")


def test_requeueing_a_CHANGED_value_gets_a_fresh_queued_at():
    p, _ = ob.queue_fields({}, source="parovania_eshop",
                           header="code;pairCode;internalNote",
                           rows=[["A", "P", "https://x"]], now="T1")
    p, _ = ob.queue_fields(p, source="parovania_eshop",
                           header="code;pairCode;internalNote",
                           rows=[["A", "P", "https://y"]], now="T2")   # DIFFERENT value
    f = p["A"]["fields"]["internalNote"]
    assert f["value"] == "https://y"
    assert f["queued_at"] == "T2", "a genuinely NEW value must get the fresh time"


def test_requeueing_the_same_value_from_a_DIFFERENT_source_still_preserves_the_time():
    p, _ = ob.queue_fields({}, source="parovania_eshop",
                           header="code;pairCode;internalNote",
                           rows=[["A", "P", "https://x"]], now="T1")
    p, _ = ob.queue_fields(p, source="split_links",
                           header="code;pairCode;internalNote",
                           rows=[["A", "P", "https://x"]], now="T2")
    f = p["A"]["fields"]["internalNote"]
    assert f["source"] == "split_links", "source still updates to the latest queuer"
    assert f["queued_at"] == "T1", "but the timestamp is about the VALUE, not the caller"


def test_a_first_time_queue_with_no_prior_field_uses_now_not_a_crash():
    # prev_field is None the very first time a (code, column) is ever queued —
    # must fall back cleanly to `now`, never raise on a missing prior field.
    p, _ = ob.queue_fields({}, source="s", header="code;pairCode;internalNote",
                           rows=[["A", "P", "u"]], now="T1")
    assert p["A"]["fields"]["internalNote"]["queued_at"] == "T1"


def test_the_later_write_of_the_SAME_field_wins_and_keeps_its_own_source():
    """#299 opravné kolo 1 review C2 — adapted (per this project's own
    `toorder-e2e.md` §6: a foreign test pinning the OLD semantics gets fixed in
    its own note, never silently weakened). This is the SAME value ("Skladom")
    written by TWO real producers that can legitimately both send it for the
    same code (restock_skladom / stock_skladom) — `source` still tracks
    whoever wrote it LAST, but `queued_at` no longer does: C2 keeps the time
    the value was FIRST queued when a later write does not actually change
    it (see the dedicated C2 tests above)."""
    p, _ = ob.queue_fields({}, source="restock_skladom",
                           header="code;pairCode;availabilityInStock",
                           rows=[["A", "P", "Skladom"]], now="T1")
    p, _ = ob.queue_fields(p, source="stock_skladom",
                           header="code;pairCode;availabilityInStock",
                           rows=[["A", "P", "Skladom"]], now="T2")
    f = p["A"]["fields"]["availabilityInStock"]
    assert f["source"] == "stock_skladom"
    assert f["queued_at"] == "T1"


def test_credit_group_and_value_ride_with_the_field():
    p, _ = ob.queue_fields(
        {}, source="parovania_eshop", header="code;pairCode;internalNote",
        rows=[["A", "P", "u"]],
        credit_group={"A": "BETALOV|60648"},
        credit_value={"A": "u"},
        now="T")
    c = p["A"]["fields"]["internalNote"]["credit"]
    assert c == {"store": "parovania_eshop", "group": "BETALOV|60648", "value": "u"}


def test_a_row_shorter_than_the_header_is_refused_loudly():
    import pytest
    with pytest.raises(ValueError):
        ob.queue_fields({}, source="s", header="code;pairCode;internalNote",
                        rows=[["A", "P"]], now="T")


def test_queue_fields_leaves_the_caller_s_table_untouched():
    before = ob.queue_fields({}, source="s", header="code;pairCode;internalNote",
                             rows=[["A", "P", "u"]], now="T1")[0]
    snapshot = copy.deepcopy(before)
    after, _ = ob.queue_fields(before, source="s2",
                               header="code;pairCode;supplier",
                               rows=[["A", "P", "FOREST"]], now="T2")
    assert before == snapshot, "the caller's table must survive the call unchanged"
    assert after is not before


def test_queue_fields_returned_table_does_not_share_nested_dicts_with_the_input():
    # A code untouched by the second call must still get its own copy of
    # `fields` and of every individual field dict — not just the top-level
    # entry — so a later in-place mutation of one snapshot (e.g. by settle())
    # can never leak into a table someone else is still holding.
    before, _ = ob.queue_fields({}, source="s", header="code;pairCode;internalNote",
                                 rows=[["A", "P", "u"]], now="T1")
    after, _ = ob.queue_fields(before, source="s2",
                                header="code;pairCode;supplier",
                                rows=[["B", "P", "FOREST"]], now="T2")
    assert after["A"]["fields"] is not before["A"]["fields"]
    assert after["A"]["fields"]["internalNote"] is not before["A"]["fields"]["internalNote"]


def _pending_two_codes():
    p, _ = ob.queue_fields({}, source="parovania_eshop",
                           header="code;pairCode;internalNote",
                           rows=[["A", "PA", "https://x"]], now="T")
    p, _ = ob.queue_fields(p, source="restock_skladom",
                           header="code;pairCode;availabilityInStock;stock",
                           rows=[["B", "PB", "Skladom", "5"]], now="T")
    return p


def test_build_import_merges_every_queued_field_into_ONE_header():
    header, rows, blocked = ob.build_import(_pending_two_codes())
    assert header == "code;pairCode;availabilityInStock;internalNote;stock"
    assert blocked == {}
    by_code = {r[0]: r for r in rows}
    assert by_code["A"] == ["A", "PA", "", "https://x", ""]
    assert by_code["B"] == ["B", "PB", "Skladom", "", "5"]


def test_a_code_the_catalogue_does_not_carry_is_blocked_not_sent():
    header, rows, blocked = ob.build_import(_pending_two_codes(),
                                            absent_codes={"B"})
    assert [r[0] for r in rows] == ["A"]
    assert blocked == {"B": "not-in-catalog"}
    # the blocked code's own columns must not widen the header of what IS sent
    assert header == "code;pairCode;internalNote"


def test_an_empty_table_builds_nothing_rather_than_an_empty_import():
    header, rows, blocked = ob.build_import({})
    assert rows == []
    assert blocked == {}
    assert header == "code;pairCode"


def test_rows_come_out_in_a_stable_order_so_two_runs_are_comparable():
    p = _pending_two_codes()
    assert [r[0] for r in ob.build_import(p)[1]] == ["A", "B"]


def test_a_code_with_no_queued_fields_is_never_sent_as_an_empty_row():
    # An entry can reach the table with an empty `fields` dict (e.g. every
    # queued value was later overwritten back to blank). Such a code has
    # nothing to say and must not surface as a bare ["code", "pairCode"] row.
    pending = dict(_pending_two_codes())
    pending["C"] = {"pairCode": "PC", "fields": {}}
    header, rows, blocked = ob.build_import(pending)
    assert [r[0] for r in rows] == ["A", "B"]
    assert blocked == {}
    assert header == "code;pairCode;availabilityInStock;internalNote;stock"


def _pending_group_of_two():
    return ob.queue_fields(
        {}, source="parovania_eshop", header="code;pairCode;internalNote",
        rows=[["A", "P", "u"], ["B", "P", "u"]],
        credit_group={"A": "K", "B": "K"},
        credit_value={"A": "u", "B": "u"}, now="T")[0]


def _sent_from(pending, codes):
    """The `sent_fields` snapshot for `codes` that matches whatever `pending`
    currently holds for them — the "nothing changed since it was sent" case
    most tests below want. A test that needs the OTHER case (something
    changed mid-import) builds its own `sent_fields` by hand — see the C1
    regression tests further down."""
    return {c: {col: f["value"]
                for col, f in (pending.get(c) or {}).get("fields", {}).items()}
            for c in codes}


def _sent_credits_from(pending, codes):
    """The `sent_credits` mirror of `_sent_from` (#299 review N2) — the credit
    dict each field carried in `pending` at send time, for `codes`. Same
    "nothing changed since it was sent" default; a test that needs the OTHER
    case builds its own `sent_credits` by hand."""
    return {c: {col: f["credit"]
                for col, f in (pending.get(c) or {}).get("fields", {}).items()
                if f.get("credit")}
            for c in codes}


def test_a_confirmed_code_leaves_the_table():
    p = _pending_group_of_two()
    settled, credits = ob.settle(p, success_codes={"A"}, blocked={},
                                 sent_fields=_sent_from(p, {"A"}),
                                 sent_credits=_sent_credits_from(p, {"A"}), now="T2")
    assert set(settled) == {"B"}


def test_a_group_is_credited_only_when_ALL_its_codes_are_confirmed():
    p = _pending_group_of_two()
    _, half = ob.settle(p, success_codes={"A"}, blocked={},
                        sent_fields=_sent_from(p, {"A"}),
                        sent_credits=_sent_credits_from(p, {"A"}), now="T2")
    assert half == {}
    _, full = ob.settle(p, success_codes={"A", "B"}, blocked={},
                        sent_fields=_sent_from(p, {"A", "B"}),
                        sent_credits=_sent_credits_from(p, {"A", "B"}), now="T2")
    assert full == {"parovania_eshop": {"K": "u"}}


def test_an_unconfirmed_code_stays_and_counts_an_attempt():
    p, _ = ob.settle(_pending_group_of_two(), success_codes=set(),
                     blocked={}, sent_fields={}, sent_credits={}, now="T2")
    assert p["A"]["attempts"] == 1
    p2, _ = ob.settle(p, success_codes=set(), blocked={}, sent_fields={},
                      sent_credits={}, now="T3")
    assert p2["A"]["attempts"] == 2


def test_a_blocked_code_records_its_reason_and_since_but_is_never_dropped():
    p, _ = ob.settle(_pending_group_of_two(), success_codes=set(),
                     blocked={"A": "not-in-catalog"}, sent_fields={},
                     sent_credits={}, now="T2")
    assert p["A"]["blocked"] == {"reason": "not-in-catalog", "since": "T2"}
    assert p["A"]["fields"]["internalNote"]["value"] == "u"


def test_a_code_that_stops_being_blocked_clears_its_flag():
    p, _ = ob.settle(_pending_group_of_two(), success_codes=set(),
                     blocked={"A": "not-in-catalog"}, sent_fields={},
                     sent_credits={}, now="T2")
    p, _ = ob.settle(p, success_codes=set(), blocked={}, sent_fields={},
                     sent_credits={}, now="T3")
    assert p["A"]["blocked"] is None


def test_stale_blocked_names_the_codes_that_have_been_stuck_for_three_runs():
    p = _pending_group_of_two()
    for t in ("T2", "T3", "T4"):
        p, _ = ob.settle(p, success_codes=set(),
                         blocked={"A": "not-in-catalog"}, sent_fields={},
                         sent_credits={}, now=t)
    assert ob.stale_blocked(p) == ["A"]
    assert ob.stale_blocked(p, min_attempts=99) == []


def test_settle_returned_table_does_not_share_nested_dicts_with_the_input():
    # Same guarantee queue_fields already has (see the mirror test above):
    # a later in-place mutation of one snapshot must never leak into a table
    # someone else is still holding.
    before = _pending_group_of_two()
    after, _ = ob.settle(before, success_codes=set(), blocked={},
                         sent_fields={}, sent_credits={}, now="T2")
    assert after["A"]["fields"] is not before["A"]["fields"]
    assert after["A"]["fields"]["internalNote"] is not before["A"]["fields"]["internalNote"]


def test_stale_blocked_ignores_unconfirmed_runs_that_were_never_blocked():
    # attempts counts every unconfirmed run, blocked or not. stale_blocked
    # must count only CONSECUTIVE blocked runs — four unblocked runs (so
    # attempts=4) followed by a single blocked run must NOT read as "stuck
    # for 3+ runs".
    p = _pending_group_of_two()
    for t in ("T2", "T3", "T4", "T5"):
        p, _ = ob.settle(p, success_codes=set(), blocked={}, sent_fields={},
                         sent_credits={}, now=t)
    assert p["A"]["attempts"] == 4
    p, _ = ob.settle(p, success_codes=set(),
                     blocked={"A": "not-in-catalog"}, sent_fields={},
                     sent_credits={}, now="T6")
    assert ob.stale_blocked(p) == []


def test_blocked_since_is_preserved_across_repeated_blocked_runs():
    p, _ = ob.settle(_pending_group_of_two(), success_codes=set(),
                     blocked={"A": "not-in-catalog"}, sent_fields={},
                     sent_credits={}, now="T2")
    assert p["A"]["blocked"]["since"] == "T2"
    p, _ = ob.settle(p, success_codes=set(),
                     blocked={"A": "not-in-catalog"}, sent_fields={},
                     sent_credits={}, now="T3")
    assert p["A"]["blocked"]["since"] == "T2"


# ── #299 záverečná recenzia I1 (fallback path) / deferred minor "attempts has ─ #
# ── no upper bound and no consumer" — a code that stays UNCONFIRMED (never ─── #
# ── explicitly blocked) for many consecutive runs needs its own loud, bounded ─
# ── escape, distinct from stale_blocked's (which only ever fires for a code ── #
# ── that IS `blocked`). ──────────────────────────────────────────────────── #

def test_stale_unconfirmed_names_codes_unconfirmed_for_min_attempts_runs():
    p = _pending_group_of_two()
    for t in ("T2", "T3", "T4"):
        p, _ = ob.settle(p, success_codes=set(), blocked={}, sent_fields={},
                         sent_credits={}, now=t)
    assert p["A"]["attempts"] == 3
    assert ob.stale_unconfirmed(p, min_attempts=3) == ["A", "B"]
    assert ob.stale_unconfirmed(p, min_attempts=99) == []


def test_stale_unconfirmed_ignores_a_code_already_blocked_for_its_own_reason():
    # A code that IS `blocked` (e.g. not-in-catalog) already has its own escape
    # (stale_blocked) — stale_unconfirmed must not ALSO report it, or the drain
    # would double-count the same stuck code under two different mechanisms.
    p = _pending_group_of_two()
    for t in ("T2", "T3", "T4"):
        p, _ = ob.settle(p, success_codes=set(),
                         blocked={"A": "not-in-catalog"}, sent_fields={},
                         sent_credits={}, now=t)
    assert p["A"]["attempts"] == 3
    assert ob.stale_unconfirmed(p, min_attempts=3) == ["B"]
    assert ob.stale_blocked(p, min_attempts=3) == ["A"]


# ── #299 záverečná recenzia I5 — a queued field has an age gate at QUEUEING ── #
# ── time only; nothing re-checks it at SEND time. `stale_fields` is that ───── #
# ── missing gate. Absolute hour offsets (23h passes, 25h does not), never ──── #
# ── derived from MAX_AGE_S itself — a threshold pinned by a value computed ─── #
# ── from the SAME constant it tests can never fail. ─────────────────────────  #
_NOW = datetime(2026, 7, 28, 12, 0, 0, tzinfo=timezone.utc)
_MAX_AGE_S = 24 * 3600


def _field(age_hours=None, queued_at=None):
    if queued_at is None and age_hours is not None:
        queued_at = (_NOW - timedelta(hours=age_hours)).isoformat()
    return {"value": "u", "source": "restock_skladom", "queued_at": queued_at}


def test_stale_fields_a_23h_old_field_passes():
    p = {"A": {"fields": {"availabilityInStock": _field(age_hours=23)}}}
    assert ob.stale_fields(p, _NOW, _MAX_AGE_S) == {}


def test_stale_fields_a_25h_old_field_is_flagged():
    p = {"A": {"fields": {"availabilityInStock": _field(age_hours=25)}}}
    assert ob.stale_fields(p, _NOW, _MAX_AGE_S) == {"A": ["availabilityInStock"]}


def test_stale_fields_exactly_at_the_boundary_counts_as_stale():
    # >= max_age_s, not >, so a field EXACTLY 24h old is refused too — never
    # let a field cross the limit on the same run it trips it.
    p = {"A": {"fields": {"availabilityInStock": _field(age_hours=24)}}}
    assert ob.stale_fields(p, _NOW, _MAX_AGE_S) == {"A": ["availabilityInStock"]}


def test_stale_fields_treats_a_missing_queued_at_as_stale():
    p = {"A": {"fields": {"availabilityInStock": _field(queued_at=None)}}}
    assert ob.stale_fields(p, _NOW, _MAX_AGE_S) == {"A": ["availabilityInStock"]}


def test_stale_fields_treats_an_unparsable_queued_at_as_stale():
    p = {"A": {"fields": {"availabilityInStock": _field(queued_at="not-a-date")}}}
    assert ob.stale_fields(p, _NOW, _MAX_AGE_S) == {"A": ["availabilityInStock"]}


def test_stale_fields_treats_a_future_queued_at_as_stale():
    p = {"A": {"fields": {"availabilityInStock": _field(age_hours=-1)}}}  # 1h in the future
    assert ob.stale_fields(p, _NOW, _MAX_AGE_S) == {"A": ["availabilityInStock"]}


def test_stale_fields_only_names_the_stale_COLUMN_not_the_whole_code():
    p = {"A": {"fields": {"internalNote": _field(age_hours=1),          # fresh
                          "availabilityInStock": _field(age_hours=48)}}}  # stale
    assert ob.stale_fields(p, _NOW, _MAX_AGE_S) == {"A": ["availabilityInStock"]}


def test_stale_fields_never_mutates_or_drops_anything_from_pending():
    """This function only REPORTS — dropping a field from the table on this
    function's own say-so would be the exact silent loss #299 is built to
    end. Pin that the input is untouched."""
    p = {"A": {"fields": {"availabilityInStock": _field(age_hours=48)}}}
    before = copy.deepcopy(p)
    ob.stale_fields(p, _NOW, _MAX_AGE_S)
    assert p == before


# ── #299 review C1 — a value queued WHILE the import is in flight must never ─ #
# ── be treated as sent, and must never be credited on someone else's say-so ── #

def test_settle_keeps_a_field_that_changed_WHILE_the_import_was_in_flight_and_withholds_its_credit():
    """The exact review probe: pending is read once to BUILD the import
    (`sent_fields` below is what that snapshot actually put on the wire —
    "https://OLD"), then a producer queues a NEW value for the SAME code
    before `settle` runs (the table `settle` actually receives). Shoptet only
    ever confirmed OLD: NEW must stay queued for the next hour, and the
    group's credit — which the OLD value earned — must NOT fire, because by
    the time settle runs the group's live value is NEW, which Shoptet has
    never seen."""
    sent, _ = ob.queue_fields(
        {}, source="parovania_eshop", header="code;pairCode;internalNote",
        rows=[["A", "P", "https://OLD"]],
        credit_group={"A": "g"}, credit_value={"A": "https://OLD"}, now="T1")
    sent_fields = {"A": {"internalNote": "https://OLD"}}   # what build_import sent
    sent_credits = _sent_credits_from(sent, {"A"})         # ditto, for the credit

    fresh, _ = ob.queue_fields(               # queued DURING the import
        sent, source="parovania_eshop", header="code;pairCode;internalNote",
        rows=[["A", "P", "https://NEW"]],
        credit_group={"A": "g"}, credit_value={"A": "https://NEW"}, now="T2")

    settled, credits = ob.settle(fresh, success_codes={"A"}, blocked={},
                                 sent_fields=sent_fields, sent_credits=sent_credits,
                                 now="T3")

    assert settled["A"]["fields"]["internalNote"]["value"] == "https://NEW", (
        "the value queued mid-import must stay in the table — Shoptet never saw it")
    assert credits == {}, "must never credit a value Shoptet never confirmed"


def test_settle_drops_a_field_whose_value_is_still_the_one_that_was_sent():
    # the ordinary case — nothing changed after the import was built — must
    # keep working exactly as before the C1 fix.
    p = _pending_group_of_two()
    settled, credits = ob.settle(p, success_codes={"A", "B"}, blocked={},
                                 sent_fields=_sent_from(p, {"A", "B"}),
                                 sent_credits=_sent_credits_from(p, {"A", "B"}), now="T2")
    assert settled == {}
    assert credits == {"parovania_eshop": {"K": "u"}}


# ── #299 review N2 — the credit VALUE settle() awards must come from the ───── #
# ── send-time snapshot too, never from `pending` (the fresh reload) ────────── #

def test_settle_credits_the_SENT_credit_value_even_when_a_later_requeue_changed_only_the_credit_value():
    """A producer can re-queue the SAME field VALUE while writing a DIFFERENT
    credit_value during the import — the field is then NOT dirty (its value
    still matches what was sent), so it IS dropped as confirmed, but the
    credit that fires must be the value Shoptet actually saw ("u1"), never
    whatever `pending` now holds ("u2"). Without the N2 fix this asserts
    credits == {"parovania_eshop": {"K": "u2"}} — the value Shoptet never saw."""
    sent, _ = ob.queue_fields(
        {}, source="parovania_eshop", header="code;pairCode;internalNote",
        rows=[["A", "P", "https://x"]],
        credit_group={"A": "K"}, credit_value={"A": "u1"}, now="T1")
    sent_fields = {"A": {"internalNote": "https://x"}}      # what build_import sent
    sent_credits = _sent_credits_from(sent, {"A"})           # the credit AS SENT (u1)

    fresh, _ = ob.queue_fields(          # re-queued DURING the import: SAME value,
        sent, source="parovania_eshop", header="code;pairCode;internalNote",
        rows=[["A", "P", "https://x"]],  # NEW credit_value
        credit_group={"A": "K"}, credit_value={"A": "u2"}, now="T2")

    settled, credits = ob.settle(fresh, success_codes={"A"}, blocked={},
                                 sent_fields=sent_fields, sent_credits=sent_credits,
                                 now="T3")

    assert settled == {}, "the field is unchanged since it was sent — it must leave"
    assert credits == {"parovania_eshop": {"K": "u1"}}, (
        "must credit the value Shoptet actually confirmed, not one queued after")
