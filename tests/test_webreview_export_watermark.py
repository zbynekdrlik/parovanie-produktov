"""#277 — the export-plausibility floor is a RATIO of what the catalogue really is,
not a fixed 1000.

`EXPORT_MIN_CODES = 1000` against a ~14 000-code catalogue left a wide band of false
confidence: a truncated export carrying 1 200 codes passed, and the app then declared
that the eshop „does not have" the other ~12 800 — holding their rows back and
accusing them on the automation card (measured on dev @ 3cdad3c: 12 866 such codes).

The floor is now `max(EXPORT_MIN_CODES, EXPORT_WATERMARK_RATIO * watermark)`, where the
watermark is the largest code count seen in the last EXPORT_WATERMARK_WINDOW_DAYS,
kept in its OWN persisted store. `len(CODE2PAIR)` cannot serve as the reference — the
same broken export rebuilds it — so the store is the reference and a bad export can
only ever fail to raise it, never lower it (the buckets fold with max()).

Self-healing is by TIME, and both halves are bounded:
  • the download guard protects the on-disk export for at most EXPORT_MAX_AGE_S;
  • the ratio floor follows a genuinely smaller catalogue once the old observations
    age out of the window.
Repetition cannot speed either up on purpose: a PERSISTENTLY broken feed produces
exactly the same repeated reading as a genuine shrink, so „N agreeing observations"
would accept the very thing this gate exists to reject.
"""
import json
import os
import sys
from datetime import timedelta, timezone, datetime

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webreview"))
import app as webapp  # noqa: E402

CATALOGUE = 14066          # the real forestshop variant-code count
TRUNCATED = 1200           # what a cut-off download carries — comfortably over 1000


def _today():
    return datetime.now(timezone.utc).date()


def _day(offset):
    return (_today() + timedelta(days=offset)).isoformat()


def _export(path, codes):
    """A real on-disk cp1250 export carrying `codes` variant codes."""
    with open(path, "w", encoding="cp1250", newline="") as f:
        f.write("code;pairCode;internalNote;supplier\r\n")
        for i in range(codes):
            f.write(f"{i}/A;{i};;\r\n")
    return str(path)


@pytest.fixture
def iso(tmp_path, monkeypatch):
    """OUT (the watermark store lives there) + SRC, both in tmp."""
    monkeypatch.setattr(webapp, "OUT", str(tmp_path))
    monkeypatch.setattr(webapp, "SRC", str(tmp_path / "products.csv"))
    return tmp_path


def _seed(tmp_path, days):
    (tmp_path / "export_watermark.json").write_text(
        json.dumps({"days": days}), encoding="utf-8")


# ── the floor itself ──────────────────────────────────────────────────────────
def test_the_first_ever_run_falls_back_to_the_absolute_floor(iso):
    """A fresh deploy has no watermark yet. It must behave EXACTLY as before — an
    empty store may never block the very first sync (that would be a deadlock out of
    the box, and there is nothing yet to compare against)."""
    assert webapp._export_watermark() == 0
    assert webapp._export_min_codes() == webapp.EXPORT_MIN_CODES


def test_the_floor_is_a_ratio_of_the_catalogue_once_it_is_known(iso, tmp_path):
    _seed(tmp_path, {_day(0): CATALOGUE})
    assert webapp._export_watermark() == CATALOGUE
    assert webapp._export_min_codes() == int(webapp.EXPORT_WATERMARK_RATIO * CATALOGUE)
    assert webapp._export_min_codes() > TRUNCATED     # the band of false confidence is gone


def test_a_truncated_export_can_never_lower_the_watermark(iso, tmp_path):
    """The whole point of a HIGH-water mark: the broken feed that we are trying to
    detect must not be able to redefine what „normal" is within the window."""
    _seed(tmp_path, {_day(0): CATALOGUE})
    webapp._export_watermark_observe(TRUNCATED)
    assert webapp._export_watermark() == CATALOGUE


def test_a_watermark_dated_in_the_future_cannot_freeze_the_gate_forever(iso, tmp_path):
    """Bounded from BOTH sides. A corrupt/clock-skewed future date compared only
    against the lower cutoff would stay inside the window for ever, so a stale high
    reading would keep the floor high and the write-back blocked with no way out —
    the same class of bug as the `at`/`claimed_at` ones the playbook records."""
    _seed(tmp_path, {_day(+400): CATALOGUE, _day(0): 6000})
    assert webapp._export_watermark() == 6000


def test_a_corrupt_watermark_store_degrades_to_the_absolute_floor(iso, tmp_path):
    """Derived state, not the manager's work: it rebuilds itself from the next sync.
    Falling back to the pre-#277 absolute floor is a known-safe state; raising here
    would take the nightly push down over a cache file."""
    for junk in ('not json at all', '[]', '{"days": null}', '{"days": {"x": "big"}}'):
        (tmp_path / "export_watermark.json").write_text(junk, encoding="utf-8")
        assert webapp._export_watermark() == 0
        assert webapp._export_min_codes() == webapp.EXPORT_MIN_CODES


# ── the two gates that consume the floor ──────────────────────────────────────
def test_a_truncated_export_is_no_longer_trusted_for_the_absent_verdict(iso, tmp_path):
    """RED before the fix: 1 200 > EXPORT_MIN_CODES, so every one of the ~12 800 codes
    the eshop really has was declared absent and its row held back + reported."""
    _seed(tmp_path, {_day(0): CATALOGUE})
    _export(tmp_path / "products.csv", TRUNCATED)
    rows = [[f"{i}/A", "P", "https://x"] for i in range(CATALOGUE)]

    v = webapp._export_row_verdicts(rows)

    assert v["absent"] == set() and v["confirmed"] == set()

    # …and the very same export IS trusted once it carries a plausible share again
    _export(tmp_path / "products.csv", CATALOGUE - 1)
    v2 = webapp._export_row_verdicts([["nope/M", "P", "https://x"]])
    assert v2["absent"] == {"nope/M"}


def test_a_genuinely_smaller_catalogue_is_accepted_once_the_window_ages_out(iso, tmp_path):
    """The gate must not deadlock the automation for ever. A real cull (14 066 → 6 000)
    is accepted with NO human action once the old readings leave the window."""
    w = webapp.EXPORT_WATERMARK_WINDOW_DAYS
    # still inside the window: the old catalogue size rules, 6 000 is refused
    _seed(tmp_path, {_day(-(w - 1)): CATALOGUE, _day(0): 6000})
    _export(tmp_path / "products.csv", 6000)
    assert webapp._export_row_verdicts([["nope/M", "P", "x"]])["absent"] == set()

    # one day later the 14 066 reading has aged out and the new reality is the reference
    _seed(tmp_path, {_day(-w): CATALOGUE, _day(0): 6000})
    assert webapp._export_watermark() == 6000
    assert webapp._export_row_verdicts([["nope/M", "P", "x"]])["absent"] == {"nope/M"}


def test_observing_prunes_the_window_so_the_store_stays_bounded(iso, tmp_path):
    w = webapp.EXPORT_WATERMARK_WINDOW_DAYS
    _seed(tmp_path, {_day(-(w + 5)): CATALOGUE, _day(-1): 6000})

    webapp._export_watermark_observe(6100)

    days = json.loads((tmp_path / "export_watermark.json").read_text())["days"]
    assert _day(-(w + 5)) not in days                 # pruned, not kept for ever
    assert days[_day(0)] == 6100
    assert len(days) <= w


def test_the_same_day_keeps_the_LARGEST_reading(iso, tmp_path):
    """Hourly syncs write into one bucket per day; one bad hour must not overwrite the
    good reading taken an hour earlier."""
    webapp._export_watermark_observe(CATALOGUE)
    webapp._export_watermark_observe(TRUNCATED)
    assert webapp._export_watermark() == CATALOGUE


def test_the_hourly_sync_records_the_fresh_catalogue_size(iso, tmp_path, monkeypatch):
    """The ONE observation point: `run_shoptet_sync`, from the index it has just
    rebuilt out of freshly downloaded bytes. No write on any read path."""
    data = tmp_path / "review_data.json"
    data.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(webapp, "DATA", str(data))
    monkeypatch.setattr(webapp, "ORDERS_CACHE", str(tmp_path / "orders_cache.csv"))
    monkeypatch.setattr(webapp, "CUSTOMERS_CACHE", str(tmp_path / "customers_cache.csv"))
    monkeypatch.setattr(webapp, "PRODUCTS", [])
    export = ("code;pairCode\r\n" + "".join(f"{i}/A;{i}\r\n" for i in range(2500)))
    monkeypatch.setattr(webapp, "_fetch_orders_csv", lambda: b"code;date\r\n")
    monkeypatch.setattr(webapp, "_fetch_export_csv", lambda: export.encode("cp1250"))
    monkeypatch.setattr(webapp, "_fetch_customers_csv", lambda: b"guid;email\r\n")

    webapp.run_shoptet_sync()

    assert webapp._export_watermark() == 2500
    assert webapp._export_min_codes() == 1250


# ── the download-side sanity check ────────────────────────────────────────────
def _arm_download(monkeypatch, body):
    monkeypatch.setattr(webapp, "_cred", lambda k: "https://x.test/e.csv?hash=SECRET")

    class R:
        content = body
        def raise_for_status(self):
            pass
    monkeypatch.setattr(webapp.requests, "get", lambda *a, **kw: R())


def test_a_truncated_download_never_replaces_a_fresh_export_on_disk(iso, tmp_path, monkeypatch):
    """`_fetch_export_csv` only checked `if not r.content`, so a cut-off download was
    saved without complaint. It must raise BEFORE the atomic swap, so the good bytes
    on disk survive and the hourly sync goes red instead of silently degrading."""
    src = _export(tmp_path / "products.csv", CATALOGUE)
    before = open(src, "rb").read()
    _arm_download(monkeypatch, b"code;pairCode\r\n1/A;1\r\n")

    with pytest.raises(RuntimeError) as e:
        webapp._fetch_export_csv()

    assert "SECRET" not in str(e.value)               # the partner hash never leaks
    assert open(src, "rb").read() == before


def test_the_download_guard_lets_go_once_the_bytes_it_protects_are_stale(
        iso, tmp_path, monkeypatch):
    """Bounded by construction: the guard exists to protect an export that is still
    USABLE. Past EXPORT_MAX_AGE_S the on-disk copy is refused by
    `_export_row_verdicts` anyway, so holding on to it could only deadlock a genuine
    catalogue shrink — this is what keeps the recovery path open."""
    _export(tmp_path / "products.csv", CATALOGUE)
    monkeypatch.setattr(webapp, "_export_age_s", lambda: webapp.EXPORT_MAX_AGE_S + 60)
    small = b"code;pairCode\r\n1/A;1\r\n"
    _arm_download(monkeypatch, small)

    assert webapp._fetch_export_csv() == small


def test_the_download_guard_is_silent_on_a_first_ever_sync(iso, monkeypatch):
    """No export on disk yet — there is nothing to compare against and nothing to
    protect, so the first download must always be accepted."""
    small = b"code;pairCode\r\n1/A;1\r\n"
    _arm_download(monkeypatch, small)
    assert webapp._fetch_export_csv() == small


def test_a_normal_sized_download_still_passes(iso, tmp_path, monkeypatch):
    _export(tmp_path / "products.csv", CATALOGUE)
    body = ("code;pairCode\r\n"
            + "".join(f"{i}/A;{i}\r\n" for i in range(CATALOGUE))).encode("cp1250")
    _arm_download(monkeypatch, body)
    assert webapp._fetch_export_csv() == body


def test_an_empty_download_is_still_refused_first(iso, tmp_path, monkeypatch):
    _export(tmp_path / "products.csv", CATALOGUE)
    _arm_download(monkeypatch, b"")
    with pytest.raises(RuntimeError, match="prázdny"):
        webapp._fetch_export_csv()


# ── #280 review, item 4: the watermark had no UPPER bound ─────────────────────
def test_one_absurd_reading_cannot_block_the_healthy_export(iso, monkeypatch):
    """Measured before the fix:

        after ONE observation 50000: watermark=50000 floor=25000
        → a real 14066-code export is REFUSED for a full 7 days, with no escape

    A single implausible reading (a duplicated feed, a parse anomaly) must not raise the
    floor above reality. The observation is therefore CLAMPED to a bounded multiple of
    what we already believe: a genuine catalogue growth is still absorbed — it just takes
    a few hourly syncs instead of one — while one spike cannot outrun it."""
    webapp._export_watermark_observe(14066)
    assert webapp._export_watermark() == 14066

    webapp._export_watermark_observe(50000)

    wm = webapp._export_watermark()
    assert wm < 50000, "an absurd single reading was adopted whole"
    # the real catalogue still clears the floor it produces — the whole point
    assert webapp._export_min_codes() <= 14066


def test_the_clamp_still_lets_a_genuine_catalogue_growth_through(iso):
    """Bounded, not blocked: repeated honest readings converge on the new size, so a shop
    that really doubles is fully reflected within a few hourly syncs — no human action."""
    webapp._export_watermark_observe(14066)
    for _ in range(6):                       # six hourly syncs
        webapp._export_watermark_observe(28000)
    assert webapp._export_watermark() == 28000


def test_the_first_ever_reading_is_never_clamped(iso):
    """A fresh deploy (or a window that aged out entirely) has nothing to clamp against —
    it must adopt what it sees, or the very first sync would understate the catalogue."""
    webapp._export_watermark_observe(14066)
    assert webapp._export_watermark() == 14066
