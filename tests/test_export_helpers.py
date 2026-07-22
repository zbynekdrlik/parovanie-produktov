from parovanie.export_helpers import (
    IMGCOLS,
    current_of,
    fill_missing_prices,
    resync_current,
    row_images,
    slug,
    state_of,
)


def test_slug_folds_diacritics_and_nonalnum():
    assert slug("Bunda ALFA – poľovnícka!") == "bunda-alfa-polovnicka"
    assert slug("") == ""


def test_slug_strip_leading_number_optional():
    assert slug("01 Bunda Alaska") == "01-bunda-alaska"
    assert slug("01 Bunda Alaska", strip_leading_number=True) == "bunda-alaska"


def test_state_of_three_states():
    assert state_of("visible", "Skladom") == 1
    assert state_of("visible", "Vypredané") == 2
    assert state_of("visible", "Momentálne nedostupné") == 2
    # the drift this consolidation fixes: 'neni skladem' (no diacritics) → state 2
    assert state_of("visible", "neni skladem") == 2
    assert state_of("visible", "Predaj výrobku skončil") == 3
    assert state_of("hidden", "") == 3
    assert state_of("blocked", "Skladom") == 3


def test_current_of_carries_price_and_state():
    # the canonical `current` dict both review scripts build: includes price/std/stock
    # so the review card can show OUR product's price (avail picks ais, else aos).
    cur = current_of("visible", "Skladom", "", price="121,90", std="126,90", stock="3")
    assert cur == {"state": 1, "off": False, "vis": "visible", "avail": "Skladom",
                   "price": "121,90", "std": "126,90", "stock": "3"}


def test_current_of_defaults_prices_empty_and_uses_aos_when_no_ais():
    # add_supplier path historically built current with NO price → card showed none.
    # Defaults keep prices empty (still a valid dict) and off follows state.
    cur = current_of("detailOnly", "", "Predaj výrobku skončil")
    assert cur["avail"] == "Predaj výrobku skončil" and cur["state"] == 3 and cur["off"] is True
    assert cur["price"] == "" and cur["std"] == "" and cur["stock"] == ""


def test_fill_missing_prices_only_fills_empty_from_first_priced_code():
    # the bug this fixes: newer-supplier review items had current with no price, so the
    # card showed no price for OUR product. Backfill fills price/std/stock from the first
    # variant code that has a price, leaving already-priced items and state/avail intact.
    items = [
        {"variant_codes": ["NOPRICE", "2211"],
         "current": {"state": 2, "off": True, "vis": "visible", "avail": "x",
                     "price": "", "std": "", "stock": ""}},
        {"variant_codes": ["A"],                      # already priced → untouched
         "current": {"state": 1, "off": False, "vis": "visible", "avail": "Skladom",
                     "price": "50,00", "std": "", "stock": "1"}},
        {"variant_codes": [],                         # no codes → stays empty
         "current": {"state": 3, "off": True, "vis": "hidden", "avail": "",
                     "price": "", "std": "", "stock": ""}},
    ]
    code2price = {"2211": ("12,90", "15,00", "3"), "A": ("99,99", "", "9")}
    n = fill_missing_prices(items, code2price)
    assert n == 1
    c0 = items[0]["current"]
    assert c0["price"] == "12,90" and c0["std"] == "15,00" and c0["stock"] == "3"
    assert c0["state"] == 2 and c0["avail"] == "x"          # untouched eshop-state
    assert items[1]["current"]["price"] == "50,00"          # already priced, not overwritten
    assert items[2]["current"]["price"] == ""               # no resolvable code


def test_fill_missing_prices_skips_codes_with_blank_price():
    # a variant code present in the export but with an empty price must not "fill" a blank
    items = [{"variant_codes": ["BLANK"],
              "current": {"state": 1, "vis": "v", "avail": "a", "price": "", "std": "", "stock": ""}}]
    n = fill_missing_prices(items, {"BLANK": ("", "", "5")})
    assert n == 0 and items[0]["current"]["price"] == ""


def test_row_images_collects_http_deduped_in_order():
    row = {"defaultImage": "https://x/a.jpg", "image2": "https://x/b.jpg",
           "image3": "https://x/a.jpg", "image4": "not-a-url", "image": ""}
    assert row_images(row) == ["https://x/a.jpg", "https://x/b.jpg"]
    assert IMGCOLS[0] == "defaultImage"


# ── resync_current (#119 — shared by scripts/resync_export.py AND the new hourly
#    in-app "Sync zo Shoptetu" automation; extracted so the two never drift) ────
def _row(supplier, name, code="", vis="visible", ais="Skladom", aos="",
         price="", std="", stock="", img=""):
    return {"supplier": supplier, "name": name, "code": code,
            "productVisibility": vis, "availabilityInStock": ais,
            "availabilityOutOfStock": aos, "price": price, "standardPrice": std,
            "stock": stock, "defaultImage": img}


def test_resync_current_updates_matched_product_by_supplier_and_name():
    rows = [_row("BETALOV", "Bunda ALFA", code="1/M", price="99,00",
                 std="109,00", stock="3", img="https://x/a.jpg"),
            _row("BETALOV", "Bunda ALFA", code="1/L")]
    rd = [{"supplier": "BETALOV", "name": "Bunda ALFA", "current": {}}]
    counts = resync_current(rows, rd, {"BETALOV"})
    assert counts == {"synced": 1, "stale": 0, "off": 0}
    assert rd[0]["variant_codes"] == ["1/M", "1/L"]
    assert rd[0]["our_images"] == ["https://x/a.jpg"]
    assert rd[0]["current"]["price"] == "99,00"
    assert rd[0]["current"]["state"] == 1


def test_resync_current_first_row_wins_price_and_vis():
    # first-non-empty semantics: the SECOND row's vis/price never overwrite the first
    rows = [_row("BETALOV", "X", code="A", price="10,00"),
            _row("BETALOV", "X", code="B", price="999,00", vis="hidden")]
    rd = [{"supplier": "BETALOV", "name": "X", "current": {}}]
    resync_current(rows, rd, {"BETALOV"})
    assert rd[0]["current"]["price"] == "10,00"
    assert rd[0]["current"]["vis"] == "visible"


def test_resync_current_not_found_by_name_flags_stale_keeps_old_vis():
    rd = [{"supplier": "BETALOV", "name": "Zmiznutý produkt",
           "current": {"vis": "visible", "price": "50,00"}}]
    counts = resync_current([], rd, {"BETALOV"})
    assert counts == {"synced": 0, "stale": 1, "off": 0}
    assert rd[0]["current"]["stale"] is True
    assert rd[0]["current"]["vis"] == "visible"           # old value preserved
    assert rd[0]["current"]["price"] == "50,00"            # untouched, not wiped


def test_resync_current_ignores_rows_from_uncovered_supplier():
    rows = [_row("UNKNOWN_SUPPLIER", "Y", code="Z")]
    rd = [{"supplier": "BETALOV", "name": "Y", "current": {}}]
    counts = resync_current(rows, rd, {"BETALOV"})
    assert counts["synced"] == 0 and counts["stale"] == 1


def test_resync_current_counts_off_products():
    rows = [_row("BETALOV", "Off product", code="C", vis="hidden", ais="", aos="")]
    rd = [{"supplier": "BETALOV", "name": "Off product", "current": {}}]
    counts = resync_current(rows, rd, {"BETALOV"})
    assert counts["off"] == 1
    assert rd[0]["current"]["state"] == 3
