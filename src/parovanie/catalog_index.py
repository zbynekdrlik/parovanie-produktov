"""Catalog-wide product search index (pure). Built once at app start from the
Shoptet export rows; grouped per product (pairCode). Search is accent-insensitive
substring over name / supplier / variant code. No live network, no fuzzy ranking."""
import unicodedata
from typing import Iterable
from urllib.parse import urlparse


def normalize_text(s: str) -> str:
    """Lowercase + strip diacritics (NFKD) for accent-insensitive matching."""
    if not s:
        return ""
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()


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
            e = out[pc] = {
                "pairCode": pc,
                "name": name,
                "supplier": (r.get("supplier") or "").strip(),
                "variant_codes": [],
                "image": (r.get("defaultImage") or "").strip(),
                "name_norm": normalize_text(name),
                "in_review": pc in review_keys,
            }
        if code not in e["variant_codes"]:
            e["variant_codes"].append(code)
    return out


def search_catalog(catalog: dict, q: str, limit: int = 50) -> list:
    """Up to `limit` product entries whose name (accent-insensitive), supplier, or
    any variant code contains `q`. Query shorter than 2 chars (normalized) -> []."""
    qn = normalize_text(q)
    if len(qn) < 2:
        return []
    results = []
    for e in catalog.values():
        if (qn in e["name_norm"]
                or qn in normalize_text(e["supplier"])
                or any(qn in normalize_text(c) for c in e["variant_codes"])):
            results.append(e)
            if len(results) >= limit:
                break
    return results


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
