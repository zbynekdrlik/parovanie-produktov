import csv

import pytest

from parovanie.import_builder import (
    EXTERNALCODE_HEADER,
    LINK_HEADER,
    RESTOCK_COLS,
    RESTOCK_STOCK,
    STATE_HEADER,
    SUPPLIER_HEADER,
    externalcode_rows,
    link_rows,
    new_order_pairing_keys,
    new_pairing_keys,
    new_supplier_keys,
    order_pairing_rows,
    restock_rows,
    sanitize_csv,
    state_rows,
    supplier_rows,
)


def test_new_pairing_keys_only_new_good_manual_with_url():
    dec = {
        "new_good": {"status": "good", "url": "https://s/a"},
        "new_manual": {"status": "manual", "url": "https://s/b"},
        "no_url": {"status": "good", "url": ""},
        "bad": {"status": "bad", "url": "https://s/c"},
        "already": {"status": "good", "url": "https://s/d"},
    }
    uploaded = {"already": "https://s/d"}
    keys = set(new_pairing_keys(dec, uploaded))
    assert keys == {"new_good", "new_manual"}


def test_new_pairing_keys_detects_changed_url():
    dec = {"k": {"status": "manual", "url": "https://s/NEW"}}
    assert new_pairing_keys(dec, {"k": "https://s/OLD"}) == ["k"]
    assert new_pairing_keys(dec, {"k": "https://s/NEW"}) == []


def _write(path, header, rows, delim=";", bom=False):
    enc = "utf-8-sig" if bom else "utf-8"
    with open(path, "w", encoding=enc, newline="") as f:
        w = csv.writer(f, delimiter=delim)
        w.writerow(header)
        w.writerows(rows)


def test_sanitize_drops_unsafe_columns_keeps_restock(tmp_path):
    # An n8n feed with price/name columns must NEVER reach Shoptet — only the
    # restock columns survive, so the live eshop's prices/names are not overwritten.
    src = tmp_path / "feed.csv"
    out = tmp_path / "import.csv"
    header = ["code", "pairCode", "name", "purchasePrice", "ourPrice",
              "productVisibility", "availabilityInStock", "availabilityOutOfStock",
              "stock", "competition_price"]
    _write(src, header, [["15233/M", "1564", "Vesta", "999", "67.80",
                          "visible", "Skladom", "Skladom", "5", "111"]])
    n = sanitize_csv(str(src), str(out))
    assert n == 1
    with open(out, encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f, delimiter=";")
        assert rd.fieldnames == RESTOCK_COLS
        row = next(rd)
    assert row == {"code": "15233/M", "pairCode": "1564",
                   "productVisibility": "visible", "availabilityInStock": "Skladom",
                   "availabilityOutOfStock": "Skladom", "stock": "5"}
    # the dangerous columns are gone
    text = out.read_text(encoding="utf-8-sig")
    for bad in ("999", "67.80", "Vesta", "purchasePrice", "ourPrice", "competition_price"):
        assert bad not in text


def test_sanitize_keeps_availability_out_of_stock(tmp_path):
    # CEO 2026-07-14: a restock must set BOTH availability fields. Shoptet shows
    # availabilityOutOfStock once stock sells down to 0, so a restocked product
    # kept flipping back to the stale "Vypredané" — the column must survive.
    src = tmp_path / "feed.csv"
    out = tmp_path / "import.csv"
    header = ["code", "pairCode", "name", "productVisibility",
              "availabilityInStock", "availabilityOutOfStock", "stock"]
    _write(src, header, [["15233/M", "1564", "Vesta", "visible",
                          "Skladom", "Skladom", "5"]])
    sanitize_csv(str(src), str(out))
    with open(out, encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f, delimiter=";")
        assert "availabilityOutOfStock" in (rd.fieldnames or [])
        assert next(rd)["availabilityOutOfStock"] == "Skladom"


def test_sanitize_rejects_feed_missing_whitelisted_column(tmp_path):
    # A present-but-EMPTY column ERASES the field in Shoptet. Padding a missing
    # input column with "" would therefore wipe live data — reject the feed.
    src = tmp_path / "feed.csv"
    out = tmp_path / "import.csv"
    _write(src, ["code", "pairCode", "stock"], [["A/1", "10", "5"]])
    with pytest.raises(ValueError, match="availabilityOutOfStock"):
        sanitize_csv(str(src), str(out))


def test_sanitize_writes_bom_utf8(tmp_path):
    src = tmp_path / "feed.csv"
    out = tmp_path / "import.csv"
    _write(src, ["code", "pairCode"], [["A/1", "10"]])
    sanitize_csv(str(src), str(out), cols=["code", "pairCode"])
    assert out.read_bytes().startswith(b"\xef\xbb\xbf")  # UTF-8 BOM (Shoptet import)


def test_sanitize_skips_empty_code_rows(tmp_path):
    src = tmp_path / "feed.csv"
    out = tmp_path / "import.csv"
    _write(src, ["code", "pairCode", "stock"], [["A/1", "10", "5"], ["", "10", "5"]])
    assert sanitize_csv(str(src), str(out), cols=["code", "pairCode", "stock"]) == 1


def test_sanitize_reads_bom_input(tmp_path):
    src = tmp_path / "feed.csv"
    out = tmp_path / "import.csv"
    _write(src, ["code", "pairCode", "stock"], [["A/1", "10", "5"]], bom=True)
    assert sanitize_csv(str(src), str(out), cols=["code", "pairCode", "stock"]) == 1


def test_sanitize_rejects_csv_without_code_paircode(tmp_path):
    src = tmp_path / "feed.csv"
    out = tmp_path / "import.csv"
    _write(src, ["name", "stock"], [["Vesta", "5"]])
    with pytest.raises(ValueError):
        sanitize_csv(str(src), str(out))


def test_headers_disjoint_no_empty_wipe():
    # link file carries ONLY internalNote; state file ONLY the state columns —
    # so neither writes an empty cell that would wipe the other's fields.
    assert LINK_HEADER == ["code", "pairCode", "internalNote"]
    assert "internalNote" not in STATE_HEADER
    assert STATE_HEADER[:2] == ["code", "pairCode"]
    assert "productVisibility" in STATE_HEADER and "availabilityInStock" in STATE_HEADER


def test_link_rows_dedupes_codes_shoptet_requires_unique():
    # Catalog has duplicate products that SHARE variant codes (e.g. lady-jacket /
    # jacket-2). If both are paired, link_rows must NOT emit the same code twice —
    # Shoptet aborts the whole import with 'Data in column "code" are not unique'.
    products = [
        {"key": "k1", "variant_codes": ["15218/40", "15221/40"]},
        {"key": "k2", "variant_codes": ["15218/40", "15221/40"]},  # same codes (dupe product)
    ]
    dec = {"k1": {"status": "good", "url": "https://s/x"},
           "k2": {"status": "manual", "url": "https://s/x"}}
    rows = link_rows(products, dec, {})
    codes = [r[0] for r in rows]
    assert len(codes) == len(set(codes)), f"duplicate codes emitted: {codes}"
    assert sorted(codes) == ["15218/40", "15221/40"]


def test_link_rows_put_url_in_internalnote():
    products = [{"key": "k1", "variant_codes": ["A/1", "A/2"]}]
    dec = {"k1": {"status": "good", "url": "https://h/x"}}
    rows = link_rows(products, dec, {"A/1": "100", "A/2": "100"})
    assert rows == [["A/1", "100", "https://h/x"], ["A/2", "100", "https://h/x"]]


def test_manual_status_is_also_a_link():
    products = [{"key": "k", "variant_codes": ["M"]}]
    rows = link_rows(products, {"k": {"status": "manual", "url": "https://h/m"}}, {"M": "1"})
    assert rows == [["M", "1", "https://h/m"]]


def test_link_rows_skip_link_without_url_and_non_links():
    products = [{"key": "k1", "variant_codes": ["A"]}, {"key": "k2", "variant_codes": ["B"]}]
    dec = {"k1": {"status": "good", "url": ""}, "k2": {"status": "unavailable"}}
    assert link_rows(products, dec, {"A": "1", "B": "2"}) == []


def test_state_rows_unavailable_is_visible_vypredane():
    products = [{"key": "k2", "variant_codes": ["B"]}]
    rows = state_rows(products, {"k2": {"status": "unavailable"}}, {"B": "200"})
    assert rows == [["B", "200", "visible", "0", "Vypredané", "Vypredané"]]


def test_state_rows_discontinued_is_detailonly_skoncil():
    products = [{"key": "k3", "variant_codes": ["C"]}]
    rows = state_rows(products, {"k3": {"status": "discontinued"}}, {"C": "300"})
    assert rows == [["C", "300", "detailOnly", "0",
                     "Predaj výrobku skončil", "Predaj výrobku skončil"]]


def test_state_rows_skip_links():
    # a link decision produces NO state row (it goes to link_rows instead)
    products = [{"key": "k1", "variant_codes": ["A"]}]
    assert state_rows(products, {"k1": {"status": "good", "url": "https://h"}}, {"A": "1"}) == []


def test_undecided_products_excluded_from_both():
    products = [{"key": "k4", "variant_codes": ["D"]}]
    assert link_rows(products, {}, {"D": "400"}) == []
    assert state_rows(products, {}, {"D": "400"}) == []


# --- order_pairing_rows: inline pairings from the Na-objednanie tab ----------- #
def test_order_pairing_rows_emit_internalnote_with_paircode():
    rows = order_pairing_rows(
        {"60028/XL": "https://supplier/a", "13325": "https://supplier/b"},
        {"60028/XL": "500", "13325": ""})
    assert ["60028/XL", "500", "https://supplier/a"] in rows
    assert ["13325", "", "https://supplier/b"] in rows   # empty pairCode kept (code-only match)
    assert len(rows) == 2


def test_order_pairing_rows_skip_empty_url_and_blank_code():
    rows = order_pairing_rows(
        {"A": "", "   ": "https://x", "B": "  https://supplier/c  "}, {})
    # empty url dropped, blank code dropped, surrounding whitespace trimmed
    assert rows == [["B", "", "https://supplier/c"]]


def test_order_pairing_rows_excludes_codes_already_in_decisions():
    # a code covered by a reviewed decision (link_rows) must NOT be re-emitted —
    # Shoptet aborts the whole import on a duplicate code.
    rows = order_pairing_rows(
        {"A/1": "https://inline", "C/1": "https://inline2"},
        {"A/1": "1", "C/1": "3"}, exclude_codes={"A/1"})
    assert rows == [["C/1", "3", "https://inline2"]]


def test_order_pairing_rows_dedupes_codes_that_normalize_equal():
    # two keys that strip to the same code keep only the first
    rows = order_pairing_rows({"X": "https://a", "X ": "https://b"}, {})
    assert [r[0] for r in rows] == ["X"]


# --- supplier_rows: supplier names assigned on the Na-objednanie tab --------- #
def test_supplier_rows_emit_supplier_with_paircode():
    rows = supplier_rows({"60028/XL": "BETALOV", "13325": "WETLAND"},
                         {"60028/XL": "500"})
    assert ["60028/XL", "500", "BETALOV"] in rows
    assert ["13325", "", "WETLAND"] in rows   # empty pairCode kept (code-only match)
    assert SUPPLIER_HEADER == ["code", "pairCode", "supplier"]


def test_supplier_rows_skip_empty_supplier_and_blank_code():
    rows = supplier_rows({"A": "", "   ": "BETALOV", "B": "  WETLAND  "}, {})
    assert rows == [["B", "", "WETLAND"]]   # empty supplier + blank code dropped, value trimmed


def test_supplier_rows_excludes_and_dedupes_codes():
    # a code already handled elsewhere is excluded; codes normalizing equal keep first.
    assert supplier_rows({"A/1": "X", "C/1": "Y"}, {}, exclude_codes={"A/1"}) == [["C/1", "", "Y"]]
    assert [r[0] for r in supplier_rows({"X": "A", "X ": "B"}, {})] == ["X"]


def test_new_supplier_keys_only_new_or_changed():
    assigns = {"new": "BETALOV", "blank": "", "  ": "X", "same": "WETLAND", "changed": "ODIMON"}
    uploaded = {"same": "WETLAND", "changed": "TRIGONA"}
    assert set(new_supplier_keys(assigns, uploaded)) == {"new", "changed"}


# --- new_order_pairing_keys: dedup state for the #38 nightly order_pairings push --- #
def test_new_order_pairing_keys_only_new_or_changed():
    # `uploaded` tracks order_pairings under the `order:<code>` namespace — distinct
    # from new_pairing_keys' review-decision namespace, never collides.
    order_pairings = {"new": "https://s/a", "blank": "", "  ": "https://s/b",
                       "same": "https://s/c", "changed": "https://s/NEW"}
    uploaded = {"order:same": "https://s/c", "order:changed": "https://s/OLD",
                "same": "https://SHOULD-NOT-MATTER"}   # a decision-namespace key, no clash
    assert set(new_order_pairing_keys(order_pairings, uploaded)) == {"new", "changed"}


# --- externalcode_rows: GRUBE per-size itemId write-back (Task 8) ------------- #
def test_externalcode_rows_basic():
    gc = {"60645/L": {"itemId": "1547734519"}, "60645/S": {"itemId": "1547734523"}}
    rows = externalcode_rows(gc, {"60645/L": "395", "60645/S": "395"})
    assert EXTERNALCODE_HEADER == ["code", "pairCode", "externalCode"]
    assert sorted(rows) == [["60645/L", "395", "1547734519"],
                            ["60645/S", "395", "1547734523"]]


def test_externalcode_rows_drops_empty_and_nonnumeric():
    # empty itemId would WIPE the existing externalCode cell; non-numeric is junk /
    # possible formula-injection lead — both dropped so only a real itemId is written.
    gc = {"a": {"itemId": ""}, "b": {"itemId": "=EVIL"}, "c": {"itemId": "1234567890"}}
    rows = externalcode_rows(gc, {"c": "9"})
    assert rows == [["c", "9", "1234567890"]]


def test_externalcode_rows_dedup_first_wins_and_exclude():
    gc = {"x": {"itemId": "1111111111"}}
    assert externalcode_rows(gc, {"x": "9"}, exclude_codes={"x"}) == []   # excluded
    # missing itemId key entirely (no 'itemId') is treated as empty -> dropped
    assert externalcode_rows({"y": {}}, {"y": "9"}) == []


def test_externalcode_rows_all_numeric_guard():
    gc = {"60645/L": {"itemId": "1547734519"}}
    rows = externalcode_rows(gc, {"60645/L": "395"})
    assert all(len(r) == 3 and r[2].isdigit() for r in rows)


# --- link_rows GRUBE internalNote normalization (Task 9) --------------------- #
def test_link_rows_grube_url_normalized_to_de():
    # a GRUBE product's note URL is rebuilt to the canonical grube.de detail URL
    products = [{"key": "GRUBE|395", "supplier": "GRUBE", "variant_codes": ["60645/L"]}]
    decisions = {"GRUBE|395": {"status": "manual",
                               "url": "https://www.grube.sk/p/grand-nord/154773/?q=a#itemId=1"}}
    rows = link_rows(products, decisions, {"60645/L": "395"})
    note = [r for r in rows if r[0] == "60645/L"][0][2]
    assert note == "https://www.grube.de/p/x/154773/"


def test_link_rows_nongrube_url_unchanged():
    # a non-grube product's note URL is written verbatim (no normalization)
    products = [{"key": "WETLAND|9", "supplier": "WETLAND", "variant_codes": ["WL/1"]}]
    decisions = {"WETLAND|9": {"status": "manual", "url": "https://www.wetland.sk/p/foo"}}
    rows = link_rows(products, decisions, {"WL/1": "9"})
    note = [r for r in rows if r[0] == "WL/1"][0][2]
    assert note == "https://www.wetland.sk/p/foo"


def test_link_rows_scoping_is_by_supplier_not_url_host():
    # anti-cheat: a NON-GRUBE product whose URL happens to be a grube.sk product URL
    # must stay VERBATIM — the normalization is gated on supplier=="GRUBE", not the host.
    products = [{"key": "OTHER|1", "supplier": "WETLAND", "variant_codes": ["O/1"]}]
    decisions = {"OTHER|1": {"status": "manual",
                             "url": "https://www.grube.sk/p/grand-nord/154773/?q=a#itemId=1"}}
    rows = link_rows(products, decisions, {"O/1": "1"})
    note = [r for r in rows if r[0] == "O/1"][0][2]
    assert note == "https://www.grube.sk/p/grand-nord/154773/?q=a#itemId=1"


def test_link_rows_grube_unparseable_url_falls_back_to_raw():
    # GRUBE product but URL has no /p/<slug>/<id>/ -> to_grube_de is None -> keep raw
    products = [{"key": "GRUBE|7", "supplier": "GRUBE", "variant_codes": ["G/1"]}]
    decisions = {"GRUBE|7": {"status": "good", "url": "https://www.grube.sk/search/?q=hose"}}
    rows = link_rows(products, decisions, {"G/1": "7"})
    note = [r for r in rows if r[0] == "G/1"][0][2]
    assert note == "https://www.grube.sk/search/?q=hose"


# ── restock_rows (#108: Vypredané → Skladom) ────────────────────────────────────
def test_restock_rows_sets_both_availability_fields_visible_stock():
    rows = restock_rows([{"code": "1/M", "pairCode": "P1"}])
    assert rows == [["1/M", "P1", "visible", "Skladom", "Skladom", RESTOCK_STOCK]]
    # the row exactly follows the whitelisted RESTOCK_COLS order (6 columns)
    assert len(rows[0]) == len(RESTOCK_COLS) == 6


def test_restock_rows_both_availability_columns_are_skladom():
    # regression guard for the CEO 2026-07-14 fix: BOTH availabilityInStock AND
    # availabilityOutOfStock must be 'Skladom' (index 3 and 4 of RESTOCK_COLS)
    assert RESTOCK_COLS[3] == "availabilityInStock"
    assert RESTOCK_COLS[4] == "availabilityOutOfStock"
    r = restock_rows([{"code": "1/M", "pairCode": "P1"}])[0]
    assert r[3] == "Skladom" and r[4] == "Skladom"


def test_restock_rows_dedupes_codes_shoptet_requires_unique():
    rows = restock_rows([{"code": "1/M", "pairCode": "P1"},
                         {"code": "1/M", "pairCode": "P1"},
                         {"code": "2/S", "pairCode": "P2"}])
    assert [r[0] for r in rows] == ["1/M", "2/S"]


def test_restock_rows_backfills_paircode_from_code2pair():
    rows = restock_rows([{"code": "9/Z"}], code2pair={"9/Z": "777"})
    assert rows == [["9/Z", "777", "visible", "Skladom", "Skladom", RESTOCK_STOCK]]


def test_restock_rows_skips_blank_code():
    assert restock_rows([{"code": "", "pairCode": "P1"}, {"pairCode": "P2"}]) == []


def test_restock_rows_empty_input():
    assert restock_rows([]) == []
