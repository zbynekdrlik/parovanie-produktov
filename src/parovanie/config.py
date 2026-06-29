from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class SupplierConfig:
    name: str
    base_url: str
    search_url_template: str  # contains "{q}" (URL-encoded query inserted there)


SUPPLIERS: dict[str, SupplierConfig] = {
    "BETALOV": SupplierConfig(
        name="BETALOV",
        base_url="https://www.huntingshop.eu",
        search_url_template="https://www.huntingshop.eu/hladanie?search={q}",
    ),
    "WETLAND": SupplierConfig(
        name="WETLAND",
        base_url="https://www.wetland.sk",
        search_url_template="https://www.wetland.sk/vyhladavanie?controller=search&s={q}",
    ),
    "ODIMON": SupplierConfig(
        name="ODIMON",
        base_url="https://www.odimon.sk",
        search_url_template="https://www.odimon.sk/vysledky-vyhladavania?term={q}",
    ),
    "TRIGONA": SupplierConfig(
        name="TRIGONA",
        base_url="https://www.trigona.sk",
        # Unisite path-based search (the index.php?page= form silently redirects to a
        # generic listing; the real, filtering URL is the SEO path the autosuggest links to).
        search_url_template=(
            "https://www.trigona.sk/eshop/searchstring/{q}"
            "/searchtype/all/searchsubmit/1/action/search/cid/0.xhtml"
        ),
    ),
    "GRUBE": SupplierConfig(
        name="GRUBE",
        base_url="https://www.grube.sk",
        # Shopware: results render only in a real browser (bot-gated) → gathered via a
        # headless-Playwright fetcher (scripts/gather_grube.py), not the requests client.
        search_url_template="https://www.grube.sk/search/?q={q}",
    ),
    "LUKO": SupplierConfig(
        name="LUKO",
        base_url="https://www.luko.cz",
        # Shoptet (manufacturer's own eshop). Forestshop carries LUKO's 6-digit code
        # in the product NAME → query by that exact code; deterministic match, no AI
        # (scripts/gather_luko.py). Static SSR results, cookie-gated (session warm-up).
        search_url_template="https://www.luko.cz/vyhledavani/?string={q}",
    ),
    # --- batch 2: 9 new suppliers (recon 2026-06-29) — by platform ---
    # Shoptet (same parser family as LUKO; static SSR, cookie-gated → session warm-up)
    "ZUBÍČEK": SupplierConfig(
        name="ZUBÍČEK",
        base_url="https://www.zubicek.cz",
        # Manufacturer's own CZECH Shoptet eshop; forestshop names are Slovak, so name
        # matching is weak → rely on the code-in-name query + strict AI verify.
        search_url_template="https://www.zubicek.cz/vyhledavani/?string={q}",
    ),
    "VIRGINIASHOP": SupplierConfig(
        name="VIRGINIASHOP",
        base_url="https://www.virginiashop.sk",
        search_url_template="https://www.virginiashop.sk/vyhladavanie/?string={q}",
    ),
    "THERMVISIA": SupplierConfig(
        name="THERMVISIA",
        base_url="https://www.tenolix.cz",
        # ThermVisia's B2C shop is tenolix.cz. MUST use ?string= (the ?q= param silently
        # returns the homepage). Strict matching: 0-hit search returns look-alike
        # "did you mean" cards, so AI verify must prefer -1 over a wrong guess.
        search_url_template="https://www.tenolix.cz/vyhledavani/?string={q}",
    ),
    # PrestaShop 1.7 (same parser family as WETLAND; #/variant URL fragment → urldefrag)
    "TTHUNT": SupplierConfig(
        name="TTHUNT",
        base_url="https://www.tthunt.sk",
        search_url_template="https://www.tthunt.sk/vyhladavanie?controller=search&s={q}",
    ),
    "LESONA": SupplierConfig(
        name="LESONA",
        base_url="https://lesona.sk",
        search_url_template="https://lesona.sk/vyhladavanie?controller=search&s={q}",
    ),
    "LASTING": SupplierConfig(
        name="LASTING",
        base_url="https://shop.lasting.eu",
        # Use the standard PS search (controller=search), NOT the Leo top-bar search
        # (controller=productsearch is AJAX/JS-only → empty in static HTML).
        search_url_template="https://shop.lasting.eu/cs/vyhledavani?controller=search&s={q}",
    ),
    # WooCommerce (WordPress ?s= product search; an EXACT single match 301s to the
    # product page → the parser also handles the single-product redirect form)
    "LOVTEK": SupplierConfig(
        name="LOVTEK",
        base_url="https://www.lovtek.sk",
        search_url_template="https://www.lovtek.sk/?s={q}&post_type=product",
    ),
    "PYRA": SupplierConfig(
        name="PYRA",
        base_url="https://pyra.eu",
        search_url_template="https://pyra.eu/?s={q}&post_type=product",
    ),
    # Custom ASP.NET/EasyWeb. The real search param is ?ProductsSearch= (?search= is a
    # decoy that returns the whole catalog unfiltered).
    "FOMEI SLOVAKIA": SupplierConfig(
        name="FOMEI SLOVAKIA",
        base_url="https://www.fomei.com",
        search_url_template="https://www.fomei.com/sk/produkty?ProductsSearch={q}",
    ),
}

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120 Safari/537.36"
)
THROTTLE_SECONDS = 0.7
REQUEST_TIMEOUT = 25
MAX_RETRIES = 3
