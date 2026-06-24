"""Tests for the BETALOV (huntingshop.eu) search-result parser.

Fixtures:
  huntingshop_search_ob570.html — "code" query (TOPEK 40); 1 product result.
      Note: the original brief requested OB570, but huntingshop.eu carries no
      OB570 stock; TOPEK 40 (a HART product code) serves as the code-query
      fixture and exercises the same parser paths.
  huntingshop_search_hart.html — name query (HART RANDO); 1 product result.
"""
import os

from parovanie.suppliers.betalov import parse_search

_FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures")

OB = open(
    os.path.join(_FIXTURE_DIR, "huntingshop_search_ob570.html"),
    encoding="utf-8",
    errors="replace",
).read()

HART = open(
    os.path.join(_FIXTURE_DIR, "huntingshop_search_hart.html"),
    encoding="utf-8",
    errors="replace",
).read()

BASE = "https://www.huntingshop.eu"


def test_code_search_returns_products() -> None:
    """A code-style query fixture must return ≥1 product with valid URLs."""
    cands = parse_search(OB, BASE)
    assert len(cands) >= 1, f"Expected ≥1 product, got {len(cands)}"
    assert all(
        c.url.startswith(BASE + "/") for c in cands
    ), f"Non-absolute URL found: {[c.url for c in cands]}"


def test_name_search_returns_hart_product() -> None:
    """A HART/RANDO name query must return at least one HART or RANDO product."""
    cands = parse_search(HART, BASE)
    assert any(
        "hart" in c.url.lower()
        or "rando" in c.url.lower()
        or "hart" in c.name.lower()
        or "rando" in c.name.lower()
        for c in cands
    ), f"No HART/RANDO product found in: {[(c.name, c.url) for c in cands]}"


def test_excludes_navigation_links() -> None:
    """Parser must not return košík, prihlasenie, or other nav/utility URLs."""
    cands = parse_search(OB, BASE)
    urls = {c.url for c in cands}
    assert f"{BASE}/kosik" not in urls, "/kosik leaked into results"
    assert f"{BASE}/prihlasenie" not in urls, "/prihlasenie leaked into results"


def test_no_duplicate_urls() -> None:
    """Each product URL must appear at most once (grid + list dedup)."""
    cands = parse_search(HART, BASE)
    urls = [c.url for c in cands]
    assert len(urls) == len(set(urls)), f"Duplicate URLs: {urls}"
