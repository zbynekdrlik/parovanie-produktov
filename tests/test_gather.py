from parovanie.models import Product, Candidate
from parovanie.matcher import query_variants, gather_candidates


class FakeClient:
    def __init__(self, mapping):
        self.mapping = mapping
        self.queries = []

    def search(self, supplier, query):
        self.queries.append((supplier, query))
        return self.mapping.get(query, [])


def test_query_variants_strips_leading_generic():
    # clean_name strips the leading numeric token "04 " → "Nohavice HART FAIRFAX FZ"
    # query_variants should also strip the leading generic word "Nohavice"
    p = Product("BETALOV", "k", None, "04 Nohavice HART FAIRFAX FZ", [])
    variants = query_variants(p)
    # The full cleaned name is expected
    assert "Nohavice HART FAIRFAX FZ" in variants
    # A variant WITHOUT the leading generic "Nohavice" must exist
    stripped = [v for v in variants if not v.lower().startswith("nohavice")]
    assert any("HART FAIRFAX FZ" in v for v in stripped), (
        f"Expected a variant without leading 'Nohavice', got: {variants}"
    )
    # Trailing groups should also be present
    assert any("FAIRFAX FZ" in v for v in variants)


def test_query_variants_code_first():
    p = Product("BETALOV", "k", "OB570", "Nohavice HART RANDO XHP", [])
    variants = query_variants(p)
    assert variants[0] == "OB570", f"Expected code first, got: {variants}"


def test_gather_merges_and_dedups():
    # Two query variants return overlapping candidate lists (same URL appears in both)
    url_a = "https://supplier/hart-rando-ob570"
    url_b = "https://supplier/hart-fairfax"
    cand_a = Candidate("HART RANDO OB570", url_a)
    cand_b = Candidate("HART FAIRFAX", url_b)

    # Both queries return cand_a; second query also returns cand_b
    mapping = {
        "OB570": [cand_a],
        "HART RANDO XHP": [cand_a, cand_b],
    }
    p = Product("BETALOV", "k", "OB570", "HART RANDO XHP", [])
    client = FakeClient(mapping)

    queries_used, candidates = gather_candidates(p, client, k=8)

    # Both query variants were tried
    assert "OB570" in queries_used
    assert "HART RANDO XHP" in queries_used

    # Deduplication: only 2 unique URLs despite 3 total candidate objects
    urls = [c.url for c in candidates]
    assert len(urls) == len(set(urls)), f"Duplicates found: {urls}"
    assert url_a in urls
    assert url_b in urls


def test_gather_respects_k_limit():
    # Build more than k candidates
    cands = [Candidate(f"Product {i}", f"https://s/p{i}") for i in range(20)]
    mapping = {"query": cands}
    p = Product("BETALOV", "k", None, "query", [])
    client = FakeClient(mapping)
    _, candidates = gather_candidates(p, client, k=5)
    assert len(candidates) <= 5


def test_gather_deduplicates_same_url_across_queries():
    url = "https://supplier/same"
    cand = Candidate("Same Product", url)
    mapping = {
        "CODE1": [cand],
        "Full Name": [cand],
    }
    p = Product("BETALOV", "k", "CODE1", "Full Name", [])
    client = FakeClient(mapping)
    _, candidates = gather_candidates(p, client, k=8)
    urls = [c.url for c in candidates]
    assert urls.count(url) == 1, f"Same URL appeared {urls.count(url)} times"
