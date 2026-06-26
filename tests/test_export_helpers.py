from parovanie.export_helpers import IMGCOLS, row_images, slug, state_of


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


def test_row_images_collects_http_deduped_in_order():
    row = {"defaultImage": "https://x/a.jpg", "image2": "https://x/b.jpg",
           "image3": "https://x/a.jpg", "image4": "not-a-url", "image": ""}
    assert row_images(row) == ["https://x/a.jpg", "https://x/b.jpg"]
    assert IMGCOLS[0] == "defaultImage"
