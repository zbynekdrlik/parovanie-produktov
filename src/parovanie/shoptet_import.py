"""Pure logic for the Shoptet auto-import script (no browser import).

Credential loading, import-CSV pre-flight, and result-log parsing. The browser
driving lives in scripts/shoptet_import.py. See docs spec
2026-06-25-shoptet-auto-import-design.md and .claude/skills/shoptet.
"""
import csv
import os

csv.field_size_limit(10**9)

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
