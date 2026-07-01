"""Catalog-wide product search index (pure). Built once at app start from the
Shoptet export rows; grouped per product (pairCode). Search is accent-insensitive,
multi-word, order-independent, word-boundary-aware and relevance-RANKED over
name / supplier / variant code. No live network. Pure stdlib (re, unicodedata)."""
import re
import unicodedata
from typing import Iterable
from urllib.parse import urlparse

# split a normalized string into alnum word-tokens (drop punctuation / whitespace)
_WORD_SPLIT = re.compile(r"[^a-z0-9]+")


def normalize_text(s: str) -> str:
    """Lowercase + strip diacritics (NFKD) for accent-insensitive matching."""
    if not s:
        return ""
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()


def _words(norm: str) -> list:
    """Alnum word-tokens of an already-normalized string (empties dropped)."""
    return [t for t in _WORD_SPLIT.split(norm) if t]


def build_catalog_index(rows: Iterable[dict], review_keys=None) -> dict:
    """Group export variant rows into {pairCode: entry}. Each `row` needs at least
    code, pairCode, name, supplier, defaultImage. `review_keys` marks in_review.
    First row of a pairCode supplies name/supplier/image; codes accumulate."""
    review_keys = review_keys or set()
    out: dict = {}
    for r in rows:
        pc = (r.get("pairCode") or "").strip()
        code = (r.get("code") or "").strip()
        if not pc or not code:
            continue
        e = out.get(pc)
        if e is None:
            name = (r.get("name") or "").strip()
            name_norm = normalize_text(name)
            e = out[pc] = {
                "pairCode": pc,
                "name": name,
                "supplier": (r.get("supplier") or "").strip(),
                "variant_codes": [],
                "image": (r.get("defaultImage") or "").strip(),
                "name_norm": name_norm,
                "name_words": _words(name_norm),
                "in_review": pc in review_keys,
            }
        if code not in e["variant_codes"]:
            e["variant_codes"].append(code)
    return out


def search_catalog(catalog: dict, q: str, limit: int = 50) -> list:
    """Up to `limit` product entries best matching `q`, ranked by relevance.

    Multi-word and ORDER-INDEPENDENT: a product is a candidate only when EVERY
    query term matches something (logical AND, any order). Per term the best of:
      3  the term is a WHOLE word of the name (word-boundary),
      2  a name word STARTS WITH the term (prefix — excludes mid-word garbage like
         'ponozky' for 'noz', since 'ponozky' does not start with 'noz'),
      2  the term is a substring of a variant code (codes are short/numeric),
      1  the supplier starts with / equals the term.
    A term scoring 0 disqualifies the product. Score = sum of per-term bests, plus
    ranking bonuses (+100 exact code, +20 code substring, +50 exact name, +5 the
    whole query appears contiguously in the name). Sorted score DESC, then shorter
    name first. Accent-insensitive throughout. Query < 2 norm chars -> []."""
    qn = normalize_text(q)
    if len(qn) < 2:
        return []
    terms = _words(qn)
    if not terms:
        return []

    scored = []
    for e in catalog.values():
        name_words = e["name_words"]
        name_norm = e["name_norm"]
        sup_norm = normalize_text(e["supplier"])
        codes_norm = [normalize_text(c) for c in e["variant_codes"]]

        total = 0
        matched_all = True
        for t in terms:
            best = 0
            if t in name_words:
                best = 3
            elif any(w.startswith(t) for w in name_words):
                best = 2
            if best < 2 and any(t in c for c in codes_norm):
                best = 2
            if best < 1 and (sup_norm.startswith(t) or sup_norm == t):
                best = 1
            if best == 0:
                matched_all = False
                break
            total += best
        if not matched_all:
            continue

        # relevance bonuses (whole-query, not per-term)
        if any(qn == c for c in codes_norm):
            total += 100
        elif any(qn in c for c in codes_norm):
            total += 20
        if qn == name_norm:
            total += 50
        if qn in name_norm:
            total += 5

        scored.append((total, len(e["name"]), e))

    # score DESC, then shorter (more precise) name first — stable tiebreak
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [e for _, _, e in scored[:limit]]


def _host(url: str) -> str:
    """Lowercased host of `url`, with a leading `www.` PREFIX stripped (not lstrip)."""
    h = (urlparse(url or "").netloc or "").lower()
    return h[4:] if h.startswith("www.") else h


def supplier_from_url(url: str, suppliers: dict) -> str:
    """Infer supplier key from a pasted product URL's host vs SUPPLIERS base_url
    hosts. grube.de/grube.sk both -> GRUBE. Unknown host -> ''. Only used to set the
    `supplier` field so link_rows applies GRUBE .de normalization correctly."""
    host = _host(url)
    if not host:
        return ""
    if "grube.de" in host or "grube.sk" in host:
        return "GRUBE"
    for name, cfg in suppliers.items():
        base = getattr(cfg, "base_url", None)
        if base is None and isinstance(cfg, dict):
            base = cfg.get("base_url", "")
        bhost = _host(base or "")
        if bhost and (host == bhost or host.endswith("." + bhost)):
            return name
    return ""


def build_promoted_entry(catalog_entry: dict, current: dict, our_url, supplier: str, idx: int) -> dict:
    """Minimal review_data entry for a catalog product paired for the first time via
    search. Shape mirrors build_review_data so link_rows + the UI card consume it.
    `supplier` is the URL-inferred key; falls back to the catalog row's supplier."""
    img = catalog_entry.get("image")
    return {
        "idx": idx,
        "supplier": supplier or catalog_entry.get("supplier", ""),
        "name": catalog_entry["name"],
        "pairCode": catalog_entry["pairCode"],
        "variant_codes": list(catalog_entry["variant_codes"]),
        "our_images": [img] if img else [],
        "ai_status": "unmatched",
        "ai_chosen_url": "",
        "ai_reason": "Ručne pridané cez vyhľadávanie.",
        "candidates": [],
        "our_url": our_url,
        "key": catalog_entry["pairCode"],
        "current": current,
    }
