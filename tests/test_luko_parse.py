"""Tests for the LUKO (luko.cz, Shoptet) search-result parser + code matcher.

Uses real saved search pages (tests/fixtures/luko_search_*.html) — no live network.
The matcher logic (extract_code / choose_exact) is the auto-ordering safety gate, so
it is covered exhaustively: exact, slug-stale-but-name-matches, ambiguous, and miss.
"""
import pathlib

from parovanie.models import Candidate
from parovanie.suppliers.luko import choose_exact, extract_code, parse_search

BASE = "https://www.luko.cz"
FIX = pathlib.Path(__file__).parent / "fixtures"


def _load(code: str) -> str:
    return (FIX / f"luko_search_{code}.html").read_text(encoding="utf-8")


def test_parse_single_result_excludes_recommended():
    # /vyhledavani/?string=034230 returns ONE real product; the page also carries a
    # 9-item "recommended-products" cross-sell block that must NOT be scraped.
    cands = parse_search(_load("034230"), BASE)
    assert len(cands) == 1
    assert cands[0].url == BASE + "/myslivecke-a-outdoorove-kosile/panska-kosile-model-034230/"
    assert "034230" in cands[0].name


def test_parse_two_results_for_shared_code():
    # code 162111 exists as both a regular and a slim-fit cut → two result cards.
    cands = parse_search(_load("162111"), BASE)
    assert len(cands) == 2
    assert all("162111" in c.name for c in cands)
    assert len({c.url for c in cands}) == 2          # distinct products


def test_parse_absolute_urls_no_fragment():
    cands = parse_search(_load("034230"), BASE)
    assert all(c.url.startswith(BASE + "/") and "#" not in c.url for c in cands)


def test_extract_code_trailing_and_midname():
    assert extract_code("Košeľa LUKO ALPINA Krátky rukáv 034230") == "034230"
    assert extract_code("Košeľa LUKO 202107 s dlhým rukávom") == "202107"
    assert extract_code("Pánska košela Luko s dlhým ruk. model 242202") == "242202"


def test_extract_code_requires_exactly_one():
    assert extract_code("LUKO bez kódu") is None
    assert extract_code("LUKO 111111 a 222222 dva kódy") is None
    assert extract_code("LUKO 12345 päť číslic") is None      # 5 digits, not 6


def test_choose_exact_single_name_match():
    cands = parse_search(_load("034230"), BASE)
    assert choose_exact("034230", cands) == 0


def test_choose_exact_slug_stale_matches_on_name():
    # 024245 lives at a stale slug (…-model-022263/) but its NAME says "model 024245".
    cands = parse_search(_load("024245"), BASE)
    i = choose_exact("024245", cands)
    assert i != -1
    assert "022263" in cands[i].url           # slug differs…
    assert "024245" in cands[i].name          # …but the name carries the real code


def test_choose_exact_ambiguous_returns_minus_one():
    # two products share code 162111 → no safe pick → -1 (wrong link is worse than none)
    cands = parse_search(_load("162111"), BASE)
    assert choose_exact("162111", cands) == -1


def test_choose_exact_no_candidates_or_no_code():
    assert choose_exact("174110", []) == -1
    assert choose_exact(None, [Candidate(name="x 174110", url=BASE + "/x/")]) == -1


def test_choose_exact_rejects_different_code_only():
    # a result whose name carries a DIFFERENT code must not match
    cands = [Candidate(name="Pánská košile model 999999", url=BASE + "/a/")]
    assert choose_exact("034230", cands) == -1


def test_choose_exact_rejects_code_inside_longer_digit_run():
    # code 024245 must NOT match when it is a substring of a longer digit run —
    # a false hit would feed a WRONG product to auto-ordering.
    cands = [Candidate(name="Pánská košile model 0242450", url=BASE + "/a/")]
    assert choose_exact("024245", cands) == -1


def test_parse_returns_empty_without_results_grid():
    # No .products.products-block grid → MUST return [] (never fall back to scraping
    # stray .product elements across the whole page: header/cart/cross-sell).
    html = '<div class="product"><a class="name" href="/x/"><span data-micro="name">model 034230</span></a></div>'
    assert parse_search(html, BASE) == []


def test_parse_rejects_lookalike_host():
    # a look-alike host (luko.cz.evil.com) must NOT pass the base-url check
    html = ('<div class="products products-block"><div class="product">'
            '<a class="name" href="https://www.luko.cz.evil.com/x/">'
            '<span data-micro="name">model 034230</span></a></div></div>')
    assert parse_search(html, BASE) == []
