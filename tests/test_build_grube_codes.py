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
