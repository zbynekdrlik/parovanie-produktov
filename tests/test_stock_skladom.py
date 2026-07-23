"""Pure-logic tests for „Máme skladom → Skladom" auto-restock (#98).

Distinct from #108 restock_skladom: the trigger here is SHOPTET'S OWN physical
stock (`stock > 0` — the „zelené pásy / máme" the manager sees in the Shoptet
admin), NOT a scraped supplier confirmation. A candidate = OUR product that has
real positive stock BUT is still shown to customers as Vypredané (state 2, visible)
— we physically have it, so put it back on sale. Every conscious-off state
(detailOnly / discontinued / hidden = state 3), the already-Skladom state (state 1,
idempotent) and any product with no real stock (stock <= 0, empty, non-numeric) is
NEVER a candidate. Hermetic: inline CSV export fixture, no network, no Flask.

The row builder (import_builder.skladom_rows) writes visible + BOTH availability
fields Skladom and — UNLIKE restock_rows — does NOT write the stock column: the
product already owns a real positive stock in Shoptet, so we never overwrite it
with a fictional value (which could oversell).
"""
from parovanie import import_builder as ib
from parovanie import stock_skladom as ss

HEADER = ("code;pairCode;name;supplier;productVisibility;availabilityInStock;"
          "availabilityOutOfStock;price;stock;internalNote")


def _csv(*rows: str) -> str:
    return HEADER + "\r\n" + "\r\n".join(rows) + "\r\n"


# A product we PHYSICALLY have (stock>0) but that still shows Vypredané to
# customers (visible, both availability fields 'Vypredané') — the target case.
def _have_but_vypredane(code="1/M", pair="P1", name="Bunda", stock="5"):
    return f"{code};{pair};{name};TESTSUP;visible;Vypredané;Vypredané;99.90;{stock};"


# ── the core positive case ──────────────────────────────────────────────────────
def test_candidate_when_we_physically_have_stock_but_show_vypredane():
    rows = ss.compute_candidates(_csv(_have_but_vypredane()))
    assert len(rows) == 1
    r = rows[0]
    assert r["code"] == "1/M" and r["pairCode"] == "P1" and r["name"] == "Bunda"
    assert r["ourPrice"] == "99.90"
    assert r["stock"] == "5"
    assert r["availabilityText"] == "Vypredané"


def test_all_candidate_fields_present():
    r = ss.compute_candidates(_csv(_have_but_vypredane()))[0]
    assert set(ss.CANDIDATE_FIELDS) <= set(r.keys())


# ── negatives: each must NOT produce a candidate ────────────────────────────────
def test_no_candidate_when_no_physical_stock():
    # visible + Vypredané but stock 0 → nothing to sell → not a candidate
    rows = ss.compute_candidates(_csv(_have_but_vypredane(stock="0")))
    assert rows == []


def test_no_candidate_when_backorder_negative_stock():
    # negative stock (Shoptet backorder) is NOT „máme skladom" → never a candidate
    rows = ss.compute_candidates(_csv(_have_but_vypredane(stock="-3")))
    assert rows == []


def test_no_candidate_when_stock_empty_or_nonnumeric():
    assert ss.compute_candidates(_csv(_have_but_vypredane(stock=""))) == []
    assert ss.compute_candidates(_csv(_have_but_vypredane(stock="n/a"))) == []


def test_no_candidate_when_already_skladom():
    # already Skladom (state 1) with stock → idempotent skip (never re-flip a live one)
    csv_text = _csv(
        "1/M;P1;Bunda;TESTSUP;visible;Skladom;Skladom;99.90;5;")
    assert ss.compute_candidates(csv_text) == []


def test_no_candidate_when_discontinued_detailonly_even_with_residual_stock():
    # detailOnly + 'Predaj výrobku skončil' (state 3) is the manager's CONSCIOUS
    # „už nepredávať" decision — a residual unit of stock must NOT re-list it. This
    # is the „neprepíše vedomé off rozhodnutie manažéra" invariant (#98).
    csv_text = _csv(
        "1/M;P1;Bunda;TESTSUP;detailOnly;Predaj výrobku skončil;"
        "Predaj výrobku skončil;99.90;5;")
    assert ss.compute_candidates(csv_text) == []


def test_no_candidate_when_hidden_even_with_stock():
    # hidden visibility (state 3) is a conscious off decision — never flipped
    csv_text = _csv(
        "1/M;P1;Bunda;TESTSUP;hidden;Vypredané;Vypredané;99.90;5;")
    assert ss.compute_candidates(csv_text) == []


def test_no_candidate_when_vypredane_but_not_visible_detailonly():
    # detailOnly + Vypredané (state 2 by state_of, but not `visible`) is „kept for
    # Google only" — the same visible-only gate restock_skladom uses; not flipped.
    csv_text = _csv(
        "1/M;P1;Bunda;TESTSUP;detailOnly;Vypredané;Vypredané;99.90;5;")
    assert ss.compute_candidates(csv_text) == []


def test_out_of_stock_text_via_aos_only_still_a_candidate():
    # availabilityInStock empty but availabilityOutOfStock='Vypredané' is state 2;
    # with physical stock>0 it is a genuine candidate.
    csv_text = _csv(
        "1/M;P1;Bunda;TESTSUP;visible;;Vypredané;99.90;5;")
    rows = ss.compute_candidates(csv_text)
    assert len(rows) == 1 and rows[0]["availabilityText"] == "Vypredané"


def test_comma_decimal_stock_is_positive():
    rows = ss.compute_candidates(_csv(_have_but_vypredane(stock="3,0")))
    assert len(rows) == 1 and rows[0]["stock"] == "3,0"


def test_missing_code_row_skipped():
    csv_text = _csv(";P1;Bunda;TESTSUP;visible;Vypredané;Vypredané;99.90;5;")
    assert ss.compute_candidates(csv_text) == []


def test_empty_or_blank_csv_never_crashes():
    assert ss.compute_candidates("") == []
    assert ss.compute_candidates(None) == []


# ── dedup + mixed catalog ───────────────────────────────────────────────────────
def test_duplicate_variant_code_emitted_once():
    csv_text = _csv(
        _have_but_vypredane(code="15/40", pair="P1"),
        _have_but_vypredane(code="15/40", pair="P1"),
    )
    rows = ss.compute_candidates(csv_text)
    assert [r["code"] for r in rows] == ["15/40"]


def test_mixed_catalog_only_the_have_but_vypredane_variant_flagged():
    csv_text = _csv(
        _have_but_vypredane(code="1/M", pair="P1", name="Máme ale vypredané"),  # target
        "2/S;P2;Uz skladom;TESTSUP;visible;Skladom;Skladom;19.90;5;",           # state 1
        "3/L;P3;Bez skladu;TESTSUP;visible;Vypredané;Vypredané;29.90;0;",       # stock 0
        "4/X;P4;Ukoncene;TESTSUP;detailOnly;Predaj výrobku skončil;"
        "Predaj výrobku skončil;39.90;5;",                                      # state 3
    )
    rows = ss.compute_candidates(csv_text)
    assert [r["code"] for r in rows] == ["1/M"]


# ── the row builder (import_builder.skladom_rows) ───────────────────────────────
def test_skladom_rows_sets_both_availability_visible_and_no_stock_column():
    cands = ss.compute_candidates(_csv(_have_but_vypredane()))
    rows = ib.skladom_rows(cands)
    assert ib.SKLADOM_COLS == ["code", "pairCode", "productVisibility",
                               "availabilityInStock", "availabilityOutOfStock"]
    assert len(rows) == 1
    row = rows[0]
    # exactly SKLADOM_COLS wide — the stock column is DELIBERATELY absent so the
    # product keeps its real positive stock (never overwritten with a fictional one)
    assert len(row) == len(ib.SKLADOM_COLS)
    assert row == ["1/M", "P1", "visible", "Skladom", "Skladom"]


def test_skladom_rows_backfills_paircode_and_dedups():
    cands = [{"code": "9/M"}, {"code": "9/M"}, {"code": "8/L", "pairCode": "PP"}]
    rows = ib.skladom_rows(cands, {"9/M": "P9"})
    assert rows == [
        ["9/M", "P9", "visible", "Skladom", "Skladom"],
        ["8/L", "PP", "visible", "Skladom", "Skladom"],
    ]


def test_skladom_rows_empty_candidates():
    assert ib.skladom_rows([]) == []
    assert ib.skladom_rows([{"code": ""}]) == []


def test_discontinued_signal_in_either_availability_field_never_candidate():
    """#98 review Finding 1: state_of() sees only ONE availability field (ais or aos),
    so a 'skončil' (discontinued) hiding in the OTHER field must STILL exclude the
    product — else a discontinued item with contradictory fields gets re-listed as
    sellable on the live eshop. RED before the either-field guard."""
    # skončil in availabilityOutOfStock, 'Vypredané' in availabilityInStock →
    # state_of only reads ais='Vypredané' (state 2) and misses the skončil in aos.
    masked = "9/X;P9;Ukončený;TESTSUP;visible;Vypredané;Predaj výrobku skončil;99.90;5;"
    assert ss.compute_candidates(_csv(masked)) == []
    # symmetric case (skončil in ais) is already caught by state_of == 3 — keep it green
    other = "9/Y;P9;Ukončený;TESTSUP;visible;Predaj výrobku skončil;Vypredané;99.90;5;"
    assert ss.compute_candidates(_csv(other)) == []
