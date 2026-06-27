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
}

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120 Safari/537.36"
)
THROTTLE_SECONDS = 0.7
REQUEST_TIMEOUT = 25
MAX_RETRIES = 3
