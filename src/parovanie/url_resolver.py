"""Resolve a forestshop product's OWN page URL from the public sitemap.

The Shoptet export carries no product URL, so we match each product against the
sitemap slugs. Two products that differ only by a number ("Moor Padded 367" vs
"393") produce the same name-token set once the digit is dropped, so the name
alone is ambiguous — and the old resolver picked one slug arbitrarily, handing
two different products the same URL (the reviewer then compared against the wrong
product). The product's IMAGE filename slug is the disambiguator.

Principles:
  - A wrong link is worse than no link → ambiguous signal returns None and the
    UI falls back to its ?string= search.
  - Two products with DIFFERENT names must never share one URL → a dedup pass
    keeps the strongest match and drops the rest to None.
  - Two catalog entries with the SAME name slug are a genuine duplicate product
    (forestshop has it twice) → both legitimately point to the one page.

Pure / offline: callers pass the sitemap slugs in; this module never does I/O.
"""
from __future__ import annotations

import re

from parovanie.export_helpers import slug

BASE = "https://www.forestshop.sk/"

_IMG_EXT = re.compile(r"\.(jpe?g|png|webp|gif)$", re.IGNORECASE)
_IMG_CODE_PREFIX = re.compile(r"^\d+(?:-\d+)?_")  # Shoptet "15233_" / "15233-1_" prefix

# Resolution strength (higher = more trustworthy), used by the dedup pass.
_EXACT = 3        # product name slug IS a sitemap slug
_IMAGE = 2        # disambiguated by the product's image filename
_SINGLE = 1       # exactly one token-superset candidate
_NONE = 0


def _tokens(slug_str: str) -> set[str]:
    """Meaningful tokens of a slug: drop pure-digit and single-char fragments."""
    return {t for t in slug_str.split("-") if t and not t.isdigit() and len(t) > 1}


def image_slug(url: str) -> str:
    """Descriptive slug from a Shoptet image URL: drop directory, query, file
    extension, and the leading numeric code prefix, then slugify the rest.
    `…/orig/15233_deerhunter-moor-padded-waistcoat-vesta.jpg?x` → that slug."""
    base = url.rsplit("/", 1)[-1].split("?")[0]
    base = _IMG_EXT.sub("", base)
    base = _IMG_CODE_PREFIX.sub("", base)
    return slug(base)


def build_index(slugs):
    """Pre-tokenize the sitemap once. Returns (slugset, slug_tokens)."""
    return set(slugs), {s: _tokens(s) for s in slugs}


def resolve(name, image_urls, slugset, slug_tokens):
    """Best forestshop URL for ONE product.

    Returns (url_or_None, strength, name_slug). The name_slug lets the dedup pass
    tell genuine duplicates (same product twice) from distinct products."""
    sn = slug(name)
    if not sn:
        return None, _NONE, ""
    if sn in slugset:
        return BASE + sn + "/", _EXACT, sn

    nt = _tokens(sn)
    if not nt:
        return None, _NONE, sn

    candidates = [s for s, st in slug_tokens.items() if nt <= st]
    if not candidates:
        return None, _NONE, sn
    if len(candidates) == 1:
        return BASE + candidates[0] + "/", _SINGLE, sn

    # Ambiguous on name alone — disambiguate with the image filename(s). Test each
    # image independently so a stray brand-logo image can't disqualify the real one.
    img_token_sets = [t for t in (_tokens(image_slug(u)) for u in image_urls[:4]) if t]
    if not img_token_sets:
        return None, _NONE, sn

    supported = [s for s in candidates
                 if any(it <= slug_tokens[s] for it in img_token_sets)]
    if not supported:
        return None, _NONE, sn
    if len(supported) == 1:
        return BASE + supported[0] + "/", _IMAGE, sn

    # Several image-supported candidates (e.g. plain / youth / lady waistcoat):
    # prefer the slug that introduces the FEWEST tokens not already explained by
    # the product's own name + images. Require a unique winner, else don't guess.
    explained = nt | set().union(*img_token_sets)
    ranked = sorted(supported, key=lambda s: len(slug_tokens[s] - explained))
    if len(slug_tokens[ranked[0]] - explained) < len(slug_tokens[ranked[1]] - explained):
        return BASE + ranked[0] + "/", _IMAGE, sn
    return None, _NONE, sn


def assign_urls(products, slugs,
                name_of=lambda p: p.get("name", ""),
                images_of=lambda p: p.get("our_images") or []):
    """Resolve every product, then enforce that two DIFFERENT products never share
    a URL. Returns {index_in_products: url_or_None}; mutates nothing."""
    slugset, slug_tokens = build_index(slugs)
    resolved = [resolve(name_of(p), images_of(p), slugset, slug_tokens) for p in products]
    out = {i: r[0] for i, r in enumerate(resolved)}

    by_url: dict[str, list[int]] = {}
    for i, (url, _, _) in enumerate(resolved):
        if url:
            by_url.setdefault(url, []).append(i)

    for url, idxs in by_url.items():
        if len(idxs) < 2:
            continue
        name_slugs = {resolved[i][2] for i in idxs}
        if len(name_slugs) == 1:
            continue  # genuine duplicate product — both keep the one page
        best = max(resolved[i][1] for i in idxs)
        winners = [i for i in idxs if resolved[i][1] == best]
        if len(winners) > 1 and len({resolved[i][2] for i in winners}) > 1:
            for i in idxs:  # tie among different products — cannot tell, drop all
                out[i] = None
            continue
        keep = set(winners)
        for i in idxs:
            if i not in keep:
                out[i] = None
    return out
