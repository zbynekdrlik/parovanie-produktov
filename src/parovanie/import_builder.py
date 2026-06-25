"""Build the Shoptet import rows from review decisions.

ONE file. Shoptet pairs by `code` but ALSO needs `pairCode` present to import
variant products, so both columns are always emitted. Import with the
"replace empty values" option OFF (empty cell = leave unchanged).

Three eshop states (see .claude/skills/shoptet):
  1. Skladom        — link product; their automation turns it on from the link.
  2. Nie je skladom — temporarily out, keep checking: visible + 'Vypredané', stock 0.
  3. Už sa nebude predávať (link kept for Google) — detailOnly + 'Predaj výrobku skončil'.
"""

HEADER = ["code", "pairCode", "textProperty10", "textProperty11",
          "productVisibility", "stock", "availabilityInStock", "availabilityOutOfStock"]

_SKONCIL = "Predaj výrobku skončil"


def import_rows(products, decisions, code2pair):
    """products: iterable of dicts with 'key' + 'variant_codes'.
    decisions: key -> {'status', 'url'}.  code2pair: code -> pairCode.

    Per decision status, one row per variant (empty cell = leave unchanged):
      - good / manual (link): code;pairCode;url;'human matched';;;;  — set link +
        marker; visibility/stock/availability left empty (their automation turns
        the product on / to Skladom from the link).
      - unavailable (state 2, nie je skladom): empty link; visible; stock 0;
        availability 'Vypredané'. Stays in the pool to re-check for a link.
      - discontinued (state 3, už sa nebude predávať): empty link; detailOnly
        (page kept for Google, hidden from offer); stock 0; 'Predaj výrobku skončil'.
    """
    rows = []
    for p in products:
        d = decisions.get(p.get("key"))
        if not d:
            continue
        status, url = d.get("status"), d.get("url", "")
        for c in p["variant_codes"]:
            pc = code2pair.get(c, "")
            if status in ("good", "manual") and url:
                rows.append([c, pc, url, "human matched", "", "", "", ""])
            elif status == "unavailable":
                rows.append([c, pc, "", "", "visible", "0", "Vypredané", "Vypredané"])
            elif status == "discontinued":
                rows.append([c, pc, "", "", "detailOnly", "0", _SKONCIL, _SKONCIL])
    return rows
