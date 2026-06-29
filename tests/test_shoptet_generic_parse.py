"""Tests for the generic Shoptet search-result parser (shoptet_generic.parse_search).

One parser serves several plain-Shoptet eshops (Zubíček, Virginiashop,
Thermvisia/Tenolix) — they share identical markup and differ only in base URL.
Uses real saved search pages (tests/fixtures/*.html) — no live network.
"""
import pathlib

from parovanie.suppliers.shoptet_generic import parse_search

FIX = pathlib.Path(__file__).parent / "fixtures"

# (fixture stem, base_url, min expected cards, distinctive substring expected in
#  at least one candidate's url or name)
CASES = [
    ("zubicek_search_rz19", "https://www.zubicek.cz", 1, "remen"),
    ("virginiashop_search_walkstool", "https://www.virginiashop.sk", 1, "walkstool"),
    ("tenolix_search_falcon", "https://www.tenolix.cz", 1, "falcon"),
]


def _load(stem: str) -> str:
    return (FIX / f"{stem}.html").read_text(encoding="utf-8", errors="replace")


def test_each_supplier_yields_valid_candidates():
    for stem, base, min_cards, needle in CASES:
        cands = parse_search(_load(stem), base)
        # >= expected number of cards
        assert len(cands) >= min_cards, f"{stem}: got {len(cands)} cards"
        # every url is on the supplier host (host-boundary safety)
        assert all(c.url.startswith(base + "/") for c in cands), stem
        # no fragment leaked into any url
        assert all("#" not in c.url for c in cands), stem
        # urls are unique (deduped)
        urls = [c.url for c in cands]
        assert len(urls) == len(set(urls)), f"{stem}: duplicate urls"
        # the known sample product appears (by distinctive url/name substring)
        blob = " ".join((c.url + " " + c.name).lower() for c in cands)
        assert needle in blob, f"{stem}: sample '{needle}' not found in {urls}"


def test_no_grid_returns_empty():
    # A page without the ``.products.products-block`` grid must yield no candidates
    # (never scrape the whole page — a stray link would feed auto-reorder).
    assert parse_search("<html><body><div class='product'><a class='name' "
                        "href='/x/'>x</a></div></body></html>",
                        "https://www.zubicek.cz") == []


def test_card_missing_link_is_skipped_not_crashed():
    html = ("<div class='products products-block'>"
            "<div class='product'><span data-micro='name'>no link</span></div>"
            "<div class='product'><a class='name' href='/ok/'>"
            "<span data-micro='name'>ok</span></a></div>"
            "</div>")
    cands = parse_search(html, "https://www.zubicek.cz")
    assert len(cands) == 1
    assert cands[0].url == "https://www.zubicek.cz/ok/"
    assert cands[0].name == "ok"


def test_relative_href_resolved_and_offsite_dropped():
    html = ("<div class='products products-block'>"
            "<div class='product'><a class='name' href='/rel/'>"
            "<span data-micro='name'>rel</span></a></div>"
            "<div class='product'><a class='name' href='https://evil.com/x/'>"
            "<span data-micro='name'>evil</span></a></div>"
            "</div>")
    cands = parse_search(html, "https://www.tenolix.cz")
    assert [c.url for c in cands] == ["https://www.tenolix.cz/rel/"]


def test_name_fallback_to_anchor_text():
    # No [data-micro="name"] / [data-testid="productCardName"] → fall back to a.name text.
    html = ("<div class='products products-block'>"
            "<div class='product'><a class='name' href='/p/'>Fallback Name</a></div>"
            "</div>")
    cands = parse_search(html, "https://www.virginiashop.sk")
    assert cands[0].name == "Fallback Name"
