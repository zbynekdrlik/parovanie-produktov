"""The ONE pending-changes table between the app and Shoptet (#299).

Every write-side automation used to build its own CSV and run its own import —
five logins and five log read-backs per cycle, and the "already uploaded" mark
was written BEFORE Shoptet confirmed anything (the #257 class of bug). Here the
automations only ever QUEUE the field values they want; one hourly drain builds
a single import out of the whole table, and only a CONFIRMED row is credited.

Pure logic: no Flask, no filesystem, no clock. The caller supplies `now`.
"""

from datetime import datetime, timezone

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

    #299 opravné kolo 1 review C2 (Critical) — `queued_at` is the time this
    field's CURRENT value was FIRST queued, not the time of the most recent
    call that happened to queue it again. A producer re-queueing the SAME
    value it already queued (parovania_eshop runs daily and, while the
    disabled upload cycle never confirms anything, re-sends its whole backlog
    every run) must NOT push `queued_at` forward — that silently reset the
    disabled-cycle staleness alarm (`_queue_stale_while_disabled_warning`) to
    "just queued" on every producer tick, so a queue that had genuinely been
    waiting for days never crossed the alarm's threshold. Same discipline
    `settle()` already applies to a blocked row's `since` (`prev.get("since")
    or now`): only a genuinely NEW or CHANGED value gets a fresh timestamp.
    """
    cols = [c.strip() for c in header.split(";")]
    out = {}
    for k, v in pending.items():
        entry = dict(v)
        entry["fields"] = {fk: dict(fv) for fk, fv in (v.get("fields") or {}).items()}
        out[k] = entry
    queued = 0
    for r in rows:
        # #299 záverečná recenzia — a row LONGER than the header used to be
        # silently TRUNCATED here: `dict(zip(cols, r))` stops at the shorter of
        # the two, so `[['A', 'P', 'u', 'EXTRA']]` against a 3-column header
        # simply dropped 'EXTRA' with no error, no log line, nothing — the one
        # place in this whole "no write may vanish silently" table where a
        # value could disappear without a trace. Symmetric with the SHORTER
        # case right below it: both directions raise now.
        if len(r) != len(cols):
            raise ValueError(
                f"row has {len(r)} cells but header {header!r} needs {len(cols)}")
        cells = dict(zip(cols, r))
        code = (cells.get("code") or "").strip()
        if not code:
            raise ValueError(f"row without a code: {r!r}")
        existed = code in out
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
            prev_field = fields.get(col)
            # C2 — the field's value is UNCHANGED from what is already sitting in
            # the table → this is a re-queue of the same fact, so it keeps its
            # ORIGINAL `queued_at` (falling back to `now` only if that field
            # somehow has none — never letting a re-queue erase a real timestamp).
            # A genuinely new or DIFFERENT value always gets `now`, same as before.
            same_value = prev_field is not None and prev_field.get("value") == val
            queued_at = (prev_field.get("queued_at") or now) if same_value else now
            field = {"value": val, "source": source, "queued_at": queued_at}
            group = (credit_group or {}).get(code)
            if group is not None:
                field["credit"] = {"store": source, "group": group,
                                    "value": (credit_value or {}).get(code, val)}
            fields[col] = field
            queued += 1
        # #299 záverečná recenzia (Minor 5) — a row whose every cell is empty
        # (all values stripped to "") queues NOTHING (`fields` stays whatever
        # it was, empty for a brand-new code). Without this guard a genuinely
        # new code would still get an entry created here — an empty shell
        # ({"blocked": None, "attempts": 0, "fields": {}}) that then sits in
        # the table FOREVER: `build_import` skips any code with empty
        # `fields`, so it can never be sent, confirmed, or blocked either —
        # nothing downstream ever removes it, while `attempts` grows on every
        # future `settle()` run for a row that was never actually queueing
        # anything. Only refuses to CREATE a new ghost; a code that already
        # existed (real prior work, however it got there) keeps its entry
        # unconditionally — this never deletes anything.
        if not fields and not existed:
            continue
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


def settle(pending, success_codes, blocked, sent_fields, sent_credits, now=""):
    """Drop what Shoptet confirmed, keep the rest, and hand back the credits.

    A producer's uploaded-store is written HERE — after the import log confirmed
    the rows — never by the producer before the fact. That is the #257 lesson in
    one place: the app used to mark work as uploaded on its own say-so.

    `pending` must be the WHOLE table, never a slice — group membership is
    computed from the table passed in, so calling this over a subset would
    credit a group off only part of its codes (the #257 bug in a new shape).

    `sent_fields` is what THIS import actually put on the wire —
    ``{code: {col: value}}``, built by the caller from the `rows` `build_import`
    returned (never re-derived from `pending`). `pending` is loaded again right
    before `settle` runs, so a producer can legitimately queue a NEW value for a
    code WHILE the import is still in flight (#299 review C1). A field only
    leaves the table when its CURRENT value still equals what was actually
    sent — a field that changed mid-flight, or a field that was never sent at
    all, is KEPT and goes out again next hour, and so does the whole code entry
    the moment it has anything left to say (`attempts` still grows for it, same
    as any other unconfirmed code). A credit group fires only when EVERY one of
    its codes is both confirmed AND unchanged since it was sent — a single
    dirty code in the group withholds the whole group's credit, never just its
    own field, because the group's credited value came from ONE of those codes
    and there is no way to tell which one without re-introducing the #257 bug.

    `sent_credits` is the SAME kind of send-time snapshot as `sent_fields`, but
    for the `credit` dict each field carried when the import was built —
    ``{code: {col: {"store": ..., "group": ..., "value": ...}}}``, built by the
    caller from the SAME `pending` read that produced the sent rows (#299
    review N2). A field's plain `value` and its `credit.value` are two
    independent pieces of a producer's queue write — a re-queue during the
    import can leave `value` unchanged (so the field is NOT dirty and IS
    dropped as confirmed) while still writing a NEW `credit.value`. Reading the
    credit from `pending` (the table reloaded right before `settle` runs) would
    then credit a value Shoptet never saw — the #257 bug in the same new shape
    C1 already closed for `value`. So the credited value for a (store, group)
    always prefers the SENT credit for whichever code first establishes that
    group; `pending`'s own (possibly newer) credit is only a fallback for a
    field that was never part of the sent snapshot at all (already excluded
    from credit anyway, since such a field is always dirty).

    When a group's queued codes carry different credit values, the value that
    wins depends on dict iteration order over `pending` — callers must queue
    the same value for every code within one group.

    Copy guarantee (same as `queue_fields`): the returned table never shares
    a mutable object with `pending` down to two levels — each entry, its
    `fields` dict, and every individual field dict are fresh copies; a field's
    own `credit` dict may still be shared, since `settle` only ever reads it.
    """
    out, credits = {}, {}

    # What is left of each success-code's fields once anything confirmed-AND-
    # unchanged is dropped, plus which success codes are "dirty" — carrying at
    # least one field that either was never sent or has since moved on.
    remaining_fields, dirty = {}, set()
    for code in success_codes:
        fields = (pending.get(code) or {}).get("fields") or {}
        sent = sent_fields.get(code) or {}
        keep = {}
        for col, field in fields.items():
            if col in sent and field.get("value") == sent[col]:
                continue
            keep[col] = field
            dirty.add(code)
        remaining_fields[code] = keep

    groups = {}
    for code, entry in pending.items():
        for col, field in (entry.get("fields") or {}).items():
            c = field.get("credit")
            if not c:
                continue
            sent_c = (sent_credits.get(code) or {}).get(col)
            value = sent_c["value"] if sent_c else c["value"]
            g = groups.setdefault((c["store"], c["group"]),
                                  {"value": value, "codes": set()})
            g["codes"].add(code)
    for (store, group), g in groups.items():
        if g["codes"] <= set(success_codes) and not (g["codes"] & dirty):
            credits.setdefault(store, {})[group] = g["value"]

    for code, entry in pending.items():
        if code in success_codes:
            keep = remaining_fields.get(code) or {}
            if not keep:
                continue                              # fully confirmed, unchanged
            e = dict(entry)
            e["fields"] = {fk: dict(fv) for fk, fv in keep.items()}
            e["blocked"] = None
            e["blocked_runs"] = 0
            e["attempts"] = int(e.get("attempts") or 0) + 1
            out[code] = e
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


def stale_unconfirmed(pending, min_attempts=24):
    """Codes that have been queued for at least `min_attempts` CONSECUTIVE drain
    runs without ever landing a fully clean confirmation, and are not already
    `blocked` for a different, self-explaining reason (`not-in-catalog` already
    has its own escape via `stale_blocked`).

    #299 záverečná recenzia — Important I1 (bounded fallback) / the deferred
    "attempts has no upper bound and no consumer" minor. The combined import
    proves confirmation only from the Shoptet import LOG's `success_codes` (a
    whole chunk cleanly accepted) — a chunk Shoptet PARTIALLY accepts never
    reveals WHICH of its rows actually landed (`_import_rows_chunked`'s own
    docstring), so every code sharing that chunk stays unconfirmed and keeps
    re-riding the SAME chunk boundary (`sorted(pending)`, deterministic) hour
    after hour, forever, together with whichever code is the real cause. The
    caller excludes what this returns from the NEXT import's `rows` — so those
    codes stop crowding a bad chunk with healthy ones, and the healthy 299 (or
    however many) get to succeed in a chunk of their own. This is deliberately
    NOT self-healing: once a code is reported here it stays excluded (and its
    `attempts` keeps climbing) until a human resolves it — the same "loud and
    bounded, not silent and infinite" trade-off `stale_blocked` already makes
    for `not-in-catalog`.

    Reads `attempts` (grows on EVERY unconfirmed run, blocked or not — see
    `settle`), never `blocked_runs` (that already has its own consumer,
    `stale_blocked`, and starts over at 0 the moment a code clears `blocked`).
    A code with no `fields` left has nothing to hold back."""
    return sorted(c for c, e in pending.items()
                  if (e.get("fields") and not e.get("blocked")
                      and int(e.get("attempts") or 0) >= min_attempts))


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


def stale_fields(pending, now, max_age_s):
    """{code: [col, ...]} for every QUEUED field whose OWN `queued_at` is at
    least `max_age_s` old, or whose `queued_at` cannot be trusted at all
    (missing, unparsable, or in the FUTURE — a clock step / NTP jump / hand
    edit). The caller excludes these from what it actually SENDS this cycle —
    #299 záverečná recenzia I5.

    Why this exists: only the moment a field is QUEUED had an age gate at all
    (the producer's own export-freshness check) — nothing re-checks it at
    SEND time. `restock_skladom`/`stock_skladom` have no dedup and re-queue
    their whole candidate list every day, so while the hourly drain is
    disabled a "switch to Skladom" decision sits in the table, re-queued
    fresh-looking every single day (`queue_fields`'s own C2 discipline keeps
    `queued_at` at the FIRST time a value was queued, not the most recent
    re-queue of the SAME value) — and once the drain comes back on it would
    ship a week-old restock decision straight to the live eshop, long after
    the product may have sold out again. An unparsable timestamp proves
    NEITHER freshness NOR staleness, so it is refused exactly like a
    provably-old one — the same fail-closed direction the supplier
    write-back already takes for an untrustworthy catalogue export.

    A refused field is NEVER dropped from `pending` — the caller only leaves
    it out of `rows`/`sent_fields` this cycle. The table itself, `attempts`,
    and `queued_at` are untouched: the field goes right on waiting, and the
    producer's OWN next run queues a fresh decision (fresh `queued_at`,
    `queue_fields`' C2 discipline) that sends normally.

    `now` is a `datetime` (aware or naive — naive is read as UTC, the same
    convention `_queue_stale_while_disabled_warning` in webreview/app.py
    already uses for the SAME `queued_at` field, so the two readers can never
    disagree about what "now" means). Pure logic: the caller supplies `now`,
    never a bare `datetime.now()` call inside this module."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    out = {}
    for code, entry in pending.items():
        stale_cols = []
        for col, field in (entry.get("fields") or {}).items():
            raw = field.get("queued_at") if isinstance(field, dict) else None
            age_s = None
            if raw:
                try:
                    dt = datetime.fromisoformat(raw)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    age_s = (now - dt).total_seconds()
                except (ValueError, TypeError):
                    age_s = None
            if age_s is None or age_s < 0 or age_s >= max_age_s:
                stale_cols.append(col)
        if stale_cols:
            out[code] = sorted(stale_cols)
    return out
