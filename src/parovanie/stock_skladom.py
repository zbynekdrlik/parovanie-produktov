"""Máme skladom → Skladom — auto-restock from Shoptet's OWN stock (#98): pure logic.

The manager marks products as „kompletné / máme" (the green stock bars in the
Shoptet admin) — i.e. the product physically HAS stock (`stock > 0` in the export).
This finds every OUR product that has real positive stock but is still shown to
customers as Vypredané (eshop state 2, visible — see export_helpers.state_of) and
(in the app's run_fn) flips it back to Skladom by importing the rows to Shoptet.

DISTINCT from #108 restock_skladom: that one triggers on a SCRAPED supplier
confirmation (the „Dodávateľský sklad" store) — this one triggers on Shoptet's
OWN physical stock, needs no supplier data, and never scrapes. They are
complementary (a product we physically have vs. one the supplier can reorder).

Only a `visible` + state-2 (Vypredané) product with real positive stock is a
candidate. Every conscious-off state — `detailOnly` / discontinued / hidden
(state 3, the manager's „už nepredávať" decision) — and the already-Skladom state
(state 1) is NEVER flipped: the same visible-only + state-2 gate restock_skladom
uses. A residual unit of stock on a discontinued product must not re-list it
(„neprepíše vedomé off rozhodnutie manažéra"). Idempotent by construction — a
product already Skladom (state 1) is never re-picked once the export refreshes.
"""
from __future__ import annotations

import csv
import io

from parovanie.export_helpers import state_of

# The candidate row fields carried into the store + tab.
CANDIDATE_FIELDS = ("code", "pairCode", "name", "ourPrice", "stock", "availabilityText")


def _positive_stock(raw: str) -> bool:
    """True only for a genuinely positive numeric stock. Empty / non-numeric →
    False (never a guess); a comma decimal (Shoptet locale) is accepted; a
    negative value (Shoptet backorder) is NOT „máme skladom"."""
    s = (raw or "").strip().replace(",", ".")
    if not s:
        return False
    try:
        return float(s) > 0
    except ValueError:
        return False


def compute_candidates(csv_text: str | None) -> list[dict]:
    """One auto-skladom candidate per OUR variant that PHYSICALLY has stock
    (`stock > 0` — the „zelené pásy / máme" signal) but is still shown as Vypredané
    (eshop state 2 via export_helpers.state_of AND productVisibility exactly
    'visible').

    ``csv_text`` is the on-disk Shoptet catalog export (cp1250-decoded — the same
    source #106/#107/#108 read; ``_read_export_for_links`` in webreview/app.py).

    Each variant `code` appears at most ONCE (first that qualifies wins) so the
    downstream import never emits a duplicate code — Shoptet aborts the whole import
    on a dupe, and the catalog has duplicate products sharing variant codes. A
    product with no real positive stock (stock <= 0, empty, non-numeric), one we
    already show as Skladom (state 1 → idempotent skip), or a conscious-off product
    (detailOnly / discontinued / hidden → state 3, or any non-visible visibility) is
    NEVER a candidate."""
    out: list[dict] = []
    seen: set[str] = set()
    for row in csv.DictReader(io.StringIO(csv_text or ""), delimiter=";"):
        code = (row.get("code") or "").strip()
        if not code or code in seen:
            continue
        if not _positive_stock(row.get("stock")):
            continue
        vis = (row.get("productVisibility") or "").strip()
        ais = (row.get("availabilityInStock") or "").strip()
        aos = (row.get("availabilityOutOfStock") or "").strip()
        # Vypredané + viditeľné (state 2, exactly visible): we physically have it but
        # customers still see it as sold out. NOT detailOnly/discontinued/hidden
        # (state 3, conscious off decision), NOT already Skladom (state 1 →
        # idempotent skip). state_of is the shared 3-state classifier (never
        # re-implemented — see .claude/skills/shoptet).
        if vis != "visible" or state_of(vis, ais or aos) != 2:
            continue
        seen.add(code)
        out.append({
            "code": code,
            "pairCode": (row.get("pairCode") or "").strip(),
            "name": (row.get("name") or "").strip(),
            "ourPrice": (row.get("price") or "").strip(),
            "stock": (row.get("stock") or "").strip(),
            "availabilityText": ais or aos,
        })
    return out
