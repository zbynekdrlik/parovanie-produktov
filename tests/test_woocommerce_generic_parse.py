"""Tests for the generic WooCommerce search-result parser (dual mode).

One ``parse_search`` is shared by 2 suppliers (LOVTEK Flatsome theme, PYRA's
theme) and must handle BOTH page shapes WooCommerce serves:
  - a RESULTS LIST (``div.products`` with ``li.product`` cards), and
  - a SINGLE-PRODUCT REDIRECT (an exact match 301s to the product detail page).

Uses real saved search pages (tests/fixtures/*.html) — no live network. For each
supplier we assert card count, host-boundary, fragment-stripping, URL uniqueness,
and that a known sample product appears.
"""
import pathlib

from parovanie.suppliers.woocommerce_generic import parse_search

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


# --- Mode A: results list -------------------------------------------------------

def test_lovtek_zamok_results_list():
    base = "https://www.lovtek.sk"
    cands = parse_search(_load("lovtek_search_zamok"), base)
    _assert_contract(cands, base, 3)
    # Known sample: the "zámok na fotopasce" query returns /produkt/ detail URLs.
    assert any("/produkt/" in c.url for c in cands)
    assert any("zamok" in c.url.lower() for c in cands)


def test_pyra_endurance_results_list():
    base = "https://pyra.eu"
    cands = parse_search(_load("pyra_search_endurance"), base)
    _assert_contract(cands, base, 2)
    # Known sample: the "HAWKE ENDURANCE ED 10x50" query → endurance product URLs.
    assert any("endurance" in c.url.lower() for c in cands)
    assert any("hawke" in c.name.lower() for c in cands)


# --- Mode B: single-product redirect --------------------------------------------

def test_pyra_natertrek_single_redirect():
    base = "https://pyra.eu"
    cands = parse_search(_load("pyra_search_natertrek_single"), base)
    # An exact single match redirected to the product detail page → EXACTLY one.
    assert len(cands) == 1
    c = cands[0]
    assert c.url.startswith(base + "/")
    assert "#" not in c.url
    assert "nature-trek" in c.url.lower() or "natur" in c.url.lower()
    assert "hawke" in c.name.lower()


# --- Robustness gates -----------------------------------------------------------

def test_missing_grid_returns_empty():
    # No div.products grid and not a single-product page → return [] rather than
    # scrape the whole page (a wrong link feeds auto-ordering).
    html = "<html><body><nav><a href='/x/'>nav link</a></nav></body></html>"
    assert parse_search(html, "https://www.lovtek.sk") == []


def test_card_missing_link_does_not_crash():
    # A li.product with no detail anchor at all is skipped, not crashed on.
    html = (
        '<div class="products">'
        '<li class="product"><div class="box-image"></div></li>'
        "</div>"
    )
    assert parse_search(html, "https://pyra.eu") == []


def test_single_product_ignores_related_products_div():
    # A single-product detail page that ALSO renders a stray related-products
    # div.products must yield ONLY its own canonical product, not the carousel.
    html = (
        '<html><head>'
        '<meta property="og:type" content="product">'
        '<link rel="canonical" href="https://pyra.eu/optika/dalekohlady/mine/">'
        '</head><body class="single-product">'
        '<h1 class="product_title entry-title">HAWKE Mine 8x42</h1>'
        '<div class="products"><li class="product">'
        '<a class="woocommerce-LoopProduct-link" href="/related/other/">'
        '<h2 class="product-title">Related Other</h2></a></li></div>'
        "</body></html>"
    )
    cands = parse_search(html, "https://pyra.eu")
    assert len(cands) == 1
    assert cands[0].url == "https://pyra.eu/optika/dalekohlady/mine/"
    assert "Mine" in cands[0].name


def test_rejects_lookalike_host():
    # A look-alike host (pyra.eu.evil.com) must NOT pass the base-url check.
    html = (
        '<div class="products"><li class="product">'
        '<a class="woocommerce-LoopProduct-link" href="https://pyra.eu.evil.com/x/">'
        '<h2 class="product-title">Evil</h2></a></li></div>'
    )
    assert parse_search(html, "https://pyra.eu") == []
