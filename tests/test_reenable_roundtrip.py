"""End-to-end OFF→ON round-trip regression lock (#97).

Boss StepanDK actively disables products that may be unavailable for a while and
relies on them coming back ON reliably once restocked. The individual pieces of the
cycle are each unit-tested elsewhere (import_builder.state_rows / restock_rows /
sanitize_csv, restock_skladom.compute_candidates, export_helpers.state_of), but NO
test walks the WHOLE cycle as one chain. This module locks it:

    sellable -> manager DISABLES (state_rows: unavailable)
             -> eshop now shows Vypredané+visible (state 2)
             -> supplier restocks -> compute_candidates DETECTS it
             -> restock_rows RE-ENABLES (both availability fields Skladom,
                visible, positive stock — never an empty cell that would leave
                the product Vypredané)
             -> eshop now shows Skladom (state 1) -> NOT detected again (idempotent)

The load-bearing property for "re-enable must reliably work": the exact CSV shape
`state_rows` writes on disable is precisely what `compute_candidates` reads back to
recognise a restock candidate (the contract between the disable writer and the
detect reader), and the re-enable output EXPLICITLY writes the selling values on
both availability fields — because Shoptet OVERWRITES a present-but-empty cell, an
implicit/blank re-enable would silently leave the product sold out.

Hermetic: inline CSV + inline supplier rows, no network, no Flask.
"""
import csv
import io
from datetime import datetime, timedelta, timezone

from parovanie.export_helpers import state_of
from parovanie.import_builder import (
    RESTOCK_COLS,
    STATE_HEADER,
    restock_rows,
    sanitize_csv,
    state_rows,
)
from parovanie.restock_skladom import compute_candidates

NOW = datetime(2026, 7, 23, 6, 0, tzinfo=timezone(timedelta(hours=2)))
FRESH = (NOW - timedelta(hours=1)).isoformat(timespec="seconds")
LINK = "https://supplier.test/p/1"

# The eshop export columns compute_candidates + state_of read back.
_EXPORT_HEADER = ("code;pairCode;name;supplier;productVisibility;availabilityInStock;"
                  "availabilityOutOfStock;price;stock;internalNote")


def _supplier(available=True, ok=True, checked_at=FRESH):
    return {"link": LINK, "ok": ok, "available": available, "price": 79.90,
            "availabilityText": "Skladom", "supplier": "TESTSUP",
            "checkedAt": checked_at}


def _export_from(state_map, *, name="Bunda", price="99.90", link=LINK):
    """Build the eshop CSV export a product would show AFTER an import landed the
    given state (a {column: value} map keyed by STATE_HEADER / RESTOCK_COLS names).
    Derives the availability/visibility/stock cells straight from the import row, so
    if the writer stopped setting a field this export — and thus detection — changes."""
    row = {
        "code": state_map["code"],
        "pairCode": state_map.get("pairCode", ""),
        "name": name,
        "supplier": "TESTSUP",
        "productVisibility": state_map.get("productVisibility", ""),
        "availabilityInStock": state_map.get("availabilityInStock", ""),
        "availabilityOutOfStock": state_map.get("availabilityOutOfStock", ""),
        "price": price,
        "stock": state_map.get("stock", "0"),
        "internalNote": link,
    }
    cols = _EXPORT_HEADER.split(";")
    line = ";".join(row[c] for c in cols)
    return _EXPORT_HEADER + "\r\n" + line + "\r\n"


def _product(code="1/M", pair="P1", key="TESTSUP|P1"):
    return {"key": key, "supplier": "TESTSUP", "variant_codes": [code], "pairCode": pair}


# ── the full OFF→ON round-trip ──────────────────────────────────────────────────
def test_full_off_on_cycle_disable_detect_reenable():
    """The whole cycle in one chain: disable -> detect -> re-enable -> idempotent."""
    prod = _product()
    code2pair = {"1/M": "P1"}

    # 1) DISABLE — manager marks the product unavailable (state 2).
    disable = dict(zip(STATE_HEADER, state_rows([prod], {"TESTSUP|P1":
                       {"status": "unavailable"}}, code2pair)[0]))
    # the disabled product genuinely reads as eshop state 2 (Vypredané, off)
    assert state_of(disable["productVisibility"],
                    disable["availabilityInStock"] or disable["availabilityOutOfStock"]) == 2

    # 2) DETECT — with the eshop now showing that disable + a fresh, in-stock
    #    supplier, the nightly restock detection must pick EXACTLY this product.
    off_export = _export_from(disable)
    cands = compute_candidates(off_export, [_supplier(available=True)], NOW)
    assert [c["code"] for c in cands] == ["1/M"], "disabled product not detected for restock"

    # 3) RE-ENABLE — the import row must EXPLICITLY put it back on sale.
    reenable = dict(zip(RESTOCK_COLS, restock_rows(cands, code2pair)[0]))
    assert reenable["productVisibility"] == "visible"
    assert reenable["availabilityInStock"] == "Skladom"
    assert reenable["availabilityOutOfStock"] == "Skladom"   # BOTH — CEO 2026-07-14
    assert int(reenable["stock"]) > 0
    # neither availability cell may be blank — an empty cell would leave it Vypredané
    assert reenable["availabilityInStock"] and reenable["availabilityOutOfStock"]

    # 4) RESULT + IDEMPOTENT — the re-enabled eshop reads as state 1, and a second
    #    detection pass with the same fresh supplier never re-flips a live product.
    on_export = _export_from(reenable)
    assert state_of(reenable["productVisibility"], reenable["availabilityInStock"]) == 1
    assert compute_candidates(on_export, [_supplier(available=True)], NOW) == []


def test_disable_output_is_exactly_what_detection_reads_back():
    """Contract guard: the visibility/availability columns `state_rows` writes on
    disable are precisely the ones `compute_candidates` reads to classify a state-2
    restock candidate. If either side is changed without the other, this breaks."""
    disable = dict(zip(STATE_HEADER, state_rows(
        [_product()], {"TESTSUP|P1": {"status": "unavailable"}}, {"1/M": "P1"})[0]))
    # every column detection depends on is present and carries the disable value
    for col in ("productVisibility", "availabilityInStock", "availabilityOutOfStock"):
        assert col in disable
    cands = compute_candidates(_export_from(disable), [_supplier()], NOW)
    assert len(cands) == 1 and cands[0]["code"] == "1/M"


def test_reenable_row_never_leaves_an_empty_availability_cell():
    """The core "reliable re-enable" property in isolation: EVERY restock row sets
    both availability fields to a non-empty selling value + visible + positive stock.
    A blank cell would be OVERWRITTEN-to-empty by Shoptet, leaving the product sold
    out — the exact failure #97 exists to prevent."""
    rows = restock_rows([{"code": "1/M", "pairCode": "P1"},
                         {"code": "2/S", "pairCode": "P2"}])
    for r in rows:
        row = dict(zip(RESTOCK_COLS, r))
        assert row["productVisibility"] == "visible"
        assert row["availabilityInStock"] == "Skladom"
        assert row["availabilityOutOfStock"] == "Skladom"
        assert row["availabilityInStock"] and row["availabilityOutOfStock"]  # never blank
        assert int(row["stock"]) > 0
        # and the product this row produces reads back as on-sale (state 1)
        assert state_of(row["productVisibility"], row["availabilityInStock"]) == 1


def test_reenable_via_n8n_feed_sanitize_produces_explicit_enable(tmp_path):
    """The OTHER live re-enable path (#108 nightly workflow -> /api/n8n/shoptet-import):
    an upstream feed with the restock columns set to selling values is sanitized to
    RESTOCK_COLS. The sanitized row must EXPLICITLY re-enable (both availability
    fields Skladom, visible, positive stock) and must NOT leak the feed's price/name
    into the eshop."""
    feed = tmp_path / "feed.csv"
    out = tmp_path / "import.csv"
    header = ["code", "pairCode", "name", "purchasePrice", "ourPrice",
              "productVisibility", "availabilityInStock", "availabilityOutOfStock", "stock"]
    with open(feed, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(header)
        w.writerow(["1/M", "P1", "Bunda", "40", "99.90",
                    "visible", "Skladom", "Skladom", "5"])
    n = sanitize_csv(str(feed), str(out))
    assert n == 1
    with open(out, encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f, delimiter=";")
        assert rd.fieldnames == RESTOCK_COLS
        row = next(rd)
    assert row["productVisibility"] == "visible"
    assert row["availabilityInStock"] == "Skladom"
    assert row["availabilityOutOfStock"] == "Skladom"
    assert int(row["stock"]) > 0
    # the sanitized re-enable reads back as on-sale, and the feed's price/name are gone
    assert state_of(row["productVisibility"], row["availabilityInStock"]) == 1
    text = out.read_text(encoding="utf-8-sig")
    for leaked in ("Bunda", "99.90", "purchasePrice", "ourPrice", "40"):
        assert leaked not in text


def test_reenabled_product_is_not_detected_again_even_with_fresh_supplier():
    """Idempotency of the whole cycle: once a product is back to Skladom (state 1),
    the detection never picks it again — even with a fresh, in-stock supplier — so a
    routine export refresh cannot double-flip a live product."""
    on_state = {"code": "1/M", "pairCode": "P1", "productVisibility": "visible",
                "availabilityInStock": "Skladom", "availabilityOutOfStock": "Skladom",
                "stock": "5"}
    assert compute_candidates(_export_from(on_state), [_supplier(available=True)], NOW) == []


def _one_row(csv_text):
    return next(csv.DictReader(io.StringIO(csv_text), delimiter=";"))


def test_export_helper_models_a_real_state2_row():
    """Sanity: the _export_from helper actually builds a Vypredané+visible row (so a
    negative round-trip result means a real code change, not a broken fixture)."""
    disable = dict(zip(STATE_HEADER, state_rows(
        [_product()], {"TESTSUP|P1": {"status": "unavailable"}}, {"1/M": "P1"})[0]))
    row = _one_row(_export_from(disable))
    assert row["productVisibility"] == "visible"
    assert row["availabilityOutOfStock"] == "Vypredané"
    assert row["internalNote"].startswith("http")
