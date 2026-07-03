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
    # --- batch 3: 9 new suppliers (recon 2026-07-03) — all on an EXISTING generic ---
    # parser (Shoptet / PrestaShop / WooCommerce). Config keys are the export
    # ``supplier`` string upper-cased (load_rows / client both upper() before lookup),
    # so accented keys are kept verbatim (cf. ZUBÍČEK).
    # Shoptet (same parser family as LUKO; static SSR, cookie-gated → session warm-up).
    # SK locale path is /vyhladavanie/, CZ locale path is /vyhledavani/ — MUST use
    # ?string= (the ?q= param silently returns the homepage).
    "JŠ SERVIS": SupplierConfig(
        name="JŠ SERVIS",
        base_url="https://www.chiruca.sk",
        search_url_template="https://www.chiruca.sk/vyhladavanie/?string={q}",
    ),
    "HUNTING24": SupplierConfig(
        name="HUNTING24",
        base_url="https://www.hunting24.cz",
        search_url_template="https://www.hunting24.cz/vyhledavani/?string={q}",
    ),
    "CITRADE": SupplierConfig(
        name="CITRADE",
        base_url="https://www.citrade.cz",
        search_url_template="https://www.citrade.cz/vyhledavani/?string={q}",
    ),
    "SOXLAND": SupplierConfig(
        name="SOXLAND",
        base_url="https://www.soxland.sk",
        search_url_template="https://www.soxland.sk/vyhladavanie/?string={q}",
    ),
    "WERRA": SupplierConfig(
        name="WERRA",
        base_url="https://www.werra.cz",
        search_url_template="https://www.werra.cz/vyhledavani/?string={q}",
    ),
    "RUTEX": SupplierConfig(
        name="RUTEX",
        base_url="https://www.termovel.sk",
        search_url_template="https://www.termovel.sk/vyhladavanie/?string={q}",
    ),
    "CHOCOLENKA": SupplierConfig(
        name="CHOCOLENKA",
        base_url="https://www.chocolenka.cz",
        # Slovak locale on a CZ Shoptet → the search path is /sk/vyhladavanie/.
        search_url_template="https://www.chocolenka.cz/sk/vyhladavanie/?string={q}",
    ),
    # PrestaShop (same parser family as WETLAND; #/variant URL fragment → urldefrag).
    "DYNAX": SupplierConfig(
        name="DYNAX",
        base_url="https://www.dynax.sk",
        search_url_template=(
            "https://www.dynax.sk/vyhladavanie?controller=search&search_query={q}"
        ),
    ),
    # WooCommerce (WordPress ?s= product search; an EXACT single match 301s to the
    # product page → the parser also handles the single-product redirect form).
    "TATRAGOAT": SupplierConfig(
        name="TATRAGOAT",
        base_url="https://tatragoat.sk",
        search_url_template="https://tatragoat.sk/?s={q}&post_type=product",
    ),
    # --- batch 3b: 3 new suppliers, each on a NEW platform + new parser (recon
    # 2026-07-03). Config keys are the export ``supplier`` string upper-cased
    # (load_rows / client both upper() before lookup): "Rappa.cz" -> "RAPPA.CZ",
    # "Mázi Hunt" -> "MÁZI HUNT" (Python .upper() keeps accents in place), "ROSLER".
    # Kabernet CMS (ASP.NET). Static HTML, no cookies. Sells Victorinox knives;
    # forestshop name carries the Victorinox CODE, ROSLER name differs → pair by
    # code downstream (AI verify). See suppliers/kabernet_generic.py.
    "ROSLER": SupplierConfig(
        name="ROSLER",
        base_url="https://www.rosler.sk",
        search_url_template="https://www.rosler.sk/produkty?q={q}",
    ),
    # Magento 1.x. Static HTML; the search param is ?q= (NOT ?string=). See
    # suppliers/magento_generic.py.
    "RAPPA.CZ": SupplierConfig(
        name="RAPPA.CZ",
        base_url="https://www.rappa.cz",
        search_url_template="https://www.rappa.cz/sk/catalogsearch/result/?q={q}",
    ),
    # OpenCart. Static HTML 200 but cookie-gated (PHPSESSID) → the SearchClient's
    # homepage warm-up handles it. Hungarian shop (HUF); the code M-xxx-xxxx is the
    # join key. Product URLs carry a ?search= tracking param → stripped by the
    # parser. See suppliers/opencart_generic.py.
    "MÁZI HUNT": SupplierConfig(
        name="MÁZI HUNT",
        base_url="https://wildzone.eu",
        search_url_template="https://wildzone.eu/index.php?route=product/search&search={q}",
    ),
    # --- batch 3c: 4 new suppliers (recon 2026-07-03). Three are the first on their
    # platform → new parser modules; HUNTINGLAND extends the PrestaShop generic with a
    # 1.6 fallback branch. Config keys are the export ``supplier`` string upper-cased
    # (load_rows / client both upper() before lookup).
    # flox.sk platform (vevo.flox.sk CDN, SSID cookie → homepage warm-up). Static SSR;
    # scope to div.productListSearch (the raw page has a cross-sell /p- carousel).
    # See suppliers/flox_generic.py.
    "ROY": SupplierConfig(
        name="ROY",
        base_url="https://www.roy.sk",
        search_url_template="https://www.roy.sk/e/search?word={q}",
    ),
    # PrestaShop **1.6** (different markup than the 1.7 suppliers → prestashop_generic
    # has a 1.6 fallback branch). Static HTML, no warm-up needed; ?search_query= is the
    # real search param (?s= with a full model string returns 0 results).
    "HUNTINGLAND": SupplierConfig(
        name="HUNTINGLAND",
        base_url="https://www.huntingland.sk",
        search_url_template="https://www.huntingland.sk/vyhladavanie?search_query={q}",
    ),
    # WordPress + **Jigoshop** plugin (NOT WooCommerce → new parser). Cookie-gated
    # (PHPSESSID) → homepage warm-up. GOTCHA: the BARE ?s= is the real search — adding
    # &post_type=product is a decoy that returns the whole catalogue. Czech shop (query
    # in Czech). See suppliers/jigoshop_generic.py.
    "KOZAP": SupplierConfig(
        name="KOZAP",
        base_url="https://www.kozap.cz",
        search_url_template="https://www.kozap.cz/?s={q}",
    ),
    # Custom SPA + JSON REST API (NOT HTML → new parser). The search endpoint returns
    # JSON {"products":[{id,name,seoName,code,...}]}; the parser json.loads it and
    # builds URLs https://shop.malfini.com/sk/sk/product/<seoName>. Search matches by
    # model-name/code only (generic words → 0). See suppliers/malfini.py.
    "MALFINI": SupplierConfig(
        name="MALFINI",
        base_url="https://shop.malfini.com",
        search_url_template=(
            "https://shop.malfini.com/api/v4/search/autocomplete"
            "?query={q}&_country=sk&_language=sk"
        ),
    ),
}

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120 Safari/537.36"
)
THROTTLE_SECONDS = 0.7
REQUEST_TIMEOUT = 25
MAX_RETRIES = 3
