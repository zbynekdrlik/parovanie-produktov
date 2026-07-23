"""our_images dead-URL detector (#135): pure logic, no network.

Chrome logs "Failed to load resource" to the console whenever the browser
directly requests a genuinely-dead image URL — the #50/#74 `onerror`
handler hides the BROKEN placeholder visually but cannot suppress that
console log (it fires before any JS callback runs, and can't be caught from
JS at all). The only real fix is to never hand the browser a dead URL in
the first place: periodically HTTP-HEAD-check every review card's
``our_images`` URL (our own cdn.myshoptet.com product photos) and drop the
ones that keep failing from what ``/api/products`` serves.

This module holds the checker-free core so it is unit-testable with a fake
HEAD result (no real network): which URLs are due for a (re)check, how one
check's outcome updates a persistent per-URL cache, and which images
survive into the serve-time view of the review products. webreview/app.py
wires this to ``requests.head`` (+ GET-Range fallback) and the
data/out/image_health.json store.
"""
from __future__ import annotations

from datetime import datetime, timedelta

# A single failed HEAD can be a transient blip (a supplier/CDN hiccup, a
# momentary network error) — only drop an image after this many CONSECUTIVE
# failures, so a transient outage never wipes a genuinely-good image out of
# a review card.
DEAD_AFTER_FAILS = 2

# Re-check a URL this often even when it was last seen healthy — catches an
# image that goes dead AFTER it was confirmed good, without re-HEADing every
# URL on every run. A URL whose LAST result was a failure is always
# re-checked regardless of this window (see needs_check) so a transient
# failure gets its confirming/clearing recheck promptly, not a day later.
FRESH_HOURS = 24.0


def collect_image_urls(products: list[dict]) -> list[str]:
    """Every non-empty ``our_images`` URL across ``products``, deduped, in
    first-seen order (deterministic — stable iteration for the checker)."""
    seen: list[str] = []
    seen_set: set[str] = set()
    for p in products:
        for u in p.get("our_images") or []:
            if u and u not in seen_set:
                seen_set.add(u)
                seen.append(u)
    return seen


def needs_check(url: str, cache: dict, now: datetime,
                 fresh_hours: float = FRESH_HOURS) -> bool:
    """True when ``url`` should be (re)HEAD-checked this run: never checked
    before, its last result was a FAILURE (always retried, so a blip gets a
    prompt confirm/clear check), its ``checked_at`` doesn't parse, or its
    last successful check is older than ``fresh_hours`` (periodic
    revalidation of a currently-healthy URL)."""
    entry = cache.get(url)
    if not entry:
        return True
    if not entry.get("ok"):
        return True
    ts = entry.get("checked_at") or ""
    try:
        checked = datetime.fromisoformat(ts)
    except ValueError:
        return True
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=now.tzinfo)
    return (now - checked) >= timedelta(hours=fresh_hours)


def record_result(cache: dict, url: str, ok: bool, now: datetime) -> dict:
    """Update ``cache[url]`` in place with one check's outcome, tracking a
    streak of CONSECUTIVE failures (a success resets it to 0 immediately —
    a URL only counts as dead while it is failing RIGHT NOW). Returns the
    (mutated) entry."""
    entry = cache.setdefault(url, {"ok": True, "fails": 0, "checked_at": ""})
    entry["ok"] = bool(ok)
    entry["checked_at"] = now.isoformat(timespec="seconds")
    entry["fails"] = 0 if ok else entry.get("fails", 0) + 1
    return entry


def is_dead(entry: dict | None, threshold: int = DEAD_AFTER_FAILS) -> bool:
    """True once a URL has failed ``threshold``-or-more CONSECUTIVE checks.
    A URL never checked (no entry), or checked but not yet at the
    threshold, is NOT dead — absence of data or a single blip must never
    hide a working image."""
    return bool(entry) and (entry.get("fails") or 0) >= threshold


def filter_dead(urls: list[str], cache: dict,
                 threshold: int = DEAD_AFTER_FAILS) -> list[str]:
    """``urls`` with dead ones (per ``is_dead``) dropped, order preserved."""
    return [u for u in urls if not is_dead(cache.get(u), threshold)]


def clean_products(products: list[dict], cache: dict,
                    threshold: int = DEAD_AFTER_FAILS) -> tuple[list[dict], int]:
    """Serve-time view of ``products`` with dead ``our_images`` URLs
    removed. Never mutates ``products`` or any item in it — an item whose
    images changed is returned as a SHALLOW COPY (mirrors
    webreview.app._grube_de_display); an item with nothing dropped is
    returned as the SAME object, so an unaffected product is unchanged by
    identity too. Returns (cleaned_products, total_images_dropped)."""
    out: list[dict] = []
    dropped = 0
    for p in products:
        imgs = p.get("our_images")
        if not imgs:
            out.append(p)
            continue
        cleaned = filter_dead(imgs, cache, threshold)
        if len(cleaned) == len(imgs):
            out.append(p)
        else:
            dropped += len(imgs) - len(cleaned)
            q = dict(p)
            q["our_images"] = cleaned
            out.append(q)
    return out, dropped
