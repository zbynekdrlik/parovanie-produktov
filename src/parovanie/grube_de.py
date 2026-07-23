"""GRUBE-only special logic: grube.de URL normalization, per-size itemId
extraction, forestshop size resolution, size matching. ALL pure & GRUBE-scoped."""
import re

_PRODUCT_ID = re.compile(r"grube\.(?:sk|de)/p/[^/]+/(\d+)/?")


def to_grube_de(url: str) -> str | None:
    """Canonical grube.DE product URL rebuilt from the productId.
    Strips slug/query/fragment (host swap alone keeps a mangled slug + stray
    ?q + single-size #itemId). Returns None when no /p/<slug>/<id>/ productId."""
    if not url:
        return None
    m = _PRODUCT_ID.search(url)
    if not m:
        return None
    return f"https://www.grube.de/p/x/{m.group(1)}/"


# schema.org Offer: "name":"[Farbe X. ]Größe <SIZE>.","price":"…","priceCurrency":"EUR","sku":"<itemId>"
_OFFER = re.compile(r'"name":"([^"]*?)","price":"[^"]*","priceCurrency":"EUR","sku":"(\d+)"')
_GROESSE = re.compile(r"Größe\s*([^.\"]+)")
# The displayed variant's itemId anchor on the page (reminder-list link + canonical
# fragment). Own itemIds are filtered by prefix==productId AND len==len(productId)+4
# exactly as the offer skus are — cross-sell products carry a DIFFERENT productId.
_ITEMID_ANCHOR = re.compile(r"itemId=(\d+)")

# Sentinel size key for a SINGLE-SIZE grube product (a knife has no Größe axis).
# "" never collides with a real Größe label (the regex requires ≥1 non-dot char).
ONE_SIZE = ""


def parse_variants(html: str, product_id: str) -> dict[str, str]:
    """{size_label: itemId} for ONE rendered grube.de detail page, parsed from the
    page's own schema.org Offer objects (name carries 'Größe <SIZE>', sku is the itemId).
    Own offers only (sku prefix==productId AND len==len(productId)+4) — cross-sell
    associatedProduct itemIds are excluded by the prefix. Returns {} (link-only) when
    a size maps to >1 itemId (multi-color / multi-axis — ambiguous) or no own offer.

    Single-size fallback (knives — issue #60 class 1): a single-size product has NO
    'Größe' Offer list; its one itemId lives only in the page's `itemId=<id>` anchor
    (reminder link + canonical fragment). When no sized Offer is found, extract the own
    itemId(s) from those anchors: EXACTLY one own itemId -> {ONE_SIZE: itemId}; zero (not
    found) or more than one (multi-color, no size axis) -> {} (link-only, fail-closed —
    never a wrong externalCode)."""
    text = html.replace('\\"', '"')          # unescape JSON-in-JSON
    want_len = len(product_id) + 4
    size2ids: dict[str, set] = {}
    for name, sku in _OFFER.findall(text):
        if not (sku.startswith(product_id) and len(sku) == want_len):
            continue
        m = _GROESSE.search(name)
        if not m:
            continue
        size2ids.setdefault(m.group(1).strip(), set()).add(sku)
    if any(len(ids) > 1 for ids in size2ids.values()):   # color/length axis -> ambiguous
        return {}
    if size2ids:                                          # multi-size product (unchanged)
        return {size: next(iter(ids)) for size, ids in size2ids.items()}
    # --- single-size fallback: no sized Offer list on the page ---
    own = {iid for iid in _ITEMID_ANCHOR.findall(text)
           if iid.startswith(product_id) and len(iid) == want_len}
    if len(own) == 1:
        return {ONE_SIZE: next(iter(own))}
    return {}                                             # 0 (not found) or >1 (multi-color)


class _MultiAxis:
    __slots__ = ()

    def __repr__(self):
        return "MULTI_AXIS"


MULTI_AXIS = _MultiAxis()

# Validated against the live export (reader E). Order does not matter; we only
# count populated and read the single one. Bunda+Nohavice together => multi-axis.
SIZE_COLUMNS = [
    "variant:Bunda veľkosť",
    "variant:Nohavice veľkosť",
    "variant:Veľkosť (všetko)",
    "variant:Veľkosť číslo",
]


def resolve_size(row: dict):
    """Forestshop variant size label from the export row's variant:* columns.
    NEVER parsed from the code suffix (unreliable: 997/S77, 62093/39/40, 61933//M,
    disambiguators 3XL2). Returns the clean label, None (one-size), or MULTI_AXIS."""
    bunda = (row.get("variant:Bunda veľkosť") or "").strip()
    nohav = (row.get("variant:Nohavice veľkosť") or "").strip()
    if bunda and nohav:
        return MULTI_AXIS
    populated = [(row.get(c) or "").strip() for c in SIZE_COLUMNS if (row.get(c) or "").strip()]
    if len(populated) == 1:
        return populated[0]
    if len(populated) == 0:
        return None
    return MULTI_AXIS  # >1 different size columns populated -> multi-axis, link-only


_LETTER_ALIASES = {"2XL": "XXL", "XXXL": "3XL", "XXXXL": "4XL", "XXXXXL": "5XL"}


def normalize_size(label: str) -> str:
    """Trim; uppercase letter sizes; apply 2XL->XXL etc. Numeric kept as-is."""
    s = (label or "").strip()
    up = s.upper()
    return _LETTER_ALIASES.get(up, up if up and up[0].isalpha() else s)


def match_variant_codes(rows: list[dict], grube_sizes: dict[str, str]) -> dict[str, str]:
    """{forestshop code: grube itemId} for EXACT-matched sizes only.
    Excludes multi-axis and any size with no exact grube label.
    Raises ValueError if two grube labels normalize to one (silent-fold guard).

    Single-size (issue #60 class 1): when the grube product is single-size
    (grube_sizes == {ONE_SIZE: itemId}), a ONE-SIZE forestshop variant
    (resolve_size -> None) matches that single itemId — but ONLY when there is
    exactly ONE one-size row, so a single grube itemId is never spread across N
    forestshop codes (N:1 stays link-only, fail-closed). Sized forestshop rows
    never match a single-size grube product (and vice-versa)."""
    norm_grube: dict[str, str] = {}
    for lbl, iid in grube_sizes.items():
        n = normalize_size(lbl)
        if n in norm_grube and norm_grube[n] != iid:
            raise ValueError(f"grube size collision: {lbl!r} folds onto existing {n!r}")
        norm_grube[n] = iid

    single_size = list(norm_grube) == [ONE_SIZE]         # grube product has no size axis
    one_size_rows = [r for r in rows if resolve_size(r) is None]

    out: dict[str, str] = {}
    for row in rows:
        size = resolve_size(row)
        if size is MULTI_AXIS:
            continue
        if size is None:
            # one-size forestshop variant: match a single-size grube product only,
            # and only when unambiguous (never 1 itemId -> many codes).
            if single_size and len(one_size_rows) == 1:
                iid = norm_grube[ONE_SIZE]
            else:
                continue
        else:
            iid = norm_grube.get(normalize_size(size))
        if iid:
            out[row["code"]] = iid
    return out
