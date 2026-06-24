from __future__ import annotations
from rapidfuzz import fuzz
from parovanie.models import Product, Candidate
from parovanie.normalize import clean_name


def _code_hit(product: Product, c: Candidate) -> bool:
    if not product.external_code:
        return False
    code = product.external_code.lower()
    hay = " ".join(filter(None, [c.name, c.url, c.code or ""])).lower()
    return code in hay


def _name_score(product: Product, c: Candidate) -> float:
    return float(fuzz.token_set_ratio(clean_name(product.name), c.name or ""))


def rank(product: Product, candidates: list[Candidate]) -> list[Candidate]:
    for c in candidates:
        c.raw_score = 1000.0 + _name_score(product, c) if _code_hit(product, c) \
            else _name_score(product, c)
    return sorted(candidates, key=lambda c: c.raw_score, reverse=True)


def pick_best(product: Product, candidates: list[Candidate]) -> tuple[Candidate | None, str]:
    if not candidates:
        return None, "none"
    ranked = rank(product, candidates)
    best = ranked[0]
    if best.raw_score >= 1000.0:
        return best, "high"
    if best.raw_score >= 80.0:
        return best, "medium"
    if best.raw_score >= 50.0:
        return best, "low"
    return best, "low"  # auto-fill: still take best even if weak
