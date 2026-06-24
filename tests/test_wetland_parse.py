from parovanie.suppliers.wetland import parse_search

HTML = open("tests/fixtures/wetland_search_deerhunter.html", encoding="utf-8",
            errors="replace").read()


def test_returns_product_candidates():
    cands = parse_search(HTML, "https://www.wetland.sk")
    assert len(cands) >= 3
    assert all(c.url.startswith("https://www.wetland.sk/") for c in cands)
    assert all("#" not in c.url for c in cands)
    assert any("deerhunter" in c.url.lower() for c in cands)


def test_dedups_variant_fragments():
    cands = parse_search(HTML, "https://www.wetland.sk")
    urls = [c.url for c in cands]
    assert len(urls) == len(set(urls))
