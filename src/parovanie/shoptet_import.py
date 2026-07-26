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
    'chybou' would wrongly grab the processed number that follows it.

    'error_detail' (#23) surfaces a HARD Shoptet error that aborted the whole
    import with NO Spracované/Zlyhanie summary at all (e.g. 'Chyba | Číslo
    riadku: 42 - Data in column code are not unique') — set only when
    'processed' could not be found AND the text mentions 'chyba', so it never
    fires on the harmless prose 'skončil s chybou' that DOES carry a real
    summary (see test_parse_import_log_chybou_prose_not_mistaken_for_hard_error)."""
    text = text or ""
    low = text.lower()
    processed = _num_after(low, r"z?pracov\w*[^0-9]{0,40}?(\d+)")
    return {
        "raw": text,
        "processed": processed,
        "updated": _num_after(low, r"uprav\w*[^0-9]{0,40}?(\d+)"),
        # explicit 'zlyhanie … N' first; only then a tight 'chyb(y)/chýb: N' (colon
        # required, so the prose 'skončil s chybou …' can't bridge to a later number)
        "failed": _num_after(low, r"zlyhan\w*[^0-9]{0,40}?(\d+)", r"ch[ýy]b\w*\s*:\s*(\d+)"),
        "error_detail": text.strip() if (processed is None and "chyba" in low) else None,
    }


# A table row that looks like a genuine Shoptet import-log ENTRY — either a
# result summary (Spracované/Zpracováno/Upravené/Zlyhanie) OR a hard error
# entry that aborts the whole import with no summary at all (#23: 'Chyba |
# Číslo riadku: N - Data in column code are not unique'). A header row
# ('Dátum Výsledok') or unrelated page chrome matches neither.
_RESULT_ROW_RE = re.compile(r"spracov|zpracov|uprav|zlyhan|chyba|riadku", re.IGNORECASE)


# Shoptet prefixes every Log entry with its own increasing id ('#12689 26.07.2026
# 21:00 …') — the only stable handle on WHICH entry a row is.
_ENTRY_ID_RE = re.compile(r"#(\d+)")


def log_entry_id(text):
    """Shoptet's own Log entry number ('#12689 …' → 12689), or None when the row
    carries none (older/other renderings — callers must degrade, not crash)."""
    m = _ENTRY_ID_RE.search(text or "")
    return int(m.group(1)) if m else None


def _new_entries(entries, baseline):
    """The log entries written AFTER `baseline` (the topmost entry captured before
    this import was submitted), newest first. Uses Shoptet's entry ids when both
    sides carry one; otherwise falls back to table position (the Log is rendered
    newest first, so everything from `baseline` down is older)."""
    base_id = log_entry_id(baseline)
    out = []
    for t in entries:
        if baseline is not None and t == baseline:
            break
        tid = log_entry_id(t)
        if base_id is not None and tid is not None and tid <= base_id:
            break
        out.append(t)
    return out


def pick_result_row(row_texts, baseline=None, expected_rows=None):
    """Pick the row that reflects the import THIS run just triggered, from a list
    of table-row texts as Shoptet renders its Log — NEWEST FIRST.

    Two facts identify our own entry, and both are required to say "this is ours":
      * `baseline` — the topmost log entry captured BEFORE this import was
        submitted. Only entries NEWER than it (by Shoptet's own '#12689' entry
        id, or by table position when ids are absent) can be ours.
      * `expected_rows` — how many rows the CSV we submitted actually carries.
        Shoptet reports it back as 'Spracované: N' (verified against every
        archived run in data/out/shoptet_import_*.log), so among several new
        entries ours is the one whose processed count matches.

    Returns None — "not ours / not readable yet", never a guess — when no entry
    looks like a log row, when nothing new appeared since the baseline, or when
    the new entries cannot be attributed. The caller MUST treat None as "poll and
    retry", and finally as an UNREADABLE result (exit 2), never as success.

    #257: without `expected_rows` the picker took the NEWEST new row, so a foreign
    import writing to the same Log within seconds (two automations in one app
    instance, or two instances) was read as this run's result — its 'Spracované: 1'
    booked our 35-row pairings import as a failure and froze uploaded_pairings.json.
    #196 is the same defect seen from the other side (a stale 'processed=1/failed=1'
    reported while the import had really processed 260 rows).
    #23: the entry-shape regex below also matches a HARD error row ('Chyba | Číslo
    riadku: N - …', no Spracované summary at all), so an aborted import can never
    fall through to an OLDER run's summary row.
    """
    entries = [t for t in (row_texts or []) if _RESULT_ROW_RE.search(t or "")]
    if not entries:
        return None
    candidates = _new_entries(entries, baseline)
    if not candidates:
        return None
    if expected_rows is None:
        return candidates[0]        # unattributed read (e.g. the baseline capture)
    matches = [c for c in candidates
               if parse_import_log(c).get("processed") == expected_rows]
    if len(matches) == 1:
        return matches[0]
    if matches:
        return None                 # two same-sized imports — genuinely ambiguous
    if len(candidates) == 1 and parse_import_log(candidates[0]).get("error_detail"):
        # a hard abort carries no 'Spracované' at all; it is ours only when it is
        # the single new entry (nothing else could have written it)
        return candidates[0]
    return None


def result_exit_code(parsed) -> int:
    """Process exit code for a parsed import result — never report a failed or
    unreadable import as success. processed=None (Log unreadable) → 2; any reported
    failures → 2; only a clean processed-count with no failures → 0."""
    if not parsed or parsed.get("processed") is None:
        return 2
    if parsed.get("failed"):
        return 2
    return 0


def chunk_outcome(rc, parsed, rows_sent) -> str:
    """Classify ONE imported chunk from the import script's exit code + the counts
    it printed. Returns:

      'ok'      — clean run (rc 0).
      'partial' — Shoptet PROCESSED every row we sent ('Spracované' == rows_sent)
                  but rejected some of them ('Zlyhanie variantov: N', N < rows_sent).
                  The rest genuinely landed in the eshop, so this must NOT abort the
                  batch and must NOT be booked as "0 rows imported".
      'failed'  — anything else: unreadable result, a hard Shoptet abort with no
                  summary, a timeout, fewer rows processed than we sent, or every
                  row failing. Nothing may be assumed to have landed.

    #257: 'Spracované: 35. Upravené: 31. Zlyhanie variantov: 2.' was treated as a
    hard failure, so 31 genuinely written rows were discarded, the remaining chunks
    never ran, and uploaded_pairings.json froze on 2026-07-22.

    NOTE: the Shoptet log reports AGGREGATE counts only — it never says WHICH rows
    failed. 'partial' therefore means "some of these rows landed, we cannot tell
    which"; proving a specific row landed needs the eshop's own catalog export (see
    webreview/app.py::_export_internal_notes)."""
    if rc == 0:
        return "ok"
    parsed = parsed or {}
    processed, failed = parsed.get("processed"), parsed.get("failed") or 0
    if (rows_sent and processed == rows_sent and 0 < failed < rows_sent
            and not parsed.get("error_detail")):
        return "partial"
    return "failed"
