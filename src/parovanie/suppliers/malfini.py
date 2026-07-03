"""MALFINI (shop.malfini.com) search-result parser — JSON REST API, not HTML.

MALFINI (a Czech promo-textile manufacturer) runs a custom SPA backed by a clean
JSON REST API. Its fulltext search is an autocomplete endpoint that returns JSON
(NOT HTML):
    https://shop.malfini.com/api/v4/search/autocomplete?query=<q>&_country=sk&_language=sk
    -> {"products": [{"id", "name", "seoName", "code", "colorCode"}, ...], "articles": []}

Unlike the HTML parsers, ``parse_search`` here ``json.loads`` the raw response body
(the ``SearchClient`` passes the raw ``r.text`` to every parser, so a JSON body is
just text). Each product yields one Candidate:
  * name = the product ``name`` (e.g. ``"Epic"``)
  * url  = ``https://shop.malfini.com/sk/sk/product/<seoName>`` (seoName is
    ``<model-slug>-<code>``, e.g. ``epic-819``)
  * code = the product ``code`` (the numeric model code, the real join key)

Robustness (auto-ordering safety — a wrong link is worse than no link): a body that
is not valid JSON, or has no ``products`` list, yields ``[]`` rather than raising or
guessing. Products missing a ``seoName`` (no derivable URL) are skipped, results are
deduplicated by URL, and the ``base_url + "/"`` host-boundary is enforced.

NOTE: MALFINI search matches by model-name / numeric code only (generic words like
``tricko``/``khaki`` return 0 products) — extracting the MALFINI model/code from our
forestshop name is a gather/matching concern downstream; the parser just parses
whatever the API returns.
"""
from __future__ import annotations

import json

from parovanie.models import Candidate


def parse_search(text: str, base_url: str) -> list[Candidate]:
    """Parse a MALFINI autocomplete JSON response → product Candidates.

    ``json.loads`` the raw body; on any parse error or a missing ``products`` list,
    return ``[]`` (a wrong link feeds auto-ordering, so malformed input means no
    match, never a guess). For each product build
    ``<base_url>/sk/sk/product/<seoName>`` (skipping any product with no ``seoName``),
    name = product ``name``, code = product ``code``. Deduped by URL; the
    ``base_url + "/"`` host-boundary is enforced.
    """
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return []
    if not isinstance(data, dict):
        return []
    products = data.get("products")
    if not isinstance(products, list):
        return []

    prefix = base_url.rstrip("/") + "/sk/sk/product/"
    out: list[Candidate] = []
    seen: set[str] = set()
    for p in products:
        if not isinstance(p, dict):
            continue
        seo = (p.get("seoName") or "").strip()
        if not seo:
            continue
        url = prefix + seo
        # host-boundary check: ``base_url + "/"`` so a look-alike host cannot satisfy
        # a bare prefix match (defensive — url is composed from our own base_url).
        if not url.startswith(base_url + "/") or url in seen:
            continue
        seen.add(url)
        name = (p.get("name") or "").strip()
        code_raw = p.get("code")
        code = str(code_raw).strip() if code_raw not in (None, "") else None
        out.append(Candidate(name=name, url=url, code=code or None))
    return out
