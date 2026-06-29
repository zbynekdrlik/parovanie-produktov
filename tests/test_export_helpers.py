from parovanie.export_helpers import (
    IMGCOLS,
    current_of,
    fill_missing_prices,
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
