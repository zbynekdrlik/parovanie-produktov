from __future__ import annotations

import time
import logging
from collections.abc import Callable
from urllib.parse import quote, urlsplit

import requests

from parovanie import config
from parovanie.models import Candidate
from parovanie.suppliers import wetland, betalov, odimon, trigona, grube, luko
from parovanie.suppliers import (
    shoptet_generic,
    prestashop_generic,
    woocommerce_generic,
    fomei,
    kabernet_generic,
    magento_generic,
    opencart_generic,
    flox_generic,
    jigoshop_generic,
    malfini,
)

log = logging.getLogger("parovanie.client")

PARSERS: dict[str, Callable[[str, str], list[Candidate]]] = {
    "WETLAND": wetland.parse_search,
    "BETALOV": betalov.parse_search,
    "ODIMON": odimon.parse_search,
    "TRIGONA": trigona.parse_search,
    "GRUBE": grube.parse_search,
    "LUKO": luko.parse_search,
    # batch 2 — Shoptet family
    "ZUBÍČEK": shoptet_generic.parse_search,
    "VIRGINIASHOP": shoptet_generic.parse_search,
    "THERMVISIA": shoptet_generic.parse_search,
    # batch 2 — PrestaShop family
    "TTHUNT": prestashop_generic.parse_search,
    "LESONA": prestashop_generic.parse_search,
    "LASTING": prestashop_generic.parse_search,
    # batch 2 — WooCommerce family
    "LOVTEK": woocommerce_generic.parse_search,
    "PYRA": woocommerce_generic.parse_search,
    # batch 2 — custom
    "FOMEI SLOVAKIA": fomei.parse_search,
    # batch 3 — Shoptet family
    "JŠ SERVIS": shoptet_generic.parse_search,
    "HUNTING24": shoptet_generic.parse_search,
    "CITRADE": shoptet_generic.parse_search,
    "SOXLAND": shoptet_generic.parse_search,
    "WERRA": shoptet_generic.parse_search,
    "RUTEX": shoptet_generic.parse_search,
    "CHOCOLENKA": shoptet_generic.parse_search,
    # batch 3 — PrestaShop family
    "DYNAX": prestashop_generic.parse_search,
    # batch 3 — WooCommerce family
    "TATRAGOAT": woocommerce_generic.parse_search,
    # batch 3b — new platforms, new parsers
    "ROSLER": kabernet_generic.parse_search,   # Kabernet CMS (ASP.NET)
    "RAPPA.CZ": magento_generic.parse_search,  # Magento 1.x
    "MÁZI HUNT": opencart_generic.parse_search,  # OpenCart
    # batch 3c — 3 new platforms/parsers + HUNTINGLAND on the PS-1.6 generic branch
    "ROY": flox_generic.parse_search,          # flox.sk platform
    "HUNTINGLAND": prestashop_generic.parse_search,  # PrestaShop 1.6 (fallback branch)
    "KOZAP": jigoshop_generic.parse_search,    # WordPress + Jigoshop
    "MALFINI": malfini.parse_search,           # custom SPA + JSON REST API
}


class _SessionFetcher:
    """Real fetcher: persistent session + per-host homepage warm-up so cookie-gated
    (Nette AJAX) search pages render their results.

    huntingshop.eu renders search results via a Nette AJAX snippet that is EMPTY
    for anonymous requests — results only SSR when a PHPSESSID session cookie is
    present.  A GET to the homepage establishes the session before the first search.
    """

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers["User-Agent"] = config.USER_AGENT
        self._warmed: set[str] = set()

    def _warm(self, url: str) -> None:
        host = urlsplit(url).netloc
        if host in self._warmed:
            return
        try:
            self._session.get(f"https://{host}/", timeout=config.REQUEST_TIMEOUT)
            log.info("warmed up session for %s", host)
        except Exception as e:  # noqa: BLE001
            log.warning("warm-up failed for %s: %s", host, e)
        self._warmed.add(host)

    def __call__(self, url: str) -> str:
        self._warm(url)
        last: Exception | None = None
        for attempt in range(config.MAX_RETRIES):
            try:
                r = self._session.get(url, timeout=config.REQUEST_TIMEOUT)
                r.raise_for_status()
                return r.text
            except Exception as e:  # noqa: BLE001
                last = e
                log.warning("fetch failed (try %d): %s", attempt + 1, e)
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"fetch failed after retries: {url}") from last


_DEFAULT_FETCH = _SessionFetcher()


class SearchClient:
    """HTTP search client with throttle, retry (via _SessionFetcher), and cache.

    Args:
        fetch: callable(url) -> str; defaults to the session-based real fetcher.
        cache: optional dict for (supplier, query) memoisation (share across calls
               to persist between invocations).
        throttle: seconds to sleep between real requests (0 to disable).
    """

    def __init__(
        self,
        fetch: Callable[[str], str] = _DEFAULT_FETCH,
        cache: dict | None = None,
        throttle: float = config.THROTTLE_SECONDS,
    ) -> None:
        self._fetch = fetch
        self._cache: dict[tuple[str, str], list[Candidate]] = cache if cache is not None else {}
        self._throttle = throttle
        # Detect real mode by identity so injected fakes skip sleep
        self._is_real = fetch is _DEFAULT_FETCH

    def search(self, supplier: str, query: str) -> list[Candidate]:
        """Search a supplier for *query* and return parsed Candidate list.

        Results are cached by (supplier, query).  The real fetcher throttles
        between requests to avoid hammering the shop.
        """
        supplier = supplier.upper()
        key = (supplier, query)
        if key in self._cache:
            return self._cache[key]

        cfg = config.SUPPLIERS[supplier]
        url = cfg.search_url_template.format(q=quote(query))
        log.info("search supplier=%s query=%r url=%s", supplier, query, url)

        if self._is_real and self._throttle:
            time.sleep(self._throttle)

        html = self._fetch(url)
        cands = PARSERS[supplier](html, cfg.base_url)
        log.info("  -> %d candidates", len(cands))
        self._cache[key] = cands
        return cands
