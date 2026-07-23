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

from parovanie.grube_de import to_grube_de

LINK_HEADER = ["code", "pairCode", "internalNote"]
STATE_HEADER = ["code", "pairCode", "productVisibility", "stock",
                "availabilityInStock", "availabilityOutOfStock"]
# Supplier write-back: the ONLY columns a supplier-assign CSV sets. `supplier` is a
# Shoptet product field present in the pattern-14 export; split out so a present-but-
# empty cell can't wipe internalNote/state/prices. NOTE: export-presence does NOT prove
# CSV import-settability (textProperty1..20 are exported yet ignored on import — see
# .claude/skills/shoptet). Import-settability MUST be verified live via an export
# read-back before trusting the nightly write-back (same check the pairings path uses).
SUPPLIER_HEADER = ["code", "pairCode", "supplier"]

# Restock import (vypredané → skladom): the ONLY columns a restock CSV may set.
# An upstream feed (n8n) carries extra info columns (name, prices, supplier, …);
# Shoptet imports every recognised header, so we must keep ONLY these — otherwise
# the feed could silently overwrite prices/names on the live eshop.
# availabilityOutOfStock MUSÍ ísť spolu s availabilityInStock: Shoptet zobrazuje
# availabilityOutOfStock keď stock klesne na 0 — bez neho reštokovaný produkt po
# vypredaní fiktívnych kusov zobrazil staré "Vypredané" (CEO nález 2026-07-14).
RESTOCK_COLS = ["code", "pairCode", "productVisibility",
                "availabilityInStock", "availabilityOutOfStock", "stock"]

# GRUBE per-size externalCode write-back: the ONLY columns this CSV sets. Own file
# (own header) so a present-but-empty cell can't wipe internalNote/state/prices.
# externalCode is variant-level importable (verified live, Task 1). GRUBE-only is
# guaranteed by the source store (grube_codes.json is built only for GRUBE).
EXTERNALCODE_HEADER = ["code", "pairCode", "externalCode"]

_VYPREDANE = "Vypredané"
_SKONCIL = "Predaj výrobku skončil"
_SKLADOM = "Skladom"
# The fictional positive stock a restock sets so the product is sellable again
# (Shoptet shows availabilityInStock while stock>0). The live n8n workflow used 5.
RESTOCK_STOCK = "5"


def restock_rows(candidates, code2pair=None):
    """Restock import rows (Vypredané → Skladom): put temporarily-sold-out products
    back on sale. One row per unique variant `code` in RESTOCK_COLS order:
    visible, availabilityInStock + availabilityOutOfStock BOTH 'Skladom', a positive
    stock. Both availability fields MUST be set together — Shoptet shows
    availabilityOutOfStock once stock hits 0, so a restock that set only
    availabilityInStock left the old 'Vypredané' showing after the fictional units
    sold out (CEO fix 2026-07-14). Each `code` appears ONCE (Shoptet aborts the whole
    import on a duplicate code — the catalog has duplicate products sharing variant
    codes; first wins). `candidates` are dicts carrying `code` (+ optional `pairCode`);
    `code2pair` backfills a missing pairCode. Pure → unit-testable."""
    code2pair = code2pair or {}
    rows = []
    seen = set()
    for c in candidates:
        code = (c.get("code") or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        pc = (c.get("pairCode") or "").strip() or code2pair.get(code, "")
        rows.append([code, pc, "visible", _SKLADOM, _SKLADOM, RESTOCK_STOCK])
    return rows


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
        missing = [c for c in cols if c not in fields]
        if missing:
            # prítomný-ale-prázdny stĺpec Shoptet import ZMAŽE — doplniť chýbajúci
            # stĺpec prázdnou bunkou by vymazalo živé dáta, preto feed odmietame
            raise ValueError(f"vstupné CSV nemá povinné stĺpce {missing} (má: {fields})")
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


def new_supplier_keys(assignments, uploaded):
    """Forestshop codes ready for the nightly supplier write-back: assigned a
    non-empty supplier name that was NOT yet uploaded (new code) or whose name
    changed since the last upload. `assignments`/`uploaded` are {code: supplier}.
    Keeps the upload incremental — only genuinely new/changed assignments go up."""
    out = []
    for code, sup in assignments.items():
        c = (code or "").strip()
        s = (sup or "").strip()
        if not c or not s:
            continue
        if uploaded.get(c) != s:
            out.append(code)   # original key (callers index assignments[c] with it)
    return out


def new_order_pairing_keys(order_pairings, uploaded):
    """Forestshop codes from the inline pairings entered on the 'Na objednanie' tab
    (order_pairings.json -- codes OUTSIDE the review set, #38) ready for the nightly
    push: a non-empty URL that was NOT yet uploaded (new code) or whose URL changed
    since the last upload. `uploaded` tracks these under the `order:<code>`
    namespace -- a distinct namespace inside the SAME uploaded_pairings.json state
    as the review-decision keys (new_pairing_keys), so the two never collide (a
    decision key is always `SUPPLIER|pairCode`, never `order:...`). Mirrors
    new_pairing_keys/new_supplier_keys."""
    out = []
    for code, url in order_pairings.items():
        c = (code or "").strip()
        u = (url or "").strip()
        if not c or not u:
            continue
        if uploaded.get(f"order:{c}") != u:
            out.append(code)   # original key (callers index order_pairings[c] with it)
    return out


def link_rows(products, decisions, code2pair, variant_links=None):
    """Reorder-link rows → `internalNote` = URL, one per variant, for good/manual
    decisions with a URL. Only code;pairCode;internalNote (no state columns → the
    eshop's stock/visibility is left untouched). Each `code` appears ONCE — Shoptet
    aborts the whole import on a duplicate code, and the catalog has duplicate
    products that share variant codes (first pairing wins).

    A `split` decision (#174 — a product whose supplier lists a DIFFERENT product
    URL per size, e.g. TRIGONA THERMOPAD S/M/L/XL/XXL) writes a per-variant link:
    for each variant code the URL is `variant_links[code]` (keyed by the STABLE
    variant code, never array position). A variant with NO stored link is SKIPPED —
    never an empty internalNote cell (an empty cell would WIPE the existing link).
    `variant_links` defaults to {} (so a plain good/manual call is unchanged).

    GRUBE-only: a GRUBE product's URL is rebuilt to the canonical grube.de detail
    URL (productId-rebuild via `to_grube_de`, strips mangled slug/query/single-size
    #itemId); a non-grube product's URL is written verbatim. Fallback to the raw URL
    if `to_grube_de` can't parse a productId."""
    variant_links = variant_links or {}
    rows = []
    seen = set()
    for p in products:
        d = decisions.get(p.get("key"))
        if not d:
            continue
        st = d.get("status")
        is_grube = p.get("supplier") == "GRUBE"
        if st == "split":
            for c in p["variant_codes"]:
                if c in seen:
                    continue
                vurl = (variant_links.get(c) or "").strip()
                if not vurl:
                    continue   # variant not yet linked → no row (never wipe internalNote)
                seen.add(c)
                if is_grube:
                    vurl = to_grube_de(vurl) or vurl
                rows.append([c, code2pair.get(c, ""), vurl])
        elif st in ("good", "manual") and (d.get("url") or "").strip():
            url = d["url"].strip()
            if is_grube:
                url = to_grube_de(url) or url
            for c in p["variant_codes"]:
                if c in seen:
                    continue
                seen.add(c)
                rows.append([c, code2pair.get(c, ""), url])
    return rows


def order_pairing_rows(order_pairings, code2pair, exclude_codes=None):
    """Reorder-link rows from INLINE pairings entered on the 'Na objednanie' tab.

    `order_pairings` is {forestshop_code: supplier_url} — the manager pastes the
    supplier reorder URL straight onto an order line while ordering it. Emits
    code;pairCode;internalNote (same shape as link_rows) so these reach the eshop's
    private 'Interná poznámka' field exactly like a review pairing. `exclude_codes`
    (codes already covered by a reviewed decision via link_rows) are skipped so a
    code is never imported twice — Shoptet aborts the whole import on a duplicate
    code, and a reviewed decision is the authoritative source. Each code once; empty
    URLs dropped. Pure → unit-testable."""
    exclude = exclude_codes or set()
    rows = []
    seen = set()
    for code, url in order_pairings.items():
        code = (code or "").strip()
        url = (url or "").strip()
        if not code or not url or code in exclude or code in seen:
            continue
        seen.add(code)
        rows.append([code, code2pair.get(code, ""), url])
    return rows


def supplier_rows(assignments, code2pair, exclude_codes=None):
    """Supplier write-back rows from supplier names assigned on the 'Na objednanie'
    tab. `assignments` is {forestshop_code: supplier_name} — the manager fills in the
    supplier for an order line that arrived without one. Emits code;pairCode;supplier
    (only the `supplier` column → internalNote/state/prices left untouched). Each code
    once; empty code/supplier dropped; `exclude_codes` skipped. Shoptet aborts the
    whole import on a duplicate code, so dedup is mandatory. Pure → unit-testable."""
    exclude = exclude_codes or set()
    rows = []
    seen = set()
    for code, sup in assignments.items():
        code = (code or "").strip()
        sup = (sup or "").strip()
        if not code or not sup or code in exclude or code in seen:
            continue
        seen.add(code)
        rows.append([code, code2pair.get(code, ""), sup])
    return rows


def externalcode_rows(grube_codes, code2pair, exclude_codes=None):
    """GRUBE per-size externalCode write-back rows: code;pairCode;externalCode from
    `grube_codes` ({code: {itemId, ...}}, the durable grube_codes.json store, GRUBE-only
    by construction). The externalCode is the grube per-size `itemId`; it MUST be a
    non-empty purely-numeric string — an empty/non-numeric cell is dropped (an empty
    cell would WIPE the existing externalCode, a non-numeric one is junk and could be a
    formula-injection lead). Each code once, first-wins; `exclude_codes` skipped. Shoptet
    aborts the whole import on a duplicate code, so dedup is mandatory. Pure → testable."""
    exclude = exclude_codes or set()
    rows = []
    seen = set()
    for code, info in grube_codes.items():
        if code in exclude or code in seen:
            continue
        iid = str(info.get("itemId", "")).strip()
        if not iid or not iid.isdigit():
            continue
        seen.add(code)
        rows.append([code, code2pair.get(code, ""), iid])
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
