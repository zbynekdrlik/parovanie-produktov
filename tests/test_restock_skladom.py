"""Pure-logic tests for „Vypredané → Skladom" restock detection (#108).

Hermetic: inline CSV export fixture + inline supplier_stock rows, no network, no
Flask. Proves the JOIN criteria (faithful to the LIVE n8n „Vyhodnoť kandidátov"):
a restock candidate = OUR product Vypredané+visible (state 2) AND the matched
supplier row is ok + available + FRESH — and that every other combination
(supplier still sold out, unknown, errored, no data, stale confirmation, we already
show it Skladom, discontinued, no link) is NEVER a candidate. Idempotency is the
state-2 gate: an already-Skladom product is never picked.
"""
from datetime import datetime, timedelta, timezone

from parovanie import restock_skladom as rs

HEADER = ("code;pairCode;name;supplier;productVisibility;availabilityInStock;"
          "availabilityOutOfStock;price;stock;internalNote")

NOW = datetime(2026, 7, 22, 6, 0, tzinfo=timezone(timedelta(hours=2)))


def _csv(*rows: str) -> str:
    return HEADER + "\r\n" + "\r\n".join(rows) + "\r\n"


def _srow(link, ok=True, available=True, price=79.90, availability_text="Skladom",
          supplier="TESTSUP", checked_at="2026-07-22T05:00:00+02:00"):
    return {"link": link, "ok": ok, "available": available, "price": price,
            "availabilityText": availability_text, "supplier": supplier,
            "checkedAt": checked_at}


# A Vypredané+visible product (CEO-canonical: both availability fields 'Vypredané',
# stock 0) with a supplier link.
def _vypredane(code="1/M", pair="P1", name="Bunda", link="https://supplier.test/p/1"):
    return f"{code};{pair};{name};TESTSUP;visible;Vypredané;Vypredané;99.90;0;{link}"


# ── the core positive case ──────────────────────────────────────────────────────
def test_candidate_when_we_are_vypredane_and_supplier_has_stock():
    rows = rs.compute_candidates(_csv(_vypredane()),
                                 [_srow("https://supplier.test/p/1")], NOW)
    assert len(rows) == 1
    r = rows[0]
    assert r["code"] == "1/M" and r["pairCode"] == "P1" and r["name"] == "Bunda"
    assert r["supplier"] == "TESTSUP" and r["ourPrice"] == "99.90"
    assert r["supplierPrice"] == "79.9"
    assert r["supplierAvailabilityText"] == "Skladom"
    assert r["link"] == "https://supplier.test/p/1"
    assert r["checkedAt"] == "2026-07-22T05:00:00+02:00"


def test_all_candidate_fields_present():
    r = rs.compute_candidates(_csv(_vypredane()),
                              [_srow("https://supplier.test/p/1")], NOW)[0]
    assert set(rs.CANDIDATE_FIELDS) <= set(r.keys())


# ── negatives: each must NOT produce a candidate ────────────────────────────────
def test_no_candidate_when_supplier_still_sold_out():
    rows = rs.compute_candidates(_csv(_vypredane()),
                                 [_srow("https://supplier.test/p/1", available=False)], NOW)
    assert rows == []


def test_no_candidate_when_supplier_availability_unknown():
    rows = rs.compute_candidates(_csv(_vypredane()),
                                 [_srow("https://supplier.test/p/1", available=None)], NOW)
    assert rows == []


def test_no_candidate_when_supplier_row_errored():
    rows = rs.compute_candidates(
        _csv(_vypredane()),
        [_srow("https://supplier.test/p/1", ok=False, available=None)], NOW)
    assert rows == []


def test_no_candidate_when_link_never_scraped():
    rows = rs.compute_candidates(_csv(_vypredane()), [], NOW)
    assert rows == []


def test_no_candidate_when_confirmation_is_stale():
    # supplier confirmation older than MAX_PAIR_AGE_H (48h) -> never flip on stale data
    old = (NOW - timedelta(hours=49)).isoformat(timespec="seconds")
    rows = rs.compute_candidates(
        _csv(_vypredane()),
        [_srow("https://supplier.test/p/1", checked_at=old)], NOW)
    assert rows == []


def test_candidate_when_confirmation_is_within_window():
    fresh = (NOW - timedelta(hours=47)).isoformat(timespec="seconds")
    rows = rs.compute_candidates(
        _csv(_vypredane()),
        [_srow("https://supplier.test/p/1", checked_at=fresh)], NOW)
    assert len(rows) == 1


def test_no_candidate_when_we_already_show_skladom():
    # already Skladom (state 1) -> idempotent skip: never re-flip a live product
    csv_text = _csv(
        "1/M;P1;Bunda;TESTSUP;visible;Skladom;Skladom;99.90;5;https://supplier.test/p/1")
    rows = rs.compute_candidates(csv_text, [_srow("https://supplier.test/p/1")], NOW)
    assert rows == []


def test_no_candidate_when_discontinued_detailonly():
    # detailOnly + 'Predaj výrobku skončil' (state 3) -> never restock a discontinued item
    csv_text = _csv(
        "1/M;P1;Bunda;TESTSUP;detailOnly;Predaj výrobku skončil;"
        "Predaj výrobku skončil;99.90;0;https://supplier.test/p/1")
    rows = rs.compute_candidates(csv_text, [_srow("https://supplier.test/p/1")], NOW)
    assert rows == []


def test_no_candidate_when_vypredane_but_hidden():
    # hidden visibility (state 3) is never restocked even if availability says Vypredané
    csv_text = _csv(
        "1/M;P1;Bunda;TESTSUP;hidden;Vypredané;Vypredané;99.90;0;https://supplier.test/p/1")
    rows = rs.compute_candidates(csv_text, [_srow("https://supplier.test/p/1")], NOW)
    assert rows == []


def test_no_candidate_when_no_internal_note_link():
    csv_text = _csv("1/M;P1;Bunda;TESTSUP;visible;Vypredané;Vypredané;99.90;0;")
    rows = rs.compute_candidates(csv_text, [_srow("https://supplier.test/p/1")], NOW)
    assert rows == []


def test_out_of_stock_via_aos_only_still_a_candidate():
    # availabilityInStock empty but availabilityOutOfStock='Vypredané' (what the
    # customer sees at stock 0) is state 2 -> a genuine restock candidate.
    csv_text = _csv(
        "1/M;P1;Bunda;TESTSUP;visible;;Vypredané;99.90;0;https://supplier.test/p/1")
    rows = rs.compute_candidates(csv_text, [_srow("https://supplier.test/p/1")], NOW)
    assert len(rows) == 1


def test_empty_supplier_rows_never_crashes_and_yields_no_candidate():
    assert rs.compute_candidates(_csv(_vypredane()), None, NOW) == []
    assert rs.compute_candidates(_csv(_vypredane()), [], NOW) == []
    assert rs.compute_candidates("", None, NOW) == []


def test_missing_checkedat_is_never_fresh():
    rows = rs.compute_candidates(
        _csv(_vypredane()),
        [_srow("https://supplier.test/p/1", checked_at="")], NOW)
    assert rows == []


def test_unparseable_checkedat_is_never_fresh():
    rows = rs.compute_candidates(
        _csv(_vypredane()),
        [_srow("https://supplier.test/p/1", checked_at="not-a-date")], NOW)
    assert rows == []


def test_naive_checkedat_inherits_now_tz_and_is_fresh():
    # a timestamp without a tz offset inherits now's tz (still within the window)
    naive = "2026-07-22T05:00:00"
    rows = rs.compute_candidates(
        _csv(_vypredane()),
        [_srow("https://supplier.test/p/1", checked_at=naive)], NOW)
    assert len(rows) == 1


# ── dedup + mixed catalog ───────────────────────────────────────────────────────
def test_duplicate_variant_code_emitted_once():
    # two rows share the same variant code (duplicate-product catalog) -> one candidate
    csv_text = _csv(
        _vypredane(code="15/40", pair="P1"),
        _vypredane(code="15/40", pair="P1"),
    )
    rows = rs.compute_candidates(csv_text, [_srow("https://supplier.test/p/1")], NOW)
    assert [r["code"] for r in rows] == ["15/40"]


def test_mixed_catalog_only_restockable_variant_flagged():
    csv_text = _csv(
        _vypredane(code="1/M", pair="P1", name="Bunda restock",
                   link="https://supplier.test/p/1"),   # vypredané + supplier has it
        "2/S;P2;Skladom uz;TESTSUP;visible;Skladom;Skladom;19.90;5;https://supplier.test/p/2",
        _vypredane(code="3/L", pair="P3", name="Este vypredane",
                   link="https://supplier.test/p/3"),   # vypredané but supplier sold out
    )
    rows = rs.compute_candidates(csv_text, [
        _srow("https://supplier.test/p/1", available=True),
        _srow("https://supplier.test/p/2", available=True),
        _srow("https://supplier.test/p/3", available=False),
    ], NOW)
    assert [r["code"] for r in rows] == ["1/M"]
