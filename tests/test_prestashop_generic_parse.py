"""Tests for the generic PrestaShop 1.7 search-result parser.

One ``parse_search`` is shared by 3 suppliers whose result themes differ only in
the title heading level (TTHUNT ``h2``, LESONA ``h1``, LASTING ``h3``). Uses real
saved search pages (tests/fixtures/*.html) — no live network. For each supplier
we assert the card count, host-boundary, fragment-stripping, URL uniqueness, and
that a known sample product appears.
"""
import pathlib

from parovanie.suppliers.prestashop_generic import parse_search

FIX = pathlib.Path(__file__).parent / "fixtures"


def _load(name: str) -> str:
    return (FIX / f"{name}.html").read_text(encoding="utf-8", errors="replace")


def _assert_contract(cands, base: str, min_count: int) -> None:
    """Shared per-supplier contract: enough cards, in-host, no fragments, unique."""
    assert len(cands) >= min_count
    assert all(c.url.startswith(base + "/") for c in cands)
    assert all("#" not in c.url for c in cands)
    urls = [c.url for c in cands]
    assert len(urls) == len(set(urls))
    assert all(c.name for c in cands)  # every card resolved a name


def test_tthunt_swedteam():
    base = "https://www.tthunt.sk"
    cands = parse_search(_load("tthunt_search_swedteam"), base)
    _assert_contract(cands, base, 10)
    # Known sample: the "swedteam" query brand appears in the result URLs.
    assert any("swedteam" in c.url.lower() for c in cands)


def test_lesona_alaska():
    base = "https://lesona.sk"
    cands = parse_search(_load("lesona_search_alaska"), base)
    _assert_contract(cands, base, 5)
    # LESONA card titles are truncated with "…" — don't depend on the full name,
    # only that an "alaska" product URL is present.
    assert any("alaska" in c.url.lower() for c in cands)


def test_lasting_oli():
    base = "https://shop.lasting.eu"
    cands = parse_search(_load("lasting_search_oli"), base)
    _assert_contract(cands, base, 1)
    # Known sample: "OLI funkční ponožky" lives at …/oli-funkcni-ponozky-…html
    assert any("oli" in c.url.lower() for c in cands)


def test_missing_grid_returns_empty():
    # No #js-product-list and no .products grid → return [] rather than scrape
    # the whole page (a wrong link feeds auto-ordering).
    html = "<html><body><nav><a href='/x.html'>nav link</a></nav></body></html>"
    assert parse_search(html, "https://www.tthunt.sk") == []


def test_card_missing_anchor_does_not_crash():
    # A product-miniature with no title anchor at all is skipped, not crashed on.
    html = (
        '<div id="js-product-list">'
        '<article class="product-miniature"><div class="thumbnail-container">'
        "</div></article>"
        "</div>"
    )
    assert parse_search(html, "https://lesona.sk") == []
