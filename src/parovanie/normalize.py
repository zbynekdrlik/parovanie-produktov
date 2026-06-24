from __future__ import annotations
import re
from parovanie.models import Product

_WS = re.compile(r"\s+")
_LEADING_INDEX = re.compile(r"^\d{1,3}\s+(?=\D)")


def clean_name(s: str) -> str:
    s = _WS.sub(" ", (s or "").strip())
    s = _LEADING_INDEX.sub("", s)
    return s.strip()


def build_query(product: Product) -> str:
    if product.external_code:
        return product.external_code
    return clean_name(product.name)
