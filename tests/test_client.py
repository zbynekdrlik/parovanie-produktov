from parovanie.client import SearchClient

WET = open("tests/fixtures/wetland_search_deerhunter.html", encoding="utf-8",
           errors="replace").read()


def test_uses_template_and_parser_with_injected_fetch():
    calls = []

    def fake_fetch(url):
        calls.append(url)
        return WET

    c = SearchClient(fetch=fake_fetch)
    cands = c.search("WETLAND", "DEERHUNTER 3989")
    assert len(cands) >= 3
    assert calls and "controller=search" in calls[0]
    assert "DEERHUNTER" in calls[0]  # encoded query present


def test_caches_by_supplier_and_query():
    n = {"count": 0}

    def fake_fetch(url):
        n["count"] += 1
        return WET

    c = SearchClient(fetch=fake_fetch)
    c.search("WETLAND", "DEERHUNTER")
    c.search("WETLAND", "DEERHUNTER")
    assert n["count"] == 1  # second call served from cache
