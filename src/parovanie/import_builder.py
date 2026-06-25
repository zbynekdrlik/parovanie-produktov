"""Build Shoptet import rows from review decisions.

TWO disjoint outputs — Shoptet OVERWRITES a present-but-empty cell, so each row
carries ONLY the columns it sets (a combined file with empty cells would wipe
existing values; this was learned the hard way against the live eshop):

  - LINK rows  → `internalNote` (private "Interná poznámka" field) = supplier URL.
    The public `textProperty*` info-params are NOT importable via CSV, so the
    reorder link goes into the private `internalNote` field instead. The presence
    of the URL there IS the "human matched" signal (no separate marker field).
    good / manual decisions.
  - STATE rows → `productVisibility` / `stock` / `availability`. unavailable /
    discontinued decisions. (No `internalNote` column → existing links untouched.)

Three eshop states (see .claude/skills/shoptet):
  1. Skladom        — link product (internalNote=URL); reorder automation reads it.
  2. Nie je skladom — visible + 'Vypredané', stock 0.
  3. Už sa nebude predávať — detailOnly + 'Predaj výrobku skončil' (page kept for Google).
"""

LINK_HEADER = ["code", "pairCode", "internalNote"]
STATE_HEADER = ["code", "pairCode", "productVisibility", "stock",
                "availabilityInStock", "availabilityOutOfStock"]

_VYPREDANE = "Vypredané"
_SKONCIL = "Predaj výrobku skončil"


def link_rows(products, decisions, code2pair):
    """Reorder-link rows → `internalNote` = URL, one per variant, for good/manual
    decisions with a URL. Only code;pairCode;internalNote (no state columns → the
    eshop's stock/visibility is left untouched)."""
    rows = []
    for p in products:
        d = decisions.get(p.get("key"))
        if not d:
            continue
        if d.get("status") in ("good", "manual") and d.get("url"):
            for c in p["variant_codes"]:
                rows.append([c, code2pair.get(c, ""), d["url"]])
    return rows


def state_rows(products, decisions, code2pair):
    """Stock-state rows → visibility/availability, one per variant:
      - unavailable (state 2): visible, stock 0, 'Vypredané'.
      - discontinued (state 3): detailOnly, stock 0, 'Predaj výrobku skončil'.
    Only code;pairCode;state columns (no internalNote → existing links untouched)."""
    rows = []
    for p in products:
        d = decisions.get(p.get("key"))
        if not d:
            continue
        st = d.get("status")
        for c in p["variant_codes"]:
            pc = code2pair.get(c, "")
            if st == "unavailable":
                rows.append([c, pc, "visible", "0", _VYPREDANE, _VYPREDANE])
            elif st == "discontinued":
                rows.append([c, pc, "detailOnly", "0", _SKONCIL, _SKONCIL])
    return rows
