"""Catalog-wide product search index (pure). Built once at app start from the
Shoptet export rows; grouped per PRODUCT (keyed by pairCode-or-code so single-variant
products with an EMPTY pairCode are their OWN entries, not dropped). Search is
accent-insensitive, multi-word, order-independent, SUBSTRING-matching over a per-product
blob of MANY fields (name/supplier/codes/externalCode/description/category/manufacturer/
ean/productNumber), and relevance-RANKED (whole-word name > name-prefix > name/code
substring > elsewhere in the blob). No live network, no IO — pure stdlib plus the
canonical 3-state classifier from parovanie.export_helpers (never duplicated)."""
import re
import unicodedata
from typing import Iterable
from urllib.parse import urlparse

from parovanie.export_helpers import current_of

# split a normalized string into alnum word-tokens (drop punctuation / whitespace)
_WORD_SPLIT = re.compile(r"[^a-z0-9]+")
# strip HTML tags (description/shortDescription are HTML) before blob normalization so a
# query can't match tag/attribute names (e.g. 'div', 'span', 'href') — search product
# CONTENT, not markup.
_TAG = re.compile(r"<[^>]+>")

# Export columns aggregated (first-non-empty across the product's variant rows) into the
# per-product searchable blob. Manager complaint: 'má vyhľadať VŠETKO nie len názov'.
# categoryText2..8 are appended when present in the export.
_BLOB_SCALAR_COLS = (
    "name", "supplier", "externalCode", "shortDescription", "description",
    "manufacturer", "ean", "productNumber",
    "categoryText", "categoryText2", "categoryText3", "categoryText4",
    "categoryText5", "categoryText6", "categoryText7", "categoryText8",
)


def _stock_int(v) -> int:
    """Variant `stock` cell → int (decimal-comma tolerated); non-numeric/empty → 0."""
    s = (str(v) if v is not None else "").strip().replace(",", ".")
    try:
        return int(float(s)) if s else 0
    except ValueError:
        return 0


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
    """Group export variant rows into {key: entry}, key = pairCode OR (when the pairCode
    is empty) the variant `code`. This keeps SINGLE-VARIANT products (čiapky/nože/
    svietidlá — ~2700 of them have an EMPTY pairCode in the Shoptet export) in the index
    instead of dropping them; each becomes its own entry keyed by its code. A row with NO
    code carries nothing (code is the variant id AND the fallback key) → skipped.

    Each entry keeps `key` (the identity above), `pairCode` (real value, may be ""),
    name/name_norm/name_words, supplier, variant_codes, image, and the commerce fields.

    Search fields (manager: 'má vyhľadať VŠETKO nie len názov'):
      search_blob_norm = one normalized (lowercased, accent-stripped, HTML-stripped)
                         string aggregating name + supplier + ALL variant codes +
                         externalCode + shortDescription + description + manufacturer +
                         ean + productNumber + categoryText(2..8). Scalars are the
                         first-non-empty across the product's variant rows.
      codes_norm / ext_norm = precomputed normalized variant codes / externalCode, used
                         by search_catalog for ranking (never recomputed per query).

    `review_keys` marks in_review: an entry is in_review iff its key OR any of its
    variant codes is in the set (the app passes bare pairCodes PLUS every variant code,
    so a single-variant product that IS reviewed still matches by its code).

    Commerce fields (the search rows were "almost no data" without them):
      price = first non-empty `price` across the entry's rows (string as-is),
      stock = int sum of parseable variant `stock` values (non-numeric → 0),
      state = BEST (lowest) 3-state classification across variant rows — 1 if ANY
              variant is sellable, else 2 if any is Vypredané, else 3. Classified by
              the canonical export_helpers.current_of (availabilityInStock or
              availabilityOutOfStock) — never re-derived.
    Rows without those columns (minimal fixtures) default to "" / 0 / 1."""
    review_keys = review_keys or set()
    out: dict = {}
    for r in rows:
        code = (r.get("code") or "").strip()
        if not code:
            continue                      # no code → nothing to index / no fallback key
        pc = (r.get("pairCode") or "").strip()
        key = pc or code                  # single-variant products have an EMPTY pairCode
        e = out.get(key)
        if e is None:
            name = (r.get("name") or "").strip()
            name_norm = normalize_text(name)
            e = out[key] = {
                "key": key,
                "pairCode": pc,
                "name": name,
                "supplier": (r.get("supplier") or "").strip(),
                "variant_codes": [],
                "image": (r.get("defaultImage") or "").strip(),
                "name_norm": name_norm,
                "name_words": _words(name_norm),
                "in_review": False,       # finalized below (key OR any variant code)
                "price": "",
                "stock": 0,
                "state": 3,   # min()-folded below; a column-less row classifies as 1
                "_blob": {},              # temp: col -> first-non-empty raw value
                "_ext": "",               # temp: raw externalCode
            }
        if code not in e["variant_codes"]:
            e["variant_codes"].append(code)
        # blob scalars — first non-empty across the product's variant rows
        for col in _BLOB_SCALAR_COLS:
            if not e["_blob"].get(col):
                v = (r.get(col) or "").strip()
                if v:
                    e["_blob"][col] = v
        if not e["_ext"]:
            e["_ext"] = (r.get("externalCode") or "").strip()
        # commerce aggregation — EVERY variant row contributes (incl. the first)
        if not e["price"]:
            e["price"] = (r.get("price") or "").strip()
        e["stock"] += _stock_int(r.get("stock"))
        st = current_of((r.get("productVisibility") or "").strip(),
                        (r.get("availabilityInStock") or "").strip(),
                        (r.get("availabilityOutOfStock") or "").strip())["state"]
        if st < e["state"]:
            e["state"] = st
    # finalize: build the normalized search blob + code/ext norms, mark in_review, and
    # drop the temp accumulators. One normalize pass per product (not per query).
    for e in out.values():
        raw = " ".join(list(e["_blob"].values()) + list(e["variant_codes"]))
        e["search_blob_norm"] = normalize_text(_TAG.sub(" ", raw))
        e["codes_norm"] = [normalize_text(c) for c in e["variant_codes"]]
        e["ext_norm"] = normalize_text(e["_ext"])
        e["in_review"] = (e["key"] in review_keys
                          or any(c in review_keys for c in e["variant_codes"]))
        del e["_blob"]
        del e["_ext"]
    return out


def search_catalog(catalog: dict, q: str, limit: int = 100) -> list:
    """Up to `limit` product entries matching `q`, ranked by relevance.

    Multi-word and ORDER-INDEPENDENT: a product is a CANDIDATE only when EVERY query
    term is a plain SUBSTRING of its search_blob_norm (logical AND, any order). Substring
    (not word-boundary) is what makes search FIND things — 'hunter' matches 'Deerhunter',
    'ah5' matches an externalCode, a description word matches. Relevance then ranks the
    good hits first so substring noise sinks. Per term, the BEST tier:
      5  the term is a WHOLE word of the name,
      4  a name word STARTS WITH the term (prefix),
      3  the term is a substring of name_norm, OR of a variant code / the externalCode,
      1  the term is only elsewhere in the blob (description / category / manufacturer …)
         — always ≥1 because the AND gate guarantees it is in the blob.
    Score = sum of per-term bests, plus whole-query bonuses: +100 exact code/externalCode,
    +20 code/ext substring, +50 exact name, +10 whole query contiguous in the name.
    Sorted score DESC, then shorter (more precise) name first. Accent-insensitive
    throughout. Query < 2 normalized chars -> []."""
    qn = normalize_text(q)
    if len(qn) < 2:
        return []
    terms = _words(qn)
    if not terms:
        return []

    scored = []
    for e in catalog.values():
        blob = e["search_blob_norm"]
        # AND gate: every term must appear somewhere in the product's searchable text.
        if not all(t in blob for t in terms):
            continue
        name_words = e["name_words"]
        name_norm = e["name_norm"]
        codes_norm = e["codes_norm"]
        ext_norm = e["ext_norm"]

        total = 0
        for t in terms:
            best = 1                      # guaranteed in the blob (the AND gate above)
            if t in name_words:
                best = 5
            elif any(w.startswith(t) for w in name_words):
                best = 4
            elif t in name_norm:
                best = 3
            if best < 3 and (any(t in c for c in codes_norm)
                             or (ext_norm and t in ext_norm)):
                best = 3
            total += best

        # relevance bonuses (whole-query, not per-term)
        if any(qn == c for c in codes_norm) or (ext_norm and qn == ext_norm):
            total += 100
        elif any(qn in c for c in codes_norm) or (ext_norm and qn in ext_norm):
            total += 20
        if qn == name_norm:
            total += 50
        if qn in name_norm:
            total += 10

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
    # key = the catalog entry's identity (pairCode-or-code). For a single-variant product
    # (empty pairCode) this is the variant code, so the promoted review entry has a real,
    # unique key that link_rows can read and its variant_codes drive the eshop write-back.
    key = catalog_entry.get("key") or catalog_entry["pairCode"]
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
        "key": key,
        "current": current,
    }
