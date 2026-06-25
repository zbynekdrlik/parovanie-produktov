"""Build the Shoptet import rows from review decisions.

ONE file. Shoptet pairs by `code` but ALSO needs `pairCode` present to import
variant products, so both columns are always emitted. Import with the
"replace empty values" option OFF (empty cell = leave unchanged), so a link row
only sets textProperty10/11 and an unavailable row only sets stock/availability.
"""

HEADER = ["code", "pairCode", "textProperty10", "textProperty11",
          "stock", "availabilityInStock", "availabilityOutOfStock"]


def import_rows(products, decisions, code2pair):
    """products: iterable of dicts with 'key' + 'variant_codes'.
    decisions: key -> {'status', 'url'}.  code2pair: code -> pairCode.

    - link (good/manual): code;pairCode;url;'human matched';;;  (stock/avail left
      empty → their reorder automation turns the product on from the link).
    - unavailable: code;pairCode;;;0;Vypredané;Vypredané  (empty link until one is
      found; sold-out. NOT marked human matched → stays in the pool to re-check).
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
                rows.append([c, pc, url, "human matched", "", "", ""])
            elif status == "unavailable":
                rows.append([c, pc, "", "", "0", "Vypredané", "Vypredané"])
    return rows
