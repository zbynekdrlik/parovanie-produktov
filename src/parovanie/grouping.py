from __future__ import annotations
import re
from parovanie.models import Product

_WS = re.compile(r"\s+")


def _name_key(name: str) -> str:
    return _WS.sub(" ", (name or "").strip()).casefold()


def group_products(rows: list[dict]) -> list[Product]:
    order: list[tuple] = []
    groups: dict[tuple, dict] = {}
    for row in rows:
        sup = (row.get("supplier") or "").strip().upper()
        pair = (row.get("pairCode") or "").strip()
        name = (row.get("name") or "").strip()
        code = (row.get("code") or "").strip()
        ext = (row.get("externalCode") or "").strip()
        key = (sup, "pc:" + pair) if pair else (sup, "nm:" + _name_key(name))
        g = groups.get(key)
        if g is None:
            g = {"supplier": sup, "pair_key": f"{sup}|{pair or _name_key(name)}",
                 "external_code": ext or None, "name": name, "variant_codes": []}
            groups[key] = g
            order.append(key)
        if code:
            g["variant_codes"].append(code)
        if g["external_code"] is None and ext:
            g["external_code"] = ext
    return [Product(g["supplier"], g["pair_key"], g["external_code"],
                    g["name"], g["variant_codes"]) for g in (groups[k] for k in order)]
