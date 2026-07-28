import copy

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


def test_the_later_write_of_the_SAME_field_wins_and_keeps_its_own_source():
    p, _ = ob.queue_fields({}, source="restock_skladom",
                           header="code;pairCode;availabilityInStock",
                           rows=[["A", "P", "Skladom"]], now="T1")
    p, _ = ob.queue_fields(p, source="stock_skladom",
                           header="code;pairCode;availabilityInStock",
                           rows=[["A", "P", "Skladom"]], now="T2")
    f = p["A"]["fields"]["availabilityInStock"]
    assert f["source"] == "stock_skladom"
    assert f["queued_at"] == "T2"


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
