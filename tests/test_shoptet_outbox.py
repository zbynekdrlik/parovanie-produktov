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
