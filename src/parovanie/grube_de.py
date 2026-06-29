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


def parse_variants(html: str, product_id: str) -> dict[str, str]:
    """{size_label: itemId} for ONE rendered grube.de detail page, parsed from the
    page's own schema.org Offer objects (name carries 'Größe <SIZE>', sku is the itemId).
    Own offers only (sku prefix==productId AND len==len(productId)+4) — cross-sell
    associatedProduct itemIds are excluded by the prefix. Returns {} (link-only) when
    a size maps to >1 itemId (multi-color / multi-axis — ambiguous) or no own offer."""
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
    return {size: next(iter(ids)) for size, ids in size2ids.items()}


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
