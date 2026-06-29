from parovanie.grube_de import to_grube_de


def test_to_grube_de_strips_query_and_fragment():
    u = "https://www.grube.sk/p/noz-morakniv-eldris/268279/?q=morakiv#itemId=2682798474"
    assert to_grube_de(u) == "https://www.grube.de/p/x/268279/"


def test_to_grube_de_clean_sk_url():
    assert to_grube_de("https://www.grube.sk/p/percussion-grand-nord/154773/") == "https://www.grube.de/p/x/154773/"


def test_to_grube_de_already_de_idempotent():
    assert to_grube_de("https://www.grube.de/p/x/154773/") == "https://www.grube.de/p/x/154773/"


def test_to_grube_de_entity_mangled_slug():
    u = "https://www.grube.sk/p/pracovn-yacute-n-ocircz-morakniv-pro-c/585117/?q=x#itemId=5851174978"
    assert to_grube_de(u) == "https://www.grube.de/p/x/585117/"


def test_to_grube_de_non_product_url_returns_none():
    assert to_grube_de("https://www.grube.sk/search/?q=hose") is None
    assert to_grube_de("") is None
