"""Pure logic for the Shoptet auto-import script (no browser import).

Credential loading, import-CSV pre-flight, and result-log parsing. The browser
driving lives in scripts/shoptet_import.py. See docs spec
2026-06-25-shoptet-auto-import-design.md and .claude/skills/shoptet.
"""
import csv
import os
import re

REQUIRED_KEYS = ("SHOPTET_ADMIN_URL", "SHOPTET_USER", "SHOPTET_PASS")


class ShoptetError(Exception):
    """Any pre-flight / config problem — fail loud, never proceed silently."""


def load_credentials(path):
    """Parse a KEY=value secret file (chmod 600, gitignored). Strips surrounding
    quotes; keeps inner spaces. Raises ShoptetError if the file is missing or any
    of REQUIRED_KEYS is absent/empty."""
    if not os.path.exists(path):
        raise ShoptetError(f"Súbor s prihlásením chýba: {path} "
                           f"(vytvor ho s kľúčmi {', '.join(REQUIRED_KEYS)}, chmod 600).")
    creds = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            creds[k.strip()] = v
    missing = [k for k in REQUIRED_KEYS if not creds.get(k)]
    if missing:
        raise ShoptetError(f"V {path} chýbajú kľúče: {', '.join(missing)}.")
    return dict(creds)   # all parsed keys (incl. optional SHOPTET_EXPORT_URL for backup)


EXPECTED_HEADER = ["code", "pairCode", "internalNote", "productVisibility",
                   "stock", "availabilityInStock", "availabilityOutOfStock",
                   "externalCode"]


def classify_row(row):
    """Classify one generated import row by its column values (mirrors
    import_builder link_rows / state_rows). A link carries the reorder URL in the
    private `internalNote` field; an externalCode write-back row carries the grube
    per-size itemId in `externalCode` with no internalNote/visibility/stock."""
    if (row.get("internalNote") or "").strip():
        return "link"
    vis = (row.get("productVisibility") or "").strip().lower()
    avail = ((row.get("availabilityInStock") or "")
             + " " + (row.get("availabilityOutOfStock") or "")).lower()
    if vis == "detailonly":
        return "discontinued"
    if vis == "visible" and "vypredan" in avail:
        return "unavailable"
    if ((row.get("externalCode") or "").strip()
            and not vis and not avail.strip()
            and not (row.get("stock") or "").strip()):
        return "externalcode"
    return "other"


def preflight_csv(path):
    """Read the generated import CSV (utf-8-sig, ';'), verify it is well-formed,
    and return a plan with per-type counts. Raises ShoptetError on any problem so
    nothing gets uploaded blindly."""
    if not os.path.exists(path):
        raise ShoptetError(f"Import súbor chýba: {path}")
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f, delimiter=";")
            columns = reader.fieldnames or []
            counts = {"link": 0, "unavailable": 0, "discontinued": 0,
                      "externalcode": 0, "other": 0}
            total = 0
            for row in reader:
                total += 1
                counts[classify_row(row)] += 1
    except UnicodeError as e:
        raise ShoptetError(f"Import súbor sa nedá prečítať ako UTF-8: {path} ({e})")
    if "code" not in columns or "pairCode" not in columns:
        raise ShoptetError(f"Import súbor nemá stĺpce code+pairCode (má: {columns}).")
    if total == 0:
        raise ShoptetError(f"Import súbor nemá žiadne riadky: {path}")
    return {"path": path, "total": total, "columns": columns, **counts}


def _num_after(low, *patterns):
    """First integer that follows any of the given keyword patterns (tried in
    order). Returns None if none match."""
    for kw in patterns:
        m = re.search(kw, low)
        if m:
            return int(m.group(1))
    return None


def parse_import_log(text):
    """Extract processed/updated/failed counts from the Shoptet import result
    line (e.g. 'Spracované: 1. Zlyhanie variantov: 1.' or the Czech
    'Zpracováno: 1. Upraveno: 1.'). Returns None for any count not found.

    'failed' prefers the explicit 'Zlyhanie …: N' count over the generic
    'skončil s chybou' prose (which carries no count) — otherwise the prose
    'chybou' would wrongly grab the processed number that follows it."""
    text = text or ""
    low = text.lower()
    return {
        "raw": text,
        "processed": _num_after(low, r"z?pracov\w*[^0-9]{0,40}?(\d+)"),
        "updated": _num_after(low, r"uprav\w*[^0-9]{0,40}?(\d+)"),
        # explicit 'zlyhanie … N' first; only then a tight 'chyb(y)/chýb: N' (colon
        # required, so the prose 'skončil s chybou …' can't bridge to a later number)
        "failed": _num_after(low, r"zlyhan\w*[^0-9]{0,40}?(\d+)", r"ch[ýy]b\w*\s*:\s*(\d+)"),
    }


def result_exit_code(parsed) -> int:
    """Process exit code for a parsed import result — never report a failed or
    unreadable import as success. processed=None (Log unreadable) → 2; any reported
    failures → 2; only a clean processed-count with no failures → 0."""
    if not parsed or parsed.get("processed") is None:
        return 2
    if parsed.get("failed"):
        return 2
    return 0
