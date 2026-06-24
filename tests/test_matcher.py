from parovanie.models import Product, Candidate
from parovanie.matcher import match_products


class FakeClient:
    def __init__(self, mapping):
        self.mapping = mapping
        self.queries = []

    def search(self, supplier, query):
        self.queries.append((supplier, query))
        return self.mapping.get(query, [])


def test_match_uses_query_and_picks_best():
    p = Product("BETALOV", "k", "OB570", "Nohavice HART RANDO XHP",
                ["60177/46", "60177/48"])
    client = FakeClient({"OB570": [
        Candidate("HART RANDO XHP", "https://h/hart-rando-ob570")]})
    matches = match_products([p], client)
    assert client.queries == [("BETALOV", "OB570")]
    assert matches[0].chosen.url == "https://h/hart-rando-ob570"
    assert matches[0].confidence == "high"
    assert matches[0].candidate_count == 1


def test_no_candidates_yields_none_match():
    p = Product("WETLAND", "k", None, "Neznámy produkt", ["c"])
    matches = match_products([p], FakeClient({}))
    assert matches[0].chosen is None
    assert matches[0].confidence == "none"


def test_falls_back_to_shorter_query_when_code_and_full_name_miss():
    p = Product("WETLAND", "k", "ZZ9", "Bunda DEERHUNTER Strike 3989", ["c"])
    client = FakeClient({"Bunda DEERHUNTER Strike": [
        Candidate("Bunda Deerhunter Strike", "https://w/bunda-deerhunter-strike")]})
    matches = match_products([p], client)
    # ladder: ZZ9 (miss), full name (miss), first-3 tokens (hit)
    assert [q for _, q in client.queries][:2] == ["ZZ9", "Bunda DEERHUNTER Strike 3989"]
    assert matches[0].query == "Bunda DEERHUNTER Strike"
    assert matches[0].chosen.url == "https://w/bunda-deerhunter-strike"
