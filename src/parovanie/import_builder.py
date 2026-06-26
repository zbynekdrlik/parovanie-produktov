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

# Restock import (vypredané → skladom): the ONLY columns a restock CSV may set.
# An upstream feed (n8n) carries extra info columns (name, prices, supplier, …);
# Shoptet imports every recognised header, so we must keep ONLY these — otherwise
# the feed could silently overwrite prices/names on the live eshop.
RESTOCK_COLS = ["code", "pairCode", "productVisibility", "availabilityInStock", "stock"]

_VYPREDANE = "Vypredané"
_SKONCIL = "Predaj výrobku skončil"


def sanitize_csv(in_path, out_path, cols=RESTOCK_COLS):
    """Read an arbitrary ';'-CSV (utf-8 or utf-8-sig) and rewrite it keeping ONLY
    `cols`, in the canonical Shoptet import dialect (utf-8-sig BOM, ';', CRLF).
    Every other column is dropped, so an upstream feed can never overwrite a field
    it didn't intend to (prices, names, …). Rows with an empty `code` are skipped.
    Returns the kept-row count. Raises ValueError if the input lacks code+pairCode."""
    import csv as _csv

    from parovanie.writer import shoptet_writer
    with open(in_path, encoding="utf-8-sig", newline="") as f:
        reader = _csv.DictReader(f, delimiter=";")
        fields = reader.fieldnames or []
        if "code" not in fields or "pairCode" not in fields:
            raise ValueError(f"vstupné CSV nemá code+pairCode (má: {fields})")
        rows = [[(row.get(c) or "").strip() for c in cols]
                for row in reader if (row.get("code") or "").strip()]
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = shoptet_writer(f)
        w.writerow(cols)
        w.writerows(rows)
    return len(rows)


def new_pairing_keys(decisions, uploaded):
    """Decision keys ready for the nightly pairing upload: good/manual decisions
    that carry a URL and were NOT yet uploaded (new key) or whose URL changed since
    the last upload. `uploaded` is the persisted {key: url} of past uploads. Keeps
    the upload incremental — only genuinely new/changed pairings go to the eshop."""
    out = []
    for k, d in decisions.items():
        if d.get("status") not in ("good", "manual"):
            continue
        url = (d.get("url") or "").strip()
        if not url:
            continue
        if uploaded.get(k) != url:
            out.append(k)
    return out


def link_rows(products, decisions, code2pair):
    """Reorder-link rows → `internalNote` = URL, one per variant, for good/manual
    decisions with a URL. Only code;pairCode;internalNote (no state columns → the
    eshop's stock/visibility is left untouched). Each `code` appears ONCE — Shoptet
    aborts the whole import on a duplicate code, and the catalog has duplicate
    products that share variant codes (first pairing wins)."""
    rows = []
    seen = set()
    for p in products:
        d = decisions.get(p.get("key"))
        if not d:
            continue
        if d.get("status") in ("good", "manual") and d.get("url"):
            for c in p["variant_codes"]:
                if c in seen:
                    continue
                seen.add(c)
                rows.append([c, code2pair.get(c, ""), d["url"]])
    return rows


def state_rows(products, decisions, code2pair):
    """Stock-state rows → visibility/availability, one per variant:
      - unavailable (state 2): visible, stock 0, 'Vypredané'.
      - discontinued (state 3): detailOnly, stock 0, 'Predaj výrobku skončil'.
    Only code;pairCode;state columns (no internalNote → existing links untouched).
    Each `code` appears ONCE (duplicate-product catalog) — Shoptet rejects dupes."""
    rows = []
    seen = set()
    for p in products:
        d = decisions.get(p.get("key"))
        if not d:
            continue
        st = d.get("status")
        for c in p["variant_codes"]:
            if c in seen:
                continue
            pc = code2pair.get(c, "")
            if st == "unavailable":
                seen.add(c)
                rows.append([c, pc, "visible", "0", _VYPREDANE, _VYPREDANE])
            elif st == "discontinued":
                seen.add(c)
                rows.append([c, pc, "detailOnly", "0", _SKONCIL, _SKONCIL])
    return rows
