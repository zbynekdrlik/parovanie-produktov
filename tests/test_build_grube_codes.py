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


def test_build_range_sizes_join():
    # #60 class 3: a range-size shirt (pid 113567) joins by pairCode exactly like
    # letter/numeric sizes — grube uses the same "39/40" slash string as forestshop.
    decisions = {"GRUBE|1380": {"status": "manual",
                                "url": "https://www.grube.sk/p/kosela-slim-fit/113567/?q=x#itemId=1135678118"}}
    itemids = {"113567": {"39/40": "1135678118", "41/42": "1135678134", "47/48": "1135678192"}}
    rows = [_erow("62093/39/40", "1380", **{"variant:Veľkosť (všetko)": "39/40"}),
            _erow("62093/47/48", "1380", **{"variant:Veľkosť (všetko)": "47/48"})]
    out = build_grube_codes(decisions, itemids, rows)
    assert out == {
        "62093/39/40": {"itemId": "1135678118", "size": "39/40",
                        "deUrl": "https://www.grube.de/p/x/113567/", "productId": "113567"},
        "62093/47/48": {"itemId": "1135678192", "size": "47/48",
                        "deUrl": "https://www.grube.de/p/x/113567/", "productId": "113567"},
    }


def test_build_dual_axis_komplet_link_only():
    # #60 class 2: a jacket+trousers komplet has TWO size axes (Bunda + Nohavice).
    # grube.de sells jacket & trousers as SEPARATE single-axis products (verified LIVE
    # 2026-07-23) -> NO single itemId represents a (bunda × nohavice) pair, and
    # externalCode is a single field. So a komplet variant stays LINK-ONLY: it produces
    # NO grube_codes entry even when itemids carry sizes. Fail-closed, never a wrong code.
    decisions = {"GRUBE|1234": {"status": "manual", "url": "https://www.grube.sk/p/x/154773/"}}
    itemids = {"154773": {"S": "1547734523", "L": "1547734519", "46": "1547734500"}}
    rows = [_erow("62X/3XL/46", "1234",
                  **{"variant:Bunda veľkosť": "3XL", "variant:Nohavice veľkosť": "46"}),
            _erow("62X/L/48", "1234",
                  **{"variant:Bunda veľkosť": "L", "variant:Nohavice veľkosť": "48"})]
    assert build_grube_codes(decisions, itemids, rows) == {}


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
