"""Vypredané → Skladom — restock detection (#108): pure logic, no network.

In-app migration of the n8n workflow „Forestshop — Vypredané → Skladom v2"
(KN1BE18HLdM8mfTc, daily ~06:00). This is the mirror of #107's read-only
riziko_vypadku — but it WRITES to the eshop: it finds OUR products that are
Vypredané + visible (eshop state 2, see export_helpers.state_of) whose SUPPLIER
now HAS stock again, and (in the app's run_fn) flips them back to Skladom by
importing the restock rows to Shoptet.

This module holds ONLY the NETWORK-FREE detection JOIN; the actual Shoptet import
is done by webreview/app.py (run_restock_skladom → import_builder.restock_rows →
the existing careful run_import path with the #23-hardened result read-back). It
reuses #106's ALREADY-SCRAPED data/out/supplier_stock.json (the „Dodávateľský
sklad" automation's store) via the SAME internalNote link both automations share;
it never scrapes anything itself.

Faithful to the LIVE n8n „Vyhodnoť kandidátov" node: a product is a candidate only
when it is Vypredané + visible AND its supplier confirmation is FRESH and positive
— the matched supplier_stock row exists, ok=True, available is True (not
None/unknown, not False), AND its checkedAt is within max_pair_age_h of ``now``.
Absence of supplier data, an errored scrape, unknown availability, a supplier still
sold out, or a STALE confirmation NEVER flips a product — a production write must
never happen on a guess. Idempotent by construction: only state-2 (Vypredané)
products are candidates, so a product already flipped to Skladom (state 1) is never
picked again (once the export refreshes).
"""
from __future__ import annotations

import csv
import io
from datetime import datetime

from parovanie.export_helpers import state_of

# The freshness window a supplier confirmation must fall within to be trusted for a
# LIVE restock write — exactly the n8n per-row check (now - checkedAt > 48h → skip).
# The scraper (SUPPLIER_STOCK_MAX_AGE_H=20) re-checks each link at least daily, so a
# genuinely fresh confirmation is well inside 48h; anything older is treated as stale
# and never flips a product.
MAX_PAIR_AGE_H = 48.0

# The candidate row fields carried into the store + tab (kód, názov, náš stav vs
# dodávateľský stav, linky, čas kontroly). No n8n-only 'size'/'editUrl' — the
# products.csv export doesn't carry them (same reasoning as riziko_vypadku).
CANDIDATE_FIELDS = ("code", "pairCode", "name", "supplier", "ourPrice",
                    "supplierPrice", "supplierAvailabilityText", "link", "checkedAt")


def _is_fresh(checked_at: str, now: datetime, max_age_h: float) -> bool:
    """True when ``checked_at`` (ISO 8601) is within ``max_age_h`` of ``now``. An
    empty / unparseable / future-parse-failing timestamp is NEVER fresh (a missing
    confirmation must never flip a product). A naive timestamp inherits ``now``'s tz."""
    if not checked_at:
        return False
    try:
        prev = datetime.fromisoformat(checked_at)
    except ValueError:
        return False
    if prev.tzinfo is None:
        prev = prev.replace(tzinfo=now.tzinfo)
    return (now - prev).total_seconds() <= max_age_h * 3600.0


def compute_candidates(csv_text: str, supplier_rows: list[dict] | None,
                       now: datetime, max_pair_age_h: float = MAX_PAIR_AGE_H) -> list[dict]:
    """One restock candidate per OUR variant that is Vypredané + visible (eshop
    state 2 via export_helpers.state_of, productVisibility exactly 'visible') whose
    supplier link (internalNote) was SUCCESSFULLY checked (ok=True), found AVAILABLE
    (available is True — not None/unknown, not False) AND checked recently (checkedAt
    within ``max_pair_age_h`` of ``now``).

    ``csv_text`` is the on-disk Shoptet catalog export (cp1250-decoded — the same
    source #106/#107 read; ``_read_export_for_links`` in webreview/app.py).
    ``supplier_rows`` is data/out/supplier_stock.json's "rows" list — None or []
    (the scraper never ran) yields NO candidates, never a guess.

    Each variant `code` appears at most ONCE (first that qualifies wins) so the
    downstream import never emits a duplicate code — Shoptet aborts the whole import
    on a dupe, and the catalog has duplicate products sharing variant codes. A
    product with no internalNote link, no supplier_stock entry (never scraped), an
    errored scrape (ok=False), unknown availability (None), a supplier still sold out
    (available is False), a stale confirmation, or one we DON'T currently show as
    Vypredané+visible (state != 2, incl. already-Skladom → idempotent skip, or
    detailOnly/discontinued) is NEVER a candidate."""
    by_link: dict[str, dict] = {}
    for r in supplier_rows or []:
        link = r.get("link")
        if link:
            by_link[link] = r

    out: list[dict] = []
    seen: set[str] = set()
    for row in csv.DictReader(io.StringIO(csv_text or ""), delimiter=";"):
        code = (row.get("code") or "").strip()
        if not code or code in seen:
            continue
        link = (row.get("internalNote") or "").strip()
        if not link.lower().startswith("http"):
            continue
        srow = by_link.get(link)
        if not srow or not srow.get("ok") or srow.get("available") is not True:
            continue
        if not _is_fresh(srow.get("checkedAt", ""), now, max_pair_age_h):
            continue
        vis = (row.get("productVisibility") or "").strip()
        ais = (row.get("availabilityInStock") or "").strip()
        aos = (row.get("availabilityOutOfStock") or "").strip()
        # Vypredané + viditeľné (state 2, exactly visible): a temporarily out-of-
        # stock product we can put back on sale. NOT detailOnly (discontinued),
        # NOT already Skladom (state 1 → idempotent skip). state_of is the shared
        # 3-state classifier (never re-implemented — see .claude/skills/shoptet).
        if vis != "visible" or state_of(vis, ais or aos) != 2:
            continue
        seen.add(code)
        out.append({
            "code": code,
            "pairCode": (row.get("pairCode") or "").strip(),
            "name": (row.get("name") or "").strip(),
            "supplier": srow.get("supplier") or (row.get("supplier") or "").strip(),
            "ourPrice": (row.get("price") or "").strip(),
            "supplierPrice": "" if srow.get("price") is None else str(srow.get("price")),
            "supplierAvailabilityText": srow.get("availabilityText", ""),
            "link": link,
            "checkedAt": srow.get("checkedAt", ""),
        })
    return out
