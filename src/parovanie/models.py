from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Candidate:
    name: str
    url: str
    code: str | None = None
    price: str | None = None
    raw_score: float = 0.0


@dataclass
class Product:
    supplier: str
    pair_key: str
    external_code: str | None
    name: str
    variant_codes: list[str] = field(default_factory=list)


@dataclass
class Match:
    product: Product
    query: str
    chosen: Candidate | None
    confidence: str  # "high" | "medium" | "low" | "none"
    candidate_count: int
