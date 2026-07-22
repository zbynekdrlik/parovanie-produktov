"""Riziko výpadku — supply-risk report (#107): pure logic, no network.

In-app migration of the n8n workflow „Forestshop — Riziko výpadku" (7ujLZ4WDNphSgsuj,
daily ~06:15). READ-ONLY / advisory — this module (and the automation that wires it
in webreview/app.py) NEVER writes to the eshop. It looks for OUR products that are
Skladom + visible (eshop state 1, see export_helpers.state_of) whose SUPPLIER has, in
the meantime, sold out — a risk that a customer buys something we cannot actually
re-order.

It reuses #106's ALREADY-SCRAPED data/out/supplier_stock.json (the „Dodávateľský
sklad" automation's store) — this module does NOT scrape anything itself, it only
JOINS the on-disk Shoptet catalog export against that store, via the SAME
internalNote link both automations share (supplier_stock.links_from_export reads the
identical column). A link that was never scraped, or whose scrape errored, is never
treated as a risk — absence of supplier data must never look like a stock-out.
"""
from __future__ import annotations

import csv
import io

from parovanie.export_helpers import state_of

# The row fields the risk report carries — kód, názov, náš stav (cena/sklad),
# dodávateľský stav (text), link, kedy naposledy skontrolované (per the digest's
# tab column list, minus the n8n-only 'size'/'editUrl' fields this export doesn't
# carry — see .claude/skills/webreview for the products.csv column set).
RISK_FIELDS = ("code", "pairCode", "name", "supplier", "ourPrice", "ourStock",
               "supplierAvailabilityText", "link", "checkedAt")


def compute_risk(csv_text: str, supplier_rows: list[dict] | None) -> list[dict]:
    """One risk row per OUR variant that is Skladom+visible (export_helpers.state_of
    == 1 — visible/detailOnly AND an in-stock availability text) whose supplier link
    (internalNote) was successfully checked (ok=True) by #106's scraper and found NOT
    available there (available is False, not None/unknown).

    ``csv_text`` is the on-disk Shoptet catalog export (cp1250-decoded — the same
    source #106 reads for links; ``_read_export_for_links`` in webreview/app.py).
    ``supplier_rows`` is data/out/supplier_stock.json's "rows" list — None or []
    (scraper never ran) yields NO rows, never a guess.

    A variant with no internalNote link, whose link has no supplier_stock entry yet
    (never scraped), whose scrape errored (ok=False), or whose supplier availability
    is unknown (None — static/LLM couldn't tell), is NEVER flagged. A variant we
    already carry as Nie je skladom / Už sa nebude predávať (state != 1) is also
    never flagged — we already show it as unavailable ourselves, no risk to a
    customer buying it."""
    by_link: dict[str, dict] = {}
    for r in supplier_rows or []:
        link = r.get("link")
        if link:
            by_link[link] = r

    out: list[dict] = []
    for row in csv.DictReader(io.StringIO(csv_text or ""), delimiter=";"):
        link = (row.get("internalNote") or "").strip()
        if not link.lower().startswith("http"):
            continue
        srow = by_link.get(link)
        if not srow or not srow.get("ok") or srow.get("available") is not False:
            continue
        vis = (row.get("productVisibility") or "").strip()
        ais = (row.get("availabilityInStock") or "").strip()
        aos = (row.get("availabilityOutOfStock") or "").strip()
        if state_of(vis, ais or aos) != 1:
            continue
        out.append({
            "code": (row.get("code") or "").strip(),
            "pairCode": (row.get("pairCode") or "").strip(),
            "name": (row.get("name") or "").strip(),
            "supplier": srow.get("supplier") or (row.get("supplier") or "").strip(),
            "ourPrice": (row.get("price") or "").strip(),
            "ourStock": (row.get("stock") or "").strip(),
            "supplierAvailabilityText": srow.get("availabilityText", ""),
            "link": link,
            "checkedAt": srow.get("checkedAt", ""),
        })
    return out
