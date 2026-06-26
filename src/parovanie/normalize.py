from __future__ import annotations
import re
from parovanie.models import Product

_WS = re.compile(r"\s+")
_LEADING_INDEX = re.compile(r"^\d{1,3}\s+(?=\D)")


def clean_name(s: str) -> str:
    s = _WS.sub(" ", (s or "").strip())
    s = _LEADING_INDEX.sub("", s)
    return s.strip()


def code_present(code: str | None, hay: str) -> bool:
    """True if `code` occurs in `hay` as a whole alphanumeric token — not as a
    substring of a longer run. So '110' matches 'model 110 x' but NOT '...1100'.
    Case-insensitive. Guards against short/numeric supplier codes false-matching
    unrelated products (which would write the wrong reorder URL)."""
    if not code:
        return False
    pat = re.compile(rf"(?<![0-9a-z]){re.escape(code.lower())}(?![0-9a-z])")
    return bool(pat.search((hay or "").lower()))


def build_query(product: Product) -> str:
    if product.external_code:
        return product.external_code
    return clean_name(product.name)
