from __future__ import annotations
import json
from parovanie.models import Product, Candidate


def record(product: Product, queries: list[str], candidates: list[Candidate]) -> dict:
    return {
        "pair_key": product.pair_key,
        "supplier": product.supplier,
        "external_code": product.external_code,
        "name": product.name,
        "variant_codes": product.variant_codes,
        "queries": queries,
        "candidates": [{"name": c.name, "url": c.url} for c in candidates],
    }


def write_candidates(records: list[dict], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


def read_candidates(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)
