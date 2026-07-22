"""Re-sync each review product's export-side fields (variant codes, images,
current on/off status) against the CURRENT forestshop export, joined by
(supplier, name). The catalog drifts (products get re-coded / re-stocked) so the
gather-time codes go stale; this keeps the review app + the eventual import in
sync with the live eshop. Preserves candidates / AI verdict / our_url / key.

Run after refreshing data/products.csv. Idempotent.

The indexing/resync logic itself lives in parovanie.export_helpers.resync_current
(#119) — shared with the in-app hourly "Sync zo Shoptetu" automation so the two
never drift apart. This script is just the CLI wrapper: load CSV, load JSON,
call the shared function, save JSON, print the summary.
"""
import csv
import json

from parovanie.config import SUPPLIERS
from parovanie.export_helpers import resync_current

csv.field_size_limit(10**9)
SRC = "data/products.csv"
OUT = "data/out"
# which suppliers the review app covers — derived from the configured set, so
# adding a supplier to config.SUPPLIERS automatically includes it here.
_SUPPLIERS = set(SUPPLIERS)

with open(SRC, encoding="cp1250", errors="replace") as f:
    rows = list(csv.DictReader(f, delimiter=";"))

rd = json.load(open(f"{OUT}/review_data.json", encoding="utf-8"))
counts = resync_current(rows, rd, _SUPPLIERS)
json.dump(rd, open(f"{OUT}/review_data.json", "w", encoding="utf-8"), ensure_ascii=False)
print(f"synced {counts['synced']}, not-found-by-name {counts['stale']}; "
      f"currently OFF (sold-out/hidden) {counts['off']}/{len(rd)}")
