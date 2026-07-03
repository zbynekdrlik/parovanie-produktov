"""Tests for batch-3c suppliers (recon 2026-07-03) — 3 NEW parsers + 1 PS-1.6 branch.

Four suppliers, three on a platform none of the existing parsers cover (→ a new
parser module each) plus HUNTINGLAND, which extends the PrestaShop generic with a
1.6 fallback branch:

  * ROY         (roy.sk)          — flox.sk platform        → ``flox_generic``
  * HUNTINGLAND (huntingland.sk)  — PrestaShop **1.6**      → ``prestashop_generic`` (1.6 branch)
  * KOZAP       (kozap.cz)        — WordPress + Jigoshop    → ``jigoshop_generic``
  * MALFINI     (shop.malfini.com)— custom SPA + JSON API   → ``malfini`` (JSON, not HTML)

Each supplier's config key is the export ``supplier`` string upper-cased, because
``csv_loader.load_rows`` and ``client.SearchClient.search`` both ``.upper()`` the
supplier before matching / lookup (all four keys are ASCII → fixed points).

We assert:
  * every batch-3c supplier is registered in ``config.SUPPLIERS`` AND
    ``client.PARSERS`` with the correct parser, and its config key equals its own
    ``.upper()`` (so it matches ``load_rows``);
  * each parser, run against a real saved search page/response, returns >= 1
    Candidate with a plausible name + in-host URL, no fragment / no leftover
    tracking query, deduped;
  * the HUNTINGLAND PS-1.6 branch does NOT regress the existing PS-1.7 suppliers
    (TTHUNT / LESONA / LASTING still parse from their fixtures).

Uses real saved search pages/responses (tests/fixtures/*) — no live network.
"""
import pathlib

from parovanie import config
from parovanie.client import PARSERS
from parovanie.suppliers import (
    flox_generic,
    jigoshop_generic,
    malfini,
    prestashop_generic,
)

FIX = pathlib.Path(__file__).parent / "fixtures"

# --- registration expectations (config key -> (base_url, parser module)) ----------
BATCH3C = {
    "ROY": ("https://www.roy.sk", flox_generic),
    "HUNTINGLAND": ("https://www.huntingland.sk", prestashop_generic),
    "KOZAP": ("https://www.kozap.cz", jigoshop_generic),
    "MALFINI": ("https://shop.malfini.com", malfini),
}


def _load(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8", errors="replace")


def test_all_batch3c_suppliers_registered():
    """Every batch-3c supplier is wired into config + PARSERS with the right parser."""
    for key, (base, mod) in BATCH3C.items():
        assert key in config.SUPPLIERS, f"{key} missing from config.SUPPLIERS"
        cfg = config.SUPPLIERS[key]
        assert cfg.base_url == base, f"{key} base_url {cfg.base_url!r} != {base!r}"
        assert "{q}" in cfg.search_url_template, f"{key} template has no {{q}} slot"
        assert key in PARSERS, f"{key} missing from client.PARSERS"
        assert PARSERS[key] is mod.parse_search, f"{key} wired to wrong parser"


def test_config_keys_match_load_rows_uppercasing():
    """load_rows matches on supplier.strip().upper(); each config key must equal its
    own upper() so it selects its export rows."""
    for key in BATCH3C:
        assert key == key.upper(), f"config key {key!r} would not match load_rows"


# --- per-supplier fixture parse ----------------------------------------------------
# (config key, fixture filename, distinctive substring expected in some candidate)
FIXTURE_CASES = [
    # ROY: ?word=Wachman → Wachman trail-camera cards (name/url carry "wachman").
    ("ROY", "roy_search.html", "wachman"),
    # HUNTINGLAND: ?search_query=minox 10x44 → "Minox … 10x44" binocular cards.
    ("HUNTINGLAND", "huntingland_search.html", "minox"),
    # KOZAP: ?s=ruksak → "ruksak …" backpack cards (Czech "ruksak").
    ("KOZAP", "kozap_search.html", "ruksak"),
    # MALFINI: ?query=epic → "Epic" (seoName epic-819 / epic-821) JSON products.
    ("MALFINI", "malfini_search_epic.json", "epic"),
]


def test_each_supplier_fixture_yields_valid_candidates():
    for key, fname, needle in FIXTURE_CASES:
        base, mod = BATCH3C[key]
        cands = mod.parse_search(_load(fname), base)
        # >= 1 real product card
        assert len(cands) >= 1, f"{key}/{fname}: got {len(cands)} cards"
        # every url is on the supplier host (host-boundary safety)
        assert all(c.url.startswith(base + "/") for c in cands), f"{key}: off-host url"
        # no fragment leaked into any url
        assert all("#" not in c.url for c in cands), f"{key}: fragment in url"
        # no leftover tracking query leaked into any url
        assert all("?" not in c.url for c in cands), f"{key}: ?query leaked in url"
        # urls are unique (deduped)
        urls = [c.url for c in cands]
        assert len(urls) == len(set(urls)), f"{key}: duplicate urls"
        # every card resolved a non-empty name
        assert all(c.name for c in cands), f"{key}: a card had no name"
        # the known sample product appears (by distinctive url/name substring)
        blob = " ".join((c.url + " " + c.name).lower() for c in cands)
        assert needle in blob, f"{key}/{fname}: sample '{needle}' not found in {urls}"


def test_malfini_products_carry_code():
    """MALFINI's JSON products carry the numeric model ``code`` (the real join key) —
    the parser must surface it on the Candidate."""
    base, mod = BATCH3C["MALFINI"]
    cands = mod.parse_search(_load("malfini_search_epic.json"), base)
    assert cands, "no candidates"
    assert all(c.code for c in cands), f"a MALFINI candidate lost its code: {cands}"


def test_malfini_bad_json_returns_empty():
    """A non-JSON / malformed body must yield [] (a wrong link feeds auto-ordering, so
    malformed input means no match, never a guess)."""
    assert malfini.parse_search("<html>not json</html>", "https://shop.malfini.com") == []
    assert malfini.parse_search("", "https://shop.malfini.com") == []
    assert malfini.parse_search('{"products": "notalist"}', "https://shop.malfini.com") == []
    assert malfini.parse_search('{"nokey": 1}', "https://shop.malfini.com") == []


# --- PS-1.7 NO-REGRESSION (the 1.6 branch must not disturb 1.7 parsing) ------------
# (fixture, base, min count, distinctive substring) — same fixtures as the existing
# test_prestashop_generic_parse.py, re-run through the extended parser.
PS17_CASES = [
    ("tthunt_search_swedteam.html", "https://www.tthunt.sk", 10, "swedteam"),
    ("lesona_search_alaska.html", "https://lesona.sk", 5, "alaska"),
    ("lasting_search_oli.html", "https://shop.lasting.eu", 1, "oli"),
]


def test_prestashop_17_no_regression():
    """The added PS-1.6 fallback branch must NOT change PS-1.7 behaviour: the three
    existing 1.7 suppliers still parse the same cards from their fixtures."""
    for fname, base, mincount, needle in PS17_CASES:
        cands = prestashop_generic.parse_search(_load(fname), base)
        assert len(cands) >= mincount, f"{fname}: {len(cands)} < {mincount}"
        assert all(c.url.startswith(base + "/") for c in cands), f"{fname}: off-host"
        assert all("#" not in c.url for c in cands), f"{fname}: fragment leaked"
        assert all(c.name for c in cands), f"{fname}: a card had no name"
        urls = [c.url for c in cands]
        assert len(urls) == len(set(urls)), f"{fname}: dup urls"
        assert any(needle in c.url.lower() for c in cands), f"{fname}: '{needle}' gone"


# --- empty / host-boundary safety for the 3 new parsers ----------------------------
def test_new_parsers_return_empty_without_results_container():
    """No recognizable results container → each new parser returns [] (never scrape
    stray links: header / nav / cross-sell — a wrong link feeds auto-ordering)."""
    # flox: no div.productListSearch (a page-wide cross-sell /p- link must NOT leak)
    stray_flox = '<div class="crosssell"><a href="/p-999/accessory">battery</a></div>'
    assert flox_generic.parse_search(stray_flox, "https://www.roy.sk") == []
    # jigoshop: no div#content ul.products
    stray_jig = (
        '<div class="widget"><ul class="products"><li class="product">'
        '<a href="/produkt/stray/">Stray</a></li></ul></div>'
    )
    assert jigoshop_generic.parse_search(stray_jig, "https://www.kozap.cz") == []
    # PS 1.6 branch: neither 1.7 grid nor #product_list/.product_list
    stray_ps = "<html><body><nav><a href='/x.html'>nav</a></nav></body></html>"
    assert prestashop_generic.parse_search(stray_ps, "https://www.huntingland.sk") == []


def test_new_parsers_reject_lookalike_host():
    """A look-alike host (base_url + ".evil.com") must NOT pass the base-url check."""
    flox = (
        '<div class="productListSearch"><li class="productListItemJS" '
        'data-href="https://www.roy.sk.evil.com/p-1/x"><h3 class="s1-listProductTitle">'
        '<a href="#">Evil</a></h3></li></div>'
    )
    assert flox_generic.parse_search(flox, "https://www.roy.sk") == []
    jig = (
        '<div id="content"><ul class="products"><li class="product">'
        '<a href="https://www.kozap.cz.evil.com/produkt/x/">Evil</a></li></ul></div>'
    )
    assert jigoshop_generic.parse_search(jig, "https://www.kozap.cz") == []
    ps16 = (
        '<div id="product_list"><div class="product-container">'
        '<a class="product-name" href="https://www.huntingland.sk.evil.com/x.html">Evil</a>'
        '</div></div>'
    )
    assert prestashop_generic.parse_search(ps16, "https://www.huntingland.sk") == []
    # malfini: a product URL is composed from our own base_url; a tampered base can't
    # smuggle an off-host url in — but assert the boundary defensively.
    body = '{"products":[{"name":"X","seoName":"x-1","code":"1"}]}'
    cands = malfini.parse_search(body, "https://shop.malfini.com")
    assert all(c.url.startswith("https://shop.malfini.com/") for c in cands)
