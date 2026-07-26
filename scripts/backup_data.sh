#!/usr/bin/env bash
# Periodic backup of the precious, gitignored state of the review/ordering app.
#
# Two kinds of file belong here, and BOTH were incomplete until #264:
#   * the manager's own work, which exists nowhere else — decisions.json,
#     review_data.json, the per-line/per-product to-order stores, the exhibitions
#     and the account list;
#   * the evidence of WHAT WE ALREADY SENT — orders_reminder.json,
#     posta_uncollected.json (which customer got which reminder) and
#     automations.json (what ran and when). Lose one of those and the automation
#     no longer knows a mail went out, so the customer gets it a second time —
#     and without history nobody can even check afterwards whether that happened.
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

FILES="decisions.json review_data.json ordered_items.json uploaded_pairings.json
       orders_reminder.json posta_uncollected.json automations.json
       order_pairings.json supplier_assignments.json variant_links.json
       waiting_items.json instock_items.json unavailable_items.json
       order_comments.json nedostupne.json vystavy.json users.json
       notes.json ui_labels.json"

for f in $FILES; do
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
