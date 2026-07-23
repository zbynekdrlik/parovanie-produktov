from scripts.build_grube_codes import build_grube_codes


def _erow(code, pair, **kw):
    r = {"code": code, "pairCode": pair, "supplier": "GRUBE",
         "variant:Bunda veľkosť": "", "variant:Nohavice veľkosť": "",
         "variant:Veľkosť (všetko)": "", "variant:Veľkosť číslo": ""}
    r.update(kw)
    return r


def test_build_joins_size_to_itemid():
    decisions = {"GRUBE|395": {"status": "manual", "url": "https://www.grube.sk/p/x/154773/#itemId=9"}}
    itemids = {"154773": {"S": "1547734523", "L": "1547734519"}}
    rows = [_erow("60645/S", "395", **{"variant:Veľkosť (všetko)": "S"}),
            _erow("60645/L", "395", **{"variant:Veľkosť (všetko)": "L"})]
    out = build_grube_codes(decisions, itemids, rows)
    assert out == {
        "60645/S": {"itemId": "1547734523", "size": "S", "deUrl": "https://www.grube.de/p/x/154773/", "productId": "154773"},
        "60645/L": {"itemId": "1547734519", "size": "L", "deUrl": "https://www.grube.de/p/x/154773/", "productId": "154773"},
    }


def test_build_skips_non_grube_and_unmatched():
    decisions = {"GRUBE|395": {"status": "manual", "url": "https://www.grube.sk/p/x/154773/"},
                 "WETLAND|9": {"status": "manual", "url": "https://wetland.sk/x"}}
    itemids = {"154773": {"S": "1547734523"}}
    rows = [_erow("60645/S", "395", **{"variant:Veľkosť (všetko)": "S"}),
            _erow("60645/XS", "395", **{"variant:Veľkosť (všetko)": "XS"}),   # no grube XS
            _erow("WL/1", "9", supplier="WETLAND", **{"variant:Veľkosť (všetko)": "S"})]
    out = build_grube_codes(decisions, itemids, rows)
    assert set(out) == {"60645/S"}    # XS unmatched, WETLAND excluded


def test_build_single_size_via_review_variant_codes():
    # single-variant knife: EMPTY pairCode (absent from the pairCode grouping) ->
    # joins via the review item's variant_codes. itemids carries the ONE_SIZE sentinel.
    decisions = {"GRUBE|poľovnícka ocieľka morakniv": {
        "status": "manual", "url": "https://www.grube.sk/p/ocielka-morakniv/254031/?q=x#itemId=2540316117"}}
    itemids = {"254031": {"": "2540316117"}}          # ONE_SIZE sentinel = single-size
    rows = [_erow("MO14238", "")]                     # empty pairCode, no size columns
    review = {"GRUBE|poľovnícka ocieľka morakniv": {"variant_codes": ["MO14238"]}}
    out = build_grube_codes(decisions, itemids, rows, review)
    assert out == {"MO14238": {"itemId": "2540316117", "size": "",
                               "deUrl": "https://www.grube.de/p/x/254031/", "productId": "254031"}}


def test_build_single_size_needs_review_data():
    # without review_data the single-variant knife cannot be joined (empty pairCode) ->
    # nothing written, and the 3-arg call stays back-compatible (no crash).
    decisions = {"GRUBE|poľovnícka ocieľka morakniv": {
        "status": "manual", "url": "https://www.grube.sk/p/ocielka-morakniv/254031/#itemId=2540316117"}}
    itemids = {"254031": {"": "2540316117"}}
    rows = [_erow("MO14238", "")]
    assert build_grube_codes(decisions, itemids, rows) == {}


def test_build_single_size_multivariant_link_only():
    # a single grube itemId must never be spread over N forestshop codes -> link-only
    decisions = {"GRUBE|k": {"status": "manual", "url": "https://www.grube.sk/p/x/254031/#itemId=2540316117"}}
    itemids = {"254031": {"": "2540316117"}}
    rows = [_erow("A", ""), _erow("B", "")]
    review = {"GRUBE|k": {"variant_codes": ["A", "B"]}}
    assert build_grube_codes(decisions, itemids, rows, review) == {}


def test_build_grube_codes_fails_loud_on_nongrube_code_collision():
    import pytest
    decisions = {"GRUBE|395": {"status": "manual", "url": "https://www.grube.sk/p/x/154773/"}}
    itemids = {"154773": {"S": "1547734523"}}
    rows = [_erow("60645/S", "395", **{"variant:Veľkosť (všetko)": "S"}),
            # SAME code 60645/S also exists as a NON-grube product (duplicate-code export)
            {"code": "60645/S", "pairCode": "999", "supplier": "WETLAND",
             "variant:Bunda veľkosť": "", "variant:Nohavice veľkosť": "",
             "variant:Veľkosť (všetko)": "", "variant:Veľkosť číslo": ""}]
    with pytest.raises(ValueError):
        build_grube_codes(decisions, itemids, rows)
