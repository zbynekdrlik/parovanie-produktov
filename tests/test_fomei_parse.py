"""Tests for the FOMEI (fomei.com, custom ASP.NET / EasyWeb) search parser.

Uses real saved search pages (tests/fixtures/fomei_search_*.html) — no live network.
Each fixture was captured via the real ``?ProductsSearch=`` fulltext endpoint.
"""
import pathlib

from parovanie.suppliers.fomei import parse_search

BASE = "https://www.fomei.com"
FIX = pathlib.Path(__file__).parent / "fixtures"


def _load(name: str) -> str:
    return (FIX / f"fomei_search_{name}.html").read_text(
        encoding="utf-8", errors="replace"
    )


def _assert_clean(cands, *, min_count: int):
    assert len(cands) >= min_count
    assert all(c.url.startswith(BASE + "/") for c in cands)
    assert all("#" not in c.url for c in cands)
    urls = [c.url for c in cands]
    assert len(urls) == len(set(urls))            # unique
    assert all(c.name for c in cands)             # every card has a name


def test_parse_leader_search():
    # ?ProductsSearch=8x56 LEADER PRO ED → the FOMEI 8x56 LEADER PRO ED ďalekohľad
    cands = parse_search(_load("leader"), BASE)
    _assert_clean(cands, min_count=1)
    assert any("leader" in c.url.lower() for c in cands)
    assert any("LEADER" in c.name and "8x56" in c.name for c in cands)
    assert any("-detail-" in c.url for c in cands)


def test_parse_beater_search():
    # ?ProductsSearch=BEATER FMC 8x42 → the FOMEI 8x42 BEATER FMC ďalekohľad
    cands = parse_search(_load("beater"), BASE)
    _assert_clean(cands, min_count=1)
    assert any("beater" in c.url.lower() and "8x42" in c.url for c in cands)
    assert any("BEATER" in c.name and "8x42" in c.name for c in cands)


def test_parse_returns_empty_without_grid():
    # No div.boxPl grid → MUST return [] (never fall back to scraping stray links:
    # header / nav / category tiles — a wrong link feeds auto-ordering).
    html = (
        '<div class="someOther"><div class="plWrap" data-shop-product="1">'
        '<a href="/sk/produkty-x-detail-1">x</a>'
        '<h2 class="plWrapTitle">Stray product</h2></div></div>'
    )
    assert parse_search(html, BASE) == []


def test_parse_excludes_category_tiles():
    # Category tiles / nav use .item-link (NOT .plWrap[data-shop-product]) and must
    # be excluded even inside the boxPl grid.
    html = (
        '<div class="boxPl" data-shop-product-stats-list="1">'
        '<a class="item-link" href="/sk/produkty"><span class="item-title">Kat</span></a>'
        '<div class="boxPlItem">'
        '<div class="plWrap" data-shop-product="42">'
        '<a href="/sk/produkty-fomei-x-detail-999">link</a>'
        '<h2 class="plWrapTitle">FOMEI Real Product</h2></div>'
        '</div></div>'
    )
    cands = parse_search(html, BASE)
    assert len(cands) == 1
    assert cands[0].url == BASE + "/sk/produkty-fomei-x-detail-999"
    assert cands[0].name == "FOMEI Real Product"


def test_parse_rejects_lookalike_host():
    # a look-alike host (fomei.com.evil.com) must NOT pass the base-url check
    html = (
        '<div class="boxPl"><div class="plWrap" data-shop-product="1">'
        '<a href="https://www.fomei.com.evil.com/sk/produkty-x-detail-1">x</a>'
        '<h2 class="plWrapTitle">Evil</h2></div></div>'
    )
    assert parse_search(html, BASE) == []


def test_parse_skips_card_missing_link():
    # a card with no -detail- link is skipped, not crashed on
    html = (
        '<div class="boxPl"><div class="plWrap" data-shop-product="1">'
        '<h2 class="plWrapTitle">No link here</h2></div>'
        '<div class="plWrap" data-shop-product="2">'
        '<a href="/sk/produkty-y-detail-7">y</a>'
        '<h2 class="plWrapTitle">Has link</h2></div></div>'
    )
    cands = parse_search(html, BASE)
    assert len(cands) == 1
    assert cands[0].url == BASE + "/sk/produkty-y-detail-7"
