"""Shared helpers for the export → review/import scripts.

These were copy-pasted (and had started to drift — the 3-state classifier lost
'neni skladem' in one copy) across build_review_data / resync_export /
resolve_urls. One tested home now.
"""
from __future__ import annotations

import re
import unicodedata

# Shoptet export image columns, in priority order.
IMGCOLS = ["defaultImage"] + [f"image{i}" for i in range(2, 16)] + ["image"]


def slug(name: str, strip_leading_number: bool = False) -> str:
    """forestshop URL slug: NFKD-fold, drop diacritics, non-alnum runs → '-'.
    strip_leading_number drops a leading 'NN ' index (resolve_urls needs that)."""
    s = name or ""
    if strip_leading_number:
        s = re.sub(r"^\d+\s+", "", s)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def state_of(visibility: str, availability: str) -> int:
    """The three eshop states (see .claude/skills/shoptet):
      1 = Skladom (predajný), 2 = Nie je skladom (Vypredané / nedostupné),
      3 = Už sa nebude predávať (Predaj výrobku skončil / hidden / blocked)."""
    v = (visibility or "").lower()
    a = (availability or "").lower()
    if "skon" in a or v in ("hidden", "blocked", "cashdeskonly", "blockunregistered"):
        return 3
    if any(x in a for x in ("vypredan", "nedostupn", "není skladem", "neni skladem")):
        return 2
    return 1


def row_images(row: dict) -> list[str]:
    """http(s) image URLs from an export row across IMGCOLS, in order, deduped."""
    out: list[str] = []
    for col in IMGCOLS:
        v = (row.get(col) or "").strip()
        if v.startswith("http") and v not in out:
            out.append(v)
    return out
