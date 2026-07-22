"""Pure-logic tests for the „Riziko výpadku" supply-risk report (#107).

Hermetic: inline CSV export fixture + inline supplier_stock rows, no network, no
Flask. Proves the join criteria: risk = OUR product Skladom+visible (state 1) AND
the matched supplier row says ok+unavailable — and that every other combination
(supplier still available, we already show it as unavailable, no supplier data,
scrape error, unknown availability, no link at all) never gets flagged.
"""
from parovanie import riziko_vypadku as rv

HEADER = "code;pairCode;name;supplier;productVisibility;availabilityInStock;availabilityOutOfStock;price;stock;internalNote"


def _csv(*rows: str) -> str:
    return HEADER + "\r\n" + "\r\n".join(rows) + "\r\n"


def _srow(link, ok=True, available=False, availability_text="Vypredané",
          supplier="TESTSUP", checked_at="2026-07-22T06:15:00+02:00"):
    return {"link": link, "ok": ok, "available": available,
            "availabilityText": availability_text, "supplier": supplier,
            "checkedAt": checked_at}


# ── the core positive case ──────────────────────────────────────────────────────
def test_risk_when_we_have_it_and_supplier_sold_out():
    csv_text = _csv(
        "1/M;P1;Bunda zimná;TESTSUP;visible;Skladom;;99.90;5;https://supplier.test/p/1")
    rows = rv.compute_risk(csv_text, [_srow("https://supplier.test/p/1")])
    assert len(rows) == 1
    r = rows[0]
    assert r["code"] == "1/M" and r["pairCode"] == "P1" and r["name"] == "Bunda zimná"
    assert r["supplier"] == "TESTSUP" and r["ourPrice"] == "99.90" and r["ourStock"] == "5"
    assert r["supplierAvailabilityText"] == "Vypredané"
    assert r["link"] == "https://supplier.test/p/1"
    assert r["checkedAt"] == "2026-07-22T06:15:00+02:00"


def test_all_risk_fields_present():
    csv_text = _csv(
        "1/M;P1;Bunda;TESTSUP;visible;Skladom;;99.90;5;https://supplier.test/p/1")
    r = rv.compute_risk(csv_text, [_srow("https://supplier.test/p/1")])[0]
    assert set(rv.RISK_FIELDS) <= set(r.keys())


# ── negatives: each must NOT produce a risk row ─────────────────────────────────
def test_no_risk_when_supplier_still_available():
    csv_text = _csv(
        "1/M;P1;Bunda;TESTSUP;visible;Skladom;;99.90;5;https://supplier.test/p/1")
    rows = rv.compute_risk(csv_text, [_srow("https://supplier.test/p/1", available=True)])
    assert rows == []


def test_no_risk_when_we_already_show_it_vypredane():
    # our own availabilityInStock/OutOfStock says "Vypredané" -> state_of != 1
    csv_text = _csv(
        "1/M;P1;Bunda;TESTSUP;visible;;Vypredané;99.90;0;https://supplier.test/p/1")
    rows = rv.compute_risk(csv_text, [_srow("https://supplier.test/p/1")])
    assert rows == []


def test_no_risk_when_we_already_discontinued():
    # productVisibility hidden -> state 3 (Už sa nebude predávať), never a risk
    csv_text = _csv(
        "1/M;P1;Bunda;TESTSUP;hidden;Skladom;;99.90;5;https://supplier.test/p/1")
    rows = rv.compute_risk(csv_text, [_srow("https://supplier.test/p/1")])
    assert rows == []


def test_no_risk_when_link_never_scraped():
    csv_text = _csv(
        "1/M;P1;Bunda;TESTSUP;visible;Skladom;;99.90;5;https://supplier.test/p/1")
    rows = rv.compute_risk(csv_text, [])   # supplier_stock has a different link only
    assert rows == []


def test_no_risk_when_supplier_row_errored():
    csv_text = _csv(
        "1/M;P1;Bunda;TESTSUP;visible;Skladom;;99.90;5;https://supplier.test/p/1")
    rows = rv.compute_risk(csv_text, [_srow("https://supplier.test/p/1", ok=False, available=None)])
    assert rows == []


def test_no_risk_when_supplier_availability_unknown():
    csv_text = _csv(
        "1/M;P1;Bunda;TESTSUP;visible;Skladom;;99.90;5;https://supplier.test/p/1")
    rows = rv.compute_risk(csv_text, [_srow("https://supplier.test/p/1", available=None)])
    assert rows == []


def test_no_risk_when_no_internal_note_link():
    csv_text = _csv("1/M;P1;Bunda;TESTSUP;visible;Skladom;;99.90;5;")
    rows = rv.compute_risk(csv_text, [_srow("https://supplier.test/p/1")])
    assert rows == []


def test_empty_supplier_rows_never_crashes_and_yields_no_risk():
    csv_text = _csv(
        "1/M;P1;Bunda;TESTSUP;visible;Skladom;;99.90;5;https://supplier.test/p/1")
    assert rv.compute_risk(csv_text, None) == []
    assert rv.compute_risk(csv_text, []) == []
    assert rv.compute_risk("", None) == []


# ── detailOnly is still "predajné" (matches export_helpers.state_of) ────────────
def test_detail_only_visibility_still_counts_as_predajne():
    csv_text = _csv(
        "1/M;P1;Bunda;TESTSUP;detailOnly;Skladom;;99.90;5;https://supplier.test/p/1")
    rows = rv.compute_risk(csv_text, [_srow("https://supplier.test/p/1")])
    assert len(rows) == 1


# ── multiple products: only the risky one is flagged ────────────────────────────
def test_mixed_catalog_only_risky_variant_flagged():
    csv_text = _csv(
        "1/M;P1;Bunda risk;TESTSUP;visible;Skladom;;99.90;5;https://supplier.test/p/1",
        "2/S;P2;Noz ok;TESTSUP;visible;Skladom;;19.90;3;https://supplier.test/p/2",
        "3/L;P3;Uz vypredane;TESTSUP;visible;;Vypredané;49.90;0;https://supplier.test/p/3",
    )
    rows = rv.compute_risk(csv_text, [
        _srow("https://supplier.test/p/1", available=False),
        _srow("https://supplier.test/p/2", available=True),
        _srow("https://supplier.test/p/3", available=False),
    ])
    assert [r["code"] for r in rows] == ["1/M"]
