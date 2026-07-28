"""The ONE pending-changes table between the app and Shoptet (#299).

Every write-side automation used to build its own CSV and run its own import —
five logins and five log read-backs per cycle, and the "already uploaded" mark
was written BEFORE Shoptet confirmed anything (the #257 class of bug). Here the
automations only ever QUEUE the field values they want; one hourly drain builds
a single import out of the whole table, and only a CONFIRMED row is credited.

Pure logic: no Flask, no filesystem, no clock. The caller supplies `now`.
"""

KEY_COLUMNS = ("code", "pairCode")


def queue_fields(pending, source, header, rows, credit_group=None,
                  credit_value=None, now=""):
    """Queue `rows` (sequences in `header` order) as per-field entries.

    Returns `(pending, queued)` — a NEW table plus how many field values landed.
    An empty cell queues nothing: in a Shoptet import an empty cell means "leave
    this field alone", so queueing it would be a promise we cannot keep.

    `credit_group[code]` / `credit_value[code]` carry the producer's dedup
    bookkeeping: the drain records `{group: value}` into the producer's
    uploaded-store only once EVERY queued code of that group is confirmed (the
    #49 rule — a group straddling a failed chunk must stay un-credited).
    """
    cols = [c.strip() for c in header.split(";")]
    out = {k: dict(v) for k, v in pending.items()}
    queued = 0
    for r in rows:
        if len(r) < len(cols):
            raise ValueError(
                f"row has {len(r)} cells but header {header!r} needs {len(cols)}")
        cells = dict(zip(cols, r))
        code = (cells.get("code") or "").strip()
        if not code:
            raise ValueError(f"row without a code: {r!r}")
        entry = dict(out.get(code) or {})
        entry.setdefault("pairCode", (cells.get("pairCode") or "").strip())
        entry.setdefault("blocked", None)
        entry.setdefault("attempts", 0)
        fields = dict(entry.get("fields") or {})
        for col in cols:
            if col in KEY_COLUMNS:
                continue
            val = cells.get(col)
            val = "" if val is None else str(val).strip()
            if not val:
                continue
            field = {"value": val, "source": source, "queued_at": now}
            group = (credit_group or {}).get(code)
            if group is not None:
                field["credit"] = {"store": source, "group": group,
                                    "value": (credit_value or {}).get(code, val)}
            fields[col] = field
            queued += 1
        entry["fields"] = fields
        out[code] = entry
    return out, queued
