"""Tests for batch-3b suppliers (recon 2026-07-03) — 3 NEW platforms, 3 NEW parsers.

Unlike batch 2 / batch 3 (which reused the existing generic parsers), each of these
three suppliers is the first on its platform, so each gets a new parser module:

  * ROSLER    (rosler.sk)   — Kabernet CMS (ASP.NET)  → ``kabernet_generic``
  * RAPPA.CZ  (rappa.cz)    — Magento 1.x             → ``magento_generic``
  * MÁZI HUNT (wildzone.eu) — OpenCart                → ``opencart_generic``

Each supplier's config key is the export ``supplier`` string upper-cased, because
``csv_loader.load_rows`` and ``client.SearchClient.search`` both ``.upper()`` the
supplier before matching / lookup. So the export strings map:
  "ROSLER" -> "ROSLER", "Rappa.cz" -> "RAPPA.CZ", "Mázi Hunt" -> "MÁZI HUNT"
(Python ``str.upper()`` upper-cases ``á`` in place → ``Á``, so the accented key is a
fixed point and selects its export rows).

We assert:
  * every batch-3b supplier is registered in ``config.SUPPLIERS`` AND
    ``client.PARSERS`` with the correct new parser, and its config key equals its own
    ``.upper()`` (so it matches ``load_rows``), and
  * each parser, run against a real saved search page, returns >= 1 Candidate with a
    plausible name + in-host URL, no fragment / no leftover ?search= query, deduped.

Uses real saved search pages (tests/fixtures/*.html) — no live network.
"""
import pathlib

from parovanie import config
from parovanie.client import PARSERS
from parovanie.suppliers import (
    kabernet_generic,
    magento_generic,
    opencart_generic,
)

FIX = pathlib.Path(__file__).parent / "fixtures"

# --- registration expectations (config key -> (base_url, parser module)) ----------
# Config keys are the export supplier strings upper-cased (load_rows / client upper()).
BATCH3B = {
    "ROSLER": ("https://www.rosler.sk", kabernet_generic),
    "RAPPA.CZ": ("https://www.rappa.cz", magento_generic),
    "MÁZI HUNT": ("https://wildzone.eu", opencart_generic),
}


def _load(stem: str) -> str:
    return (FIX / f"{stem}.html").read_text(encoding="utf-8", errors="replace")


def test_all_batch3b_suppliers_registered():
    """Every batch-3b supplier is wired into config + PARSERS with the right parser."""
    for key, (base, mod) in BATCH3B.items():
        assert key in config.SUPPLIERS, f"{key} missing from config.SUPPLIERS"
        cfg = config.SUPPLIERS[key]
        assert cfg.base_url == base, f"{key} base_url {cfg.base_url!r} != {base!r}"
        assert "{q}" in cfg.search_url_template, f"{key} template has no {{q}} slot"
        assert key in PARSERS, f"{key} missing from client.PARSERS"
        assert PARSERS[key] is mod.parse_search, f"{key} wired to wrong parser"


def test_config_keys_match_load_rows_uppercasing():
    """load_rows matches on supplier.strip().upper(); so each config key must equal
    its own upper() — verifying the accented key MÁZI HUNT + the dotted RAPPA.CZ
    select their export rows ("Mázi Hunt"/"Rappa.cz" -> these keys)."""
    for key in BATCH3B:
        assert key == key.upper(), f"config key {key!r} would not match load_rows"


# --- per-supplier fixture parse ----------------------------------------------------
# (config key, fixture stem, distinctive substring expected in some candidate)
FIXTURE_CASES = [
    # ROSLER: Victorinox code 0.8341 → "Victorinox Hunter XT GRIP" (name differs from
    # forestshop name — pairing is by code downstream; the parser just returns it).
    ("ROSLER", "rosler_search_0_8341", "victorinox"),
    # RAPPA: ?q=vevericka → plush squirrels ("veverka" in the URL slug).
    ("RAPPA.CZ", "rappa_search_vevericka", "veverk"),
    # MÁZI HUNT: ?search=szarvas → "M-269-1917 Prime póló szarvas" (code M-269-1917).
    ("MÁZI HUNT", "mazihunt_search_szarvas", "szarvas"),
]


def test_each_supplier_fixture_yields_valid_candidates():
    for key, stem, needle in FIXTURE_CASES:
        base, mod = BATCH3B[key]
        cands = mod.parse_search(_load(stem), base)
        # >= 1 real product card
        assert len(cands) >= 1, f"{key}/{stem}: got {len(cands)} cards"
        # every url is on the supplier host (host-boundary safety)
        assert all(c.url.startswith(base + "/") for c in cands), f"{key}: off-host url"
        # no fragment leaked into any url
        assert all("#" not in c.url for c in cands), f"{key}: fragment in url"
        # urls are unique (deduped)
        urls = [c.url for c in cands]
        assert len(urls) == len(set(urls)), f"{key}: duplicate urls"
        # every card resolved a non-empty name
        assert all(c.name for c in cands), f"{key}: a card had no name"
        # the known sample product appears (by distinctive url/name substring)
        blob = " ".join((c.url + " " + c.name).lower() for c in cands)
        assert needle in blob, f"{key}/{stem}: sample '{needle}' not found in {urls}"


def test_mazihunt_strips_search_tracking_query():
    """OpenCart product URLs carry a ?search=<query> tracking param — the parser must
    strip it so the same product resolves to one stable URL regardless of the term
    (a wrong/unstable link feeds auto-ordering)."""
    base, mod = BATCH3B["MÁZI HUNT"]
    cands = mod.parse_search(_load("mazihunt_search_szarvas"), base)
    assert cands, "no candidates"
    assert all("?" not in c.url for c in cands), f"?query leaked: {[c.url for c in cands]}"


def test_parsers_return_empty_without_results_container():
    """No recognizable results container → each parser MUST return [] (never fall back
    to scraping stray links: header / nav / cross-sell — a wrong link feeds
    auto-ordering)."""
    # kabernet: no #category-detail
    stray_kab = (
        '<div class="somewhere"><div class="product-thumb">'
        '<a href="/produkty/x/stray">x</a><h2><a href="/produkty/x/stray">Stray</a></h2>'
        '</div></div>'
    )
    assert kabernet_generic.parse_search(stray_kab, "https://www.rosler.sk") == []
    # magento: no ul.products-grid
    stray_mag = (
        '<ol class="somewhere"><li class="item">'
        '<h2 class="product-name"><a href="/sk/stray.html">Stray</a></h2></li></ol>'
    )
    assert magento_generic.parse_search(stray_mag, "https://www.rappa.cz") == []
    # opencart: no #content
    stray_oc = (
        '<div class="somewhere"><div class="product-layout">'
        '<div class="name"><a href="/stray.html">Stray</a></div></div></div>'
    )
    assert opencart_generic.parse_search(stray_oc, "https://wildzone.eu") == []


def test_parsers_reject_lookalike_host():
    """A look-alike host (base_url + ".evil.com") must NOT pass the base-url check."""
    kab = (
        '<div id="category-detail"><div class="product-thumb">'
        '<a href="https://www.rosler.sk.evil.com/produkty/x">x</a>'
        '<h2><a href="#">Evil</a></h2></div></div>'
    )
    assert kabernet_generic.parse_search(kab, "https://www.rosler.sk") == []
    mag = (
        '<ul class="products-grid"><li class="item">'
        '<h2 class="product-name"><a href="https://www.rappa.cz.evil.com/x.html">Evil</a>'
        '</h2></li></ul>'
    )
    assert magento_generic.parse_search(mag, "https://www.rappa.cz") == []
    oc = (
        '<div id="content"><div class="product-layout">'
        '<div class="name"><a href="https://wildzone.eu.evil.com/x.html">Evil</a></div>'
        '</div></div>'
    )
    assert opencart_generic.parse_search(oc, "https://wildzone.eu") == []
