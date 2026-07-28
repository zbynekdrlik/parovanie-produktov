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

    Copy guarantee: the returned table never shares a mutable object with
    `pending` down to two levels — each entry, its `fields` dict, and every
    individual field dict are fresh copies — so a caller holding an older
    snapshot is safe even if something later mutates a field dict in place;
    values nested deeper inside a field (e.g. its `credit` dict) may still
    be shared.
    """
    cols = [c.strip() for c in header.split(";")]
    out = {}
    for k, v in pending.items():
        entry = dict(v)
        entry["fields"] = {fk: dict(fv) for fk, fv in (v.get("fields") or {}).items()}
        out[k] = entry
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


def build_import(pending, absent_codes=frozenset()):
    """Build ONE Shoptet import out of the whole table.

    Shoptet's import takes every column at once and treats an empty cell as
    "leave this field alone", so all queued fields for all codes ride in a
    single file — one login, one log read-back per cycle.

    A code the catalogue does not carry can never import (Shoptet rejects that
    row on every run, forever — #270), so it is held back and REPORTED, never
    dropped: it stays in the table with a reason and reappears in the import the
    moment the code shows up in the catalogue.
    """
    blocked = {c: "not-in-catalog" for c in sorted(pending) if c in absent_codes}
    sendable = [c for c in sorted(pending) if c not in blocked]
    cols = sorted({f for c in sendable for f in (pending[c].get("fields") or {})})
    header = ";".join([*KEY_COLUMNS, *cols])
    rows = []
    for code in sendable:
        entry = pending[code]
        fields = entry.get("fields") or {}
        if not fields:
            continue
        rows.append([code, entry.get("pairCode") or ""]
                    + [(fields.get(col) or {}).get("value", "") for col in cols])
    return header, rows, blocked


def settle(pending, success_codes, blocked, now=""):
    """Drop what Shoptet confirmed, keep the rest, and hand back the credits.

    A producer's uploaded-store is written HERE — after the import log confirmed
    the rows — never by the producer before the fact. That is the #257 lesson in
    one place: the app used to mark work as uploaded on its own say-so.

    `pending` must be the WHOLE table, never a slice — group membership is
    computed from the table passed in, so calling this over a subset would
    credit a group off only part of its codes (the #257 bug in a new shape).

    When a group's queued codes carry different credit values, the value that
    wins depends on dict iteration order over `pending` — callers must queue
    the same value for every code within one group.

    Copy guarantee (same as `queue_fields`): the returned table never shares
    a mutable object with `pending` down to two levels — each entry, its
    `fields` dict, and every individual field dict are fresh copies; a field's
    own `credit` dict may still be shared, since `settle` only ever reads it.
    """
    out, credits = {}, {}
    groups = {}
    for code, entry in pending.items():
        for field in (entry.get("fields") or {}).values():
            c = field.get("credit")
            if not c:
                continue
            g = groups.setdefault((c["store"], c["group"]),
                                  {"value": c["value"], "codes": set()})
            g["codes"].add(code)
    for (store, group), g in groups.items():
        if g["codes"] <= set(success_codes):
            credits.setdefault(store, {})[group] = g["value"]
    for code, entry in pending.items():
        if code in success_codes:
            continue
        e = dict(entry)
        e["fields"] = {fk: dict(fv) for fk, fv in (entry.get("fields") or {}).items()}
        if code in blocked:
            prev = e.get("blocked") or {}
            e["blocked"] = {"reason": blocked[code],
                            "since": prev.get("since") or now}
            e["blocked_runs"] = int(e.get("blocked_runs") or 0) + 1
        else:
            e["blocked"] = None
            e["blocked_runs"] = 0
        e["attempts"] = int(e.get("attempts") or 0) + 1
        out[code] = e
    return out, credits


def stale_blocked(pending, min_attempts=3):
    """Codes stuck blocked for at least `min_attempts` CONSECUTIVE runs — the
    silent-death guard for the table itself: a held-back row must never wait
    forever unseen.

    Reads `blocked_runs` (resets to 0 the moment a code clears), never
    `attempts` (which counts every unconfirmed run, blocked or not, so a code
    that spent runs merely unconfirmed and only just became blocked must not
    read as long-stuck).
    """
    return sorted(c for c, e in pending.items()
                  if (e.get("blocked") and int(e.get("blocked_runs") or 0) >= min_attempts))
