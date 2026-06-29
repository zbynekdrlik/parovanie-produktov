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
