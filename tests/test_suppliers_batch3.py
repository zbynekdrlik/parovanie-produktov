"""Tests for batch-3 suppliers (recon 2026-07-03) — all reuse an EXISTING generic
platform parser (Shoptet / PrestaShop / WooCommerce), no new parser code.

Each supplier's config key is the export ``supplier`` string upper-cased, because
``csv_loader.load_rows`` and ``client.SearchClient.search`` both ``.upper()`` the
supplier before matching / lookup (so ``JŠ SERVIS`` / ``CHOCOLENKA`` / ``TATRAGOAT``
select their export rows). We assert:

  * every batch-3 supplier is registered in ``config.SUPPLIERS`` AND ``client.PARSERS``
    with the correct generic parser, and
  * for every supplier we captured a real saved search page for, the matching generic
    ``parse_search`` returns >= 1 Candidate with a plausible name + in-host URL.

DYNAX (dynax.sk, PrestaShop) is registered but its live site was in scheduled
maintenance (HTTP 503 "Prebieha údržba") when fixtures were captured, so it has no
fixture yet — the gather step will surface it once the shop is back up. Its config +
registration are still asserted here.

Uses real saved search pages (tests/fixtures/*.html) — no live network.
"""
import pathlib

from parovanie import config
from parovanie.client import PARSERS
from parovanie.suppliers import (
    shoptet_generic,
    prestashop_generic,
    woocommerce_generic,
)

FIX = pathlib.Path(__file__).parent / "fixtures"

# --- registration expectations (config key -> (base_url, parser module)) ----------
# Config keys are the export supplier strings upper-cased (load_rows / client upper()).
BATCH3 = {
    "JŠ SERVIS": ("https://www.chiruca.sk", shoptet_generic),
    "HUNTING24": ("https://www.hunting24.cz", shoptet_generic),
    "CITRADE": ("https://www.citrade.cz", shoptet_generic),
    "SOXLAND": ("https://www.soxland.sk", shoptet_generic),
    "WERRA": ("https://www.werra.cz", shoptet_generic),
    "RUTEX": ("https://www.termovel.sk", shoptet_generic),
    "CHOCOLENKA": ("https://www.chocolenka.cz", shoptet_generic),
    "DYNAX": ("https://www.dynax.sk", prestashop_generic),
    "TATRAGOAT": ("https://tatragoat.sk", woocommerce_generic),
}


def _load(stem: str) -> str:
    return (FIX / f"{stem}.html").read_text(encoding="utf-8", errors="replace")


def test_all_batch3_suppliers_registered():
    """Every batch-3 supplier is wired into config + PARSERS with the right parser."""
    for key, (base, mod) in BATCH3.items():
        assert key in config.SUPPLIERS, f"{key} missing from config.SUPPLIERS"
        cfg = config.SUPPLIERS[key]
        assert cfg.base_url == base, f"{key} base_url {cfg.base_url!r} != {base!r}"
        assert "{q}" in cfg.search_url_template, f"{key} template has no {{q}} slot"
        assert key in PARSERS, f"{key} missing from client.PARSERS"
        assert PARSERS[key] is mod.parse_search, f"{key} wired to wrong parser"


def test_config_keys_match_load_rows_uppercasing():
    """load_rows matches on supplier.strip().upper(); so each config key must equal
    its own upper() — verifying accented keys (JŠ SERVIS, CHOCOLENKA) select rows."""
    for key in BATCH3:
        assert key == key.upper(), f"config key {key!r} would not match load_rows"


# --- per-supplier fixture parse (only suppliers we captured a real page for) -------
# (config key, fixture stem, distinctive substring expected in some candidate)
FIXTURE_CASES = [
    ("JŠ SERVIS", "chiruca_search_torcaz", "torcaz"),
    ("HUNTING24", "hunting24_search_deerhunter", "deerhunter"),
    ("CITRADE", "citrade_search_mikina", "mikina"),
    ("SOXLAND", "soxland_search_ponozky", "ponoz"),
    ("WERRA", "werra_search_klobouk", "klobouk"),
    ("RUTEX", "termovel_search_cratasy", "kratasy"),
    ("CHOCOLENKA", "chocolenka_search_cokolada", "cokolad"),
    ("TATRAGOAT", "tatragoat_search_tricko", "tricko"),
]


def test_each_supplier_fixture_yields_valid_candidates():
    for key, stem, needle in FIXTURE_CASES:
        base, mod = BATCH3[key]
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
