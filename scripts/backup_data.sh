#!/usr/bin/env bash
# Periodic backup of the precious, gitignored state of the review/ordering app:
#   decisions.json       — workers' pairing decisions (the irreplaceable work)
#   review_data.json     — the product list shown in the app
#   ordered_items.json   — the "Na objednanie" tab's ordered state
#   uploaded_pairings.json — nightly pairing-upload incremental state
#
# Content-addressed: a file is only copied when its content changed since the last
# backup (no churn), and the newest ~80 snapshots per file are kept. Safe to run
# every few minutes from a systemd timer. Never deletes the live files.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

OUT="data/out"
BK="data/backups/state"
mkdir -p "$BK"
TS="$(date +%Y%m%d-%H%M%S)"

for f in decisions.json review_data.json ordered_items.json uploaded_pairings.json; do
    src="$OUT/$f"
    [ -s "$src" ] || continue
    sum="$(sha1sum "$src" | cut -c1-12)"
    base="${f%.json}"
    # skip if the most recent backup of this file already has the same content hash
    last="$(ls -t "$BK/${base}_"*".json" 2>/dev/null | head -1)"
    if [ -n "$last" ] && [[ "$last" == *"_$sum.json" ]]; then
        continue
    fi
    cp "$src" "$BK/${base}_${TS}_${sum}.json"
    # keep newest 80 snapshots per file, prune older
    ls -t "$BK/${base}_"*".json" 2>/dev/null | tail -n +81 | xargs -r rm -f
done
