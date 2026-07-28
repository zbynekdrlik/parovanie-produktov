"""Web na ručnú kontrolu párovania: vľavo náš produkt, vpravo dodávateľ,
fajka/krížik (matched) alebo ručný výber/URL (unmatched). Rozhodnutia sa
ukladajú do data/out/decisions.json.

Run: PYTHONPATH=src .venv/bin/python webreview/app.py   (počúva na 0.0.0.0:8799)
"""
from __future__ import annotations
import collections
import contextlib
import csv
import fcntl
import hmac
import io
import json
import logging
import os
import re
import hashlib
import secrets
import signal
import smtplib
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from datetime import date, datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.utils import formataddr, make_msgid
from urllib.parse import quote, urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from flask import (Flask, Response, jsonify, redirect, render_template, request,
                   send_from_directory, session)
from werkzeug.security import check_password_hash, generate_password_hash

from parovanie import (
    __version__, config, image_health, import_builder, nedostupne, orders_reminder,
    posta_uncollected, restock_skladom, riziko_vypadku, shoptet_outbox, stock_skladom,
    supplier_stock, vystavy_imap, writer)
from parovanie.automation_runner import (
    Automation, AutomationRunner, AutomationStateCorrupt)
from parovanie.catalog_index import (
    build_catalog_index, build_promoted_entry, search_catalog, supplier_from_url)
from parovanie.export_helpers import current_of, norm_status, resync_current
from parovanie.shoptet_import import chunk_outcome, parse_result_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Data dir is env-overridable so tests/E2E can boot the app against a fixture.
OUT = os.environ.get("WEBREVIEW_OUT") or os.path.join(ROOT, "data", "out")


class _StorePath(os.PathLike):
    """A file inside OUT whose path is resolved on EVERY use — never frozen.

    #261: these used to be plain `os.path.join(OUT, "x.json")` constants, computed
    once at IMPORT time. Repointing `OUT` (a test helper, a fixture server) therefore
    redirected NOTHING, and on 2026-07-26 a helper that patched `OUT` but not the
    frozen `DECISIONS` wrote a fixture over the manager's live decisions.json —
    all 2831 review decisions gone. Deriving from the CURRENT `OUT` at call time
    makes `OUT` the single knob it always looked like.

    It stays usable exactly like the string it replaced: `open()`, `os.path.*` and
    `os.replace()` take the PathLike directly, and `__add__`/`__str__` keep the
    `path + ".tmp"` / `"%s" % path` idioms working, so no call site (here or in
    `automation_runner`) has to know the path is lazy. Patching a single store with
    a plain `str` (what most tests do) keeps working too."""

    __slots__ = ("_name",)

    def __init__(self, name: str) -> None:
        self._name = name

    def __fspath__(self) -> str:
        return os.path.join(OUT, self._name)

    def __str__(self) -> str:
        return self.__fspath__()

    def __repr__(self) -> str:
        return f"<store {self._name} → {self.__fspath__()}>"

    def __add__(self, other: str) -> str:
        return self.__fspath__() + other

    def __radd__(self, other: str) -> str:
        return other + self.__fspath__()

    def __eq__(self, other) -> bool:
        if isinstance(other, (str, os.PathLike)):
            return self.__fspath__() == os.fspath(other)
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.__fspath__())


def _store(name: str) -> _StorePath:
    """Declare a store file under OUT. ALWAYS use this for a new store — a plain
    `os.path.join(OUT, ...)` constant re-introduces the #261 freeze (and
    `test_no_store_path_is_frozen_at_import` will fail on it)."""
    return _StorePath(name)


class StoreWipeRefused(RuntimeError):
    """A write that would replace a populated store with an empty one — refused.

    The manager's stores only ever lose entries ONE at a time (an undo, a toggle
    off), so a populated store collapsing to empty in a SINGLE write is never a
    click — it is a bug (a fixture map, a store loaded as `{}` after a failed read)
    about to erase months of work. Refuse loudly instead: raising is visible in the
    log, in the HTTP response and in a test run; a silent `{}` is not. Emptying the
    LAST remaining entry stays allowed — that one really is a click."""


DATA = _store("review_data.json")
DECISIONS = _store("decisions.json")
IMGCACHE = _store("imgcache")
os.makedirs(IMGCACHE, exist_ok=True)

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
app = Flask(__name__, static_folder="static", template_folder="templates")


class StoreLockTimeout(RuntimeError):
    """Another process held the store lock for far too long — better a loud 500 than
    a request that hangs forever, and better than writing anyway (a lost update)."""


class _StoreLock:
    """The app's ONE store lock: `threading.RLock` in-process **plus** an
    `fcntl.flock` on OUT/.store.lock across processes (#264).

    Every read-modify-write in this module already runs inside `with _lock:` — that
    is the app's deliberate coarse-grained design (one lock for all stores; network
    and SMTP calls stay OUTSIDE it). It only ever serialised THREADS, though: the
    atomic tmp+replace keeps a file from being half-written, but it cannot stop two
    PROCESSES from both reading the same map and the second one's write erasing the
    first one's entry. A second instance ran over this data dir for four days (#262),
    so that was live exposure, not theory.

    Re-entrant on purpose: `_atomic_write_json` takes the lock itself (so a write is
    protected even on a path that forgot to), and almost every save is already inside
    a `with _lock:` block — a plain Lock would deadlock on the first click.

    The lock file is resolved from the CURRENT OUT, so each data dir (live service,
    fixture server, test tmp dir) has its own lock and they never contend."""

    def __init__(self, name: str = ".store.lock", timeout: float = None) -> None:
        self.path = _store(name)
        # env-tunable so a test (or an operator debugging a stuck instance) does not have
        # to sit through the full 30 s; the default is unchanged.
        self.timeout = float(os.environ.get("WEBREVIEW_STORE_LOCK_TIMEOUT") or 30.0) \
            if timeout is None else timeout
        self._rlock = threading.RLock()
        self._depth = 0
        self._fd = None

    def _flock(self, timeout=None) -> int:
        p = os.fspath(self.path)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        fd = os.open(p, os.O_RDWR | os.O_CREAT, 0o600)
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return fd
            except OSError:
                if time.monotonic() >= deadline:
                    os.close(fd)
                    log.error("store lock %s still held by another process — refusing "
                              "to write (a second instance? see #262)", p)
                    raise StoreLockTimeout(
                        f"Úložisko {os.path.basename(p)} drží iný proces a nepustilo "
                        "ho — nič sa nezapísalo. Skús to o chvíľu znova; ak to trvá, "
                        "beží pravdepodobne druhá inštancia aplikácie (#262).") from None
                time.sleep(0.02)

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        started = time.monotonic()
        if not self._rlock.acquire(blocking, timeout):
            return False
        if self._depth == 0:
            # the caller's budget governs BOTH halves — a non-blocking acquire that
            # then waits 30s on the file lock would be a trap
            budget = None
            if not blocking:
                budget = 0.0
            elif timeout is not None and timeout >= 0:
                budget = max(0.0, timeout - (time.monotonic() - started))
            try:
                self._fd = self._flock(budget)
            except StoreLockTimeout:
                # `threading.Lock` semantics for a BOUNDED acquire: answer False, never
                # raise. `with _lock:` (unbounded) still raises loudly — that one really
                # is „another process has held the data dir for 30 s", not a poll.
                self._rlock.release()
                if budget is not None:
                    return False
                raise
            except BaseException:
                self._rlock.release()
                raise
        self._depth += 1
        return True

    def release(self) -> None:
        self._depth -= 1
        if self._depth == 0 and self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None
        self._rlock.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc) -> bool:
        self.release()
        return False


_lock = _StoreLock()
_import_lock = threading.Lock()   # one Shoptet import at a time (browser automation)
CRED_PATH = os.environ.get("SHOPTET_CRED") or os.path.join(ROOT, "data", ".shoptet_admin")
IMPORT_SCRIPT = os.path.join(ROOT, "scripts", "shoptet_import.py")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("webreview")
log.info("starting webreview v%s", __version__)


def _measure_store(d) -> dict:
    """Entry counts of one store: `""` = the top-level map, plus one entry per
    top-level key whose value is ITSELF a map/list.

    The nested counts are what makes `protect` real for the two fail-closed dedup
    stores (PR #265 review): `orders_reminder.json` is
    `{"orders": {...}, "red": [...], "stats": {...}, …}` and `posta_uncollected.json`
    is `{"escalation": {...}, "terminal": {...}, …}` — the record of who was already
    e-mailed is the NESTED map, and every real writer keeps the same top-level key
    set, so an outer-dict count can never notice its loss."""
    m = {"": len(d) if isinstance(d, (dict, list)) else 0}
    if isinstance(d, dict):
        for k, v in d.items():
            if isinstance(v, (dict, list)):
                m[k] = len(v)
    return m


def _stored_measures(p) -> tuple:
    """`(measures, corrupt)` for the store as it is on disk RIGHT NOW.

    A MISSING or zero-byte file is a legitimate fresh store: `({}, False)` — nothing
    to lose. Anything present that we cannot parse (a write cut mid-JSON or
    mid-UTF-8, an I/O error) is `corrupt=True`, NOT „0 entries": counting it as zero
    is what let a truncated decisions.json — ~1400 recoverable entries — be replaced
    by a single click, silently and without a backup copy (PR #265 review)."""
    try:
        with open(p, "rb") as f:
            raw = f.read()
    except FileNotFoundError:
        return {}, False
    except OSError:
        return {}, True          # present but unreadable — we cannot know what is at stake
    if not raw.strip():
        return {}, False
    try:
        d = json.loads(raw.decode("utf-8"))
    except ValueError:           # incl. UnicodeDecodeError — a write cut mid-character
        return {}, True
    return _measure_store(d), False


# path → `(the object a read of that store handed back, its counts AT READ TIME)`.
#
# The OBJECT is the receipt (#265 review). The first cut stored only a count, which
# made this a stale-read detector rather than a provenance check: `_load_decisions()`
# runs at import and on every /api/products + /api/orders, so „this process last read
# N and the disk holds N" was permanently true and the guard never fired — the wipe
# that started all this would have been allowed. Identity cannot be faked by an
# unrelated read, and the strong reference kept here also stops `id()` from being
# recycled onto a different object.
#
# NOT one slot per store (PR #265 second review): a single slot made the LAST READ win,
# and every GET re-reads these stores (one /api/orders loads eight of them, the tab
# polls, `_require_login` reads users on every single request) while Flask serves
# threaded. A display read landing between the writer's load and its guard check
# replaced the receipt and the manager's own un-toggle came back as a 503 — 11 of 400
# clicks with three pollers, 317 of 400 with no sleep between reads. Two structures
# carry it instead, and a write is legitimate when EITHER remembers the map:
#
#   _thread_reads — what THIS thread read, per store. A read-modify-write happens in
#       one thread (all 24 `protect=` sites load under `_lock` immediately before
#       saving), and no other thread can evict this one, so the manager's click is
#       refused-proof no matter how hard the tab polls. Dies with the thread.
#   _store_reads  — the last few reads by ANY thread, for the cross-thread shape the
#       single slot used to allow. BEST EFFORT ONLY, and nothing may depend on it
#       (PR #265 third review): it is one 8-slot ring shared by every thread, and every
#       GET appends, so a cross-thread receipt is evicted within microseconds — measured
#       300/300 refusals for a writer relying on it with four pollers running. Nothing in
#       the tree does: all 24 `protect=` sites load and save inside ONE `with _lock:`
#       block, i.e. in one thread, which is what `_thread_reads` above covers. Treat a
#       new cross-thread read-modify-write as UNSUPPORTED — pass `prev=` and read in the
#       writing thread — not as something this ring will carry.
#
# Retention: entries hold the loaded store alive (a review_data read is ~15 MB), so a
# successful write RESETS both rings to the one object it just wrote — every older
# receipt is stale the moment the file changes anyway. BETWEEN writes the rings do hold
# up to 8 loaded copies per path per ring (a decisions.json read is ~1.3 MB, so ~10 MB
# for that path; review_data's is reset by every `_save_products`) — bounded and
# accepted, but not the „at most one per store" the first cut of this comment claimed.
_READ_RING = 8
_store_reads: dict = {}            # path -> deque[(object, measures)], any thread
_thread_reads = threading.local()  # path -> deque[(object, measures)], this thread only

# The manager's REAL data dir — never derived from OUT (which tests repoint), so it
# still names the live files when OUT points at a tmp dir. `realpath`, not `abspath`:
# abspath does not resolve symlinks, so a helper pointing WEBREVIEW_OUT at a LINK to
# data/out walked straight past the net (PR #265 second review).
_LIVE_OUT = os.path.realpath(os.path.join(ROOT, "data", "out"))
_LIVE_PRODUCTS = os.path.realpath(os.path.join(ROOT, "data", "products.csv"))


def _refuse_live_data_under_pytest(p) -> None:
    """Belt and braces: while pytest runs, nothing may write into the manager's live
    data dir — whatever OUT resolved to.

    tests/conftest.py pinning WEBREVIEW_OUT is the real defence; this is the net under
    it, for a helper that repoints paths by hand (or a child process that inherits the
    env). It costs two string compares per write and would have stopped the 2026-07-26
    incident on its own."""
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        return
    # realpath both sides: a symlinked tmp dir must not be a way around this
    a = os.path.realpath(os.fspath(p))
    live_out = os.path.realpath(_LIVE_OUT)
    live_products = os.path.realpath(_LIVE_PRODUCTS)
    # `data/` ITSELF, not just the export file inside it: the startup sweep walks TWO
    # directories and the second is `dirname(SRC)`, which is neither `data/out` nor
    # `products.csv` — so a helper repointing WEBREVIEW_PRODUCTS at the real export let
    # it unlink live `data/*.tmp` from a test run (PR #265 final review).
    if (a == live_products or a == live_out or a.startswith(live_out + os.sep)
            or a == os.path.dirname(live_products)):
        log.error("REFUSED a write to the live data dir from a pytest run: %s", a)
        raise StoreWipeRefused(
            f"Test sa pokúsil zapísať do živých dát ({a}) — zamietnuté. "
            "Testy musia bežať proti dočasnému WEBREVIEW_OUT.")


def _read_json_store(path, default):
    """The ONE json-store reader (the „SAFE loader" shape: missing / unparseable /
    wrong-type → `default`, never an exception — one bad file must not 500 a whole
    tab). Broader than the old per-store copies in one way on purpose: a write cut
    mid-UTF-8 raises UnicodeDecodeError, which is a ValueError like JSONDecodeError
    and degrades the same way instead of escaping as a 500.

    An I/O error on a store that IS there (permissions, EIO, a directory in its place)
    is NOT one of those cases and propagates: „the file is unreadable" is not evidence
    that the manager did no work, and degrading it to `{}` would let the next click
    persist a one-entry file over a full one — the silent loss this module exists to
    prevent (found in review of this change).

    The read is recorded (how many entries this process saw on disk — what the wipe
    guard checks) ONLY when it really yielded the stored content. A default handed
    back after a failed or wrong-type read must never legitimise a later shrink.

    NOTE: the two FAIL-CLOSED dedup stores (orders_reminder, posta_uncollected — #225)
    deliberately do NOT use this reader; losing THEIR contents means mailing a customer
    twice, so they raise instead of degrading."""
    return _read_json_store_state(path, default)[0]


def _read_json_store_state(path, default) -> tuple:
    """`(value, from_disk)` — `_read_json_store` plus the one thing its callers cannot
    otherwise recover: whether the value it hands back is the STORED content or the
    default standing in for a read that did not work out (PR #295 review).

    An empty `{}` means two opposite things and the difference decides deletions: „the
    manager really has nothing recorded here" (evidence) versus „missing / corrupt /
    wrong type" (no evidence at all). Anything that DELETES on the absence of a record
    must ask for `from_disk` and refuse without it — `_do_upload_suppliers`'s #215 rule
    condemns every assignment when `uploaded_suppliers.json` degrades to `{}`, and that
    file does not exist on the live box at all. store-prune §1: absence is never
    evidence.

    The default is `from_disk=False` on EVERY degraded path, so a caller cannot get the
    answer half-right by accident."""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except FileNotFoundError:
        return default, False
    except ValueError:
        # Degrading keeps the tab alive, but it must not be SILENT: a protected write
        # over this file is about to be refused, and this line is what explains why.
        log.error("úložisko %s sa nedá prečítať (poškodený/neúplný zápis) — zobrazujem "
                  "prázdno, ale zápis doň bude zamietnutý, kým sa neopraví", path)
        return default, False
    if not isinstance(d, type(default)):
        return default, False
    _note_store_read(path, d)
    return d, True


def _thread_ring(p: str) -> collections.deque:
    """This thread's receipt ring for `p` (created on first use, dies with the thread)."""
    rings = getattr(_thread_reads, "rings", None)
    if rings is None:
        rings = _thread_reads.rings = {}
    ring = rings.get(p)
    if ring is None:
        ring = rings[p] = collections.deque(maxlen=_READ_RING)
    return ring


def _note_store_read(path, data) -> None:
    """Record the READ ITSELF as the receipt for `path` (#261/#265 wipe guard): the
    object handed back plus the counts it held at that moment. Kept in BOTH rings —
    this thread's (which no concurrent reader can evict, and which is the one that
    actually carries every read-modify-write in the tree) and the shared one, which is
    best effort: any other thread's read evicts it within microseconds."""
    p = os.fspath(path)
    entry = (data, _measure_store(data))
    _thread_ring(p).append(entry)
    _store_reads.setdefault(p, collections.deque(maxlen=_READ_RING)).append(entry)


def _note_store_write(path, data) -> None:
    """After a successful write the process HAS the store it just wrote, so that object
    becomes the receipt — and every OLDER one is stale, because the file on disk is no
    longer what they were read from. Dropping them here is what bounds retention: at the
    moment of a write, exactly one loaded store per path survives it. Between writes the
    rings refill (up to `_READ_RING` per ring per path) — see the retention note beside
    `_store_reads` (PR #265 third review corrected the „at most one" claim)."""
    p = os.fspath(path)
    entry = (data, _measure_store(data))
    ring = _thread_ring(p)
    ring.clear()
    ring.append(entry)
    shared = collections.deque(maxlen=_READ_RING)
    shared.append(entry)
    _store_reads[p] = shared


def _store_receipts(p: str):
    """Every receipt this process may honour for `p` — this thread's first.

    SNAPSHOTS both rings (PR #265 third review): the guard consumes this generator
    while every GET is appending to the very same deques WITHOUT the store lock, and
    CPython raises `RuntimeError: deque mutated during iteration` for exactly that.
    It surfaced as a raw 500 on the manager's click — a RuntimeError is not
    `StoreWipeRefused`, so not even the 503 with something to fix."""
    yield from tuple(_thread_ring(p))
    yield from tuple(_store_reads.get(p, ()))


def _canonical(x) -> str:
    """Order-independent text of one JSON value, for VALUE containment below."""
    return json.dumps(x, sort_keys=True, ensure_ascii=False, default=repr)


def _list_is_narrowing(data: list, prev: list) -> bool:
    """Is every entry of `data` also in `prev`? Identity first — the common case is a
    caller filtering the very list it read — then VALUE containment.

    Identity alone refused a rebuild written the natural way
    (`[dict(x) for x in prev if …]`) with a message saying the write „did not come from
    a read of that store": wrong, and an invitation for the next author to reach for a
    bypass instead of a `prev=` (PR #265 third review). The value pass is keyed on a
    canonical projection so it stays linear rather than O(n·m) over unhashable dicts."""
    kept = {id(x) for x in prev}
    if all(id(x) in kept for x in data):
        return True
    try:
        pool = collections.Counter(_canonical(x) for x in prev)
        for x in data:
            k = _canonical(x)
            if not pool[k]:
                return False
            pool[k] -= 1
    except (TypeError, ValueError):   # not projectable → identity is all we can trust
        return False
    return True


def _is_derived_from(data, prev, keys=()) -> bool:
    """Is `data` something the caller could have BUILT from the map/list it read?

    `prev=` names the read a rebuilt map came from (the startup decision prune, the
    výstavy retention sweep). Without this check it was a complete BYPASS rather than a
    narrowing — the map being written was never compared to anything, so naming a real
    read authorised writing ANY map over the store (PR #265 second review). A rebuild
    only ever DROPS entries, so what it writes must be contained in what it read.

    `keys` are the NESTED maps `protect=("orders",)` guards. Checking only the
    top-level key set made the narrowing inert for exactly those two stores: every real
    writer of `orders_reminder.json` / `posta_uncollected.json` keeps the same outer
    keys, so a `prev=` write could keep the shape and empty the who-was-already-mailed
    map — the loss that re-mails every customer (PR #265 third review)."""
    if data is prev:
        return True
    if isinstance(data, dict) and isinstance(prev, dict):
        if not set(data) <= set(prev):
            return False
        for k in keys:
            if not k:
                continue                      # "" is the top-level count, checked above
            a, b = data.get(k), prev.get(k)
            # …and only when the value STAYS that container. Both pairs below compare
            # like with like, so a write REPLACING the guarded map with something else
            # entirely (`None`, a list, a string, `0`) matched neither branch and passed
            # as „derived" — all four measured ALLOWED on disk (PR #265 final review).
            # A guarded key that DISAPPEARS lands here too (`None` against a real map),
            # which is the same loss written a different way.
            if type(a) is not type(b):
                return False
            if isinstance(a, dict) and isinstance(b, dict) and not set(a) <= set(b):
                return False
            if isinstance(a, list) and isinstance(b, list) and not _list_is_narrowing(a, b):
                return False
        return True
    if isinstance(data, list) and isinstance(prev, list):
        return _list_is_narrowing(data, prev)
    return False


def _guarded_measures(p, protect) -> tuple:
    """`(keys, disk)` — which maps this write must not silently shrink, and what the
    store on disk holds. Raises when the file is there but unreadable."""
    disk, corrupt = _stored_measures(p)
    if corrupt:
        backup = _quarantine_corrupt_store(p)
        log.error("REFUSED to write over %s: the file is there but unparseable, so we "
                  "cannot know how much work it holds — a copy is preserved as %s and "
                  "the original is left untouched for repair",
                  p, backup or "-")
        raise StoreWipeRefused(
            f"Zápis do {os.path.basename(p)} zamietnutý: súbor sa nedá prečítať "
            "(neúplný/poškodený zápis), takže nevieme, koľko práce v ňom je. Nič sa "
            + (f"neprepísalo, kópia je v {os.path.basename(backup)}. " if backup
               else "neprepísalo. ")
            + "Súbor NEMAŽ — obnov ho zo zálohy (data/backups/state).")
    keys = ("",) if protect is True else ("",) + tuple(protect)
    return keys, disk


def _fsync_dir(d: str) -> None:
    """Make a rename durable. Best effort: some filesystems refuse a directory fsync,
    and losing THIS is far less bad than failing a write the manager just made — so it
    warns instead of raising (ext4, which the box runs, supports it)."""
    try:
        fd = os.open(d, os.O_RDONLY)
    except OSError as e:  # noqa: BLE001 — durability hint only, the data is already written
        log.warning("adresár %s sa nedá otvoriť na fsync (%r)", d, e)
        return
    try:
        os.fsync(fd)
    except OSError as e:  # noqa: BLE001 — see above
        log.warning("fsync adresára %s zlyhal (%r)", d, e)
    finally:
        # The close was the ONE error this „best effort" helper still let escape — and
        # both callers run it AFTER the replace, inside an `except BaseException:
        # unlink(tmp); raise`. A raise there reported a write that is durably on disk as
        # a failure (plus a spurious „temp file could not be removed" — the temp file is
        # gone by then). Durability hint only; the data is already published.
        try:
            os.close(fd)
        except OSError as e:  # noqa: BLE001 — see above
            log.warning("adresár %s sa nedá zavrieť po fsync (%r)", d, e)


def _sweep_stale_tmp(*dirs: str, max_age_h: float = 12.0) -> int:
    """Remove `*.tmp` leftovers older than `max_age_h` from the data dirs.

    `_atomic_write_bytes` names its temp file with `tempfile.mkstemp`, so a SIGKILL/OOM
    during the ~55 MB export write leaves one ~55 MB orphan PER EVENT and nothing ever
    reuses that name (the old pid-derived name was self-limiting at one per process
    lifetime — PR #265 second review). The age bound is what makes this safe: a live
    write of any store takes seconds, so nothing another instance is still using can be
    12 hours old."""
    cutoff = time.time() - max_age_h * 3600
    removed = 0
    for d in dirs:
        # The one destructive operation here — and the only one in the module that used
        # to skip the pytest net (PR #265 third review, I4). A test run with an un-pinned
        # WEBREVIEW_OUT is the incident's own configuration, and this unlinks at IMPORT.
        _refuse_live_data_under_pytest(d)
        try:
            names = os.listdir(d)
        except OSError as e:  # noqa: BLE001 — housekeeping, never a reason to fail the boot
            log.warning("nedá sa prehľadať %s na zvyškové .tmp súbory (%r)", d, e)
            continue
        for name in names:
            if not name.endswith(".tmp"):
                continue
            f = os.path.join(d, name)
            try:
                if os.stat(f).st_mtime >= cutoff:
                    continue
                os.unlink(f)
            except OSError as e:  # noqa: BLE001 — someone else may have won the race
                log.warning("zvyškový %s sa nepodarilo odstrániť (%r)", f, e)
                continue
            removed += 1
            log.info("odstránený zvyškový dočasný súbor %s (staršī ako %g h)", f, max_age_h)
    return removed


def _atomic_write_json(path, data, *, indent=2, mode=None, protect=False,
                       prev=None) -> None:
    """The ONE json-store writer: refuse-wipe check → temp file → atomic os.replace.

    Every `_save_*` in this module goes through here (it used to be 29 copies of the
    same four lines). `mode` (0600) is applied to the temp file BEFORE any content is
    written, so a store holding secrets is never briefly world-readable.

    `protect=True` marks a store holding IRREPLACEABLE work — the manager's
    decisions / flags / pairings; `protect=("orders",)` additionally guards the NESTED
    map(s) that carry the real content (the dedup stores keep theirs one level down).
    There the writer refuses any write that SHRINKS the store *unless it is the tail of
    a read-modify-write THIS caller performed on THAT store*: losing entries
    is legitimate only as the tail of a read-modify-write the manager just performed
    (undo the last decision, un-mark a whole supplier group via /api/ordered/bulk),
    and those all load the live store under `_lock` moments earlier. The 2026-07-26
    wipe had exactly the opposite shape — `_load_decisions` was stubbed to a fixture,
    so nothing ever read the 2831 entries that the fixture then erased. It is
    deliberately not an „empty payload" rule: that fixture was small, not empty, and
    would have overwritten 2831 entries just as happily. Growth never needs a read (an
    incremental upload state only ever adds), and a stale count (another process wrote
    in between) refuses too: that write is a lost update, the #264 half of this story.

    The receipt is the READ, not a count (PR #265 review): `_read_json_store` remembers
    the OBJECT it handed back, and a shrink is allowed only when the map being written
    IS that object (or names it via `prev=`, for the few callers that rebuild a new map
    from the one they read — and a rebuild may only DROP entries, see
    `_is_derived_from`; without that, naming a read authorised writing anything at all). A count alone made this a stale-read detector: the app
    reads decisions on every page load, so the count always matched the disk and the
    guard was disarmed for every caller in the process — including one that had never
    touched the store.

    Nothing is written when it refuses — not even a stray .tmp — and it raises rather
    than logging quietly, so the failure is visible in the response and in tests."""
    p = os.fspath(path)
    with _lock:
        _write_json_locked(p, data, indent, mode, protect, prev)


def _write_json_locked(p, data, indent, mode, protect, prev=None) -> None:
    """The body of `_atomic_write_json`, always under the store lock."""
    _refuse_live_data_under_pytest(p)
    if protect:
        keys, disk = _guarded_measures(p, protect)
        new = _measure_store(data)
        prev = data if prev is None else prev
        derived = _is_derived_from(data, prev, keys)
        for k in keys:
            was = disk.get(k, 0)
            if not was or new.get(k, 0) >= was:
                continue                      # nothing to lose, or the map is growing
            receipted = any(r[0] is prev and r[1].get(k, 0) == was
                            for r in _store_receipts(p))
            if derived and receipted:
                continue                      # the tail of a read-modify-write on THIS store
            what = "záznamov" if k == "" else f"položiek v „{k}“"
            # Name the check that actually failed — the two have different fixes, and a
            # blanket „did not come from a read" over a rebuild that DID read the store
            # sends the next author looking for a bypass (PR #265 third review).
            why = ("obsahuje záznamy, ktoré v načítanom úložisku neboli"
                   if not derived else
                   "vychádza z inej (staršej) verzie, než akú má teraz disk")
            log.error("REFUSED to shrink %s%s from %d to %d entries (#261/#265): "
                      "derived-from-the-named-read=%s, receipt-matches-disk=%s — the "
                      "smaller map is not something the manager just did; nothing was "
                      "written", p, f" [{k}]" if k else "", was, new.get(k, 0),
                      derived, receipted)
            raise StoreWipeRefused(
                f"Zápis do {os.path.basename(p)} zamietnutý: {was} {what} by sa "
                f"zmenšilo na {new.get(k, 0)}, ale zapisovaný obsah {why} "
                "(na disku môže byť novšia verzia, alebo ju zapísal iný proces/skript). "
                "Dáta ostali nedotknuté. V prehliadači načítaj "
                "stránku znova; ak to hlási automatizácia, spusti ju znova. Súbor NEMAŽ.")
    tmp = f"{p}.{os.getpid()}.tmp"   # per-process: two instances never share a temp
    if mode is None:
        f = open(tmp, "w", encoding="utf-8")
    else:
        f = os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode),
                      "w", encoding="utf-8")
    try:
        with f:
            json.dump(data, f, ensure_ascii=False, indent=indent)
            # os.replace is atomic against concurrent READERS, not against a crash: the
            # rename can be durable while the bytes are not, which is precisely how a
            # truncated store appears after a power loss (PR #265 review).
            f.flush()
            os.fsync(f.fileno())
        if mode is not None:
            os.chmod(tmp, mode)   # exact perms regardless of the process umask
        os.replace(tmp, p)
        # …and the RENAME itself must be durable too: fsyncing the bytes but not the
        # directory entry lets a power loss bring the OLD file back (PR #265 second
        # review). One call, in the one place every json store is written.
        _fsync_dir(os.path.dirname(p) or ".")
        # what is on disk now IS this object — so the manager's NEXT undo in a row is
        # still a legitimate read-modify-write and does not 503 on a stale receipt
        _note_store_write(p, data)
    except BaseException:
        # temp files are per-process now, so a failed dump would otherwise leave one
        # orphan per process lifetime; the store itself is untouched either way
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _record_uploaded(load_fn, save_fn, entries: dict) -> dict:
    """Merge freshly-uploaded keys into the CURRENT on-disk incremental state.

    The nightly write-backs read their state, then spend MINUTES inside the Shoptet
    import subprocess, and only then record what landed. Saving the map they read up
    front would discard everything written meanwhile — and a lost review key means the
    next run re-uploads a link the manager has since corrected. `_import_lock` is
    in-process only, so the second-instance scenario this PR is about (#262/#264)
    reaches straight into that window. Re-read under the store lock and merge.

    Returns the post-run map — the run's summary counts are built from it, so they
    describe what is really on disk rather than a snapshot from before the import."""
    if not entries:
        return load_fn()
    with _lock:
        fresh = load_fn()
        fresh.update(entries)
        save_fn(fresh)
    return fresh


def _atomic_write_bytes(path, data: bytes, *, mode: int = 0o644) -> None:
    """Same temp-file + atomic replace for the raw cp1250 CSV caches (export,
    orders, customers) — a half-written cache would poison every reader.

    The temp name comes from `tempfile.mkstemp`, NOT from the pid: `orders_cache.csv`
    has TWO writers in one process — `_orders_csv_cached()` on any request thread when
    the 30-min cache is stale, and `run_shoptet_sync()` on the scheduler thread every
    hour. A pid-derived name is the SAME string for both, so the one that finishes
    first renames the inode the other is still writing into place, and the loser then
    keeps appending into the LIVE cache before its own replace fails (PR #265 review).

    Still no store lock on purpose: with distinct temp files tmp+replace is enough, and
    the export dump is ~55 MB — holding the cross-process lock across it would queue
    every other instance's writes behind a multi-second write."""
    p = os.fspath(path)
    _refuse_live_data_under_pytest(p)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p) or ".",
                               prefix=os.path.basename(p) + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())          # durable bytes before the rename (see above)
        # mkstemp creates the temp file 0600. The export is public-ish data and stays
        # 0644 as it always was; the ORDER and CUSTOMER caches hold customer names,
        # e-mails and phones and keep mkstemp's 0600 — the same class of data
        # posta_uncollected.json / orders_reminder.json already keep private, and only
        # this one systemd --user service ever reads them (PR #265 second review).
        os.chmod(tmp, mode)               # exact perms regardless of the process umask
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError as e:  # noqa: BLE001 — cleanup only, the real failure re-raises
            log.warning("temp file %s sa nepodarilo odstrániť (%r)", tmp, e)
        raise
    # …and the rename itself must be durable, exactly as in the JSON writer: the
    # directory fsync was added there only, leaving the 55 MB export and the two
    # customer caches with durable bytes published by a losable directory entry
    # (PR #265 third review). OUTSIDE the handler above (final review): past the
    # replace the write has SUCCEEDED and the temp file no longer exists, so anything
    # escaping here would be cleaned up as a failure and re-raised at the caller.
    _fsync_dir(os.path.dirname(p) or ".")

try:
    with open(DATA, encoding="utf-8") as f:
        PRODUCTS = json.load(f)
    _note_store_read(DATA, PRODUCTS)   # #261 — the receipt a later save is checked against
    log.info("loaded %d products from %s", len(PRODUCTS), DATA)
except FileNotFoundError:
    PRODUCTS = []
    log.warning("review data missing: %s — starting with 0 products", DATA)

# ONE cp1250 pass over the Shoptet export builds BOTH:
#   CODE2PAIR — code -> pairCode (the Shoptet import needs both present), and
#   CATALOG   — the catalog-wide search index grouped per pairCode (canonical
#               build_catalog_index), powering /api/search + promote-on-pair.
SRC = os.environ.get("WEBREVIEW_PRODUCTS") or os.path.join(ROOT, "data", "products.csv")


def _variant_label(row, variant_cols):
    """Human-readable variant label for one export row = the populated `variant:*`
    axis value(s) joined (e.g. size 'M', or 'Červená · L' for a colour×size). Used
    only for DISPLAY in the #174 split-into-sizes panel — the authoritative key is
    always the variant CODE, never this label. Empty for a single-variant product."""
    vals = [v for col in variant_cols if (v := (row.get(col) or "").strip())]
    return " · ".join(vals)


def _load_catalog(path, review_keys):
    """Single cp1250 pass over the Shoptet export → (code2pair, code2variant, catalog).
    Missing export → ({}, {}, {}) (the app already tolerates a dataless boot). `rows`
    is held only for the duration of the build, then released. code2variant maps each
    variant code → its `variant:*` axis label (size/colour), for the #174 split panel."""
    code2pair: dict = {}
    code2variant: dict = {}
    rows: list = []
    if not os.path.exists(path):
        return code2pair, code2variant, {}
    csv.field_size_limit(10**9)
    # newline="" — REQUIRED by the csv module (#279). Without it the text layer
    # rewrites \r\n and lone \r to \n before csv sees them, INSIDE quoted fields too,
    # where they are data (a multi-line description / size label), not a separator.
    with open(path, encoding="cp1250", errors="replace", newline="") as _f:
        reader = csv.DictReader(_f, delimiter=";")
        # the variant AXIS columns are `variant:<name>` (colon) — NOT `variantVisibility`.
        variant_cols = [c for c in (reader.fieldnames or []) if c.startswith("variant:")]
        for _row in reader:
            _c = (_row.get("code") or "").strip()
            if _c:
                code2pair[_c] = (_row.get("pairCode") or "").strip()
                _lbl = _variant_label(_row, variant_cols)
                if _lbl:
                    code2variant[_c] = _lbl
            rows.append(_row)
    return code2pair, code2variant, build_catalog_index(rows, review_keys)


# review_keys is the COVERAGE set that marks a catalog entry in_review. The index is
# grouped by entry KEY = pairCode-or-code (single-variant products have an EMPTY pairCode
# → keyed by their own code). Most review entries are keyed "SUPPLIER|pairCode" (e.g.
# GRUBE|425), so we cannot collect `key` (C1 — every such product wrongly not-in-review).
# We collect the BARE pairCodes PLUS every variant code; build_catalog_index marks
# in_review via key-or-any-variant-code membership, so a single-variant reviewed product
# (empty pairCode) still matches by its code.
_review_cover = ({p.get("pairCode") for p in PRODUCTS if p.get("pairCode")}
                 | {c for p in PRODUCTS for c in (p.get("variant_codes") or [])})
CODE2PAIR, CODE2VARIANT, CATALOG = _load_catalog(SRC, _review_cover)
log.info("catalog: %d products indexed (%d codes, %d variant labels) from %s",
         len(CATALOG), len(CODE2PAIR), len(CODE2VARIANT), SRC)


def _load_decisions() -> dict:
    # Corrupt/wrong-type store degrades to {} (like _load_instock/_load_ordered) — a
    # hand-corrupted or partially-written decisions.json (written on EVERY review click)
    # must never raise: it feeds /api/orders + /api/products, so one bad file would 500
    # the whole tab. Always a dict (a stray non-dict would break every .get() caller).
    return _read_json_store(DECISIONS, {})


def _save_decisions(d: dict, *, prev: dict = None) -> None:
    """`prev` = the map this one was BUILT FROM, for a caller that rebuilds a new dict
    instead of mutating the one it read (the startup prune). It is the read receipt the
    shrink guard checks; a mutate-in-place caller needs nothing."""
    _atomic_write_json(DECISIONS, d, protect=True, prev=prev)


# --------------------------------------------------------------------------- #
# Auth (#91): email+password login (Flask session), user store, reset tokens.
# The WHOLE app + every /api/* endpoint sits behind the login gate; the only
# public surface is /login, /forgot, /reset/<token>, static assets, /favicon,
# /api/version (login-page footer) and the /api/n8n/* machine endpoints (those
# carry their OWN bearer auth — n8n has no session).
# --------------------------------------------------------------------------- #


def _load_env_file(path):
    """KEY=VALUE lines from a gitignored creds file → os.environ DEFAULTS (a real
    env var always wins). Lets the systemd service keep auth/mail config in
    data/.auth_env + data/.mail_env (chmod 600) instead of unit files."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


_load_env_file(os.path.join(ROOT, "data", ".auth_env"))
_load_env_file(os.path.join(ROOT, "data", ".mail_env"))
# #106 — OPENAI_API_KEY for the „Dodávateľský sklad" scraper's LLM fallback.
# Gitignored, chmod 600; absent = LLM fallback degrades gracefully (static-only).
_load_env_file(os.path.join(ROOT, "data", ".ai_env"))
# #115 — GITHUB_TOKEN + GITHUB_REPO for the „Vývoj" tab (list issues) + the idea
# lightbulb (create issue). Gitignored, chmod 600; absent = the tab + lightbulb
# degrade gracefully („GitHub nedostupný"), never crash. The token is a repo-write
# credential — it lives ONLY in the server-side Authorization header (never sent to
# the browser, never in a URL or log).
_load_env_file(os.path.join(ROOT, "data", ".gh_env"))


def _secret_key():
    """Stable session-signing key: SECRET_KEY env (data/.auth_env) wins; else a
    generated key persisted in OUT/.auth_secret (0600), so sessions survive
    restarts even with zero config (a fresh key each boot would log everyone
    out on every deploy)."""
    env = os.environ.get("SECRET_KEY")
    if env:
        return env
    path = os.path.join(OUT, ".auth_secret")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            key = f.read().strip()
        if key:
            return key
    key = secrets.token_hex(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(key)
    log.info("auth: generated new session secret at %s", path)
    return key


app.secret_key = _secret_key()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    # The public site is https (Cloudflare tunnel) → AUTH_COOKIE_SECURE=1 lives
    # in data/.auth_env there; plain-http dev/E2E boots leave it off.
    SESSION_COOKIE_SECURE=os.environ.get("AUTH_COOKIE_SECURE") == "1",
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)

USERS = _store("users.json")          # email -> {pw_hash,is_admin,created_at}
RESET_TOKENS = _store("reset_tokens.json")   # sha256(token) -> {email,exp}
RESET_TTL = 2 * 3600                             # reset link validity: 2 hours
PW_MIN_LEN = 8
LOGIN_MAX_FAILS = 5                              # failed logins per IP…
LOGIN_WINDOW = 15 * 60                           # …within 15 minutes → 429
_login_fails: dict = {}                          # ip -> [fail timestamps]
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Burned on login attempts for UNKNOWN emails so they cost the same as a wrong
# password (no account enumeration via response latency).
_DUMMY_HASH = generate_password_hash(secrets.token_hex(16))


def _load_users() -> dict:
    # Deliberately NOT the SAFE reader: an unreadable user store must fail closed
    # (nobody gets in) rather than degrade to „no accounts" and re-bootstrap an admin.
    if os.path.exists(USERS):
        with open(USERS, encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict):
            # Parses, but is not an account map — the one corruption shape this loader
            # missed while every other loader in #265 checks it. `[]` used to reach
            # `_bootstrap_admin` as a TypeError (import dies) and `_current_user` as an
            # AttributeError (500 per request); both are outside the except tuples that
            # turn a bad store into a 503 with repair instructions. A wrong hand-repair
            # or restore lands here — exactly what that message invites (third review).
            raise ValueError(f"{USERS} nie je zoznam účtov (je to {type(d).__name__})")
        _note_store_read(USERS, d)   # #261 — the receipt a later save is checked against
        return d
    return {}


def _save_json_0600(path, d, *, protect=False) -> None:
    """Atomic write with 0600 perms — users.json holds password hashes and
    reset_tokens.json holds live reset-token hashes."""
    _atomic_write_json(path, d, mode=0o600, protect=protect)


def _save_users(d: dict) -> None:
    # protect: losing the account list locks everyone out of the app (#261).
    _save_json_0600(USERS, d, protect=True)


def _load_reset_tokens() -> dict:
    if os.path.exists(RESET_TOKENS):
        with open(RESET_TOKENS, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_reset_tokens(d: dict) -> None:
    _save_json_0600(RESET_TOKENS, d)


def _norm_email(e) -> str:
    return (e or "").strip().lower()


def _bootstrap_admin() -> None:
    """First-run admin from ADMIN_EMAIL/ADMIN_PW (data/.auth_env), so the manager
    is never locked out after a deploy. Creates the account only when missing —
    a password changed later in the UI is NEVER overwritten by a restart."""
    email = _norm_email(os.environ.get("ADMIN_EMAIL"))
    pw = os.environ.get("ADMIN_PW") or ""
    if not email or not pw:
        return
    with _lock:
        users = _load_users()
        if email in users:
            return
        users[email] = {
            "pw_hash": generate_password_hash(pw), "is_admin": True,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        _save_users(users)
    log.info("auth: bootstrapped admin %s from env", email)


try:
    _bootstrap_admin()
except (StoreLockTimeout, StoreWipeRefused, OSError, ValueError, TypeError) as e:  # noqa: BLE001
    # This runs at IMPORT. Anything raising here does not degrade a feature — it stops the
    # service from STARTING at all (systemd restart loop, no web UI, nothing to look at).
    # All five are real: another instance holding the store lock for 30 s, a refused
    # write, the OSError `_load_users` deliberately re-raises on a users.json that is
    # there but unreadable, and — the most likely one of the lot — the ValueError a write
    # cut mid-JSON or mid-UTF-8 raises, which is exactly the corruption the fsync work
    # exists to make less likely and was the one shape NOT caught (PR #265 second review,
    # C2). TypeError is the belt to `_load_users`' isinstance brace: a store that PARSES
    # but is not a map (`[]` from a wrong hand-repair) used to reach `users[email] = …`
    # and take the whole import down (PR #265 third review, I2).
    # Existing accounts are unaffected; only the first-run bootstrap is skipped.
    log.error("auth: admin bootstrap skipped (%r) — the app keeps serving, existing "
              "accounts are unaffected; fix data/out/users.json and restart", e)


def _current_user():
    """Session → live user record. Re-checks the store on EVERY request, so a
    deleted user (or a stale cookie) loses access immediately."""
    email = session.get("user")
    if not email:
        return None
    u = _load_users().get(email)
    if not u:
        return None
    return {"email": email, "is_admin": bool(u.get("is_admin"))}


_PUBLIC_ENDPOINTS = {"login", "forgot_password", "reset_password", "favicon",
                     "api_version", "static", "static_files"}


@app.before_request
def _require_login():
    """Default-deny login gate: every endpoint (present and future) is protected
    unless explicitly public. /api/n8n/* keep their own bearer auth."""
    if request.endpoint in _PUBLIC_ENDPOINTS or request.path.startswith("/api/n8n/"):
        return None
    try:
        if _current_user():
            return None
    except (ValueError, OSError, TypeError) as e:
        # `_load_users` fails CLOSED on purpose (nobody gets in over a store we cannot
        # read) — but „fails closed" must still SAY what to fix. A bare 500 reads as a
        # transient glitch and invites another click (PR #265 second review, C2).
        log.error("auth: the account store is unreadable (%r) — refusing every request "
                  "until data/out/users.json is repaired", e)
        msg = ("Zoznam účtov (data/out/users.json) sa nedá prečítať — neúplný/poškodený "
               "zápis. Nikoho nepustím dnu, kým sa neopraví: obnov súbor zo zálohy "
               "(data/backups/state) a reštartuj službu. Súbor NEMAŽ — prázdny zoznam "
               "účtov znamená, že sa vytvorí nový admin a všetky ostatné účty zmiznú.")
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": msg}), 503
        # a browser must not get raw JSON on the page it actually opened
        return Response(msg, status=503, mimetype="text/plain; charset=utf-8")
    log.info("auth: unauthenticated %s %s from %s", request.method, request.path,
             _client_ip())
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    nxt = request.full_path if request.query_string else request.path
    return redirect("/login?next=" + quote(nxt))


def _csrf_token() -> str:
    tok = session.get("_csrf")
    if not tok:
        tok = secrets.token_hex(16)
        session["_csrf"] = tok
    return tok


def _csrf_ok() -> bool:
    tok = session.get("_csrf") or ""
    sent = request.form.get("_csrf") or ""
    # compare BYTES — compare_digest raises TypeError on non-ASCII str, and the
    # form value is attacker-controlled (must yield 400, never a 500)
    return bool(tok) and hmac.compare_digest(tok.encode(), sent.encode())


def _rate_limited(ip) -> bool:
    now = time.time()
    fails = [t for t in _login_fails.get(ip, []) if now - t < LOGIN_WINDOW]
    if fails:
        _login_fails[ip] = fails
    else:
        _login_fails.pop(ip, None)   # no lingering entry per visitor IP
    return len(fails) >= LOGIN_MAX_FAILS


def _note_fail(ip) -> None:
    _login_fails.setdefault(ip, []).append(time.time())


def _safe_next(nxt) -> str:
    """Post-login redirect target: same-site paths only (no open redirect)."""
    if nxt and nxt.startswith("/") and not nxt.startswith("//"):
        return nxt
    return "/"


def _login_page(error=None, status=200):
    return render_template(
        "login.html", csrf=_csrf_token(), version=__version__, error=error,
        nxt=request.values.get("next", ""),
        reset_done=request.args.get("reset") == "1"), status


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        if _current_user():
            return redirect("/")
        return _login_page()
    ip = _client_ip()
    if not _csrf_ok():
        log.warning("auth: login with bad/missing CSRF from %s", ip)
        return _login_page("Neplatná relácia — skús to znova.", 400)
    if _rate_limited(ip):
        log.warning("auth: login rate-limited ip=%s", ip)
        return _login_page("Priveľa pokusov — počkaj 15 minút a skús znova.", 429)
    email = _norm_email(request.form.get("email"))
    pw = request.form.get("password") or ""
    u = _load_users().get(email)
    # unknown email verifies against a dummy hash → same cost as a wrong
    # password (no enumeration via timing); malformed stored hash → False
    if not check_password_hash((u or {}).get("pw_hash") or _DUMMY_HASH, pw) or not u:
        _note_fail(ip)
        log.warning("auth: failed login email=%s ip=%s", email, ip)
        return _login_page("Nesprávny e-mail alebo heslo.", 401)
    session.clear()
    session["user"] = email
    session["_csrf"] = secrets.token_hex(16)   # fresh token for the fresh session
    session.permanent = True
    log.info("auth: login ok %s ip=%s", email, ip)
    return redirect(_safe_next(request.form.get("next")))


@app.route("/logout", methods=["POST"])
def logout():
    email = session.get("user")
    session.clear()
    log.info("auth: logout %s", email)
    return redirect("/login")


@app.route("/api/me")
def api_me():
    return jsonify(_current_user())   # the login gate guarantees a user here


# „BCC vždy" (#105/#126/#127) — the owner gets a copy of every mail the app sends. A missing
# MAIL_BCC is a CONFIG problem, not a per-mail one, so it is surfaced ONCE per process instead
# of on every single send (log spam would bury it).
_BCC_WARNED = False


def _warn_missing_bcc_once(what: str) -> None:
    global _BCC_WARNED
    if _BCC_WARNED:
        return
    _BCC_WARNED = True
    log.warning("mail: MAIL_BCC nie je nastavené (data/.mail_env) — mail '%s' ide príjemcovi BEZ "
                "kópie pre majiteľa (konvencia BCC vzdy); doplň MAIL_BCC", what)


def _mail_bcc():
    """The owner's „BCC vždy" address, or None when it is not usable. ONE definition of
    „configured", shared by every sender, by the override endpoint's pre-flight and by the
    automations' `bcc_missing` stat — otherwise a blank `MAIL_BCC=` line in data/.mail_env
    reads as configured to one of them and as missing to another, and the tab disagrees with
    what the sender actually does."""
    return (os.environ.get("MAIL_BCC") or "").strip() or None


def _smtp_deliver(sender, rcpt, msg_str, to) -> bool:
    """Connect, hand the message over, disconnect. Returns True only when the PRIMARY recipient
    `to` was accepted by the server. Raises on a connection / handshake / send failure — every
    caller wraps this in its own log-and-degrade except block.

    Two subtleties, each of which used to cost a real customer a duplicate or a lost mail:

    * The mail is DELIVERED the moment `sendmail()` returns. A `quit()` that then raises (the
      server drops the connection right after DATA) must NEVER be reported as a send failure —
      the caller would skip its dedup write and the next run would e-mail the same customer
      AGAIN (BUG 1).
    * `sendmail()` only RAISES when EVERY recipient is refused; a PARTIAL refusal comes back as
      a plain dict. So a refused CUSTOMER address must be reported as a failure (state not
      bumped → retried next run), while a refused BCC-only address must not throw away the
      fact that the customer's copy did go out (BUG 5).

    The connection is closed on EVERY exit path (PR #223 review): `quit()` is only reached on
    the success path, so a raise out of starttls()/login()/sendmail() — or out of quit()
    itself — used to leave the socket open until the garbage collector happened to run. This
    box mails all day (two customer automations + password resets), so that is a slow
    file-descriptor leak here and a pile of half-open connections on the SMTP relay."""
    host = os.environ.get("MAIL_HOST")
    port = int(os.environ.get("MAIL_PORT", "587"))
    user = os.environ.get("MAIL_USER", "")
    pw = os.environ.get("MAIL_PASS", "")
    if port == 465:
        smtp = smtplib.SMTP_SSL(host, port, timeout=20)
    else:
        smtp = smtplib.SMTP(host, port, timeout=20)
    closed = False
    try:
        if port != 465:
            smtp.starttls()
        if user:
            smtp.login(user, pw)
        refused = smtp.sendmail(sender, rcpt, msg_str) or {}
        try:      # handed over — from here on NOTHING may turn this into a reported failure
            smtp.quit()
            closed = True
        except Exception as e:  # noqa: BLE001 — the mail already left; a polite close is optional
            log.warning("mail: SMTP quit() zlyhalo PO úspešnom odoslaní na %s (mail UŽ odišiel, "
                        "beriem to ako úspech): %r", to, e)
    finally:
        if not closed:
            # best-effort teardown of a connection quit() never got to (or failed on); it must
            # never mask the original failure, nor turn a delivered mail into a reported one
            try:
                smtp.close()
            except Exception as e:  # noqa: BLE001
                log.debug("mail: SMTP close() po zlyhaní: %r", e)
    if refused:
        if str(to).strip().lower() in {str(a).strip().lower() for a in refused}:
            log.error("mail: server ODMIETOL príjemcu %s (%r) — mail mu NEODIŠIEL, stav "
                      "nebumpujem (skúsim znova)", to, refused)
            return False
        log.warning("mail: server odmietol vedľajšieho príjemcu (%r) — zákazníkovi %s mail "
                    "odišiel, pokračujem", refused, to)
    return True


def _send_mail(to, subject, body) -> bool:
    """Plain-text mail via SMTP from data/.mail_env (MAIL_HOST/PORT/USER/PASS/FROM).
    Unconfigured or failing SMTP is LOGGED and reported False — the forgot page
    never 500s and never leaks whether a mail actually left.

    Always BCCs MAIL_BCC (data/.mail_env) when set — the "BCC vždy" convention
    (Marek, comment on #105/#126/#127): every mail the app sends is BCC'd to
    the owner. _send_mail_html already applies this; #127 closed the gap for
    this (reset-password) path. bcc is envelope-only (no header), matching
    _send_mail_html."""
    bcc = _mail_bcc()
    host = os.environ.get("MAIL_HOST")
    if not host:
        log.error("auth: SMTP not configured (data/.mail_env) — mail to %s NOT sent", to)
        return False
    if not bcc:
        # a reset mail is not an automation customer mail — it still goes out, just noisily
        _warn_missing_bcc_once(subject)
    try:
        # config parsing INSIDE the try: a malformed MAIL_PORT in .mail_env must
        # log-and-degrade like any other send failure, never 500 the forgot page
        port = int(os.environ.get("MAIL_PORT", "587"))
        # user only for the MAIL_FROM fallback below — _smtp_deliver reads the SMTP
        # credentials (and the port) from the same env itself
        user = os.environ.get("MAIL_USER", "")
        sender = os.environ.get("MAIL_FROM") or user
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = to
        rcpt = [to] + ([bcc] if bcc else [])
        ok = _smtp_deliver(sender, rcpt, msg.as_string(), to)
        if ok:
            log.info("auth: reset mail sent to %s (bcc %s) via %s:%s", to, bcc or "-", host, port)
        return ok
    except Exception as e:  # noqa: BLE001 — log full context + degrade, never 500
        log.error("auth: SMTP send to %s via %s:%s failed: %r",
                  to, host, os.environ.get("MAIL_PORT", "587"), e)
        return False


def _send_mail_html(to, subject, html_body, bcc=None, require_bcc=False) -> bool:
    """HTML mail via the same SMTP config (data/.mail_env) as _send_mail — used
    by the automations (#93 customer notifications). Sender defaults to the
    SMTP account's MAIL_FROM; POSTA_MAIL_FROM (data/.mail_env) overrides it if
    the SMTP server allows the eshop@ alias the old n8n workflow used. bcc is
    envelope-only (no header), matching the n8n emailSend behavior.

    bcc defaults to MAIL_BCC (data/.mail_env) when the caller doesn't pass one
    — the "BCC vždy" convention (Marek, comment on #105/#126): every
    automation e-mail is BCC'd to the owner. Pass bcc="" explicitly to opt a
    specific send out of that default. Failure is logged + False — the
    automation records it and retries next run.

    require_bcc=True makes that convention BINDING instead of best-effort: with no
    MAIL_BCC configured the mail is NOT sent at all (False → the caller does not bump
    its state, so it is retried once MAIL_BCC is back). The automations that write to a
    real customer (Pošta escalation, order reminders) pass it — a customer mail the
    owner never sees is worse than a delayed one. Every other path (password reset,
    „Nedostupné tovary" preview-gated sends) keeps the old behaviour and only gets a
    one-shot warning."""
    explicit_no_bcc = (bcc == "")
    if bcc is None:
        bcc = _mail_bcc()
    bcc = bcc or None
    host = os.environ.get("MAIL_HOST")
    if not host:
        log.error("mail: SMTP not configured (data/.mail_env) — mail '%s' to %s NOT sent",
                  subject, to)
        return False
    if not bcc and not explicit_no_bcc:
        if require_bcc:
            log.error("mail: MAIL_BCC nie je nastavené (data/.mail_env) — automatizačný mail "
                      "'%s' pre zákazníka %s NEODOSIELAM (konvencia BCC vzdy); doplň MAIL_BCC "
                      "a beh ho pošle znova", subject, to)
            return False
        _warn_missing_bcc_once(subject)
    try:
        port = int(os.environ.get("MAIL_PORT", "587"))
        # user only for the MAIL_FROM fallback below — _smtp_deliver reads the SMTP
        # credentials (and the port) from the same env itself
        user = os.environ.get("MAIL_USER", "")
        sender = (os.environ.get("POSTA_MAIL_FROM")
                  or os.environ.get("MAIL_FROM") or user)
        msg = MIMEText(html_body, "html", "utf-8")
        msg["Subject"] = subject
        msg["From"] = formataddr(("Forestshop.sk", sender))
        msg["To"] = to
        rcpt = [to] + ([bcc] if bcc else [])
        ok = _smtp_deliver(sender, rcpt, msg.as_string(), to)
        if ok:
            log.info("mail: sent '%s' to %s (bcc %s) via %s:%s", subject, to,
                     bcc or "-", host, port)
        return ok
    except Exception as e:  # noqa: BLE001 — log full context + degrade, never crash the run
        log.error("mail: send '%s' to %s via %s:%s failed: %r",
                  subject, to, host, os.environ.get("MAIL_PORT", "587"), e)
        return False


def _send_vystava_mail(to, subject, text_body):
    """Plain-text mail with an EXPLICIT Message-ID (returned) so the „Poľovnícke
    výstavy" IMAP reply-detection (#111) can thread on it — _send_mail_html only
    returns a bool. Reuses the same SMTP config as _send_mail_html (host/port/user/
    pass/from) and the same „BCC vždy" default (MAIL_BCC). From is „Forestshop.sk"
    <MAIL_FROM>. Returns the generated Message-ID on success, None on failure (the
    caller then leaves the výstava's state unchanged and can retry)."""
    bcc = _mail_bcc()
    host = os.environ.get("MAIL_HOST")
    if not host:
        log.error("vystavy mail: SMTP not configured (data/.mail_env) — '%s' to %s NOT sent",
                  subject, to)
        return None
    try:
        port = int(os.environ.get("MAIL_PORT", "587"))
        # user only for the MAIL_FROM fallback below — _smtp_deliver reads the SMTP
        # credentials (and the port) from the same env itself
        user = os.environ.get("MAIL_USER", "")
        sender = os.environ.get("MAIL_FROM") or user
        msgid = make_msgid(domain="forestshop.sk")
        msg = MIMEText(text_body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = formataddr(("Forestshop.sk", sender))
        msg["To"] = to
        msg["Message-ID"] = msgid
        rcpt = [to] + ([bcc] if bcc else [])
        if not _smtp_deliver(sender, rcpt, msg.as_string(), to):
            return None            # organizer address refused → state unchanged, retried
        log.info("vystavy mail: sent '%s' to %s (msgid %s, bcc %s) via %s:%s",
                 subject, to, msgid, bcc or "-", host, port)
        return msgid
    except Exception as e:  # noqa: BLE001 — log full context + degrade, never crash
        log.error("vystavy mail: send '%s' to %s via %s:%s failed: %r",
                  subject, to, host, os.environ.get("MAIL_PORT", "587"), e)
        return None


def _base_url() -> str:
    """Absolute base for reset links: APP_BASE_URL (data/.auth_env — the public
    tunnel URL) wins; request.url_root is the dev/test fallback."""
    return (os.environ.get("APP_BASE_URL") or request.url_root).rstrip("/")


def _forgot_page(sent, error=None, status=200):
    return render_template("forgot.html", csrf=_csrf_token(), version=__version__,
                           sent=sent, error=error), status


@app.route("/forgot", methods=["GET", "POST"])
def forgot_password():
    if request.method == "GET":
        return _forgot_page(sent=False)
    ip = _client_ip()
    if not _csrf_ok():
        log.warning("auth: forgot with bad/missing CSRF from %s", ip)
        return _forgot_page(sent=False, error="Neplatná relácia — skús to znova.",
                            status=400)
    if _rate_limited(ip):   # shares the login fail budget — brakes mail-bombing too
        log.warning("auth: forgot rate-limited ip=%s", ip)
        return _forgot_page(sent=False,
                            error="Priveľa pokusov — počkaj 15 minút.", status=429)
    email = _norm_email(request.form.get("email"))
    if email in _load_users():
        token = secrets.token_urlsafe(32)
        th = hashlib.sha256(token.encode()).hexdigest()
        now = time.time()
        with _lock:
            toks = {k: v for k, v in _load_reset_tokens().items()
                    if v.get("exp", 0) > now}        # purge expired on the way
            toks[th] = {"email": email, "exp": now + RESET_TTL}
            _save_reset_tokens(toks)
        link = _base_url() + "/reset/" + token
        sent_ok = _send_mail(
            email, "Obnova hesla — Párovanie Forestshop",
            "Na nastavenie nového hesla klikni na tento odkaz (platí 2 hodiny a "
            f"funguje iba raz):\n\n{link}\n\nAk si o obnovu hesla nežiadal, tento "
            "e-mail ignoruj — heslo sa nemení.")
        log.info("auth: reset token issued for %s ip=%s mail_sent=%s",
                 email, ip, sent_ok)
    else:
        _note_fail(ip)   # unknown-email probing eats the same budget
        log.info("auth: forgot for unknown email=%s ip=%s", email, ip)
    # identical answer whether the account exists or not (no enumeration)
    return _forgot_page(sent=True)


def _reset_page(valid, token, error=None, status=200):
    return render_template("reset.html", valid=valid, token=token,
                           csrf=_csrf_token(), version=__version__,
                           error=error), status


@app.route("/reset/<token>", methods=["GET", "POST"])
def reset_password(token):
    th = hashlib.sha256(token.encode()).hexdigest()
    rec = _load_reset_tokens().get(th)
    if not rec or rec.get("exp", 0) < time.time():
        log.info("auth: reset with invalid/expired token from %s", _client_ip())
        return _reset_page(valid=False, token="", status=410)
    if request.method == "GET":
        return _reset_page(valid=True, token=token)
    if not _csrf_ok():
        return _reset_page(valid=True, token=token,
                           error="Neplatná relácia — skús to znova.", status=400)
    pw = request.form.get("password") or ""
    pw2 = request.form.get("password2") or ""
    if len(pw) < PW_MIN_LEN:
        return _reset_page(valid=True, token=token,
                           error=f"Heslo musí mať aspoň {PW_MIN_LEN} znakov.",
                           status=400)
    if pw != pw2:
        return _reset_page(valid=True, token=token,
                           error="Heslá sa nezhodujú.", status=400)
    with _lock:
        toks = _load_reset_tokens()
        rec = toks.pop(th, None)                     # single-use: consume NOW
        if not rec or rec.get("exp", 0) < time.time():
            return _reset_page(valid=False, token="", status=410)
        _save_reset_tokens(toks)
        users = _load_users()
        u = users.get(rec["email"])
        if u:
            u["pw_hash"] = generate_password_hash(pw)
            _save_users(users)
    log.info("auth: password reset completed for %s from %s",
             rec["email"], _client_ip())
    return redirect("/login?reset=1")


# ── admin user management (sekcia „Užívatelia") ──────────────────────────────


def _admin_or_none():
    u = _current_user()
    return u if (u and u["is_admin"]) else None


def _forbidden():
    log.warning("auth: non-admin %s denied on %s", session.get("user"), request.path)
    return jsonify({"ok": False, "error": "forbidden"}), 403


@app.route("/api/users", methods=["GET", "POST"])
def api_users():
    me = _admin_or_none()
    if not me:
        return _forbidden()
    if request.method == "GET":
        return jsonify({"users": [
            {"email": e, "is_admin": bool(r.get("is_admin")),
             "created_at": r.get("created_at", "")}
            for e, r in sorted(_load_users().items())]})
    d = request.get_json(silent=True) or {}
    email = _norm_email(d.get("email"))
    pw = d.get("password") or ""
    if not _EMAIL_RE.match(email):
        return jsonify({"ok": False, "error": "neplatný e-mail"}), 400
    if len(pw) < PW_MIN_LEN:
        return jsonify({"ok": False,
                        "error": f"heslo musí mať aspoň {PW_MIN_LEN} znakov"}), 400
    with _lock:
        users = _load_users()
        if email in users:
            return jsonify({"ok": False, "error": "používateľ už existuje"}), 409
        users[email] = {
            "pw_hash": generate_password_hash(pw),
            "is_admin": bool(d.get("is_admin")),
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        _save_users(users)
    log.info("auth: %s created user %s (admin=%s)",
             me["email"], email, bool(d.get("is_admin")))
    return jsonify({"ok": True})


@app.route("/api/users/delete", methods=["POST"])
def api_users_delete():
    me = _admin_or_none()
    if not me:
        return _forbidden()
    email = _norm_email((request.get_json(silent=True) or {}).get("email"))
    if email == me["email"]:
        # also guarantees ≥1 admin always remains (you can't remove yourself)
        return jsonify({"ok": False, "error": "nemôžeš zmazať vlastný účet"}), 400
    with _lock:
        users = _load_users()
        if email not in users:
            return jsonify({"ok": False, "error": "používateľ neexistuje"}), 404
        del users[email]
        _save_users(users)
    log.info("auth: %s deleted user %s", me["email"], email)
    return jsonify({"ok": True})


@app.route("/api/users/admin", methods=["POST"])
def api_users_admin():
    me = _admin_or_none()
    if not me:
        return _forbidden()
    d = request.get_json(silent=True) or {}
    email = _norm_email(d.get("email"))
    is_admin = bool(d.get("is_admin"))
    if email == me["email"] and not is_admin:
        # self-demotion off → the last admin can never disappear
        return jsonify({"ok": False,
                        "error": "nemôžeš odobrať admina sám sebe"}), 400
    with _lock:
        users = _load_users()
        if email not in users:
            return jsonify({"ok": False, "error": "používateľ neexistuje"}), 404
        users[email]["is_admin"] = is_admin
        _save_users(users)
    log.info("auth: %s set admin=%s for %s", me["email"], is_admin, email)
    return jsonify({"ok": True})


@app.route("/api/users/password", methods=["POST"])
def api_users_password():
    me = _admin_or_none()
    if not me:
        return _forbidden()
    d = request.get_json(silent=True) or {}
    email = _norm_email(d.get("email"))
    pw = d.get("password") or ""
    if len(pw) < PW_MIN_LEN:
        return jsonify({"ok": False,
                        "error": f"heslo musí mať aspoň {PW_MIN_LEN} znakov"}), 400
    with _lock:
        users = _load_users()
        if email not in users:
            return jsonify({"ok": False, "error": "používateľ neexistuje"}), 404
        users[email]["pw_hash"] = generate_password_hash(pw)
        _save_users(users)
    log.info("auth: %s set new password for %s", me["email"], email)
    return jsonify({"ok": True})


# Per-line "objednané" state for the Na-objednanie tab (key = '<orderCode>|<itemCode>').
ORDERED = _store("ordered_items.json")


def _load_ordered() -> dict:
    # Corrupt/wrong-type store degrades to {} (like _load_instock) — one bad file
    # must never 500 the whole /api/orders tab.
    return _read_json_store(ORDERED, {})


def _save_ordered(d: dict) -> None:
    _atomic_write_json(ORDERED, d, protect=True)


# Inline pairings entered on the Na-objednanie tab: {forestshop_code: supplier_url}.
# Lets the manager paste a reorder URL straight onto an order line he's ordering —
# covers ANY ordered code, not only the review-dataset subset (decisions.json). Same
# safe load/save as ordered/decisions; NEVER pruned (an order code may be outside the
# review set, so a prune would wrongly drop it). Gitignored data/out → survives deploy.
ORDER_PAIRINGS = _store("order_pairings.json")


def _load_order_pairings() -> dict:
    return _read_json_store(ORDER_PAIRINGS, {})


def _save_order_pairings(d: dict) -> None:
    _atomic_write_json(ORDER_PAIRINGS, d, protect=True)


# Per-variant reorder links (#174): {forestshop_variant_code: supplier_url}. A product
# whose supplier lists a DIFFERENT product page per size (e.g. TRIGONA THERMOPAD
# S/M/L/XL/XXL) is "split": the manager sets one link per size, keyed by the STABLE
# variant code (never array position). import_builder.link_rows reads these for a
# `split`-status decision → per-variant internalNote on the eshop. Same safe atomic
# gitignored store; NEVER pruned → survives deploy.
VARIANT_LINKS = _store("variant_links.json")


def _load_variant_links() -> dict:
    return _read_json_store(VARIANT_LINKS, {})


def _save_variant_links(d: dict) -> None:
    _atomic_write_json(VARIANT_LINKS, d, protect=True)


# Per-line "čaká sa" flag (key='<orderCode>|<itemCode>'): an ACTIVE order line that
# can't be stocked yet — waiting on the supplier, batching more items, or deferred by
# agreement with the customer. Independent of "objednané". Same safe gitignored store;
# NEVER pruned → survives deploy.
WAITING = _store("waiting_items.json")


def _load_waiting() -> dict:
    return _read_json_store(WAITING, {})


def _save_waiting(d: dict) -> None:
    _atomic_write_json(WAITING, d, protect=True)


# Per-line "skladom" / "nedostupné" flags (key='<orderCode>|<itemCode>') — two more
# independent to-order markers, same shape as ordered/waiting. "skladom" = we already
# have it in stock / it's been restocked; "nedostupné" = the supplier can't deliver it.
# The manager toggles each on its own; a row can carry any combination. Same safe
# gitignored stores; NEVER pruned → survive deploy.
INSTOCK = _store("instock_items.json")
UNAVAIL = _store("unavailable_items.json")


def _load_instock() -> dict:
    return _read_json_store(INSTOCK, {})


def _save_instock(d: dict) -> None:
    _atomic_write_json(INSTOCK, d, protect=True)


def _load_unavailable() -> dict:
    return _read_json_store(UNAVAIL, {})


def _save_unavailable(d: dict) -> None:
    _atomic_write_json(UNAVAIL, d, protect=True)


# #291 — WHEN a flag write committed, as a number the client can order answers by.
#
# The tab writes optimistically and keeps a per-(flag, row) counter so a stale answer can
# never overwrite a newer one. That counter is taken when a write is ISSUED, but what
# decides the bytes on disk is the order the server threads took `with _lock:` — the order
# they COMMITTED. Two writes issued inside one round-trip go out on two connections, so
# the two orders can diverge, and the client then stands on the answer that committed
# EARLIER while the store holds the other one. No client-side bookkeeping can close that;
# only the server knows its own commit order, so it says so.
#
# It must never go BACKWARDS, and the wall clock alone does not give that. A tab that is
# open across a deploy holds numbers from the old process; hand it a LOWER one and it
# rejects every answer for the rest of its life, its `confirmed` baseline frozen on stale
# data — the #290 failure shape through a different door. `time.time()` is CLOCK_REALTIME,
# so a restart plus an NTP correction (or a VM snapshot restore, or a hand-set clock) does
# exactly that; so does a SECOND process, which each seed their own counter (#262 records
# one running over this data dir for four days).
#
# So the highest number handed out is RESERVED on disk and the seed is
# `max(wall clock, reservation)`. Reserved in BLOCKS so a click costs no disk write, and
# the block is extended BEFORE the counter reaches it — a reservation the counter has
# already overrun would protect nothing.
COMMIT_SEQ = _store("flag_commit_seq.json")
COMMIT_SEQ_BLOCK = 100_000     # ~one write per disk touch per 100k clicks
_flag_commit_seq = 0
_flag_commit_reserved = 0


def _reserve_commit_seq(upto: int) -> None:
    """Persist „numbers up to `upto` may have been handed out". Derived state, so an
    unwritable file degrades to the wall-clock-only behaviour instead of refusing writes —
    the same stance the export watermark takes."""
    global _flag_commit_reserved
    _flag_commit_reserved = upto
    try:
        _atomic_write_json(COMMIT_SEQ, {"reserved": upto}, indent=None)
    except (StoreLockTimeout, StoreWipeRefused, OSError, ValueError) as e:  # noqa: BLE001
        log.warning("commit-seq rezerváciu sa nepodarilo zapísať (%r) — poradie commitov "
                    "ostáva chránené len systémovým časom", e)


def _seed_commit_seq() -> int:
    """Start (or restart) the clock above BOTH the wall clock and anything a previous
    process may already have handed out.

    Called LAZILY, from the first `_next_commit_seq()` — never at import. Seeding at import
    made importing this module WRITE into OUT, which breaks the invariant the whole #261
    story rests on: a script (or a dry run) that has not yet pointed `WEBREVIEW_OUT` at a
    copy would touch the manager's live dir before its own guard could fire, and the import
    would take the inter-process flock behind a running service."""
    global _flag_commit_seq
    state = _read_json_store(COMMIT_SEQ, {})
    prev = state.get("reserved") if isinstance(state, dict) else None
    if not isinstance(prev, int) or isinstance(prev, bool) or prev < 0:
        prev = 0
    _flag_commit_seq = max(int(time.time() * 1000), prev)
    _reserve_commit_seq(_flag_commit_seq + COMMIT_SEQ_BLOCK)
    return _flag_commit_seq


def _next_commit_seq() -> int:
    """The commit number for the write being made. MUST be called inside the writer's own
    `with _lock:` block — called outside it, two writes could take the same number, or
    take them in the opposite order to the one they committed in, which is precisely the
    thing this number exists to make impossible. Pinned by
    `test_every_endpoint_takes_its_commit_number_while_holding_the_store_lock`, because no
    browser test can see it: writes issued one after another come out increasing either way."""
    global _flag_commit_seq
    if not _flag_commit_seq:          # first write of this process — seed it now, in-lock
        _seed_commit_seq()
    _flag_commit_seq += 1
    if _flag_commit_seq >= _flag_commit_reserved:
        _reserve_commit_seq(_flag_commit_seq + COMMIT_SEQ_BLOCK)
    return _flag_commit_seq


# #211 — the four to-order markers are TWO AXES, not four independent ticks. Decided from
# the manager's own live stores: 27 rows carried a combination and EVERY one of them
# involved „objednané", while „čaká sa"/„skladom"/„nedostupné" never once overlapped each
# other (0 of 41 keys).
#   axis A  „objednané"                            — independent, coexists with anything
#                                                    (objednané + čaká sa na dodávateľa is
#                                                    what the ⏳ button's own tooltip
#                                                    describes; objednané + skladom = it
#                                                    arrived)
#   axis B  „čaká sa" ⊕ „skladom" ⊕ „nedostupné"   — the line's status, mutually exclusive
#                                                    (waiting for it / having it / the
#                                                    supplier not having it contradict)
# The SERVER is the authority: setting an axis-B flag clears the other two in the SAME
# `with _lock:` write, so no reader can ever observe a row holding two contradictory
# statuses. The client only mirrors it. Existing combinations are all legal under this
# semantics (they all carry „objednané" plus at most one axis-B flag), so nothing is
# migrated — the manager's markings stay exactly as he made them.
_STATUS_AXIS = ("waiting", "instock", "unavailable")


def _status_stores() -> dict:
    """Loader/saver per axis-B flag, resolved PER CALL. A module-level tuple of function
    OBJECTS would freeze them at import exactly like a module-level path constant froze
    `OUT` (#261) — a later indirection (or a test) rebinding `_save_waiting` would then
    silently not reach `_write_status_flag`, and the write would look fine while doing
    something else."""
    return {"waiting": (_load_waiting, _save_waiting),
            "instock": (_load_instock, _save_instock),
            "unavailable": (_load_unavailable, _save_unavailable)}


def _write_status_flag(field: str, key: str, on: bool) -> tuple:
    """Write ONE axis-B flag and, when turning it ON, clear the other two for that key.
    Turning a flag OFF says nothing about the others and touches only its own store.
    Returns `(flags, commit_seq)`: the resulting state of all four flags — so the answer
    itself tells the client what the row now IS — and WHEN that state committed (#291),
    which is the only thing that can order two answers correctly.

    The whole read-modify-write sits in ONE `with _lock:` block (which is inter-process),
    so no other WRITER can interleave. It is NOT atomic across FILES, though — `os.replace`
    is atomic per file and this touches up to two. That is why the CLICKED store is saved
    FIRST and the clears after: if a later save fails (disk full, `StoreWipeRefused`), the
    row is left holding a SUPERSET — the new status plus the one it was replacing — which
    the next axis-B write heals by itself. Saving the clear first would instead leave the
    row with NO status at all: the flag the manager just set never landed AND the one it
    replaced was erased, i.e. exactly the irreplaceable-work loss `protect=True` exists to
    prevent. A GET landing in that same window can therefore briefly see both flags; a
    transient superset is self-healing, a lost marking is not."""
    stores = _status_stores()
    with _lock:
        loaded = {name: load() for name, (load, _save) in stores.items()}
        dirty = set()
        for name, d in loaded.items():
            if name == field:
                if on and not d.get(key):
                    d[key] = True
                    dirty.add(name)
                elif not on and key in d:
                    d.pop(key, None)
                    dirty.add(name)
            elif on and key in d:      # the conflicting statuses go out with it
                d.pop(key, None)
                dirty.add(name)
        # A write that changed nothing must not rewrite the file: these stores are
        # `protect=True` (the manager's irreplaceable work), and a store this call never
        # touched must not even be CREATED just because a conflicting flag was checked.
        # Clicked store first — see the docstring on why the ORDER is load-bearing.
        for name in [field] + [n for n in _STATUS_AXIS if n != field]:
            if name in dirty:
                stores[name][1](loaded[name])
        # A mark turned ON is NEW work on that order, so its closure record no longer
        # describes anything the manager has seen — it goes, and the order earns a full
        # grace from the next sync that still finds it closed (PR #295 review, A2).
        if on:
            _clear_closed_seen([key])
        flags = {"ordered": bool(_load_ordered().get(key))}
        flags.update({name: bool(d.get(key)) for name, d in loaded.items()})
        # …inside the SAME lock as the saves above, so the number really does order this
        # write against every other one (#291).
        commit_seq = _next_commit_seq()
    if dirty - {field}:
        log.info("status %s key=%s on=%s cleared=%s",
                 field, key, on, ",".join(sorted(dirty - {field})))
    return flags, commit_seq


# #212 — the four per-line flag stores, as (name, loader, saver). Named here so the prune
# below cannot drift from the set it is supposed to cover; `order_comments.json` is
# deliberately NOT in it (see its own comment — it is keyed per ORDER and is meant to
# outlive the order), and neither is any per-PRODUCT store.
def _line_flag_stores() -> tuple:
    """Resolved PER CALL, for the same reason as `_status_stores`: a module-level tuple of
    function objects would freeze at import and quietly stop reaching a later rebinding."""
    return (("ordered_items.json", _load_ordered, _save_ordered),
            ("waiting_items.json", _load_waiting, _save_waiting),
            ("instock_items.json", _load_instock, _save_instock),
            ("unavailable_items.json", _load_unavailable, _save_unavailable))


def _orders_with_flags(loaded) -> set:
    """Which ORDERS still own at least one per-line mark, across the loaded flag stores.

    `loaded` is `[(name, save, dict), …]` — the dicts are mutated in place by the prune, so
    calling this before and after the deletions answers „who is being timed" and „who is
    left" from the same objects."""
    return {k.split("|", 1)[0] for _n, _s, d in loaded
            for k in d if "|" in k and k.split("|", 1)[0]}


# An orders export carrying FAR fewer orders than the shop makes is not a quiet quarter,
# it is a broken feed — a changed export pattern, a filter left on, a truncated download.
# Same shape and same reasoning as `EXPORT_MIN_CODES` for the catalogue: an absolute floor
# far below reality, which cannot fire on a running shop and cannot disarm itself.
#
# Measured on the live 90-day export (2026-07-28): 521 distinct order codes, 57 of them
# still „Vybavuje sa". 50 is an order of magnitude under that — a rate no month of this
# shop has ever come near — while still catching „the export came back with three rows".
ORDERS_PRUNE_MIN_ORDERS = 50
ORDERS_PRUNE_LOG_KEYS = 20     # how many removed keys are named in the log line

# The statuses that MEAN the order is finished. Closure is membership in THIS set — never
# „everything that is not the one open literal", which is the same negative evidence this
# whole prune exists to avoid, just applied to the status instead of to the presence.
#
# This is not a two-status shop. The live 90-day export (2026-07-28) carries nine:
#   Vybavená 387 · Stornovaná 63 · Vybavuje sa 57 · Vybavená výmena 4 · Osob. odber 3
#   Vybavený Dobropis 3 · Kompletná 2 · Vratený tovar 1 · Výmena tovaru 1
# `Osob. odber` (waiting to be collected) and `Výmena tovaru` (an exchange being handled)
# are LIVE states, and `seen - still_open` called all of them closed.
#
# Two independent signals in that same data pick out exactly the four below, which is why
# the list can be trusted rather than guessed:
#   * the shop's naming convention — the finished form carries a „Vybavená/Vybavený"
#     prefix: „Vybavená výmena" against „Výmena tovaru", „Vybavený Dobropis" against
#     „Vratený tovar";
#   * the tracking number, i.e. the goods physically left: Vybavená 250/387,
#     Vybavená výmena 4/4, Vybavený Dobropis 3/3 — against Kompletná 0/2, Vratený tovar
#     0/1, Výmena tovaru 0/1, Osob. odber 0/3.
# `Stornovaná` is the exception that proves the rule (1/63 — a cancelled order has nothing
# to dispatch) and is unambiguously over.
#
# DELIBERATELY OFF the list, both against the review's suggested minimum:
#   * `Vratený tovar` — by the convention above it is the in-progress counterpart of
#     `Vybavený Dobropis` (the goods came back, the credit note is not issued yet). Two of
#     today's 24 deletions sit on it.
#   * `Kompletná` — both live ones are 2 and 5 days old with NO tracking number, i.e. fresh
#     and not dispatched, which reads as „assembled, waiting to go", not as over.
# The asymmetry decides both: leaving a status off costs a handful of keys that linger until
# the order reaches a status we do recognise; putting one on wrongly deletes the manager's
# irreplaceable marks from an order still being handled — and if it returns to „Vybavuje sa"
# the line is ordered from the supplier a second time, the exact harm the marks prevent.
# Promoting a status onto this list needs evidence; the reverse cannot be undone.
ORDERS_TERMINAL_STATUSES = frozenset({
    "Vybavená", "Vybavená výmena", "Vybavený Dobropis", "Stornovaná",
})

# The statuses we know about and have deliberately judged NOT finished. They are not
# reported as „unknown": the unknown list exists so a genuinely NEW status is noticed, and a
# signal that fires permanently on four expected values is noise that hides the one case it
# was built for. Adding a status here is a claim that somebody weighed it — see the comment
# above for how the four below were weighed.
ORDERS_KNOWN_OPEN_STATUSES = frozenset({
    "Vybavuje sa", "Osob. odber", "Výmena tovaru", "Vratený tovar", "Kompletná",
})

# The statuses that mean „this order is being processed" — the ones whose lines belong on
# „Na objednanie", drive „Nedostupné" and select the customer reminders. It used to be the
# bare literal „Vybavuje sa" written out in FOUR places (here, `build_to_order_rows`,
# `nedostupne.ORDER_STATUS`, `orders_reminder.ORDER_STATUS`).
ORDERS_OPEN_STATUSES = frozenset({"Vybavuje sa"})

# `statusName` is an untrusted CSV cell: a shifted or corrupt export puts arbitrary row
# content there, and whatever lands in the unknown list is logged, persisted into
# `automations.json` and rendered on the card. Bound both axes so a broken export can never
# dump its contents — possibly customer data — into the manager's automation store for good.
ORDERS_UNKNOWN_STATUS_MAX = 20
ORDERS_UNKNOWN_STATUS_MAXLEN = 80

# #209 — Shoptet's order statuses are a TEXT field the shop owner edits, so none of the
# three sets above may stay baked in. They become the DEFAULTS of one store the manager
# edits on the „Sync zo Shoptetu" card, right under the line that reports the statuses this
# app does not recognise: he learns about a new status there, so he classifies it there.
#
# ONE store for all three sets on purpose. The prune's allow-list (PR #292) and the tab's
# „open" literal are two halves of the same question — split across two settings they would
# drift, and a status could end up meaning „still being handled" AND „over" at once, which
# is the one state that deletes the marks of live orders.
ORDER_STATUSES = _store("order_statuses.json")
ORDER_STATUS_DEFAULTS = {
    "to_order": tuple(sorted(ORDERS_OPEN_STATUSES)),
    "terminal": tuple(sorted(ORDERS_TERMINAL_STATUSES)),
    # the DEFAULT third set is the known-open one minus the open literal itself, which now
    # lives in `to_order`; the effective „known" set is the union of all three (see
    # `_order_statuses`), so nothing changes for a shop that never edits the config.
    "known_open": tuple(sorted(ORDERS_KNOWN_OPEN_STATUSES - ORDERS_OPEN_STATUSES)),
    # #296 — the CANCELLED half of `terminal`. „Finished" is not one thing to the Pošta
    # automation: a dispatched order MUST already carry a package number (it is the alarm's
    # denominator), a cancelled one must never be chased at all — mailing „vyzdvihnite si
    # zásielku" to somebody who cancelled is the one thing #93 deliberately never does.
    # Measured on the live export (28.7.2026): renaming „Stornovaná" put 16 cancelled orders
    # back into the chased set (144 → 160), and renaming „Vybavená" flipped the coverage
    # alarm from red to GREEN over a source that was genuinely dead.
    "cancelled": ("Stornovaná",),
}
# The three sets that PARTITION the shop's statuses — no status may be in two of them.
# `cancelled` is deliberately excluded: it REFINES `terminal` (it is required to be a subset
# of it), so overlapping with it is the whole point rather than a contradiction.
ORDER_STATUS_EXCLUSIVE = ("to_order", "terminal", "known_open")
# Same two axes, and the same reason, as `ORDERS_UNKNOWN_STATUS_MAX/MAXLEN`: a status is
# untrusted text that ends up in the log, in `automations.json` and on the card. A shop with
# more than 50 order statuses does not exist; a broken paste does.
ORDER_STATUS_MAX = 50
# What each set is called where the manager reads it — the card and the refusal messages use
# the SAME words, so an error names the box he was editing.
ORDER_STATUS_LABELS = {
    "to_order": "objednávka sa spracúva",
    "terminal": "objednávka je ukončená",
    "known_open": "ostatné známe stavy (nie sú ukončené)",
    "cancelled": "objednávka je zrušená",
}
# The two sets that must never be empty: without `to_order` the tab, „Nedostupné" and the
# customer reminders show nothing, and without `terminal` nothing is ever finished, so the
# prune silently stops. `known_open` may legitimately be empty — it only decides which
# statuses are reported as unclassified.
#
# `cancelled` is load-bearing for the same reason (#296): emptied, „dispatched" becomes the
# WHOLE `terminal` set, so cancelled orders join the coverage alarm's denominator — where they
# can never carry a package number — and any that did would have their customer chased with
# escalation mails.
ORDER_STATUS_REQUIRED = ("to_order", "terminal", "cancelled")


# A status name is free text the shop owner types, and it lands in `log.info(...)`, in the
# prune's `", ".join(open_statuses)` and in `automations.json`. An interior newline in it
# forges a log line; a NUL or an ANSI escape corrupts a terminal reading the log. None of
# them can occur in a real Shoptet status, so they are simply not names (PR #295 review).
_STATUS_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def _clean_status_list(value):
    """A stored/posted set of status names → a clean, bounded, de-duplicated list.

    `None` ONLY when the value is not a list at all. An empty RESULT is returned as `[]`,
    because „absent" and „deliberately emptied" are different answers and only the caller
    knows which sets may be empty (PR #295 review, B4): `known_open: []` is an outcome the
    POST explicitly supports — it means „report every unclassified status" — and the loader
    used to replace it with the four built-in defaults, i.e. silently do the opposite. The
    two LOAD-BEARING sets still cannot be empty (`ORDER_STATUS_REQUIRED`), and that is
    decided by the resolver, not here.

    Names are NFC-normalised (`export_helpers.norm_status`, the same form the export side
    uses — a decomposed name is byte-different, looks identical and matches nothing) and
    names carrying control characters are DROPPED rather than logged."""
    if not isinstance(value, list):
        return None
    out, seen = [], set()
    for v in value:
        if not isinstance(v, str):
            continue
        s = norm_status(v)[:ORDERS_UNKNOWN_STATUS_MAXLEN]
        if _STATUS_CTRL.search(s):
            # a hand-edited file is invited by the docstring above, and the endpoint
            # refuses this at the door — so reaching here means the FILE carries it
            log.error("nastavenie stavov: názov stavu obsahuje riadiace znaky (%r) — "
                      "vynechávam ho, aby sa nedostal do logu", s)
            continue
        # dedup through a SET and stop at the cap: this runs on the stored file on every
        # request path (to-order tab, „Nedostupné", the prune, the reminders), and the
        # docstring above invites a hand-edited file, so a pasted list must not turn into
        # a quadratic scan over an unbounded list.
        if s and s not in seen:
            seen.add(s)
            out.append(s)
            if len(out) >= ORDER_STATUS_MAX:
                break
    return out


def _status_overlap(sets) -> list:
    """Statuses claimed by MORE THAN ONE of the three sets — always a contradiction, and
    every combination of two ends in deleted work or work that is never cleaned:

    * `to_order` ∩ `terminal` — „still being handled" AND „over": the prune deletes the
      marks of live orders, the exact harm #212/#292 exist to prevent;
    * `terminal` ∩ `known_open` — the likeliest mis-edit of three copy-pasteable boxes
      („moved it, forgot to delete it"), and its outcome is deletion while the card's own
      help text for that box promises the marks stay;
    * `to_order` ∩ `known_open` — harmless in effect, but it means the manager thinks one
      of the two boxes does something it does not."""
    seen, dupes = set(), set()
    for key in ORDER_STATUS_EXCLUSIVE:
        for v in sets[key]:
            (dupes if v in seen else seen).add(v)
    return sorted(dupes)


def _cancelled_outside_terminal(sets) -> list:
    """Statuses called CANCELLED that the configuration does not also call FINISHED (#296).

    `cancelled` refines `terminal` — it names which of the finished statuses mean „this order
    was called off" rather than „this order went out". Letting the two drift apart is the very
    failure #296 is about, one box further along: the manager renames „Stornovaná" in
    `terminal` (which he must, or the prune stops recognising it), the stale name lingers here,
    and cancelled orders silently rejoin the set the Pošta automation chases.

    Used by the SAVE endpoint only, and deliberately not by the loader — see the note in
    `_resolve_status_sets` for why a loader-level check would break an upgrade."""
    return sorted(sets["cancelled"] - sets["terminal"])


def _order_statuses_state() -> tuple:
    """`({"to_order": …, "terminal": …, "known_open": …, "cancelled": …}, reason)` — the
    EFFECTIVE sets (each a frozenset), plus `""` or the reason the STORED configuration could
    not be used. Resolved PER CALL (a module-level dict would freeze at import and stop seeing an
    edit until the service restarts, the same trap `_line_flag_stores` avoids).

    „Nothing configured" and „configured, but unreadable" get DIFFERENT answers, and that
    asymmetry is the whole point:

    * no file at all = a fresh install → the measured defaults, so the app works out of
      the box exactly as it did before #209;
    * a file that IS there and cannot be used → we do not know what the manager decided.
      Falling back to the built-in `terminal` list would RE-ARM the prune on precisely the
      statuses he removed — and the card tells him to narrow that list only when he is
      sure, because deleted marks cannot be brought back. So the sets are still returned
      (the tab has to render something), but the reason is too, and the prune refuses on
      it under its own name instead of quietly deleting. `store-prune.md` §1's asymmetry
      argument in one sentence: a missed status costs a few keys, a wrongly included one
      costs irreplaceable work.

    An individual set that is unusable falls back to its own default; a configuration whose
    sets OVERLAP is discarded WHOLE, because patching one side of it would leave the
    manager running a configuration he never wrote. Both are reported as a reason."""
    try:
        raw = _read_json_store(ORDER_STATUSES, {})
        missing = not os.path.exists(os.fspath(ORDER_STATUSES))
    except OSError as e:
        # `_read_json_store` propagates an I/O error on a file that IS there, deliberately:
        # „unreadable" is not evidence that the manager did no work. Every other store
        # breaks ONE tab that way; this one is read by /api/orders, /api/nedostupne,
        # /api/nedostupne/<code> and the prune, so an unhandled OSError here 500s four read
        # paths at once (PR #295 review, B6). It is exactly the „present but unusable"
        # case this function already has an answer for — take it, loudly.
        log.error("nastavenie stavov objednávok sa nedá prečítať (%r) — bežím na "
                  "PREDVOLENÝCH stavoch a mazanie starých značiek sa zastavuje", e)
        raw, missing = {}, False
    sets, why = _resolve_status_sets(raw, missing)
    if why:
        # ONE reason code for the refusal (they all send the manager to the same panel),
        # the specific cause in the log — the operator needs the detail, the card needs a
        # single case to render (store-prune §7).
        log.error("nastavenie stavov objednávok sa nedá použiť (%s) — mazanie starých "
                  "značiek sa zastavuje, kým sa to neopraví", why)
    return sets, ("bad-status-config" if why else "")


def _resolve_status_sets(raw, missing=False) -> tuple:
    """`(sets, why)` for a CANDIDATE configuration dict — the resolution alone, with no
    file access and no logging.

    Split out of `_order_statuses_state` so the SAVE endpoint can run the very rule the
    loader will run, on the configuration it is about to write (PR #295 review, B3). The
    endpoint used to validate the payload AS POSTED, while the loader re-read the file and
    substituted DEFAULTS for anything it found unusable — defaults that then clashed with
    the sets the manager DID write, and a clash discards the configuration WHOLE. The card
    answered „✅ Uložené. Platí to hneď pre celú appku." while the rename reverted, the
    mails went to nobody and the prune was disarmed under a banner naming a „contradictory
    list" the panel rendered as EMPTY — a state unreachable from the screen that caused it.

    `missing=True` means „no file at all" = a fresh install, which is NOT a broken
    configuration (`_read_json_store` answers `{}` to both)."""
    out, why = {}, ""
    for key, default in ORDER_STATUS_DEFAULTS.items():
        vals = _clean_status_list(raw.get(key))
        # `[]` is now a real answer, and only the two LOAD-BEARING sets refuse it: an empty
        # `to_order` blanks the tab and the customer mails, an empty `terminal` silently
        # disarms the prune. An empty `known_open` is a legitimate „report EVERY status I
        # have not classified" and is honoured (PR #295 review, B4).
        usable = vals is not None and (vals or key not in ORDER_STATUS_REQUIRED)
        if not usable and key in raw:
            why = why or "zoznam „%s“ sa nedá použiť" % ORDER_STATUS_LABELS[key]
        out[key] = frozenset(vals) if usable else frozenset(default)
    # A file present with nothing usable in it is not a fresh install.
    if not raw and not missing:
        why = "súbor s nastavením sa nedá prečítať"
    clash = _status_overlap(out)
    if clash:
        why = "stav %s je naraz vo viacerých zoznamoch" % ", ".join(clash)
        out = {k: frozenset(v) for k, v in ORDER_STATUS_DEFAULTS.items()}
    # NOTE — `cancelled ⊄ terminal` is deliberately NOT a reason here, only at the endpoint
    # (#296). Two things decided that. It is not DANGEROUS: „dispatched" is `terminal −
    # cancelled`, so a stray name simply subtracts nothing, and the cancelled filter keeps
    # working off the names it was given. And treating it as a reason would turn a
    # configuration that was valid yesterday — a stored `terminal` narrowed below the built-in
    # default, with no `cancelled` key at all because this version had not shipped yet — into a
    # red banner with the prune disarmed, on upgrade, for a state nobody caused. The endpoint
    # catches the mis-edit where the manager can act on it; the card's „this name is not in the
    # export" warning covers the rest without ever disarming anything.
    return out, why


def _order_statuses() -> dict:
    """The effective status sets, for every caller that does not decide about DELETING."""
    return _order_statuses_state()[0]


def _posta_statuses() -> tuple:
    """`(cancelled, dispatched, reason)` for the Pošta automation (#296).

    „Dispatched" is DERIVED as `terminal − cancelled` and deliberately has no box of its own.
    A fourth editable list would turn ONE rename into TWO edits, and the second one would not
    be on the card where the manager is told a new status appeared — which is precisely the
    silent death #209 removed. Deriving it means the box the prune already forces him to keep
    correct is the only one he has to touch.

    The REASON comes with it (PR #298 review, A2). This used to call `_order_statuses()`, the
    wrapper that drops it — the very fail-open shape automation-health §3 names: „when the
    loader returns (value, reason), a wrapper without the reason is a NEW fail-open path, and
    a consumer that SENDS or DELETES must call the version that carries it". `run_orders_reminder`
    was closed against this in PR #295's review; this is the same configuration, the same
    customer and the same kind of escalation, one path further along — and worse, because a
    manager who correctly reconfigured a renamed cancelled status gets the built-in default
    back, which puts cancelled orders into the set the escalation chases."""
    st, reason = _order_statuses_state()
    return st["cancelled"], st["terminal"] - st["cancelled"], reason


def _dispatched_status_blind_message(eligible: int, dispatched_statuses,
                                     dispatched: int = 0) -> str:
    """The ERROR line for the alarm's own blind spot — naming the CONFIGURED statuses.

    store-prune §7: a refusal that names a literal the shop no longer uses sends the manager
    looking for something that does not exist. The old line told him to fill the new name into
    `DISPATCHED_STATUS`, a name only the source code has.

    Since PR #298's review (A1) the signal also fires with 1-4 recognised orders — a renamed
    main status whose rare siblings survive — so the sentence may no longer assert that not one
    order carries the names. automation-health §4: the flag has to report the number it actually
    triggered on, or the ERROR is false about the only figure the manager can act on."""
    names = ", ".join(sorted(dispatched_statuses)) or "zoznam je prázdny"
    found = ("ANI JEDNA nemá" if not dispatched
             else "len %d z nich má" % dispatched)
    return ("posta: v okne je %d objednávok, ale %s niektorý zo stavov, ktoré "
            "znamenajú „odoslaná“ (%s) — stavy sa v Shoptete zrejme premenovali; kontrola "
            "pokrytia podacích čísel je odteraz slepá, kým sa nové názvy nedoplnia do "
            "nastavenia stavov na karte „Sync zo Shoptetu“" % (eligible, found, names))
# a blank status is not falsy-therefore-ignorable: it is an unreadable one, and dropping it
# would narrow the prune with no trace anywhere. It gets a name instead.
ORDERS_BLANK_STATUS_LABEL = "(prázdny stav)"

# A floor on the age of the ORDER ITSELF — and the name says that, because it is NOT the
# reopen grace it was first written as, and cannot be one.
#
# What it was meant for: a „Vybavená" order can be REOPENED to „Vybavuje sa" — this repo
# says so itself where the reminder dedup store explains why IT keeps records
# (`orders_reminder.py`, DEDUP_RETENTION_DAYS). A line that comes back with the manager's
# „objednané u dodávateľa" silently gone is a line he orders a second time, which is the
# exact harm those marks exist to prevent.
#
# What it actually measures: days since the order was PLACED. The export carries 67 columns
# and its only date is `date` = order creation; there is no status-change or last-modified
# column anywhere in it (re-verified on the live export for #294), so the moment an order
# closed cannot be read from the source at all.
#
# Since #294 it is no longer the whole rule — it is the FLOOR UNDER the real grace, which is
# measured from when this app first SAW the order closed (`ORDERS_CLOSED_SEEN` below). It
# stays because it is the one bound that needs no store of ours: an unreadable date is an
# unknown age, and an unknown age is never „old enough".
ORDERS_PRUNE_MIN_ORDER_AGE_DAYS = 30

# #294 — the REAL grace, and where it is measured from.
#
# The export cannot tell us when an order closed, so the app remembers it: `{order code:
# the ISO day we first saw that order in a terminal status}`. An order placed 40 days ago
# and closed TODAY used to be pruned on the very next hourly run — no grace whatsoever, and
# precisely at the long supplier-wait order these marks exist for (live open flagged orders
# run up to 75 days old).
#
# ORDER OF OPERATIONS IS THE WHOLE DESIGN: the run RECORDS first and DECIDES after. A newly
# closed order therefore gets the full grace on the very first run that sees it. Deciding
# first would make every newly closed order „unrecorded", i.e. fall back to the order-age
# rule, i.e. no grace at all — for ever, not just once. The same order also makes a lost or
# corrupt store fail-CLOSED by construction: it degrades to „we do not know when anything
# closed", everything is re-recorded as closed today, and that run deletes nothing.
#
# It holds NO work of the manager's — it is our own observation log, and LOSING it can only
# DELAY a deletion, never cause one. Hence `protect=False` (a legitimate „the last records
# went with the last keys" write would otherwise raise `StoreWipeRefused` every hour and
# take the prune's own bookkeeping down with it) and no place in `backup_data.sh`, which is
# for stores whose loss costs work nobody can redo.
#
# That argument covers LOSS only, and the PR #295 review was right to say so: a record that
# is WRONG — a day in the past, from a clock stepped back or a hand edit — would cause a
# deletion, because it reads as a grace already served. Losing this store is therefore
# still free, but TRUSTING it is not: `_prune_due` refuses a record that predates its own
# order, which is the one thing a real observation can never do.
ORDERS_CLOSED_SEEN = _store("orders_closed_seen.json")

# How long a key survives after we first see its order closed. Same 30 days as the order-age
# floor, and for the same reason it was reached for: a „Vybavená" order can come back to
# „Vybavuje sa", and a line that returns without „objednané u dodávateľa" is ordered twice.
ORDERS_PRUNE_REOPEN_GRACE_DAYS = 30

# store-prune §1c also asks: do the grace and the source window together leave a key STUCK?
# Here the honest answer is YES, for one narrow band — and that is the DELIBERATE trade,
# because both ways of avoiding it are worse. The export is a 90-day window measured from
# the ORDER date, so an order that closes after day ~60 leaves it before 30 days of grace
# can elapse, and its keys then stay for good.
#
# The two escapes, and why neither is taken:
#
#   * DELETE EARLY, just before the order disappears (a first cut did this at 80 days).
#     It hands ZERO grace to exactly the orders the grace exists for — the long
#     supplier-wait ones — and, because it decided on the ORDER's age alone, it deleted
#     even when the grace store was lost, which quietly made „losing this store cannot cost
#     a mark" false.
#   * KEEP EVALUATING after the order leaves the export, on the surviving record. It reads
#     well until you notice that „not in the export" is also what a TRUNCATED download
#     looks like: the run would then delete keys of orders that are merely missing from a
#     cut file, and „a damaged source can only ever prune FEWER keys" — the property §1
#     buys and `test_losing_rows_from_the_export_can_only_prune_FEWER_keys` pins — is gone.
#     A reopen inside a truncated window would be invisible, and the marks would go.
#
# So: no key is ever deleted without its full grace, and the residue is left to linger.
# `store-prune.md` §1's asymmetry decides it — a few keys that stay cost nothing anyone can
# see, while a mark deleted from a live order sends the manager to order the same line from
# the supplier a second time. The residue is also small: it is only orders that both carry
# marks AND close in the last ~30 days of their window (most close far earlier — the live
# stores held 176 keys across 66 orders when this was measured), so the stores still settle
# instead of growing the way they did before #212.


def _orders_by_openness(orders_csv, state=None):
    """`(seen, still_open, finished, first_date_per_order, unknown, reason)` — `reason` is
    `""` when the export can be believed, and otherwise names WHY it cannot.

    `finished` is the set the caller deletes against, and it is built by POSITIVE
    membership in `ORDERS_TERMINAL_STATUSES`: an order is finished only when it has rows
    and not one of them carries a status outside that list. A status this code has never
    met — a renamed one, a newly added one — therefore keeps its keys instead of losing
    them, which `seen - still_open` got exactly backwards. `unknown` collects those
    statuses so an added one is reported rather than silently narrowing the prune.

    `statusName` is the whole ORDER's status and is repeated on every one of its lines
    (`build_to_order_rows` reads it per row for exactly that reason), so a mixed order
    should not exist; if one ever does, one unfinished row keeps the whole order.

    Three ways this refuses to answer, all fail-closed — the floor counts only `seen`, and
    the `protect=True` shrink guard cannot fire on a prune because a prune IS a legitimate
    read-modify-write, so a lying export has nothing else standing in its way:

    * **no `statusName` column** — every row would read as an unknown status. A changed
      export template does this, and so does a creds URL repointed at another export that
      happens to have a `code` column too. Caught on the HEADER, before a row is judged.
    * **a body that does not end in a newline** — it is incomplete by definition, and the
      row the cut landed in comes back with a truncated or missing status, i.e. a genuinely
      OPEN order reading as closed. The trailing partial row is dropped. (Where the cut
      lands INSIDE a quoted field the slice can keep a partial row — but `code`, `date` and
      `statusName` are the export's first three columns and every multi-line field
      (`remark`, `shopRemark`) sits far past them, so the status is still whole. That
      column ORDER is what makes it safe; a future export template that moves `statusName`
      behind a free-text column would break it silently.)
    * **`csv.Error`** — a bare CR in an unquoted field, or a field past
      `csv.field_size_limit`. It is neither `ValueError` nor `OSError`, so left to escape it
      would sail past the caller's housekeeping `except` and take the whole hourly sync
      down; `errors="replace"` guarantees any byte soup reaches the parser.
    """
    text = (orders_csv.decode("cp1250", errors="replace")
            if isinstance(orders_csv, bytes) else orders_csv or "")
    # An incomplete last line is not data — see the docstring. `splitlines` would also cut
    # a legitimate embedded newline inside a quoted field, so slice on the raw text.
    if text and not text.endswith(("\n", "\r")):
        cut = max(text.rfind("\n"), text.rfind("\r"))
        text = text[:cut + 1] if cut >= 0 else ""
    # ONE resolution per run: the caller passes the state it already read, so the answer and
    # the statuses the answer NAMES cannot come from two different reads with an admin's
    # save in between.
    st, bad_config = _order_statuses_state() if state is None else state
    open_set, terminal = st["to_order"], st["terminal"]
    if bad_config:
        # The manager's own classification is what decides which keys may be deleted. When
        # the stored one cannot be used we do not know it, and guessing it from the
        # built-in defaults would delete on statuses he may have deliberately removed.
        # Refuse, name it, and let the card show the red banner — the same shape as the
        # other three refusals (store-prune §7). Count the orders anyway, so the refusal
        # can still say what it fired on (automation-health §3).
        try:
            got = {(r.get("code") or "").strip()
                   for r in csv.DictReader(io.StringIO(text), delimiter=";")}
        except csv.Error:
            got = set()
        return {c for c in got if c}, set(), set(), {}, set(), bad_config
    # „known" is the union of ALL THREE sets: a status is only reported as unknown when the
    # manager has classified it NOWHERE (store-prune §1a — the signal must mean genuinely
    # UNJUDGED, or it fires permanently on expected values and hides the one new one).
    known = open_set | terminal | st["known_open"]
    seen, still_open, unfinished, dates, unknown = set(), set(), set(), {}, set()
    try:
        rd = csv.DictReader(io.StringIO(text), delimiter=";")
        if "statusName" not in (rd.fieldnames or []):
            # count the orders anyway: an alarm has to return the number it fired on, or
            # the operator is told „your export is wrong" with nothing to go and look at
            # (`.claude/rules/automation-health.md` point 3)
            got = {(r.get("code") or "").strip() for r in rd}
            return {c for c in got if c}, set(), set(), {}, set(), "no-status-column"
        for r in rd:
            code = (r.get("code") or "").strip()
            if not code:
                continue
            seen.add(code)
            # NFC + strip, the SAME form the configuration is stored in — a decomposed
            # name is byte-different, renders identically and matches nothing (PR #295
            # review, B5). `export_helpers.norm_status` is that one form.
            status = norm_status(r.get("statusName"))
            if status in open_set:
                still_open.add(code)
            if status not in terminal:
                unfinished.add(code)
                if status not in known:
                    unknown.add(status[:ORDERS_UNKNOWN_STATUS_MAXLEN] if status
                                else ORDERS_BLANK_STATUS_LABEL)
            if code not in dates:
                dates[code] = (r.get("date") or "").strip()[:10]
    except csv.Error as e:
        log.warning("orders export sa nedá rozparsovať (%r) — prune nemaže nič", e)
        return set(), set(), set(), {}, set(), "unparsable-source"
    return seen, still_open, seen - unfinished, dates, unknown, ""


def _order_age_days(day):
    """Whole days since the order was PLACED, or `None` when its date cannot be read.

    `None` is not zero and not „old": an unreadable or missing date means we do NOT know
    the age, and an unknown age is never „old enough" (the same stance `_parse_date`'s
    callers take for a date that would go to a customer)."""
    try:
        placed = date.fromisoformat((day or "").strip())
    except (TypeError, ValueError):
        return None
    return (date.today() - placed).days


def _grace_elapsed(first_closed) -> bool:
    """Has the reopen grace run out for a record of „first seen closed on this day"? (#294)

    No usable record = NO. A record dated in the future (a clock stepped back, a hand edit)
    gives a negative age and is likewise not due. This is the ONE condition that decides a
    deletion, so everything it cannot read is a refusal."""
    return (_order_age_days(first_closed if isinstance(first_closed, str) else None) or -1) \
        >= ORDERS_PRUNE_REOPEN_GRACE_DAYS


def _closed_seen_day(first_closed, day):
    """The USABLE „first seen closed on this day" record for an order placed on `day`, or
    `None` when there is none we may act on (PR #295 review, A1).

    A record can never predate the order itself — we cannot have seen an order closed
    before it was placed. Such a record is corrupt: a clock stepped back (an NTP
    correction, a VM restored from a snapshot), or a hand edit. And a corrupt record is
    „no record", i.e. a refusal, because the alternative is the whole grace evaporating on
    the very next HEALTHY run — the record reads as long aged, and the order-age floor
    cannot help, since the order genuinely IS old.

    This is also what keeps `ORDERS_CLOSED_SEEN`'s own justification honest. „Losing this
    store can only DELAY a deletion" is true of a LOST store and false of a WRONG one; the
    two callers together restore it — the prune refuses on a contradictory record, and the
    run REPLACES it with today, so the grace starts over instead of the order becoming
    unprunable for ever.

    An order date we cannot read leaves the record alone: it is not evidence against it,
    and `_prune_due` already refuses on an unknown age."""
    if not isinstance(first_closed, str):
        return None
    try:
        seen = date.fromisoformat(first_closed.strip())
    except (TypeError, ValueError):
        return None
    try:
        placed = date.fromisoformat((day or "").strip()[:10])
    except (TypeError, ValueError):
        return first_closed
    return None if seen < placed else first_closed


def _clear_closed_seen(keys) -> None:
    """Drop the closure record of every ORDER a per-line mark was just turned ON for, so
    the mark earns a FULL grace of its own (PR #295 review, A2).

    The grace is kept per ORDER, and the record is written only when there is none — so a
    mark made AFTER the record exists inherits whatever is left of that order's clock,
    which can be nothing at all. It needs no clock games to reach: the order closed 30 days
    ago, was reopened and re-closed between two hourly runs (invisible to the reopen-pop),
    or the manager simply still had the tab open on a stale row. He marks a NEW line today
    and the next sync deletes it the same hour — the exact „he orders the line a second
    time" harm the grace exists to prevent.

    Dropping the record instead of re-keying the store per ITEM is the cheaper trade: the
    order then earns a full grace from the next sync that still sees it closed, and the
    store keeps its one-row-per-order shape and its bounded growth.

    MUST be called inside the caller's `with _lock:` — it does not take the lock itself, so
    it is the legitimate tail of the same read-modify-write that saved the flag. Turning a
    flag OFF is not new work and must NOT reset anybody's clock. Nothing to remove means no
    write at all (store-prune §3), and a failure here is HOUSEKEEPING: the manager's click
    has already landed and must not 500 because our own bookkeeping could not be updated."""
    codes = {k.split("|", 1)[0].strip() for k in keys
             if isinstance(k, str) and "|" in k}
    codes.discard("")
    if not codes:
        return
    try:
        d = _read_json_store(ORDERS_CLOSED_SEEN, {})
        gone = sorted(c for c in codes if c in d)
        if not gone:
            return
        for c in gone:
            d.pop(c, None)
        _atomic_write_json(ORDERS_CLOSED_SEEN, d, protect=False)
        log.info("odklad pred mazaním: nová značka na objednávkach %s — ich záznam o "
                 "zatvorení sa ruší, odklad začne odznova", ", ".join(gone))
    except (StoreLockTimeout, StoreWipeRefused, OSError, ValueError) as e:  # noqa: BLE001
        log.error("odklad pred mazaním: záznam o zatvorení sa nepodarilo zrušiť (%r) — "
                  "značka je uložená, ale môže sa zmazať skôr, než by mala", e)


def _prune_due(day, first_closed) -> bool:
    """May this order's per-line marks go, for an order the export still CARRIES? (#294)

    The grace decides, and the order date only ever REFUSES:

    * an unreadable or missing order date is an unknown age, and an unknown age is never
      „old enough" — the row itself is suspect, so nothing about it is acted on;
    * the order-age floor (`ORDERS_PRUNE_MIN_ORDER_AGE_DAYS`) is a backstop against a
      back-dated or hand-edited record, which could otherwise delete the marks of an order
      placed three days ago. In normal operation it can never be the deciding condition: a
      record day is never earlier than the order day, so 30 days of grace already implies
      30 days of order age.

    „No record" is therefore also a refusal, and it is not a hole: the caller writes the
    record for every FLAGGED finished order before it asks, so an order with marks always
    has one by the time this runs.

    …and a record that CONTRADICTS the order is no record either — see
    `_closed_seen_day`."""
    age = _order_age_days(day)
    if age is None or age < ORDERS_PRUNE_MIN_ORDER_AGE_DAYS:
        return False
    return _grace_elapsed(_closed_seen_day(first_closed, day))


def _prune_orphan_line_flags(orders_csv) -> dict:
    """#212 — drop per-line flag keys whose ORDER the export positively shows as closed.

    `orderCode` is transient: the tab only ever shows „Vybavuje sa" orders, so once an
    order is dispatched its `<orderCode>|<itemCode>` keys can never be seen or cleared
    again and the stores grow without bound (measured on the manager's live data: 141 of
    217 keys were already orphans).

    THE RULE IS POSITIVE EVIDENCE — on BOTH axes, which is the whole safety argument. A key
    goes only when its order IS in the export (presence) AND every one of its rows carries a
    status that MEANS finished (`ORDERS_TERMINAL_STATUSES`). „Not the open literal" is not
    evidence of anything: the shop's status names are configurable text and it uses nine of
    them, so an unknown or newly added one used to read as closed. An order the
    export does not mention is not closed, it is UNSEEN — it may simply be older than
    `ORDERS_EXPORT_WINDOW_DAYS`, or its rows may have been lost from a truncated download.
    Because a cut export can only make rows DISAPPEAR (it cannot rewrite an order's status
    onto its remaining lines), a damaged source can only ever make this prune remove FEWER
    keys, never more. The ticket's own wording — „delete what is not among the open
    orders" — would instead sweep away every key outside the window, which is live work we
    merely cannot see.

    On top of that, a fail-closed floor on the source (`ORDERS_PRUNE_MIN_ORDERS`): an
    export carrying implausibly few orders prunes nothing at all.

    And a GRACE, because closure is not the end — a „Vybavená" order can come back to
    „Vybavuje sa" (#294). It runs from the day this app FIRST SAW the order closed, which
    the run records here (`ORDERS_CLOSED_SEEN`) because the export carries no such date;
    see `_prune_due` for the three conditions and `ORDERS_CLOSED_SEEN` for why the record is
    written BEFORE the decision.

    Read-modify-write in ONE `with _lock:` block (inter-process, #264), removing IN PLACE
    from the loaded map so the write is the legitimate tail of a read-modify-write and the
    `protect=True` shrink guard (#261/#265) stays fully armed. A store with nothing to
    remove is not written at all — a no-op write to a protected store burns its read
    receipt and rewrites a file nothing asked to change (`_write_status_flag` follows the
    same rule).

    Returns `{"pruned": n, "skipped": reason, "orders_seen": n, "orders_open": n,
    "unknown_statuses": [...], "per_store": {...}}` — counts the caller can put in front of
    a human. `unknown_statuses` is the honest cost of the allow-list: a status nobody added
    to it quietly stops being pruned, so the run names it instead of hiding it.

    It deliberately takes NO commit number (#291), unlike every other writer of these
    stores: those numbers order two answers to the SAME open row for a client that is
    watching it, and a pruned key belongs to an order the tab can no longer display at
    all, so there is no client state for it to order against."""
    state = _order_statuses_state()
    seen, still_open, finished, dates, unknown, reason = _orders_by_openness(
        orders_csv, state)
    unknown_statuses = sorted(unknown)[:ORDERS_UNKNOWN_STATUS_MAX]
    # #209 — every answer carries the statuses this run was actually looking for. The
    # „nothing is open" banner used to name the hard-coded „Vybavuje sa", which after a
    # rename sends the manager looking for exactly the wrong thing.
    open_statuses = sorted(state[0]["to_order"])
    base = {"orders_seen": len(seen), "orders_open": len(still_open),
            "unknown_statuses": unknown_statuses, "open_statuses": open_statuses,
            "per_store": {}}
    if reason:
        log.error("prune riadkových príznakov PRESKOČENÝ (%s): export nesie %d objednávok "
                  "— nemaže sa nič", reason, len(seen))
        return {**base, "pruned": 0, "skipped": reason}
    if len(seen) < ORDERS_PRUNE_MIN_ORDERS:
        log.warning("prune riadkových príznakov PRESKOČENÝ: export nesie len %d objednávok "
                    "(minimum %d) — vyzerá neúplne, nemaže sa nič",
                    len(seen), ORDERS_PRUNE_MIN_ORDERS)
        return {**base, "pruned": 0, "skipped": "implausible-source"}
    # An export in which NOTHING is open is a sign we are reading something other than what
    # we think: a renamed open status (Shoptet's status names are shop-configurable TEXT), a
    # different export, a filter left on. On a healthy feed it is impossible — measured: 57
    # open of 521 — so it is not a quiet week.
    #
    # Since the allow-list above, this can no longer turn into a wipe on its own (closure
    # needs positive membership, and a renamed status is simply not on the list), so it is
    # now belt to that braces. It stays because it is still the cheapest signal that the
    # source changed under us, and it names the failure instead of leaving the run to look
    # healthy while pruning nothing. Since #209 the renamed status is also FIXABLE from the
    # card the banner points at, so the message names the configured statuses, not a literal.
    if not still_open:
        log.error("prune riadkových príznakov PRESKOČENÝ: v exporte s %d objednávkami nie "
                  "je ani jedna v stave %s — premenovaný stav alebo iný export? "
                  "Nemaže sa nič", len(seen), ", ".join(open_statuses))
        return {**base, "pruned": 0, "skipped": "no-open-orders"}
    # An added status narrows what gets pruned without anything failing — the honest cost of
    # the allow-list, so it is logged and reported (rendered on the card) rather than hidden.
    if unknown_statuses:
        log.info("prune riadkových príznakov: export nesie stavy, ktoré nepoznám a preto "
                 "ich nepovažujem za ukončené: %s", ", ".join(unknown_statuses))
    per_store, total = {}, 0
    with _lock:
        # Everything below is ONE read-modify-write over five stores, so they are all loaded
        # first: the grace bookkeeping needs to know which orders still own a key AFTER the
        # deletions, and the deletions need the grace to decide.
        loaded = [(name, save, load()) for name, load, save in _line_flag_stores()]
        flagged = _orders_with_flags(loaded)
        closed_seen = _read_json_store(ORDERS_CLOSED_SEEN, {})
        before = dict(closed_seen)     # what „nothing changed" is measured against
        today = date.today().isoformat()
        # #294 — RECORD FIRST, DECIDE AFTER. An order we are seeing closed for the first
        # time starts its grace NOW; deciding before recording would give every newly closed
        # order the order-age rule instead, i.e. no grace at all, which is the ticket's bug.
        # It is also what makes a lost store fail-CLOSED: nothing read → everything recorded
        # today → nothing deleted this run.
        for code in sorted(flagged & finished):
            # „no usable record" also covers one that CONTRADICTS its order (PR #295
            # review): replacing it here is what stops a clock-skewed value from either
            # deleting with no grace or, once the prune refuses it, sitting there for ever.
            if _closed_seen_day(closed_seen.get(code), dates.get(code)) is None:
                closed_seen[code] = today
        # A REOPEN drops the record, so the grace runs again from the SECOND closure — on
        # POSITIVE evidence only (the order is in the export and is not finished). An order
        # the export does not mention is UNSEEN, not reopened, and keeps its record; a
        # truncated download must not restart everybody's grace.
        for code in [c for c in closed_seen if c in seen and c not in finished]:
            closed_seen.pop(code, None)
        closed = {c for c in finished if _prune_due(dates.get(c), closed_seen.get(c))}
        for name, save, d in loaded:
            # a key with no `<order>|<item>` shape cannot be attributed to an order, so it
            # can never be SHOWN to be orphaned — it is left alone rather than guessed at
            gone = sorted(k for k in d
                          if "|" in k and k.split("|", 1)[0] in closed)
            per_store[name] = len(gone)
            if not gone:
                continue
            for k in gone:
                d.pop(k, None)
            save(d)
            total += len(gone)
            log.info("prune %s: odstránených %d osirelých kľúčov (objednávka je v exporte, "
                     "jej stav znamená vybavené a od prvého videného zatvorenia ubehol "
                     "odklad): %s%s", name, len(gone),
                     ", ".join(gone[:ORDERS_PRUNE_LOG_KEYS]),
                     " …" if len(gone) > ORDERS_PRUNE_LOG_KEYS else "")
        # Bounded growth: a record exists to TIME the deletion of that order's keys, so once
        # the last of them is gone it has nothing left to time. The store therefore stays as
        # big as „the orders the manager currently has marked", not as the export window.
        left = _orders_with_flags(loaded)
        for code in [c for c in closed_seen if c not in left]:
            closed_seen.pop(code, None)
        # …and when nothing changed, the file is not touched at all (store-prune §3). It is
        # the record SET that is compared, not a „something happened" flag: a record added
        # and dropped again in the same run leaves the set as it was, and a flag would then
        # create the file to write a value nobody asked for.
        if closed_seen != before:
            _atomic_write_json(ORDERS_CLOSED_SEEN, closed_seen)
    if not total:
        log.info("prune riadkových príznakov: nič na odstránenie (%d objednávok v exporte, "
                 "%d otvorených)", len(seen), len(still_open))
    return {**base, "pruned": total, "skipped": "", "per_store": per_store}


def _flag_snapshot(key: str) -> dict:
    """All four flags for one line — what every flag write answers with. Call it INSIDE the
    writer's own `with _lock:` block (`_lock` is reentrant): read outside it and the answer
    can describe a concurrent LATER write instead of the one being answered."""
    flags = {"ordered": bool(_load_ordered().get(key))}
    flags.update({name: bool(load().get(key)) for name, (load, _s) in _status_stores().items()})
    return flags


# Per-ORDER free-text comment for the Na-objednanie tab (key = '<orderCode>'). #101:
# the manager's note about a WHOLE order — the same thing as the Shoptet admin's
# "Poznámka e-shopu" (the order export's `shopRemark` column), which the shop already
# fills for most orders. Keyed per ORDER (not per line) because the note is about the
# order as a whole, exactly like shopRemark. This is OUR side (always built); writing
# the note BACK into Shoptet's shopRemark is feasible (admin-form automation, verified
# 2026-07-23) but deferred to a follow-up pending the boss's decision (overwrite vs
# append, when to sync). Same safe atomic gitignored store, NEVER pruned → survives
# deploy (an order code lives outside any review set, so a prune would wrongly drop it).
ORDER_COMMENTS = _store("order_comments.json")
ORDER_COMMENT_MAX = 2000   # generous cap — shopRemark in the admin holds multi-line notes


def _load_order_comments() -> dict:
    return _read_json_store(ORDER_COMMENTS, {})


def _save_order_comments(d: dict) -> None:
    _atomic_write_json(ORDER_COMMENTS, d, protect=True)


# „Nedostupné tovary" (#100): per-PRODUCT (itemCode) e-mail state — the two checkbox
# intents (nedostupne/alternativa) + a `sent` dedup map keyed '<orderCode>|<type>' so the
# same customer/order never gets a duplicate of the same e-mail. Gitignored, NEVER pruned
# → survives deploy, exactly like the other manager stores.
NEDOSTUPNE = _store("nedostupne.json")


def _load_nedostupne() -> dict:
    return _read_json_store(NEDOSTUPNE, {})


def _save_nedostupne(d: dict) -> None:
    _atomic_write_json(NEDOSTUPNE, d, protect=True)


# Admin-set custom display names for nav tabs + automations (#173): {key: label}.
# Key = the nav/automation key (TABS/AUTOMATION_TABS keys == Automation.key, plus
# 'users'/'dev' — one flat namespace, validated against NAV_KEYS at write time).
# Renaming an automation IS renaming its tab — the panel never renders a.name
# anywhere on its own (grepped: only the nav label + page title show it), so this
# single map covers both #173 asks (rename automations / rename every tab) with no
# separate name-override plumbing in automation_runner.py. Same safe gitignored
# atomic store as ordered/waiting; survives deploy.
UI_LABELS = _store("ui_labels.json")
UI_LABEL_MAX = 60


def _load_ui_labels() -> dict:
    return _read_json_store(UI_LABELS, {})


def _save_ui_labels(d: dict) -> None:
    _atomic_write_json(UI_LABELS, d, protect=True)


# Free-form notes for the "📝 Poznámky" tab — a Discord replacement for ad-hoc
# reminders ("objednať na výmenu betelavo", "pridať spreje do roy"). A plain list of
# {id, text, done, ts}, newest-first. Same safe gitignored store, atomic save, tolerant
# of a missing/corrupt file. NOT written to any CSV/import → no formula-injection guard
# needed, just a length cap on the free text.
NOTES = _store("notes.json")
NOTE_MAX_LEN = 5000


def _load_notes() -> list:
    return _read_json_store(NOTES, [])


def _save_notes(d: list) -> None:
    _atomic_write_json(NOTES, d, protect=True)


# Supplier assigned on the Na-objednanie tab for an order line that arrived WITHOUT a
# supplier: {forestshop_code: supplier_name}. Keyed by code (a property of the product,
# like order_pairings) so it applies across every order line of that product and is the
# natural key for the eshop write-back. Same safe gitignored store; NEVER pruned →
# survives deploy. Written back to the eshop `supplier` field by the nightly upload.
SUPPLIER_ASSIGN = _store("supplier_assignments.json")


def _load_supplier_assign() -> dict:
    # Corrupt/wrong-type store degrades to {} (like _load_instock/_load_ordered) — this
    # store is written by the app AND by n8n, so it is the most exposed to a partial
    # write. It feeds /api/orders + the nightly write-back; a JSONDecodeError here would
    # 500 the whole to-order tab. Always a dict (a stray non-dict breaks .get() callers).
    return _read_json_store(SUPPLIER_ASSIGN, {})


def _save_supplier_assign(d: dict) -> None:
    _atomic_write_json(SUPPLIER_ASSIGN, d, protect=True)


# GRUBE per-size externalCode store (durable, built by scripts/build_grube_codes.py):
# {code: {itemId, size, deUrl, productId}}. Read-only here — feeds the externalCode
# write-back CSV. Missing/corrupt → {} (the file may not exist until the first gather).
GRUBE_CODES = _store("grube_codes.json")


def _load_grube_codes() -> dict:
    try:
        with open(GRUBE_CODES, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _attach_grube(r, store=None):
    """Attach the GRUBE per-size order code + grube.de link to an order row, keyed by
    its forestshop variant code (r['itemCode']). Mutates and returns r so it's both a
    tiny unit-testable helper and usable inline in the api_orders loop.

    - r['grubeItemId'] = the per-size grube itemId (copyable code) or '' (non-grube /
      unmatched line — most rows).
    - r['grubeDeUrl']  = the grube.de order link, but ONLY if it is https:// (it lands
      in an <a href> on the client; a non-https value is dropped server-side so a
      javascript:/data:/http url can never reach the DOM).

    `store` (the grube_codes map) may be passed once per request; else loaded here."""
    if store is None:
        store = _load_grube_codes()
    g = store.get((r.get("itemCode") or "").strip()) or {}
    r["grubeItemId"] = str(g.get("itemId", "") or "")
    de = str(g.get("deUrl", "") or "")
    r["grubeDeUrl"] = de if de.startswith("https://") else ""
    return r


def _prune_orphan_decisions(products) -> None:
    """At startup, drop decisions whose key matches no product (a stale 'None'/'bad'
    from before stable keys) so the progress count == the import count.

    NEVER prunes against an EMPTY product list: a missing review_data.json leaves
    PRODUCTS == [] (the app deliberately boots dataless), which
    would make EVERY decision look orphaned and delete the manager's whole history
    on the next restart — the same wipe as #261, just via a second door."""
    if not products:
        log.warning("skipping the startup decision prune — 0 products loaded "
                    "(review_data.json missing?); decisions left untouched")
        return
    valid = {p.get("key") for p in products}
    with _lock:
        d0 = _load_decisions()
        d1 = {k: v for k, v in d0.items() if k in valid}
        if len(d1) != len(d0):
            log.info("pruned %d orphan decisions at startup", len(d0) - len(d1))
            _save_decisions(d1, prev=d0)   # a REBUILT map — name the read it came from


try:
    _prune_orphan_decisions(PRODUCTS)
except (StoreLockTimeout, StoreWipeRefused, OSError, ValueError) as e:  # noqa: BLE001
    # Tidying orphans is housekeeping — never a reason for the service to fail to boot.
    # Widened past the lock timeout (PR #265 review): a refused write, or the OSError
    # `_read_json_store` now re-raises on an unreadable decisions.json, would abort the
    # import just as effectively and leave no UI to diagnose it from. ValueError joins
    # them for the same reason it joined the admin bootstrap (PR #265 second review).
    log.error("startup decision prune skipped (%r) — the app keeps serving", e)

def _sweep_stale_tmp_at_startup() -> int:
    """The startup sweep, reading OUT/SRC at CALL time — never frozen at import
    (`test_no_store_path_is_frozen_at_import`, which caught this very line)."""
    return _sweep_stale_tmp(os.fspath(OUT), os.path.dirname(os.fspath(SRC)) or ".")


try:
    _sweep_stale_tmp_at_startup()
except (OSError, StoreWipeRefused) as e:  # noqa: BLE001 — housekeeping; never a reason for the service not to start
    # StoreWipeRefused: the pytest net now guards the sweep too, and a test importing the
    # app with an un-pinned WEBREVIEW_OUT must get a skipped sweep, not a dead import.
    log.error("startup temp-file sweep skipped (%r) — the app keeps serving", e)


_IMG_NOISE = ("logo", "/producer/", ".svg", "/svg/", "placeholder", "no-image",
              "banner", "/img/m/")  # m/ = presta related-product thumbs


def _extract_images(html: str, base: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    imgs: list[str] = []

    def add(s):
        if not s:
            return
        u = urljoin(base, s)
        low = u.lower()
        if any(x in low for x in _IMG_NOISE):
            return
        if u not in imgs:
            imgs.append(u)

    # og:image is reliably THE product's main image on both supplier platforms.
    # Gallery selectors leak related/carousel products (user-confirmed), so we
    # trust ONLY og:image, with a single product-detail image as fallback.
    og = soup.find("meta", attrs={"property": "og:image"})
    if og:
        add(og.get("content"))
    if not imgs:
        for sel in [".p-detail img", ".product-detail img", ".product-images img",
                    "[itemprop='image']"]:
            el = soup.select_one(sel)
            if el:
                add(el.get("src") or el.get("data-src") or el.get("data-zoom-image"))
                if imgs:
                    break
    return imgs[:1]


@app.after_request
def _no_cache(resp):
    # tool is actively developed + the index/decisions must always be fresh
    resp.headers["Cache-Control"] = "no-cache, must-revalidate, max-age=0"
    return resp


_AVAIL_WORDS = ("Skladom", "Na sklade", "Vypredané", "Momentálne nedostupné",
                "Na objednávku", "Posledný kus", "Predaj výrobku skončil", "Na dotaz")


def _supplier_meta(html: str):
    """Best-effort price + availability from a supplier product page."""
    price = ""
    m = re.search(r'(?:product:price:amount|og:price:amount)"\s+content="([0-9]+(?:[.,][0-9]+)?)"', html)
    if not m:
        m = re.search(r'"price"\s*:\s*"?([0-9]+(?:[.,][0-9]+)?)', html)
    if m:
        price = m.group(1).replace(".", ",")   # match our EUR formatting (5,41)
    avail = next((w for w in _AVAIL_WORDS if w in html), "")
    return price, avail


# --------------------------------------------------------------------------- #
# Na objednanie: forestshop "Vybavuje sa" orders → supplier reorder links
# --------------------------------------------------------------------------- #
ORDERS_CACHE = _store("orders_cache.csv")
CUSTOMERS_CACHE = _store("customers_cache.csv")  # hourly Shoptet customer export (cp1250)
ORDERS_MAXAGE = 1800  # s — refresh the cached orders export at most every 30 min (Marek: raz za pol hodinu stačí)
# How far back the orders export is fetched. NAMED because the reminder dedup store's retention
# (orders_reminder.DEDUP_RETENTION_DAYS, 180 d) is justified purely as „twice this window": a
# record old enough to be pruned then cannot belong to an order the export still carries, not
# even a truncated one — which is what makes an age-only prune safe from duplicate customer
# mails (#220). Widening this past half the retention would silently break that argument, so
# test_retention_stays_at_least_twice_the_orders_export_window pins the two together.
ORDERS_EXPORT_WINDOW_DAYS = 90


def _cred(key: str):
    """Read a single KEY=value from the gitignored creds file (data/.shoptet_admin).
    None if missing — callers degrade/refuse rather than crash."""
    try:
        with open(CRED_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith(key + "=") and "=" in line:
                    return line.split("=", 1)[1].strip().strip("'\"") or None
    except FileNotFoundError:
        return None
    return None


def build_to_order_rows(orders_csv, products, decisions, code2pair, variant_links=None,
                        statuses=None):
    """Forestshop orders.csv (cp1250 bytes or str) → to-order rows.

    Keeps the rows whose `statusName` is one of the CONFIGURED „being processed" statuses
    (#209 — `statuses`, defaulting to the store the manager edits; it was the bare literal
    „Vybavuje sa", so renaming the status in Shoptet emptied this tab in silence), drops
    SHIPPING*/BILLING* pseudo-items, and joins
    each item code to its supplier reorder URL via the canonical
    import_builder.link_rows() (code -> internalNote). One row per order line; row
    key = '<orderCode>|<itemCode>'. Pure (no network) -> unit-testable."""
    text = (orders_csv.decode("cp1250", errors="replace")
            if isinstance(orders_csv, bytes) else orders_csv)
    # ONE pass gives both the URL and the decision that produced it: the row must be
    # able to say WHICH reviewed decision owns its link, so the tab can correct that
    # decision in place instead of writing to a parallel store the eshop write-back
    # would discard (#242 — `order_pairing_rows` excludes codes already covered here).
    # `variant_links` is NOT optional in practice: without it the `split` branch (#174)
    # yields nothing, so a product paired PER SIZE renders as unpaired — an empty paste
    # box whose save goes to order_pairings, which the zip discards and the nightly
    # ships, permanently clobbering an already-uploaded per-size link.
    specs = list(import_builder.link_row_specs(products, decisions, code2pair,
                                               variant_links or {}))
    code2url = {s[0]: s[2] for s in specs}
    # A spec whose review key is EMPTY owns nothing the tab can edit: `savePairUrl`
    # routes on `reviewKey`, so advertising a status without a key would send the
    # correction to order_pairings — the silent no-op #242 exists to remove.
    code2owner = {s[0]: (s[3], s[4]) for s in specs if s[3]}
    open_set = (_order_statuses()["to_order"] if statuses is None
                else frozenset(norm_status(s) for s in statuses) - {""})
    rows = []
    for r in csv.DictReader(io.StringIO(text), delimiter=";"):
        if norm_status(r.get("statusName")) not in open_set:
            continue
        code = (r.get("itemCode") or "").strip()
        if not code or re.match(r"^(SHIPPING|BILLING)", code, re.I):
            continue
        order = (r.get("code") or "").strip()
        owner_key, owner_status = code2owner[code] if code in code2owner else ("", "")
        rows.append({
            "key": f"{order}|{code}",
            "orderCode": order,
            "orderDate": (r.get("date") or "").strip()[:10],   # YYYY-MM-DD (drop time)
            "itemCode": code,
            "size": (r.get("itemVariantName") or "").strip(),
            "qty": (r.get("itemAmount") or "").strip(),
            "supplier": (r.get("itemSupplier") or "").strip(),
            "name": (r.get("itemName") or "").strip(),
            "supplierUrl": code2url.get(code, ""),
            # #242 — the reviewed decision behind `supplierUrl` (empty for a code with
            # no decision, which keeps the inline-pairing path exactly as it was). The
            # tab uses it to route a correction at the value it is actually showing.
            "reviewKey": owner_key,
            "reviewStatus": owner_status,
            # #101 — the shop's own internal note about this order ("Poznámka e-shopu"
            # in the admin). Per-ORDER value (same on every line of the order); shown
            # read-only on the row as context next to our editable comment.
            "shopRemark": (r.get("shopRemark") or "").strip(),
        })
    return rows


def _strip_date_params(url: str) -> str:
    """Remove any dateFrom/dateUntil the configured export URL already carries, so the window
    _fetch_orders_csv appends is the one that actually applies (with the parameter present twice
    it is the server that decides which wins, and ORDERS_EXPORT_WINDOW_DAYS would be a lie).
    Every other parameter is kept BYTE-IDENTICAL — the URL carries a `hash` token, so it must
    never be re-encoded by a parse/urlencode round-trip."""
    head, sep, query = url.partition("?")
    if not sep:
        return url
    kept = [p for p in query.split("&")
            if p and p.split("=", 1)[0].lower() not in ("datefrom", "dateuntil")]
    return head + ("?" + "&".join(kept) if kept else "")


def _fetch_orders_csv() -> bytes:
    base = _cred("SHOPTET_ORDERS_URL")
    if not base:
        raise RuntimeError(f"SHOPTET_ORDERS_URL chýba v {CRED_PATH}")
    configured, base = base, _strip_date_params(base)
    if base != configured:
        # Never silent: this is the one function whose window underwrites the dedup prune's
        # „a droppable record cannot belong to a live order" argument, so an operator who
        # hand-widened it in the creds file must see that it was narrowed back.
        log.warning("orders export: SHOPTET_ORDERS_URL niesla vlastné dateFrom/dateUntil — "
                    "ignorované, platí okno %d dní (väzba na dedup retention)",
                    ORDERS_EXPORT_WINDOW_DAYS)
    today = time.strftime("%Y-%m-%d")
    frm = time.strftime("%Y-%m-%d",
                        time.localtime(time.time() - ORDERS_EXPORT_WINDOW_DAYS * 86400))
    sep = "&" if "?" in base else "?"
    r = requests.get(f"{base}{sep}dateFrom={frm}&dateUntil={today}",
                     headers={"User-Agent": UA}, timeout=60)
    r.raise_for_status()
    return r.content


def _orders_csv_cached() -> bytes:
    if (os.path.exists(ORDERS_CACHE)
            and time.time() - os.path.getmtime(ORDERS_CACHE) < ORDERS_MAXAGE):
        with open(ORDERS_CACHE, "rb") as f:
            return f.read()
    data = _fetch_orders_csv()
    _atomic_write_bytes(ORDERS_CACHE, data, mode=0o600)
    return data


def _fetch_export_csv() -> bytes:
    """Full Shoptet catalog export (pattern 14, cp1250 bytes) — the same URL
    scripts/shoptet_import.py downloads as an import-time backup, reused here
    for the hourly read-only refresh (#119)."""
    url = _cred("SHOPTET_EXPORT_URL")
    if not url:
        raise RuntimeError(f"SHOPTET_EXPORT_URL chýba v {CRED_PATH}")
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=120)
        r.raise_for_status()
    except requests.RequestException as e:
        # NEvkladaj `e`/URL do hlášky ani do reťazenej výnimky — obsahuje partner
        # hash (rovnaký dôvod ako scripts/shoptet_import.py::_backup_export).
        # `from None` navyše potlačí chained traceback, aby URL neunikla ani cez
        # log.exception() v automation_runner._execute (last_error v UI je aj
        # tak už len táto sanitizovaná správa, nie surová `e`).
        raise RuntimeError(f"stiahnutie katalógového exportu zlyhalo: {type(e).__name__} "
                           "(URL skrytá — over SHOPTET_EXPORT_URL)") from None
    if not r.content:
        raise RuntimeError("stiahnutý export katalógu je prázdny")
    _refuse_implausible_export_download(len(r.content))
    return r.content


class ExportDownloadRefused(RuntimeError):
    """The catalogue-export download looked truncated, so we kept what is on disk.

    Its OWN type because `run_shoptet_sync` treats it very differently from a network
    failure (PR #280 review, MUST FIX 2): this refusal is self-inflicted and, by
    construction, only ever raised while the on-disk export is BOTH fresh and plausible
    — i.e. exactly when carrying on with those bytes is safe. A network failure proves
    nothing about the on-disk copy, so it stays fatal. Still a RuntimeError, so every
    existing caller and `except` keeps behaving as before."""


def _refuse_implausible_export_download(size: int) -> None:
    """#277 — a truncated download must not silently replace a good export.

    Raising here happens BEFORE `run_shoptet_sync`'s atomic swap, so the bytes already
    on disk survive (the fetch-then-swap contract that function documents).

    That caller catches this ONE type and carries on with those protected bytes,
    surfacing `export_error` (PR #280 review): raising it all the way up used to kill
    the review-card price/stock resync and the customer export every hour, and the
    export then aged past EXPORT_MAX_AGE_S — which is exactly the staleness that
    disarmed the supplier hold. The refusal is NOT a reason to abandon the refresh; it
    only ever fires while what we hold is fresh and plausible.

    Bounded by construction, so it can never deadlock a genuine catalogue shrink: it
    only defends an export that is still USABLE. Once the on-disk copy is older than
    EXPORT_MAX_AGE_S, `_export_row_verdicts` refuses to trust it anyway, so there is
    nothing left worth protecting and the smaller download is let through — which is
    what lets the watermark window then observe the new, smaller reality.

    Bytes vs bytes: comparing a downloaded LINE count against a code-count watermark
    would mix units (multi-line HTML descriptions make lines far exceed codes)."""
    try:
        have = os.path.getsize(SRC)
    except OSError:
        return                              # nothing on disk yet — first ever sync
    age = _export_age_s()
    if not have or age is None or age > EXPORT_MAX_AGE_S:
        return                              # what we hold is stale/empty — let it land
    floor = int(EXPORT_FETCH_MIN_RATIO * have)
    if size < floor:
        # NEvkladaj URL ani `e` do hlášky (partner hash) — same rule as above.
        raise ExportDownloadRefused(
            f"stiahnutý export katalógu je nepravdepodobne malý ({size} B oproti "
            f"{have} B na disku, limit {floor} B) — vyzerá useknuto, nechávam na disku "
            "ten predošlý")


def _fetch_customers_csv() -> bytes:
    """Full Shoptet customer export (cp1250 bytes) — refreshed hourly alongside the
    orders + catalog so customer-facing automations always read fresh data. Nothing
    parses it yet; the raw bytes are cached exactly like the orders export. Secret-
    safe: the partner-hash URL never enters an error message or a chained traceback
    (same rule as _fetch_export_csv)."""
    url = _cred("SHOPTET_CUSTOMERS_URL")
    if not url:
        raise RuntimeError(f"SHOPTET_CUSTOMERS_URL chýba v {CRED_PATH}")
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=120)
        r.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"stiahnutie exportu zákazníkov zlyhalo: {type(e).__name__} "
                           "(URL skrytá — over SHOPTET_CUSTOMERS_URL)") from None
    if not r.content:
        raise RuntimeError("stiahnutý export zákazníkov je prázdny")
    return r.content


@app.route("/")
def index():
    return send_from_directory("templates", "index.html")


@app.route("/favicon.ico")
def favicon():
    return ("", 204)


@app.route("/api/version")
def api_version():
    """Deployed version (single source: parovanie.__version__) — shown in the
    footer for post-deploy verification."""
    return Response(f"v{__version__}", content_type="text/plain; charset=utf-8")


def _grube_de_display(products, decisions):
    """Serve-time DISPLAY normalization for /api/products: a GRUBE product's
    supplier URLs are rebuilt to the canonical grube.DE detail URL in the RESPONSE
    only (review card AND search tab both render these hrefs). GRUBE == grube.de
    (German availability); the eshop internalNote + 'Na objednanie' chip already
    normalize via import_builder.link_rows — this mirrors the SAME rebuild on the
    display path (import_builder.to_grube_de, productId-based).

    The manager's stored .sk pairings are PRESERVED: in-memory PRODUCTS is never
    mutated and decisions.json is never rewritten — only SHALLOW COPIES of the
    GRUBE entries are swapped. Non-GRUBE products/decisions are returned unchanged.
    Fallback to the raw URL when to_grube_de can't parse a productId."""
    to_de = import_builder.to_grube_de
    out_products = []
    grube_keys = set()
    for p in products:
        if p.get("supplier") != "GRUBE":
            out_products.append(p)
            continue
        grube_keys.add(p.get("key"))
        q = dict(p)                                   # shallow copy — don't mutate PRODUCTS
        cands = p.get("candidates")
        if cands:
            new_cands = []
            for c in cands:
                url = c.get("url")
                if url:
                    c = {**c, "url": to_de(url) or url}
                new_cands.append(c)                   # url-less candidate kept as-is
            q["candidates"] = new_cands
        ai_url = p.get("ai_chosen_url")
        if ai_url:
            q["ai_chosen_url"] = to_de(ai_url) or ai_url
        out_products.append(q)
    out_decisions = {}
    for k, d in decisions.items():
        if k in grube_keys and isinstance(d, dict) and (d.get("url") or "").strip():
            d = {**d, "url": to_de(d["url"]) or d["url"]}   # shallow copy of GRUBE decision
        out_decisions[k] = d
    return {"products": out_products, "decisions": out_decisions}


@app.route("/api/products")
def api_products():
    # #135 — never hand the browser a genuinely dead our_images URL (Chrome logs
    # "Failed to load resource" regardless of the #50/#74 onerror placeholder).
    # image_health only MAINTAINS the cache; this is where it's applied — review_data.json
    # itself is never touched.
    cache = _load_image_health().get("cache") or {}
    cleaned, _dropped = image_health.clean_products(PRODUCTS, cache)
    resp = _grube_de_display(cleaned, _load_decisions())
    resp["variant_links"] = _load_variant_links()   # #174 per-variant split links
    return jsonify(resp)


@app.route("/api/images")
def api_images():
    """Title + images for any supplier URL (so a manually entered link pulls its
    data). Cached on disk."""
    url = request.args.get("url", "").strip()
    if not url.startswith("http"):
        return jsonify({"title": "", "images": []})
    key = hashlib.sha1(url.encode()).hexdigest()
    cache = os.path.join(IMGCACHE, key + ".json")
    if os.path.exists(cache):
        with open(cache, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):              # legacy cache format
            data = {"title": "", "images": data}
        data.setdefault("price", "")
        data.setdefault("availability", "")
        return jsonify(data)
    try:
        # Short timeout (was 20s): under a fast-scroll burst the client caps concurrent
        # /api/images calls (#74), but a slow/unresponsive supplier can still tie up a
        # worker for the full timeout — 8s sheds a hung supplier fast enough that the
        # queued requests behind it drain well inside Cloudflare's edge timeout instead
        # of all piling up and failing with 524.
        r = requests.get(url, headers={"User-Agent": UA}, timeout=8)
        if r.ok:
            from parovanie.verify import extract_page
            title = extract_page(r.text).get("title", "")
            imgs = _extract_images(r.text, url)
            price, avail = _supplier_meta(r.text)
        else:
            log.warning("image fetch non-OK url=%s status=%s", url, r.status_code)
            title, imgs, price, avail = "", [], "", ""
    except Exception as e:  # noqa: BLE001 — best-effort scrape; log cause and degrade
        log.warning("image fetch failed url=%s: %r", url, e)
        title, imgs, price, avail = "", [], "", ""
    data = {"title": title, "images": imgs, "price": price, "availability": avail}
    # the dir is created at boot, but OUT can be repointed afterwards (#261 lazy
    # paths) — a missing/unwritable imgcache degrades to "no cache", never a 500
    try:
        os.makedirs(IMGCACHE, exist_ok=True)
        with open(cache, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError as e:  # noqa: BLE001 — the scrape result is still returned below
        log.warning("image cache write failed (%s): %r", cache, e)
    return jsonify(data)


@app.route("/api/decision", methods=["POST"])
def api_decision():
    body = request.get_json(force=True)
    key = str(body.get("key"))
    status = body.get("status")
    # same cap as /api/order-pair and /api/order-decision-url — this value is re-read
    # on every /api/orders and ends up in a Shoptet internalNote cell
    if len(str(body.get("url") or "")) > URL_MAX:
        return jsonify({"ok": False,
                        "error": f"adresa je príliš dlhá (max {URL_MAX} znakov)"}), 400
    with _lock:
        d = _load_decisions()
        if status in (None, "", "undo"):          # undo / un-decide
            d.pop(key, None)
        else:
            d[key] = {"status": status, "url": body.get("url", "").strip()}
        _save_decisions(d)
    # both values are manager-supplied and reach the log verbatim — a CR/LF in either
    # forges a log record of its own (this endpoint does not even validate the scheme,
    # so the forged value is not filtered on the way in)
    log.info("decision key=%s status=%s url=%s",
             _log_safe(key), status, _log_safe(body.get("url", "")))
    return jsonify({"ok": True})


# CSV/spreadsheet formula-injection guard. A cell beginning with one of these is a
# live formula when the file is opened in Excel/LibreOffice. Real forestshop codes,
# pairCodes and http(s) URLs never start with these, so legit cells are untouched
# (Shoptet matching unaffected); a malicious cell is prefixed with ' → inert text.
_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value):
    s = str(value)
    return "'" + s if s[:1] in _FORMULA_LEAD else s


# A supplier product page is a few hundred characters at worst. Every store that keeps
# one is re-read on every /api/orders AND its value ends up in a Shoptet internalNote
# cell, so an unbounded URL is both a store-bloat and an import hazard: 300 kB of 'a'
# was accepted and written straight into decisions.json.
URL_MAX = 2000


def _log_safe(value) -> str:
    """CR/LF out of any free-form value before it reaches a log line — otherwise a URL
    carrying `\\r\\nSet-Cookie: x` forges a log record of its own (log-line injection)."""
    return str(value).replace("\r", "\\r").replace("\n", "\\n")


def _csv_response(header, rows, filename):
    buf = io.StringIO()
    w = writer.shoptet_writer(buf)
    w.writerow(header)
    w.writerows(rows)
    # UTF-8 with BOM — universal, avoids the cp1250 'č'→'è' mojibake. Import into
    # Shoptet as UTF-8.
    data = buf.getvalue().encode("utf-8-sig")
    return Response(data, content_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.route("/api/import")
def api_import():
    # TWO files (Shoptet wipes empty cells, so columns are split — see import_builder):
    #   import_links.csv  = code;pairCode;internalNote (reorder URL in the private field)
    #   import_states.csv = code;pairCode;productVisibility;stock;availability (Vypredané / Predaj skončil)
    dec = _load_decisions()
    # reviewed pairings (decisions) + inline pairings from the Na-objednanie tab.
    # A reviewed decision is authoritative, so inline rows skip any code it already
    # covers (Shoptet aborts on a duplicate code).
    # #174 — a `split`-status decision writes a DIFFERENT link per variant code from
    # variant_links.json (the manager split the product into sizes); good/manual keep
    # one link for the whole product. Both come out of link_rows in one pass.
    link = import_builder.link_rows(PRODUCTS, dec, CODE2PAIR, _load_variant_links())
    link += import_builder.order_pairing_rows(
        _load_order_pairings(), CODE2PAIR, exclude_codes={r[0] for r in link})
    files = [
        ("import_links.csv", import_builder.LINK_HEADER, link),
        ("import_states.csv", import_builder.STATE_HEADER,
         import_builder.state_rows(PRODUCTS, dec, CODE2PAIR)),
        # supplier write-back: only code;pairCode;supplier (own file → can't wipe
        # internalNote/state). Independent column from the link rows, so no exclude.
        ("import_suppliers.csv", import_builder.SUPPLIER_HEADER,
         import_builder.supplier_rows(_load_supplier_assign(), CODE2PAIR)),
        # GRUBE per-size externalCode write-back: only code;pairCode;externalCode (own
        # file → can't wipe internalNote/state). Independent column, so no exclude.
        ("import_externalcode.csv", import_builder.EXTERNALCODE_HEADER,
         import_builder.externalcode_rows(_load_grube_codes(), CODE2PAIR)),
    ]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, header, rows in files:
            s = io.StringIO()
            w = writer.shoptet_writer(s)
            w.writerow(header)
            w.writerows([_csv_safe(c) for c in row] for row in rows)   # formula-injection guard
            z.writestr(name, s.getvalue().encode("utf-8-sig"))
    return Response(buf.getvalue(), content_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="import_forestshop.zip"'})


@app.route("/api/export")
def api_export():
    """All decisions joined to products — for building the corrected import +
    the unavailable list. Stable key = supplier|pairCode."""
    dec = _load_decisions()
    rows = []
    for p in PRODUCTS:
        d = dec.get(p.get("key"))
        if not d:
            continue
        rows.append({"key": p.get("key"), "supplier": p["supplier"], "name": p["name"],
                     "variant_codes": p["variant_codes"], "status": d.get("status"),
                     "url": d.get("url", "")})
    return jsonify({"decisions": rows})


# --------------------------------------------------------------------------- #
# Catalog search + promote-on-pair (CATALOG built at startup from the export)
# --------------------------------------------------------------------------- #
def _save_products(products) -> None:
    """Atomic write of review_data.json (tmp + os.replace). Mirrors the other _save_*
    stores; ensure_ascii=False to keep the Slovak names readable, like build_review_data."""
    _atomic_write_json(DATA, products, indent=None, protect=True)


def _current_for_entry(ce: dict) -> dict:
    """Build the eshop-side `current` snapshot for a freshly paired catalog product by
    scanning the Shoptet export for the FIRST matching row — matched by pairCode (when
    the entry has one) OR by variant code (single-variant products have an EMPTY pairCode,
    so they must be matched by their code). A rare manual action, so a one-off cp1250 scan
    is acceptable. Column mapping mirrors build_review_data's current_of() call. Missing
    export / no matching row -> {} (the card just renders without our-side state — never
    a 500)."""
    pc = (ce.get("pairCode") or "").strip()
    codes = set(ce.get("variant_codes") or [])
    if not os.path.exists(SRC):
        return {}
    csv.field_size_limit(10**9)
    try:
        # newline="" — see _load_catalog (#279): the availability label is free text
        # the shop owner writes, so a multi-line one must reach `current` verbatim.
        with open(SRC, encoding="cp1250", errors="replace", newline="") as f:
            for r in csv.DictReader(f, delimiter=";"):
                rpc = (r.get("pairCode") or "").strip()
                rc = (r.get("code") or "").strip()
                if (pc and rpc == pc) or (rc and rc in codes):
                    # Column names + arg order MUST match build_review_data.py /
                    # resync_export.py (productVisibility — there is NO "visibility"
                    # column; reading the wrong one left vis="" so hidden/blocked
                    # products never got state 3 — snapshot drift).
                    return current_of(
                        (r.get("productVisibility") or "").strip(),
                        (r.get("availabilityInStock") or "").strip(),
                        (r.get("availabilityOutOfStock") or "").strip(),
                        (r.get("price") or "").strip(),
                        (r.get("standardPrice") or "").strip(),
                        (r.get("stock") or "").strip(),
                    )
    except (OSError, csv.Error) as e:
        # Best-effort contract: a missing/unreadable export OR a malformed row
        # (csv.Error — NUL byte / oversized field) degrades to {}, never a 500.
        log.warning("current_for_entry scan failed key=%s: %r", ce.get("key"), e)
    return {}


# Lazily-built {code: ORIG_URL} from the marketing XML — None = not yet attempted.
_CODE2URL = None


def _ensure_code2url() -> dict:
    """Lazily build + cache {code: ORIG_URL} from the marketing XML (the authoritative eshop URL
    by exact variant code). ANY failure (missing XML, parse error, scripts not importable) -> {},
    which is an acceptable result (callers fall back to a search link)."""
    global _CODE2URL
    if _CODE2URL is None:
        _CODE2URL = {}
        try:
            mx = os.path.join(OUT, "marketing.xml")
            if os.path.exists(mx):
                # scripts/ is not on sys.path; load the pure function from the file.
                import importlib.util
                _p = os.path.join(ROOT, "scripts", "url_from_marketing_xml.py")
                _spec = importlib.util.spec_from_file_location("_uxml", _p)
                _mod = importlib.util.module_from_spec(_spec)
                _spec.loader.exec_module(_mod)
                _CODE2URL = _mod.build_code2url(mx)
                log.info("our_url: marketing XML loaded (%d codes)", len(_CODE2URL))
        except Exception as e:  # noqa: BLE001 — best-effort; {} is acceptable
            log.warning("our_url marketing-XML resolve failed: %r", e)
            _CODE2URL = {}
    return _CODE2URL


def _our_url_for_entry(ce: dict):
    """Best-effort forestshop our_url for a promoted product, from the marketing XML's ORIG_URL
    by exact variant code. -> None when no variant code matches (acceptable — search-link fallback)."""
    if not ce or not ce.get("variant_codes"):
        return None
    c2u = _ensure_code2url()
    for c in ce["variant_codes"]:
        if c in c2u:
            return c2u[c]
    return None


# „Nedostupné tovary" alternatives (#100): {code|pairCode -> product name} and {code|pairCode ->
# relatedProduct* codes}, built once from the SAME cp1250 export the search index uses (a one-off
# scan on first tab open — a rarely-used tab, so lazy beats a bigger startup + RSS). Reset on
# resync (fresh export). None = not yet attempted.
_NEDOSTUPNE_CAT = None
# The 8 assigned-alternative columns in the pattern-14 export: the FIRST is bare `relatedProduct`
# (NOT relatedProduct1), then relatedProduct2..8 (boss: relatedProduct1..8 = the alternatives).
_RELATED_COLS = ["relatedProduct"] + [f"relatedProduct{i}" for i in range(2, 9)]


def _ensure_nedostupne_catalog():
    """(code2name, code2related) for the alternatives resolver — keyed by BOTH the variant `code`
    and the `pairCode` (a relatedProduct value can be either). Best-effort: a missing/unreadable
    export -> ({}, {}) (the tab still renders, just without alternative names/links)."""
    global _NEDOSTUPNE_CAT
    if _NEDOSTUPNE_CAT is None:
        code2name, code2related = {}, {}
        if os.path.exists(SRC):
            csv.field_size_limit(10**9)
            try:
                # newline="" — see _load_catalog (#279).
                with open(SRC, encoding="cp1250", errors="replace", newline="") as f:
                    for r in csv.DictReader(f, delimiter=";"):
                        code = (r.get("code") or "").strip()
                        pc = (r.get("pairCode") or "").strip()
                        name = (r.get("name") or "").strip()
                        if name:
                            if code:
                                code2name.setdefault(code, name)
                            if pc:
                                code2name.setdefault(pc, name)
                        rel = [(r.get(c) or "").strip() for c in _RELATED_COLS]
                        rel = [x for x in rel if x][:nedostupne.MAX_ALTERNATIVES]
                        if rel:
                            if code:
                                code2related.setdefault(code, rel)
                            if pc:
                                code2related.setdefault(pc, rel)
            except (OSError, csv.Error) as e:  # malformed row / unreadable → degrade to {}
                log.warning("nedostupne catalog scan failed: %r", e)
                code2name, code2related = {}, {}
        _NEDOSTUPNE_CAT = (code2name, code2related)
        log.info("nedostupne: catalog maps built (%d names, %d with alternatives)",
                 len(code2name), len(code2related))
    return _NEDOSTUPNE_CAT


def _resolve_alternatives(code: str):
    """(product_name, [{code,name,url}]) — the resolve() callback nedostupne.build_view expects.
    Alternatives = the product's relatedProduct* codes resolved to a name (catalog) + a link
    (marketing XML ORIG_URL, else a forestshop search-by-code link so it is ALWAYS clickable)."""
    code2name, code2related = _ensure_nedostupne_catalog()
    c2u = _ensure_code2url()
    alts = []
    for rc in code2related.get(code, []):
        url = c2u.get(rc) or ("https://www.forestshop.sk/vyhladavanie/?string=" + quote(rc))
        alts.append({"code": rc, "name": code2name.get(rc, rc), "url": url})
    return code2name.get(code, ""), alts


def _review_products_for(e: dict, by_paircode=None, by_code=None) -> list:
    """ALL in-review products matching a catalog entry — by pairCode (most review entries
    are keyed "SUPPLIER|pairCode", so a key==pairCode test missed them, C1) PLUS any
    sharing a variant code (single-variant products have an empty pairCode, so matching an
    empty e["pairCode"] against PRODUCTS would wrongly hit every other empty-pairCode
    product; match those by code instead).

    #64: the SAME pairCode can be reviewed under TWO+ DIFFERENT suppliers (e.g.
    GRUBE|425 AND WETLAND|425 for one forestshop product, when it was matched against
    candidates from more than one supplier) — a "first match wins" lookup silently hid
    every duplicate past the first, and its decision could never be repaired via search.
    Returns every distinct match (by identity — a product matched by BOTH pairCode and a
    shared code counts once), in stable PRODUCTS order. Lookup maps are built once per
    request by the caller."""
    seen: set = set()
    out: list = []
    pc = e.get("pairCode")
    if pc and by_paircode is not None:
        for p in by_paircode.get(pc, []):
            if id(p) not in seen:
                seen.add(id(p))
                out.append(p)
    if by_code is not None:
        for c in e.get("variant_codes") or []:
            for p in by_code.get(c, []):
                if id(p) not in seen:
                    seen.add(id(p))
                    out.append(p)
    return out


def _search_results_for_entry(e: dict, decisions=None, by_paircode=None, by_code=None) -> list:
    """Shape one catalog entry into one-or-more /api/search result rows. `key`
    (pairCode-or-code) is the catalog identity the client promotes-and-pairs by.

    Normally a catalog entry matches at most one in-review product, so this returns a
    single row. #64: when the pairCode is reviewed under MULTIPLE suppliers, EVERY
    matching review product gets its OWN row — own `idx`/`our_url`/`paired_url` and a
    `review_key` (that product's REAL key) the client uses to open/repair THAT specific
    one, instead of always landing on the first duplicate. A catalog entry with zero
    review matches still returns exactly one "not yet paired" row (unchanged shape).

    price/stock/state come from the catalog entry (the manager's "almost no data"
    complaint) and are the same on every row of a duplicated entry. `paired_url` = the
    matching product's CURRENT decision URL (good/manual only), read under its REAL key;
    a GRUBE product's URL is DISPLAY-normalized to grube.de (mirrors /api/products —
    storage untouched). `decisions` is loaded ONCE per request by the caller."""
    base = {
        "key": e["key"],
        "pairCode": e["pairCode"],
        "name": e["name"],
        "supplier": e["supplier"],
        "codes": e["variant_codes"],
        "image": e["image"],
        "in_review": e["in_review"],
        "price": e.get("price", ""),
        "stock": e.get("stock", 0),
        "state": e.get("state", 1),
    }
    matches = _review_products_for(e, by_paircode, by_code)
    if not matches:
        return [dict(base, our_url=None, idx=None, paired_url=None, review_key=None,
                      review_supplier=None)]
    rows = []
    for p in matches:
        paired_url = None
        if decisions is not None:
            d = decisions.get(p.get("key"))
            if isinstance(d, dict) and d.get("status") in ("good", "manual"):
                url = (d.get("url") or "").strip()
                if url:
                    if p.get("supplier") == "GRUBE":
                        url = import_builder.to_grube_de(url) or url
                    paired_url = url
        # review_supplier = THIS matching product's OWN supplier (may differ from the
        # catalog entry's generic `supplier` column) — #64: when a pairCode is reviewed
        # under 2+ suppliers, every row shares the same catalog `supplier`, so the client
        # needs the per-match supplier to tell duplicate rows apart.
        rows.append(dict(base, our_url=p.get("our_url"), idx=p.get("idx"),
                          paired_url=paired_url, review_key=p.get("key"),
                          review_supplier=p.get("supplier")))
    return rows


def _product_lookups():
    """{pairCode: [products]}, {code: [products]} over PRODUCTS — built once per
    /api/search so _search_results_for_entry finds EVERY in-review product matching a
    catalog entry (by pairCode or shared code), not just the first. #64: a pairCode CAN
    be reviewed under more than one product (different suppliers) — collecting lists
    (instead of first-writer-wins) is what lets every duplicate surface as its own row."""
    by_paircode: dict = {}
    by_code: dict = {}
    for p in PRODUCTS:
        pc = p.get("pairCode")
        if pc:
            by_paircode.setdefault(pc, []).append(p)
        for c in (p.get("variant_codes") or []):
            by_code.setdefault(c, []).append(p)
    return by_paircode, by_code


@app.route("/api/search")
def api_search():
    """Accent-insensitive catalog search over the whole per-product blob (name / supplier
    / codes / externalCode / description / category / manufacturer / ean / productNumber)
    — pure search_catalog over the startup CATALOG. Empty/short query -> no results.
    A catalog entry reviewed under more than one supplier (#64) yields more than one
    result row — see _search_results_for_entry."""
    q = request.args.get("q", "")
    dec = _load_decisions()   # once per request, not per result
    by_paircode, by_code = _product_lookups()
    results = []
    for e in search_catalog(CATALOG, q):
        results.extend(_search_results_for_entry(e, dec, by_paircode, by_code))
    return jsonify({"results": results})


@app.route("/api/search-pair", methods=["POST"])
def api_search_pair():
    """Manually pair a catalog product to a supplier URL from the search box. Identified
    by `key` (the catalog entry's pairCode-or-code; legacy `pairCode` accepted as a
    fallback). If the product is not yet in the review set it is PROMOTED (a minimal
    review_data entry built from the catalog row + the export `current` snapshot +
    best-effort our_url), appended to PRODUCTS and persisted; then a `manual` decision is
    recorded. The URL must be http(s) (else 400); an unknown key -> 404.

    #64: `review_key` (optional) targets a SPECIFIC review product's REAL key —
    required to repair one of several review entries duplicated under the same catalog
    key (the same pairCode reviewed under two+ different suppliers, e.g. GRUBE|425 AND
    WETLAND|425). Without it, "first PRODUCTS match wins" is ambiguous and can silently
    fix the wrong duplicate. An unknown `review_key` -> 404 (never falls back to the
    ambiguous scan)."""
    body = request.get_json(silent=True) or {}
    key = str(body.get("key") or body.get("pairCode") or "").strip()
    url = str(body.get("url") or "").strip()
    review_key = str(body.get("review_key") or "").strip()
    # authoritative URL guard (matches /api/order-pair) — blocks javascript:/data: and
    # malformed values from reaching the import's internalNote / a CSV cell.
    if not re.match(r"^https?://", url):
        return jsonify({"ok": False, "error": "url must start with http(s)://"}), 400
    # same cap as every other URL-storing endpoint: this writes a decision, which is
    # re-read on every /api/orders and whose value reaches a Shoptet internalNote cell
    if len(url) > URL_MAX:
        return jsonify({"ok": False,
                        "error": f"adresa je príliš dlhá (max {URL_MAX} znakov)"}), 400
    ce = CATALOG.get(key)
    if not ce:
        return jsonify({"ok": False, "error": "unknown key"}), 404
    if review_key and not any(p.get("key") == review_key for p in PRODUCTS):
        return jsonify({"ok": False, "error": "unknown review_key"}), 404
    # Match an already-in-review product by pairCode (when the entry has one) OR by a
    # shared variant code. Most review entries are keyed "SUPPLIER|pairCode" (e.g.
    # GRUBE|425); a key==entry test missed every such entry → it wrongly promoted a
    # DUPLICATE entry AND wrote the decision under a key link_rows never reads, silently
    # dropping the manager's corrected URL (C1). Single-variant products (empty pairCode)
    # match by code — an empty-pairCode == test would collide with every other such item.
    pc = (ce.get("pairCode") or "").strip()
    entry_codes = set(ce.get("variant_codes") or [])

    def _find_existing():
        if review_key:
            # explicit target (#64) — bypasses the ambiguous first-match scan below,
            # which is exactly what's needed when the same pairCode is duplicated
            # across suppliers. Validity already checked above (404 if unknown).
            return next((p for p in PRODUCTS if p.get("key") == review_key), None)
        for p in PRODUCTS:
            if pc and p.get("pairCode") == pc:
                return p
            if entry_codes & set(p.get("variant_codes") or []):
                return p
        return None

    in_review = _find_existing() is not None
    # The two heavy read-only scans (55 MB cp1250 export + 59 MB marketing XML) depend
    # ONLY on the catalog entry, never on mutable state → compute them OUTSIDE the lock so
    # a promote never stalls every other write endpoint for seconds. Needed only when
    # promoting a genuinely NEW catalog product; an existing entry just gets its decision
    # rewritten.
    if not in_review:
        snapshot = _current_for_entry(ce)
        our_url = _our_url_for_entry(ce)
        supplier = supplier_from_url(url, config.SUPPLIERS)
    with _lock:
        # re-check under the lock (append-only store → monotonic; a tiny TOCTOU on
        # concurrent same-key promotes is fine — single manager user — and this dedups)
        existing = _find_existing()
        if existing is None:
            entry = build_promoted_entry(ce, snapshot, our_url, supplier, len(PRODUCTS))
            PRODUCTS.append(entry)
            _save_products(PRODUCTS)
            ce["in_review"] = True   # keep the catalog snapshot consistent for re-search
            target_key = entry["key"]  # promoted entry's key == pairCode-or-code
            promoted = True
            log.info("search-pair promoted key=%s supplier=%s codes=%d our_url=%s",
                     _log_safe(target_key), _log_safe(entry["supplier"]),
                     len(entry["variant_codes"]), _log_safe(entry["our_url"]))
        else:
            target_key = existing["key"]   # write under the REAL key (e.g. GRUBE|425)
            promoted = False
        dec = _load_decisions()
        dec[target_key] = {"status": "manual", "url": url}
        _save_decisions(dec)
    # `^https?://` happily passes a URL carrying CR/LF — sanitise before it reaches a log
    log.info("search-pair decision key=%s url=%s promoted=%s",
             _log_safe(target_key), _log_safe(url), promoted)
    return jsonify({"ok": True, "promoted": promoted, "key": target_key})


@app.route("/api/ordered", methods=["GET", "POST"])
def api_ordered():
    """Per-line 'objednané' state (key='<orderCode>|<itemCode>'), persisted like
    decisions. GET -> the map; POST {key, ordered} toggles a single line."""
    if request.method == "GET":
        return jsonify({"ordered": _load_ordered()})
    body = request.get_json(force=True)
    key = str(body.get("key") or "").strip()   # blank key must never write a "None" entry
    if not key:
        return jsonify({"ok": False, "error": "key required"}), 400
    ordered = bool(body.get("ordered"))
    with _lock:
        d = _load_ordered()
        if ordered:
            d[key] = True
        else:
            d.pop(key, None)
        _save_ordered(d)
        if ordered:                   # PR #295 review A2 — a new mark earns its own grace
            _clear_closed_seen([key])
        # axis A: „objednané" clears nothing — it says we placed the order, not what the
        # line's status is. It still answers with the resulting state of all four flags,
        # so the client has ONE shape to mirror (#211). Snapshot INSIDE the same lock, or
        # the answer can describe a concurrent later write instead of this one.
        flags = _flag_snapshot(key)
        commit_seq = _next_commit_seq()          # #291 — inside the same lock as the save
    log.info("ordered key=%s ordered=%s", key, ordered)
    return jsonify({"ok": True, "flags": flags, "commitSeq": commit_seq})


@app.route("/api/ordered/bulk", methods=["POST"])
def api_ordered_bulk():
    """Mark a WHOLE supplier group ordered/un-ordered in one atomic write — the
    manager orders everything from a supplier at once instead of clicking 15-20
    rows. POST {keys:[...], ordered:bool}; each key is a per-line '<orderCode>|
    <itemCode>' (same store as /api/ordered). Blank/non-string keys are dropped; a
    missing/non-list `keys` → 400."""
    body = request.get_json(force=True)
    raw = body.get("keys")
    if not isinstance(raw, list):
        return jsonify({"ok": False, "error": "keys must be a list"}), 400
    keys = [k for k in (str(x or "").strip() for x in raw) if k]
    if not keys:
        return jsonify({"ok": False, "error": "no valid keys"}), 400
    ordered = bool(body.get("ordered"))
    with _lock:
        d = _load_ordered()
        for key in keys:
            if ordered:
                d[key] = True
            else:
                d.pop(key, None)
        _save_ordered(d)
        if ordered:                   # PR #295 review A2 — a new mark earns its own grace
            _clear_closed_seen(keys)
        commit_seq = _next_commit_seq()          # #291 — inside the same lock as the save
    log.info("ordered bulk n=%d ordered=%s", len(keys), ordered)
    return jsonify({"ok": True, "count": len(keys), "commitSeq": commit_seq})


@app.route("/api/waiting", methods=["GET", "POST"])
def api_waiting():
    """Per-line 'čaká sa' flag (key='<orderCode>|<itemCode>'): active order line that
    can't be stocked yet. GET -> the map; POST {key, waiting} toggles a single line.
    Same shape as /api/ordered, independent state."""
    if request.method == "GET":
        return jsonify({"waiting": _load_waiting()})
    body = request.get_json(force=True)
    key = str(body.get("key") or "").strip()   # blank key must never write a "None" entry
    if not key:
        return jsonify({"ok": False, "error": "key required"}), 400
    waiting = bool(body.get("waiting"))
    flags, commit_seq = _write_status_flag("waiting", key, waiting)   # axis B — clears the other two
    log.info("waiting key=%s waiting=%s", key, waiting)
    return jsonify({"ok": True, "flags": flags, "commitSeq": commit_seq})


@app.route("/api/instock", methods=["GET", "POST"])
def api_instock():
    """Per-line 'skladom' flag (key='<orderCode>|<itemCode>') — independent of
    ordered/waiting/unavailable. GET -> the map; POST {key, instock} toggles one line."""
    if request.method == "GET":
        return jsonify({"instock": _load_instock()})
    body = request.get_json(force=True)
    key = str(body.get("key") or "").strip()
    if not key:
        return jsonify({"ok": False, "error": "key required"}), 400
    instock = bool(body.get("instock"))
    flags, commit_seq = _write_status_flag("instock", key, instock)   # axis B — clears the other two
    log.info("instock key=%s instock=%s", key, instock)
    return jsonify({"ok": True, "flags": flags, "commitSeq": commit_seq})


@app.route("/api/unavailable", methods=["GET", "POST"])
def api_unavailable():
    """Per-line 'nedostupné' flag (key='<orderCode>|<itemCode>') — independent of
    ordered/waiting/instock. GET -> the map; POST {key, unavailable} toggles one line."""
    if request.method == "GET":
        return jsonify({"unavailable": _load_unavailable()})
    body = request.get_json(force=True)
    key = str(body.get("key") or "").strip()
    if not key:
        return jsonify({"ok": False, "error": "key required"}), 400
    unavailable = bool(body.get("unavailable"))
    flags, commit_seq = _write_status_flag("unavailable", key, unavailable)   # axis B — clears the rest
    log.info("unavailable key=%s unavailable=%s", key, unavailable)
    return jsonify({"ok": True, "flags": flags, "commitSeq": commit_seq})


# --------------------------------------------------------------------------- #
# Nedostupné tovary (#100): flagged-unavailable products collected in one place,
# joined to open orders (customers) + relatedProduct alternatives, with two
# preview-gated customer e-mails. SAFETY: no e-mail is EVER sent automatically —
# a checkbox only persists intent; a send needs the explicit preview → Odoslať.
# --------------------------------------------------------------------------- #
def _nedostupne_view(csv_bytes, unavail=None, state=None):
    """The ONE place the „Nedostupné" view is built — so the CONFIGURED „being processed"
    statuses (#209) reach it and a second call site cannot quietly fall back to the literal
    the module still defaults to."""
    return nedostupne.build_view(
        csv_bytes,
        _load_unavailable() if unavail is None else unavail,
        _load_nedostupne() if state is None else state,
        _resolve_alternatives,
        order_status=_order_statuses()["to_order"])


@app.route("/api/nedostupne")
def api_nedostupne():
    """Tab data: flagged-unavailable products grouped per product, joined to open orders +
    alternatives + the persisted e-mail state. Degrades to [] on orders fetch error."""
    try:
        csv_bytes = _orders_csv_cached()
    except Exception as e:  # noqa: BLE001 — degrade to empty, log the cause
        log.warning("nedostupne orders fetch failed: %r", e)
        return jsonify({"products": [], "error": str(e)})
    # The view still RENDERS (on the defaults) — but it says so, because the statuses it
    # rendered by may not be the manager's (PR #295 review, B1).
    return jsonify({"products": _nedostupne_view(csv_bytes),
                    "bad_status_config": bool(_order_statuses_state()[1])})


@app.route("/api/nedostupne/state", methods=["POST"])
def api_nedostupne_state():
    """Persist ONE checkbox intent (field=nedostupne|alternativa) for a product. NO e-mail is
    sent — this is only the manager's visual mark; sending is the separate preview-gated action."""
    body = request.get_json(silent=True) or {}
    code = str(body.get("code") or "").strip()
    field = str(body.get("field") or "").strip()
    value = bool(body.get("value"))
    if not code or field not in nedostupne.EMAIL_TYPES:
        return jsonify({"ok": False, "error": "neplatná požiadavka"}), 400
    with _lock:
        d = _load_nedostupne()
        rec = d.setdefault(code, {})
        if value:
            rec[field] = True
        else:
            rec.pop(field, None)
        # tidy: drop a record that carries no flag AND no sent history
        if not any(rec.get(t) for t in nedostupne.EMAIL_TYPES) and not rec.get("sent"):
            d.pop(code, None)
        _save_nedostupne(d)
    log.info("nedostupne state code=%s field=%s value=%s", code, field, value)
    return jsonify({"ok": True})


def _nedostupne_orders_alts(code: str):
    """(product_name, order_rows, alternatives) for ONE flagged product from the cached orders +
    catalog. Raises on orders-fetch failure (caller maps to 502)."""
    csv_bytes = _orders_csv_cached()
    orders = nedostupne.affected_orders(csv_bytes, {code},
                                        _order_statuses()["to_order"]).get(code, [])
    cat_name, alts = _resolve_alternatives(code)
    name = (orders[0]["itemName"] if orders and orders[0].get("itemName") else "") or cat_name
    return name, orders, alts


@app.route("/api/nedostupne/preview", methods=["POST"])
def api_nedostupne_preview():
    """Preview a customer e-mail WITHOUT sending: the recipients still to notify (dedup applied)
    and the rendered e-mail (personalised to the first recipient). SAFE — reads only."""
    body = request.get_json(silent=True) or {}
    code = str(body.get("code") or "").strip()
    type_key = str(body.get("type") or "").strip()
    if not code or type_key not in nedostupne.EMAIL_TYPES:
        return jsonify({"ok": False, "error": "neplatná požiadavka"}), 400
    try:
        name, orders, alts = _nedostupne_orders_alts(code)
    except Exception as e:  # noqa: BLE001 — orders fetch failed
        log.warning("nedostupne preview fetch failed: %r", e)
        return jsonify({"ok": False, "error": "objednávky sa nepodarilo načítať"}), 502
    sent = (_load_nedostupne().get(code) or {}).get("sent") or {}
    plan = nedostupne.plan_sends(orders, sent, type_key)
    lead_name = plan[0]["billFullName"] if plan else ""
    if type_key == nedostupne.TYPE_UNAVAILABLE:
        subject, html = nedostupne.build_unavailable_email(lead_name, name)
    else:
        subject, html = nedostupne.build_alternative_email(lead_name, name, alts)
    already = sum(1 for o in orders if sent.get(f"{o['orderCode']}|{type_key}"))
    return jsonify({"ok": True, "product": name, "subject": subject, "html": html,
                    "alternatives": alts, "already_sent": already,
                    "recipients": [{"orderCode": r["orderCode"], "email": r["email"],
                                    "name": r["billFullName"]} for r in plan]})


@app.route("/api/nedostupne/send", methods=["POST"])
def api_nedostupne_send():
    """Send the chosen customer e-mail (type=nedostupne|alternativa) to every not-yet-notified
    customer with an open order for this product. Dedup per order+type (never double-send). The
    SMTP round-trip runs OUTSIDE the global store lock (like run_orders_reminder) and each success
    is persisted immediately (a crash mid-batch must not re-send). A per-recipient re-check under
    lock NARROWS (does not fully close) the concurrent-send window — the frontend disabling the
    button covers the single-user double-click; two truly simultaneous senders could still both
    pass the re-check, the same accepted trade-off as run_orders_reminder (a claim-before-send lock
    is deferred)."""
    body = request.get_json(silent=True) or {}
    code = str(body.get("code") or "").strip()
    type_key = str(body.get("type") or "").strip()
    if not code or type_key not in nedostupne.EMAIL_TYPES:
        return jsonify({"ok": False, "error": "neplatná požiadavka"}), 400
    try:
        name, orders, alts = _nedostupne_orders_alts(code)
    except Exception as e:  # noqa: BLE001 — orders fetch failed
        log.warning("nedostupne send fetch failed: %r", e)
        return jsonify({"ok": False, "error": "objednávky sa nepodarilo načítať"}), 502
    with _lock:
        sent = dict((_load_nedostupne().get(code) or {}).get("sent") or {})
    plan = nedostupne.plan_sends(orders, sent, type_key)
    if not plan:
        return jsonify({"ok": True, "sent": 0, "failed": 0, "skipped": 0,
                        "note": "žiadni noví príjemcovia"})
    sent_ok = failed = skipped = 0
    now_iso = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    for r in plan:
        sk = f"{r['orderCode']}|{type_key}"
        with _lock:   # concurrent send for the same order+type already recorded → skip
            if (_load_nedostupne().get(code) or {}).get("sent", {}).get(sk):
                skipped += 1
                continue
        if type_key == nedostupne.TYPE_UNAVAILABLE:
            subject, html = nedostupne.build_unavailable_email(r["billFullName"], name)
        else:
            subject, html = nedostupne.build_alternative_email(r["billFullName"], name, alts)
        if _send_mail_html(r["email"], subject, html):
            sent_ok += 1
            low = (r["email"] or "").strip().lower()
            with _lock:
                d = _load_nedostupne()
                smap = d.setdefault(code, {}).setdefault("sent", {})
                # Record EVERY open order sharing this customer's e-mail + type — not
                # just the winning order. plan_sends dedups per e-mail, so a repeat
                # buyer with the SAME product in two open orders is planned once; if we
                # marked only that one order, the sibling would stay unrecorded and the
                # next send would re-e-mail the same customer (#100 review, Finding 1).
                for o in orders:
                    if (o.get("email") or "").strip().lower() == low:
                        smap[f"{o['orderCode']}|{type_key}"] = {"at": now_iso, "email": r["email"]}
                _save_nedostupne(d)
        else:
            failed += 1
    log.info("nedostupne send code=%s type=%s sent=%d failed=%d skipped=%d user=%s",
             code, type_key, sent_ok, failed, skipped, session.get("user"))
    ok = failed == 0
    return jsonify({"ok": ok, "sent": sent_ok, "failed": failed, "skipped": skipped}), (
        200 if ok else 502)


# --------------------------------------------------------------------------- #
# Poľovnícke výstavy (#111) — migrated from the n8n „Polovnicke vystavy" workflow.
# Store (data/out/vystavy.json, atomic, gitignored — survives deploy), CRUD, the
# two manual send buttons (Pošli otázku / Ideme na túto výstavu) and the plain-text
# mail templates. The 3 background chains (rozposlať otázky / kontrola odpovedí)
# live as default-OFF automations further down (run_vystavy_*).
# --------------------------------------------------------------------------- #
VYSTAVY = _store("vystavy.json")

# Canonical state-machine values (kept 1:1 with the original n8n `email_status`).
VY_NEW = ""                                    # Nová
VY_OTAZKA = "otazka"                            # Otázka poslaná
VY_AKCIA = "akcia bude"                          # Odpovedali — čaká na rozhodnutie
VY_POZIADANE = "poziadane"                        # Prihláška poslaná
VY_HOTOVO = "odpovedane od organizatora"          # Potvrdené (konečný)
VY_STATUSES = {VY_NEW, VY_OTAZKA, VY_AKCIA, VY_POZIADANE, VY_HOTOVO}

# Fields the manager may edit in the app (all others are state, set only by the
# send buttons / automations). These go into the mail, so they are formula-guarded.
VY_EDIT_FIELDS = ("nazov", "datum", "miesto", "kontakt_osoba", "tel", "email",
                  "velkost_stanku", "kedy_riesit", "sposob")
# …except tel + kontakt_osoba: a phone like „+421 905 …" legitimately leads with '+',
# and neither reaches any CSV/formula sink (the mails interpolate only nazov/datum/
# velkost_stanku), so guarding them just blocks valid data (the edit form posts every
# field, so one '+' phone made the whole výstava unsavable). #198 FIX 2.
VY_NO_FORMULA_GUARD = ("tel", "kontakt_osoba")
VY_FIELD_MAX = 500                              # length cap per editable field
VY_FEED_MAX = 100                               # feed entries kept per výstava

# SK monthLong names (sk-SK) — the „kedy_riesit" filter for chain A matches the
# current month name (lowercased), exactly like the old n8n sk-SK monthLong compare.
_SK_MONTHS = ("", "január", "február", "marec", "apríl", "máj", "jún", "júl",
              "august", "september", "október", "november", "december")

# Mail texts — VERBATIM from the n8n workflow (data/out/vystavy_workflow_digest.md).
VY_OTAZKA_SUBJECT = "Otázka ohľadom: {nazov} dňa {datum}"
VY_OTAZKA_BODY = """Dobrý deň,

obraciam sa na Vás s otázkou, či aj tento rok plánujete organizovať podujatie {nazov} v termíne {datum}.

Ak áno, prosím Vás o krátke potvrdenie. Následne Vám pošlem ďalší email so všetkými potrebnými detailmi a informáciami k prihláseniu.

Vopred ďakujem za odpoveď.

S pozdravom,
Štepán Drlík
ForestShop.sk"""

VY_PRIHLASKA_SUBJECT = "Žiadosť o účasť: {nazov} dňa {datum}"
VY_PRIHLASKA_BODY = """Dobrý deň,

ďakujem za potvrdenie, že podujatie {nazov} sa bude konať aj tento rok dňa {datum}.

Týmto sa záväzne prihlasujem ako vystavovateľ za spoločnosť ForestShop.sk. Predbežne mám záujem o stánok veľkosti {velkost_stanku}.

Prosím o potvrdenie prijatia prihlášky.

V prípade akýchkoľvek otázok ma kedykoľvek kontaktujte.

Vopred ďakujem a teším sa na spoluprácu.

S pozdravom,
Štepán Drlík
ForestShop.sk"""


def _load_vystavy() -> list:
    return _read_json_store(VYSTAVY, [])


def _save_vystavy(d: list, *, prev: list = None) -> None:
    # `prev` — see _save_decisions: the delete path REBUILDS the list it read.
    _atomic_write_json(VYSTAVY, d, protect=True, prev=prev)


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _sk_month_now() -> str:
    """Current month's sk-SK long name, lowercased (the chain-A filter key)."""
    return _SK_MONTHS[datetime.now(ZoneInfo("Europe/Bratislava")).month]


def _vy_find(vystavy: list, vid: str):
    return next((v for v in vystavy if v.get("id") == vid), None)


def _vy_feed(v: dict, typ: str, text: str) -> None:
    """Prepend a newest-first feed entry (the in-app replacement for the Discord
    notifications), capped to VY_FEED_MAX."""
    entry = {"ts": _now_iso(), "typ": typ, "text": text}
    v["feed"] = ([entry] + (v.get("feed") or []))[:VY_FEED_MAX]


def _vy_otazka_mail(v: dict):
    """(subject, plain-text body) of the intro-question mail for one výstava."""
    nazov, datum = v.get("nazov", ""), v.get("datum", "")
    return (VY_OTAZKA_SUBJECT.format(nazov=nazov, datum=datum),
            VY_OTAZKA_BODY.format(nazov=nazov, datum=datum))


def _vy_prihlaska_mail(v: dict):
    """(subject, plain-text body) of the formal application mail for one výstava."""
    nazov, datum = v.get("nazov", ""), v.get("datum", "")
    return (VY_PRIHLASKA_SUBJECT.format(nazov=nazov, datum=datum),
            VY_PRIHLASKA_BODY.format(nazov=nazov, datum=datum,
                                    velkost_stanku=v.get("velkost_stanku", "")))


def _vy_clean_fields(raw: dict):
    """Whitelist + validate editable fields from a request body. Returns (fields, error):
    a formula-injection lead (=,+,-,@,tab,cr) on any field is rejected (they go into the
    mail); over-long values are rejected. Only VY_EDIT_FIELDS are kept."""
    fields = {}
    for k in VY_EDIT_FIELDS:
        if k not in raw:
            continue
        val = str(raw.get(k) or "").strip()
        if len(val) > VY_FIELD_MAX:
            return None, f"pole '{k}' je príliš dlhé"
        if k not in VY_NO_FORMULA_GUARD and val[:1] in _FORMULA_LEAD:
            return None, f"pole '{k}' nesmie začínať znakom = + - @"
        fields[k] = val
    return fields, None


def _vy_sort_key(v: dict):
    """Display order: action-needed first, then new, then in-flight, then done;
    within a bucket by name."""
    order = {VY_AKCIA: 0, VY_NEW: 1, VY_OTAZKA: 2, VY_POZIADANE: 3, VY_HOTOVO: 4}
    return (order.get(v.get("status", VY_NEW), 9), (v.get("nazov") or "").lower())


@app.route("/api/vystavy", methods=["GET", "POST"])
def api_vystavy():
    """GET → {vystavy:[...]} (sorted by state then name). POST adds a new výstava:
    a whitelisted+formula-guarded field set, a fresh uuid, status Nová, empty feed."""
    if request.method == "GET":
        return jsonify({"vystavy": sorted(_load_vystavy(), key=_vy_sort_key)})
    raw = request.get_json(silent=True) or {}
    raw = raw.get("fields") if isinstance(raw.get("fields"), dict) else raw
    fields, err = _vy_clean_fields(raw)
    if err:
        return jsonify({"ok": False, "error": err}), 400
    if not (fields.get("nazov") or "").strip():
        return jsonify({"ok": False, "error": "názov výstavy je povinný"}), 400
    v = {"id": uuid.uuid4().hex, "nazov": "", "datum": "", "miesto": "",
         "kontakt_osoba": "", "tel": "", "email": "", "velkost_stanku": "",
         "kedy_riesit": "", "sposob": "email", "status": VY_NEW,
         "email_datum": "", "email_otazka_msgid": "", "email_ziadost_msgid": "",
         "feed": []}
    v.update(fields)
    if v["sposob"] not in ("email", "pdf"):
        v["sposob"] = "email"
    with _lock:
        vystavy = _load_vystavy()
        vystavy.append(v)
        _save_vystavy(vystavy)
    log.info("vystavy: added %s (%s) user=%s", v["id"], v["nazov"], session.get("user"))
    return jsonify({"ok": True, "vystava": v})


@app.route("/api/vystava", methods=["POST"])
def api_vystava():
    """Edit / delete / manual status-reset of one výstava (vzor api_note).
    - {id, delete:true}         → remove it
    - {id, fields:{...}}        → overwrite the whitelisted editable fields
    - {id, status:"<hodnota>"}  → manual state reset (reset to Nová clears the msgids
                                   so a fresh cycle starts)."""
    body = request.get_json(silent=True) or {}
    vid = str(body.get("id") or "").strip()
    with _lock:
        vystavy = _load_vystavy()
        v = _vy_find(vystavy, vid)
        if not v:
            return jsonify({"ok": False, "error": "výstava neexistuje"}), 404
        if body.get("delete"):
            kept = [x for x in vystavy if x.get("id") != vid]
            _save_vystavy(kept, prev=vystavy)   # a REBUILT list — name the read it came from
            log.info("vystavy: deleted %s user=%s", vid, session.get("user"))
            return jsonify({"ok": True})
        if "status" in body:
            new_status = str(body.get("status") or "")
            if new_status not in VY_STATUSES:
                return jsonify({"ok": False, "error": "neplatný stav"}), 400
            v["status"] = new_status
            if new_status == VY_NEW:
                v["email_otazka_msgid"] = ""
                v["email_ziadost_msgid"] = ""
            _vy_feed(v, "manual", f"Manažér ručne nastavil stav: {new_status or 'Nová'}")
            _save_vystavy(vystavy)
            return jsonify({"ok": True, "vystava": v})
        raw = body.get("fields") if isinstance(body.get("fields"), dict) else {}
        fields, err = _vy_clean_fields(raw)
        if err:
            return jsonify({"ok": False, "error": err}), 400
        if "nazov" in fields and not fields["nazov"]:
            return jsonify({"ok": False, "error": "názov výstavy je povinný"}), 400
        if fields.get("sposob") and fields["sposob"] not in ("email", "pdf"):
            return jsonify({"ok": False, "error": "neplatný spôsob"}), 400
        v.update(fields)
        _save_vystavy(vystavy)
    log.info("vystavy: edited %s fields=%s user=%s", vid, list(fields), session.get("user"))
    return jsonify({"ok": True, "vystava": v})


@app.route("/api/vystava/posli-otazku", methods=["POST"])
def api_vystava_posli_otazku():
    """Manual send of the intro-question mail for ONE výstava (chain A, off-schedule).
    Allowed ONLY from the Nová (empty) state — like /ideme guards VY_AKCIA — so re-sending
    on an in-flight výstava can't reset it to Otázka poslaná and re-mail the organizer;
    wrong state → 409. The SMTP round-trip runs OUTSIDE the global _lock; on success
    status→Otázka poslaná, the msgid is stored (for IMAP threading) and the feed records
    it. Mail failure → 502, state unchanged (retryable). #198 FIX 3."""
    body = request.get_json(silent=True) or {}
    vid = str(body.get("id") or "").strip()
    with _lock:
        v = _vy_find(_load_vystavy(), vid)
        if not v:
            return jsonify({"ok": False, "error": "výstava neexistuje"}), 404
        if v.get("status") != VY_NEW:
            return jsonify({"ok": False,
                            "error": "otázku možno poslať len z novej výstavy"}), 409
        snapshot = dict(v)
    email = (snapshot.get("email") or "").strip()
    if not email:
        return jsonify({"ok": False, "error": "výstava nemá e-mail"}), 400
    subject, mbody = _vy_otazka_mail(snapshot)
    msgid = _send_vystava_mail(email, subject, mbody)       # outside the lock
    if not msgid:
        return jsonify({"ok": False, "error": "e-mail sa nepodarilo odoslať"}), 502
    with _lock:
        vystavy = _load_vystavy()
        v = _vy_find(vystavy, vid)
        if not v:
            return jsonify({"ok": False, "error": "výstava neexistuje"}), 404
        if v.get("status") != VY_NEW:                       # changed meanwhile → don't double
            return jsonify({"ok": False, "error": "stav sa medzičasom zmenil"}), 409
        v["status"] = VY_OTAZKA
        v["email_otazka_msgid"] = msgid
        v["email_datum"] = _now_iso()
        _vy_feed(v, "otazka_poslana", f"Poslaná otázka organizátorovi ({email}).")
        _save_vystavy(vystavy)
        result = dict(v)
    log.info("vystavy: posli-otazku %s → %s user=%s", vid, email, session.get("user"))
    return jsonify({"ok": True, "vystava": result})


@app.route("/api/vystava/ideme", methods=["POST"])
def api_vystava_ideme():
    """In-app approval (chain C, replaces the Discord ✅): the manager decides to attend →
    send the formal application mail. Only valid from state 'akcia bude'; on success
    status→Prihláška poslaná + msgid stored. Wrong state → 409; mail failure → 502
    (state unchanged). The SMTP round-trip runs OUTSIDE the global _lock."""
    body = request.get_json(silent=True) or {}
    vid = str(body.get("id") or "").strip()
    with _lock:
        v = _vy_find(_load_vystavy(), vid)
        if not v:
            return jsonify({"ok": False, "error": "výstava neexistuje"}), 404
        if v.get("status") != VY_AKCIA:
            return jsonify({"ok": False,
                            "error": "prihlášku možno poslať len keď organizátor odpovedal"}), 409
        snapshot = dict(v)
    email = (snapshot.get("email") or "").strip()
    if not email:
        return jsonify({"ok": False, "error": "výstava nemá e-mail"}), 400
    subject, mbody = _vy_prihlaska_mail(snapshot)
    msgid = _send_vystava_mail(email, subject, mbody)       # outside the lock
    if not msgid:
        return jsonify({"ok": False, "error": "e-mail sa nepodarilo odoslať"}), 502
    with _lock:
        vystavy = _load_vystavy()
        v = _vy_find(vystavy, vid)
        if not v:
            return jsonify({"ok": False, "error": "výstava neexistuje"}), 404
        if v.get("status") != VY_AKCIA:                     # changed meanwhile → don't double
            return jsonify({"ok": False, "error": "stav sa medzičasom zmenil"}), 409
        v["status"] = VY_POZIADANE
        v["email_ziadost_msgid"] = msgid
        v["email_datum"] = _now_iso()
        _vy_feed(v, "prihlaska_poslana", f"Poslaná prihláška organizátorovi ({email}).")
        _save_vystavy(vystavy)
        result = dict(v)
    log.info("vystavy: ideme %s → %s user=%s", vid, email, session.get("user"))
    return jsonify({"ok": True, "vystava": result})


@app.route("/api/notes", methods=["GET", "POST"])
def api_notes():
    """Free-form notes list ('📝 Poznámky' tab). GET -> newest-first list; POST {text}
    appends a new note. Not written to any CSV/import, so no formula-injection guard —
    just a length cap on the free text."""
    if request.method == "GET":
        return jsonify({"notes": _load_notes()})
    body = request.get_json(force=True)
    text = str(body.get("text") or "").strip()
    if not text or len(text) > NOTE_MAX_LEN:
        return jsonify({"ok": False, "error": f"text must be 1..{NOTE_MAX_LEN} chars"}), 400
    note = {"id": uuid.uuid4().hex, "text": text, "done": False, "ts": time.time()}
    with _lock:
        d = _load_notes()
        d.insert(0, note)          # newest-first
        _save_notes(d)
    log.info("note added id=%s len=%d", note["id"], len(text))
    return jsonify({"note": note})


@app.route("/api/note", methods=["POST"])
def api_note():
    """Toggle 'done' or delete a single note by id. Unknown id -> 404."""
    body = request.get_json(force=True)
    nid = str(body.get("id") or "")
    with _lock:
        d = _load_notes()
        idx = next((i for i, n in enumerate(d) if n.get("id") == nid), None)
        if idx is None:
            return jsonify({"ok": False, "error": "unknown id"}), 404
        if body.get("delete"):
            d.pop(idx)
        elif "done" in body:
            d[idx]["done"] = bool(body.get("done"))
        _save_notes(d)
    log.info("note update id=%s delete=%s done=%s", nid, body.get("delete"), body.get("done"))
    return jsonify({"ok": True})


# --------------------------------------------------------------------------- #
# „Vývoj" tab (#115): list this repo's GitHub issues (open + closed, PRs filtered)
# + the idea lightbulb (create an issue). ALL GitHub traffic is proxied through the
# backend — the repo-write token (data/.gh_env) NEVER reaches the browser. It lives
# only in the server-side Authorization header (never a URL, never a log), so an
# error string carries no secret. No token/repo configured → every path degrades
# gracefully to „GitHub nedostupný" (available=False), never a 500.
# --------------------------------------------------------------------------- #
GITHUB_API = (os.environ.get("GITHUB_API_BASE") or "https://api.github.com").rstrip("/")
GH_TIMEOUT = 15                 # s per GitHub API call
GH_LIST_PER_PAGE = 100          # GitHub max page size
GH_MAX_PAGES = 5                # bounded pagination (≤500 items): /issues returns
#                                 issues AND PRs, so a single page could push older
#                                 issues off after PR-filtering — page through so the
#                                 boss sees every issue (incl. all the closed/done ones).
IDEA_TITLE_MAX = 200
IDEA_BODY_MAX = 5000
IDEA_RATE_MAX = 20              # ideas per user per window (anti-spam / runaway guard)
IDEA_RATE_WINDOW = 300          # 5 min
_idea_times: dict = {}          # user email -> [timestamps]

NOTE_MAX = 5000                 # chars per in-app „doplniť detail" note (→ GitHub comment)
# In-app priority — the boss never sees GitHub: two hidden labels drive the
# „čoskoro / neskôr" split in the Vývoj tab. The frontend maps them to Slovak and
# never shows the raw label names. Colours come from the soft palette (#143).
PRIO_LABELS = {"soon": "prio:soon", "later": "prio:later"}
PRIO_LABEL_SET = set(PRIO_LABELS.values())
PRIO_COLORS = {"soon": "d14d3b", "later": "e0b341"}   # 6-hex, no '#'


def _gh_config():
    """(token, repo) from data/.gh_env — or (None, None) when unconfigured, so the
    Vývoj tab + lightbulb degrade gracefully instead of crashing."""
    token = (os.environ.get("GITHUB_TOKEN") or "").strip()
    repo = (os.environ.get("GITHUB_REPO") or "").strip()
    if not token or not repo:
        return None, None
    return token, repo


def _gh_headers(token):
    """GitHub REST headers — the token is a Bearer credential in the HEADER only."""
    return {"Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "parovanie-webreview"}


def _slim_issue(it: dict) -> dict:
    """GitHub issue JSON → the slim shape the frontend renders (no token, no PII).

    The hidden priority labels (`prio:soon`/`prio:later`) are lifted into a plain
    `priority` field ('', 'soon' or 'later') and STRIPPED from the visible labels,
    so the boss sees „čoskoro/neskôr" in the UI, never the raw GitHub label."""
    all_labels = [lbl.get("name") for lbl in (it.get("labels") or [])
                  if isinstance(lbl, dict) and lbl.get("name")]
    priority = next((k for k, n in PRIO_LABELS.items() if n in all_labels), "")
    return {
        "number": it.get("number"),
        "title": it.get("title") or "",
        "state": it.get("state") or "open",
        "labels": [nm for nm in all_labels if nm not in PRIO_LABEL_SET],
        "priority": priority,
        "updated_at": it.get("updated_at") or "",
        "html_url": it.get("html_url") or "",
        "comments": it.get("comments") or 0,
    }


def _do_dev_issues():
    """(payload, status) — this repo's GitHub issues (open + closed), PRs filtered
    out (the /issues endpoint returns PRs too — they carry a `pull_request` key).
    No token → graceful „unavailable" (never 500); an upstream/network error is
    caught and also degrades gracefully so the tab never crashes."""
    token, repo = _gh_config()
    if not token:
        return {"ok": False, "available": False, "issues": [],
                "error": "GitHub nedostupný — token nie je nastavený"}, 200
    try:
        issues = []
        for page in range(1, GH_MAX_PAGES + 1):
            r = requests.get(
                f"{GITHUB_API}/repos/{repo}/issues",
                params={"state": "all", "per_page": GH_LIST_PER_PAGE,
                        "sort": "updated", "direction": "desc", "page": page},
                headers=_gh_headers(token), timeout=GH_TIMEOUT)
            if r.status_code != 200:
                log.warning("gh issues: HTTP %s for %s (page %d)", r.status_code, repo, page)
                if page == 1:
                    return {"ok": False, "available": False, "issues": [],
                            "error": f"GitHub API vrátil {r.status_code}"}, 200
                break                            # keep the pages already collected
            batch = r.json()
            if not isinstance(batch, list) or not batch:
                break
            issues.extend(_slim_issue(it) for it in batch
                          if isinstance(it, dict) and "pull_request" not in it)
            if len(batch) < GH_LIST_PER_PAGE:    # last page reached
                break
        return {"ok": True, "available": True, "issues": issues}, 200
    except Exception as e:  # noqa: BLE001 — the tab must never crash on GitHub trouble
        log.warning("gh issues fetch failed: %r", e)
        return {"ok": False, "available": False, "issues": [],
                "error": "GitHub nedostupný"}, 200


def _do_create_idea(title: str, description: str, author: str = ""):
    """(payload, status) — create a GitHub issue from a manager idea. No token →
    graceful „unavailable"; an upstream/network error is caught (never 500)."""
    token, repo = _gh_config()
    if not token:
        return {"ok": False, "available": False,
                "error": "GitHub nedostupný — token nie je nastavený"}, 200
    body = description
    if author:
        body = (body + "\n\n" if body else "") + f"_Nápad cez appku (Vývoj) — {author}_"
    try:
        r = requests.post(
            f"{GITHUB_API}/repos/{repo}/issues",
            json={"title": title, "body": body},
            headers=_gh_headers(token), timeout=GH_TIMEOUT)
        if r.status_code not in (200, 201):
            log.warning("gh create idea: HTTP %s for %s", r.status_code, repo)
            return {"ok": False, "error": f"GitHub API vrátil {r.status_code}"}, 200
        log.info("gh idea created by %s: %r", author or "?", title)
        return {"ok": True, "issue": _slim_issue(r.json())}, 201
    except Exception as e:  # noqa: BLE001 — never crash on GitHub trouble
        log.warning("gh create idea failed: %r", e)
        return {"ok": False, "error": "GitHub nedostupný"}, 200


def _gh_issue_or_refuse(token, repo, number: int):
    """(issue, refusal) — read one issue by number and refuse anything that is not one.

    GitHub serves PULL REQUESTS from /repos/{repo}/issues/{n} under the same numbering,
    so a number is only an ADDRESS — never proof that it belongs to a task. The list
    endpoint has always filtered PRs out (`pull_request` key), and #243 added the same
    check to /edit; every other by-number path went without it, so any logged-in shop
    user could comment on, label, or read back any PR of the repo just by typing its
    number. One helper, so a future by-number endpoint inherits the guard instead of
    re-deciding it. `refusal` is a ready (payload, status) pair; callers keep their own
    try/except — a network error is theirs to degrade."""
    r = requests.get(f"{GITHUB_API}/repos/{repo}/issues/{number}",
                     headers=_gh_headers(token), timeout=GH_TIMEOUT)
    if r.status_code != 200:
        log.warning("gh read issue: HTTP %s for %s#%s", r.status_code, repo, number)
        return None, ({"ok": False, "error": f"GitHub API vrátil {r.status_code}"}, 200)
    parsed = r.json()                                   # parse ONCE
    cur = parsed if isinstance(parsed, dict) else {}
    if cur.get("pull_request"):
        log.warning("gh %s#%s is a pull request — refused", repo, number)
        return None, ({"ok": False, "error": "toto číslo nepatrí úlohe"}, 200)
    return cur, None


def _do_add_note(number: int, text: str, author: str = ""):
    """(payload, status) — append the boss's detail as a GitHub issue COMMENT
    (non-destructive; the issue body is never overwritten). The boss never sees
    GitHub — the token is used server-side only. No token → graceful „unavailable";
    upstream/network errors are caught (never 500). A PULL REQUEST is refused: the
    number is read back first, because /issues/{n} serves PRs too."""
    token, repo = _gh_config()
    if not token:
        return {"ok": False, "available": False,
                "error": "GitHub nedostupný — token nie je nastavený"}, 200
    body = text
    if author:
        body = body + f"\n\n_Doplnené cez appku (Vývoj) — {author}_"
    try:
        _cur, refusal = _gh_issue_or_refuse(token, repo, number)
        if refusal:
            return refusal
        r = requests.post(
            f"{GITHUB_API}/repos/{repo}/issues/{number}/comments",
            json={"body": body},
            headers=_gh_headers(token), timeout=GH_TIMEOUT)
        if r.status_code not in (200, 201):
            log.warning("gh add note: HTTP %s for %s#%s", r.status_code, repo, number)
            return {"ok": False, "error": f"GitHub API vrátil {r.status_code}"}, 200
        log.info("gh note added by %s on #%s (%d chars)", author or "?", number, len(text))
        return {"ok": True}, 201
    except Exception as e:  # noqa: BLE001 — never crash on GitHub trouble
        log.warning("gh add note failed: %r", e)
        return {"ok": False, "error": "GitHub nedostupný"}, 200


# The app's own trailing signature lines in an issue BODY. They are bookkeeping, not
# the boss's text, so the edit form must neither show them nor make him retype them —
# they are stripped before editing and rewritten on save (#243). Matching „Upravené"
# too is what keeps them from stacking up one line per edit.
_APP_BODY_MARKER = re.compile(r"^_(?:Nápad|Upravené) cez appku \(Vývoj\) — .*_$")


def _split_app_markers(body: str):
    """(text the boss wrote, [the app's own trailing marker lines])."""
    lines = (body or "").replace("\r\n", "\n").split("\n")
    markers = []
    while lines and (not lines[-1].strip() or _APP_BODY_MARKER.match(lines[-1].strip())):
        line = lines.pop().strip()
        if line:
            markers.insert(0, line)
    return "\n".join(lines).rstrip(), markers


def _compose_edited_body(text: str, prev_markers, editor: str) -> str:
    """The boss's new text + the preserved „who first wrote this" line + ONE fresh
    „who last edited it" line. The origin marker is kept because an edit must not
    erase who raised the request; the edit marker is rewritten (never appended) so
    repeated edits cannot grow a changelog nobody asked for."""
    keep = [m for m in prev_markers if m.startswith("_Nápad ")]
    if editor:
        keep.append(f"_Upravené cez appku (Vývoj) — {editor}_")
    tail = "\n".join(keep)
    text = (text or "").strip()
    if text and tail:
        return text + "\n\n" + tail
    return text or tail


def _do_edit_issue(number: int, title: str, text: str, author: str = ""):
    """(payload, status) — rewrite an issue's title + text (#243).

    The boss could add a comment but never CORRECT what he had already sent, so a
    request that came out wrong was simply abandoned („nedá sa, tak som sa na to
    vykašlal"). `gh issue edit` cannot do this on this repo (it goes through the
    deprecated classic-Projects GraphQL and fails), so this is a plain REST PATCH.
    A closed issue is refused — reopening work by editing it would be invisible to
    whoever already acted on it; a comment is the right tool there. So is a PULL
    REQUEST: GitHub's /issues/{n} also serves PRs, so without this check any logged-in
    user could rewrite a PR's title and body just by typing its number (the list
    endpoint already filters PRs out — this one has to as well)."""
    token, repo = _gh_config()
    if not token:
        return {"ok": False, "available": False,
                "error": "GitHub nedostupný — token nie je nastavený"}, 200
    try:
        cur, refusal = _gh_issue_or_refuse(token, repo, number)
        if refusal:
            return refusal
        if (cur.get("state") or "open") != "open":
            return {"ok": False,
                    "error": "úloha je už uzavretá — doplň ju radšej detailom"}, 200
        _prev, markers = _split_app_markers(cur.get("body") or "")
        r = requests.patch(
            f"{GITHUB_API}/repos/{repo}/issues/{number}",
            json={"title": title, "body": _compose_edited_body(text, markers, author)},
            headers=_gh_headers(token), timeout=GH_TIMEOUT)
        if r.status_code != 200:
            log.warning("gh edit issue: HTTP %s for %s#%s", r.status_code, repo, number)
            return {"ok": False, "error": f"GitHub API vrátil {r.status_code}"}, 200
        log.info("gh issue %s edited by %s: %r", number, author or "?", title)
        return {"ok": True, "issue": _slim_issue(r.json())}, 200
    except Exception as e:  # noqa: BLE001 — never crash on GitHub trouble
        log.warning("gh edit issue failed: %r", e)
        return {"ok": False, "error": "GitHub nedostupný"}, 200


def _do_issue_detail(number: int):
    """(payload, status) — one issue's full text (body) + ALL its comments, so the
    boss reads everything IN the app (GitHub stays hidden). No token → graceful
    „unavailable"; upstream/network errors are caught (never 500). A PULL REQUEST is
    refused here too — his task list never shows one, so serving a PR's body and
    comments on a hand-typed number only shows him something this tab is not about."""
    token, repo = _gh_config()
    if not token:
        return {"ok": False, "available": False,
                "error": "GitHub nedostupný — token nie je nastavený"}, 200
    try:
        it, refusal = _gh_issue_or_refuse(token, repo, number)
        if refusal:
            return refusal
        comments = []
        rc = requests.get(f"{GITHUB_API}/repos/{repo}/issues/{number}/comments",
                          params={"per_page": GH_LIST_PER_PAGE},
                          headers=_gh_headers(token), timeout=GH_TIMEOUT)
        if rc.status_code == 200 and isinstance(rc.json(), list):
            comments = [{"body": c.get("body") or "",
                         "created_at": c.get("created_at") or ""}
                        for c in rc.json() if isinstance(c, dict)]
        # `title` + `editable` feed the in-app edit form (#243): `editable` is the body
        # WITHOUT the app's own signature lines, so the boss never has to see — or
        # accidentally delete — bookkeeping he did not write.
        editable, _markers = _split_app_markers(it.get("body") or "")
        return {"ok": True, "body": it.get("body") or "", "comments": comments,
                "title": it.get("title") or "", "editable": editable,
                "state": it.get("state") or "open"}, 200
    except Exception as e:  # noqa: BLE001 — never crash on GitHub trouble
        log.warning("gh issue detail failed: %r", e)
        return {"ok": False, "error": "GitHub nedostupný"}, 200


def _ensure_label(token, repo, name, color):
    """Create the label if it doesn't exist yet (idempotent — an „already_exists"
    422 is fine). Best-effort: a failure here never blocks the priority set."""
    try:
        requests.post(f"{GITHUB_API}/repos/{repo}/labels",
                      json={"name": name, "color": color},
                      headers=_gh_headers(token), timeout=GH_TIMEOUT)
    except Exception as e:  # noqa: BLE001 — never crash on GitHub trouble
        log.warning("gh ensure label %s failed: %r", name, e)


def _do_set_priority(number: int, priority: str):
    """(payload, status) — set the boss's in-app priority by managing the hidden
    prio labels: add the chosen one, remove the other. `priority` is 'soon',
    'later' or 'none' (clear). Graceful on no-token / upstream / network error.
    A PULL REQUEST is refused — /issues/{n} serves PRs, so without the read-back a
    number alone was enough to label any PR of the repo."""
    token, repo = _gh_config()
    if not token:
        return {"ok": False, "available": False,
                "error": "GitHub nedostupný — token nie je nastavený"}, 200
    keep = PRIO_LABELS.get(priority)                       # None for 'none' (clear)
    remove = [n for k, n in PRIO_LABELS.items() if n != keep]
    try:
        _cur, refusal = _gh_issue_or_refuse(token, repo, number)
        if refusal:
            return refusal
        for name in remove:                               # drop the opposite label(s)
            dr = requests.delete(
                f"{GITHUB_API}/repos/{repo}/issues/{number}/labels/{quote(name, safe='')}",
                headers=_gh_headers(token), timeout=GH_TIMEOUT)
            # 200 = removed, 404 = label wasn't set (both fine); anything else
            # (403 secondary rate-limit, 5xx) is a REAL failure — surface it, don't
            # report success for a label that is still on the issue.
            if dr.status_code not in (200, 404):
                log.warning("gh set prio delete %s: HTTP %s for %s#%s",
                            name, dr.status_code, repo, number)
                return {"ok": False, "error": f"GitHub API vrátil {dr.status_code}"}, 200
        if keep:
            _ensure_label(token, repo, keep, PRIO_COLORS[priority])
            r = requests.post(
                f"{GITHUB_API}/repos/{repo}/issues/{number}/labels",
                json={"labels": [keep]},
                headers=_gh_headers(token), timeout=GH_TIMEOUT)
            if r.status_code not in (200, 201):
                log.warning("gh set prio: HTTP %s for %s#%s", r.status_code, repo, number)
                return {"ok": False, "error": f"GitHub API vrátil {r.status_code}"}, 200
        log.info("gh priority %s set on #%s", priority, number)
        return {"ok": True, "priority": priority if keep else ""}, 200
    except Exception as e:  # noqa: BLE001 — never crash on GitHub trouble
        log.warning("gh set priority failed: %r", e)
        return {"ok": False, "error": "GitHub nedostupný"}, 200


def _idea_rate_limited(key: str) -> bool:
    """Coarse anti-spam guard on idea creation (per user). Records this attempt."""
    now = time.time()
    times = [t for t in _idea_times.get(key, []) if now - t < IDEA_RATE_WINDOW]
    if len(times) >= IDEA_RATE_MAX:
        _idea_times[key] = times
        return True
    times.append(now)
    _idea_times[key] = times
    return False


@app.route("/api/dev/issues")
def api_dev_issues():
    """List this repo's GitHub issues (open + closed) for the „Vývoj" tab."""
    payload, status = _do_dev_issues()
    return jsonify(payload), status


@app.route("/api/dev/idea", methods=["POST"])
def api_dev_idea():
    """Create a GitHub issue from a manager idea (the lightbulb). Validates the
    title (required, capped); the token is used server-side only."""
    body = request.get_json(force=True, silent=True) or {}
    title = str(body.get("title") or "").strip()
    if not title:
        return jsonify({"ok": False, "error": "názov nápadu je povinný"}), 400
    if len(title) > IDEA_TITLE_MAX:
        return jsonify({"ok": False,
                        "error": f"názov môže mať najviac {IDEA_TITLE_MAX} znakov"}), 400
    desc = str(body.get("description") or "").strip()
    if len(desc) > IDEA_BODY_MAX:
        return jsonify({"ok": False,
                        "error": f"popis môže mať najviac {IDEA_BODY_MAX} znakov"}), 400
    u = _current_user()
    email = u["email"] if u else ""
    if _idea_rate_limited(email):
        return jsonify({"ok": False,
                        "error": "priveľa nápadov za krátky čas — skús o chvíľu"}), 429
    payload, status = _do_create_idea(title, desc, email)
    return jsonify(payload), status


@app.route("/api/dev/issue/<int:number>")
def api_dev_issue_detail(number):
    """One issue's full text + all its details/comments, so the boss reads
    everything in the app (GitHub stays hidden)."""
    payload, status = _do_issue_detail(number)
    return jsonify(payload), status


@app.route("/api/dev/issue/<int:number>/note", methods=["POST"])
def api_dev_note(number):
    """Append the boss's detail to an existing issue as a GitHub comment. The boss
    writes it in the app; GitHub stays hidden (token server-side only). Validated
    (non-empty, capped) and rate-limited (shared idea/note anti-spam counter)."""
    body = request.get_json(force=True, silent=True) or {}
    text = str(body.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "text detailu je povinný"}), 400
    if len(text) > NOTE_MAX:
        return jsonify({"ok": False,
                        "error": f"detail môže mať najviac {NOTE_MAX} znakov"}), 400
    u = _current_user()
    email = u["email"] if u else ""
    if _idea_rate_limited(email):
        return jsonify({"ok": False,
                        "error": "priveľa zápisov za krátky čas — skús o chvíľu"}), 429
    payload, status = _do_add_note(number, text, email)
    return jsonify(payload), status


@app.route("/api/dev/issue/<int:number>/edit", methods=["POST"])
def api_dev_edit(number):
    """#243 — correct/extend an already-submitted request. Same scope as the detail
    comment and the priority buttons (any logged-in user, open issues only): the boss
    also needs to fix the requests that were written down FOR him, which is exactly the
    case that prompted this. GitHub keeps the full edit history, so nothing is lost."""
    body = request.get_json(force=True, silent=True) or {}
    title = str(body.get("title") or "").strip()
    text = str(body.get("text") or "")
    if not title:
        return jsonify({"ok": False, "error": "názov úlohy je povinný"}), 400
    if len(title) > IDEA_TITLE_MAX:
        return jsonify({"ok": False,
                        "error": f"názov môže mať najviac {IDEA_TITLE_MAX} znakov"}), 400
    if len(text) > IDEA_BODY_MAX:
        return jsonify({"ok": False,
                        "error": f"text môže mať najviac {IDEA_BODY_MAX} znakov"}), 400
    u = _current_user()
    email = u["email"] if u else ""
    if _idea_rate_limited(email):
        return jsonify({"ok": False,
                        "error": "priveľa zápisov za krátky čas — skús o chvíľu"}), 429
    payload, status = _do_edit_issue(number, title, text, email)
    return jsonify(payload), status


@app.route("/api/dev/issue/<int:number>/priority", methods=["POST"])
def api_dev_priority(number):
    """Set the boss's in-app priority for an issue ('soon'/'later'/'none'). Drives
    the „čoskoro/neskôr" split via hidden labels — the boss never sees GitHub."""
    body = request.get_json(force=True, silent=True) or {}
    prio = str(body.get("priority") or "").strip().lower()
    if prio not in ("soon", "later", "none"):
        return jsonify({"ok": False, "error": "neplatná priorita"}), 400
    payload, status = _do_set_priority(number, prio)
    return jsonify(payload), status


@app.route("/api/order-pair", methods=["POST"])
def api_order_pair():
    """Save/clear an inline supplier reorder URL for a forestshop order code
    (keyed by itemCode). Mirrors /api/decision but keyed by the forestshop product
    code, so it covers order lines that are NOT in the review dataset. Empty url
    clears the pairing. The URL then shows as the row's reorder link and is included
    in the import (import_builder.order_pairing_rows)."""
    body = request.get_json(force=True)
    code = str(body.get("code") or "").strip()
    url = str(body.get("url") or "").strip()
    if not code:
        return jsonify({"ok": False, "error": "missing code"}), 400
    # forestshop codes always start alphanumeric — a leading formula char (=,+,-,@,…)
    # is either malformed or a CSV-injection attempt; reject it at the source.
    if code[:1] in _FORMULA_LEAD:
        return jsonify({"ok": False, "error": "invalid code"}), 400
    # authoritative URL guard (matches the client) — only real http(s) links reach
    # the import's internalNote; blocks javascript:/data: and malformed 'httpfoo'.
    if url and not re.match(r"^https?://", url):
        return jsonify({"ok": False, "error": "url must start with http(s)://"}), 400
    if len(url) > URL_MAX:
        return jsonify({"ok": False,
                        "error": f"adresa je príliš dlhá (max {URL_MAX} znakov)"}), 400
    with _lock:
        d = _load_order_pairings()
        if url:
            d[code] = url
        else:
            d.pop(code, None)
        _save_order_pairings(d)
    log.info("order-pair code=%s url=%s", _log_safe(code), _log_safe(url))
    return jsonify({"ok": True})


@app.route("/api/order-decision-url", methods=["POST"])
def api_order_decision_url():
    """#242 — correct the reorder URL of a REVIEWED pairing straight from the
    „Na objednanie" tab.

    A row whose link comes from a review decision used to be read-only there, so the
    only way to fix a wrong link was to find the product in the review tab and pair it
    again. Writing the correction into `order_pairings` instead would have been a
    silent no-op: the decision outranks it both in the row render and in the eshop
    write-back (`order_pairing_rows(..., exclude_codes=…)`). So this rewrites THE
    DECISION — the value the row shows and the import actually ships.

    'manual' (not 'good'): the review card renders a 'good' decision from
    `ai_chosen_url`, so keeping 'good' would show the OLD link straight back.
    Guards mirror /api/order-pair — the value becomes an href AND an eshop
    internalNote cell.

    Every refusal is worded in SLOVAK: `postToOrder` surfaces `error` verbatim into
    the manager's alert, and the reachable one (a stale tab whose product a resync has
    pruned) used to reach him as 'unknown review key'."""
    body = request.get_json(force=True, silent=True) or {}
    key = str(body.get("key") or "").strip()
    url = str(body.get("url") or "").strip()
    if not key:
        return jsonify({"ok": False, "error": "chýba kľúč produktu"}), 400
    # Clearing a pairing is a review-tab decision ('↩ Vrátiť'), not a side effect of an
    # empty save here — an empty url would drop the product out of the import silently.
    if not url:
        return jsonify({"ok": False, "error": "zadaj párovaciu adresu"}), 400
    if not re.match(r"^https?://", url):
        return jsonify({"ok": False,
                        "error": "adresa musí začínať http:// alebo https://"}), 400
    if len(url) > URL_MAX:
        return jsonify({"ok": False,
                        "error": f"adresa je príliš dlhá (max {URL_MAX} znakov)"}), 400
    if not any(p.get("key") == key for p in PRODUCTS):
        # a decision whose key is not a live review product is pruned away at the next
        # start — accepting it would throw the manager's correction away later
        return jsonify({"ok": False,
                        "error": "tento produkt už nie je v revízii — obnov stránku"}), 404
    with _lock:
        d = _load_decisions()
        cur = d.get(key) or {}
        st = cur.get("status")
        # `reviewKey` is frozen in the client's ORDERS snapshot, so by the time this
        # arrives the decision may be something else entirely — anything the manager
        # changed in the review tab meanwhile (another window, a tab left open).
        # Only a real product-wide PAIRING may be rewritten from here; every other
        # status was silently overwritten with 'manual', which threw away exactly the
        # assertion the eshop needs (Vypredané+stock 0 for 'unavailable', „Predaj
        # skončil" for 'discontinued') — and a MISSING decision was CREATED, marking
        # an unreviewed product reviewed behind his back.
        if st == "split":
            # a split product carries a DIFFERENT URL per size (#174); one product-wide
            # link would discard every per-size link it holds
            return jsonify({"ok": False,
                            "error": "produkt je rozdelený na veľkosti — oprav link "
                                     "pri konkrétnej veľkosti v párovacom tabe"}), 409
        if st not in ("good", "manual"):
            return jsonify({"ok": False,
                            "error": "stav produktu sa medzitým zmenil v revízii — "
                                     "obnov stránku a pozri sa naň tam"}), 409
        d[key] = {"status": "manual", "url": url}
        _save_decisions(d)
        # The decision now provably outranks any inline pairing for the SAME codes
        # (link_rows covers them, so order_pairing_rows excludes them for good). Left
        # behind, that stale value just sits in the store waiting for a night when the
        # exclusion slips — drop it at the one moment it is certainly superseded.
        codes = [c for p in PRODUCTS if p.get("key") == key
                 for c in (p.get("variant_codes") or [])]
        pairings = _load_order_pairings()
        dropped = [c for c in codes if c in pairings]
        if dropped:
            for c in dropped:
                pairings.pop(c, None)
            _save_order_pairings(pairings)
            log.info("order-decision-url dropped superseded inline pairings: %s",
                     _log_safe(dropped))
    log.info("order-decision-url key=%s url=%s", _log_safe(key), _log_safe(url))
    return jsonify({"ok": True})


@app.route("/api/variants")
def api_variants():
    """#174 — the variants (sizes) of ONE review product for the split-into-sizes
    panel: [{code, size, link}] for each variant code, in order. `size` is the
    export's `variant:*` axis label (from CODE2VARIANT, DISPLAY only); `link` is the
    manager's stored per-variant reorder URL (variant_links.json). Unknown key → 404."""
    key = request.args.get("key", "")
    p = next((x for x in PRODUCTS if x.get("key") == key), None)
    if p is None:
        return jsonify({"ok": False, "error": "unknown key"}), 404
    vlinks = _load_variant_links()
    variants = [{"code": c, "size": CODE2VARIANT.get(c, ""), "link": vlinks.get(c, "")}
                for c in (p.get("variant_codes") or [])]
    return jsonify({"ok": True, "key": key, "variants": variants})


@app.route("/api/variant-link", methods=["POST"])
def api_variant_link():
    """#174 — save/clear the per-variant reorder URL for one forestshop variant code
    (keyed by the STABLE variant code). Mirrors /api/order-pair: empty url clears the
    link; a leading formula char in the code and a non-http(s) URL are rejected at the
    source (the URL reaches a CSV internalNote cell). The link is written to the eshop
    per variant by import_builder.link_rows for a `split`-status decision."""
    body = request.get_json(force=True)
    code = str(body.get("code") or "").strip()
    url = str(body.get("url") or "").strip()
    if not code:
        return jsonify({"ok": False, "error": "missing code"}), 400
    # forestshop codes always start alphanumeric — a leading formula char (=,+,-,@,…)
    # is malformed or a CSV-injection attempt; reject it at the source.
    if code[:1] in _FORMULA_LEAD:
        return jsonify({"ok": False, "error": "invalid code"}), 400
    # authoritative URL guard (matches /api/order-pair) — only real http(s) links reach
    # the import's internalNote; blocks javascript:/data: and malformed 'httpfoo'.
    if url and not re.match(r"^https?://", url):
        return jsonify({"ok": False, "error": "url must start with http(s)://"}), 400
    # same cap as every other URL-storing endpoint — and this store is now HOT:
    # build_to_order_rows() re-reads it on every /api/orders, and the value lands in a
    # Shoptet internalNote cell per variant
    if len(url) > URL_MAX:
        return jsonify({"ok": False,
                        "error": f"adresa je príliš dlhá (max {URL_MAX} znakov)"}), 400
    with _lock:
        d = _load_variant_links()
        if url:
            d[code] = url
        else:
            d.pop(code, None)
        _save_variant_links(d)
    log.info("variant-link code=%s url=%s", _log_safe(code), _log_safe(url))
    return jsonify({"ok": True})


@app.route("/api/order-supplier", methods=["POST"])
def api_order_supplier():
    """Assign/clear a supplier name for a forestshop order code (keyed by itemCode).
    Lets the manager fill in the supplier for an order line that arrived without one;
    the row then regroups under that supplier on the tab and the name is written back
    to the eshop `supplier` field by the nightly upload. Empty supplier clears it.
    Mirrors /api/order-pair (same code guard); the supplier name reaches a CSV, so a
    leading formula char is rejected here AND escaped at the CSV sink (_csv_safe)."""
    body = request.get_json(force=True)
    code = str(body.get("code") or "").strip()
    # #203 — normalise whitespace on write: a stray double space / tab makes 'Citrade
    # s.r.o.' and 'Citrade  s.r.o.' two different suppliers in the store AND writes two
    # spellings into the eshop `supplier` column. Case is deliberately NOT folded — the
    # value goes VERBATIM into import_suppliers.csv → Shoptet, so lower-casing it would
    # rewrite the supplier's real name in the eshop. Case-insensitive GROUPING is a
    # display concern and lives in the tab (supKey in app.js).
    supplier = " ".join(str(body.get("supplier") or "").split())
    if not code:
        return jsonify({"ok": False, "error": "missing code"}), 400
    # forestshop codes always start alphanumeric — a leading formula char (=,+,-,@,…)
    # is malformed or a CSV-injection attempt; reject at the source.
    if code[:1] in _FORMULA_LEAD:
        return jsonify({"ok": False, "error": "invalid code"}), 400
    # supplier name is written verbatim into the import CSV's `supplier` column — a
    # leading formula char would be a CSV-injection vector; real names start
    # alphanumeric, so reject it here too (belt-and-braces with _csv_safe at the sink).
    if supplier and supplier[:1] in _FORMULA_LEAD:
        return jsonify({"ok": False, "error": "invalid supplier"}), 400
    with _lock:
        d = _load_supplier_assign()
        if supplier:
            d[code] = supplier
        else:
            d.pop(code, None)
        _save_supplier_assign(d)
    log.info("order-supplier code=%s supplier=%s", code, supplier)
    return jsonify({"ok": True})


@app.route("/api/order-comment", methods=["GET", "POST"])
def api_order_comment():
    """#101 — per-ORDER free-text comment for the Na-objednanie tab (key='<orderCode>').
    GET -> the {orderCode: comment} map. POST {orderCode, comment} sets/clears ONE
    order's comment (empty comment clears it). Login-gated automatically (#91
    before_request). Length-capped at ORDER_COMMENT_MAX. This is OUR side; the comment
    is the manager's note about the whole order — the same thing as the Shoptet admin's
    "Poznámka e-shopu" (shopRemark). Writing it BACK into Shoptet is a follow-up pending
    the boss's decision (overwrite vs append the existing shopRemark, when to sync)."""
    if request.method == "GET":
        return jsonify({"comments": _load_order_comments()})
    body = request.get_json(force=True)
    order = str(body.get("orderCode") or "").strip()
    comment = str(body.get("comment") or "").strip()
    if not order:
        return jsonify({"ok": False, "error": "missing orderCode"}), 400
    if len(comment) > ORDER_COMMENT_MAX:
        return jsonify({"ok": False, "error": "comment too long"}), 400
    with _lock:
        d = _load_order_comments()
        if comment:
            d[order] = comment
        else:
            d.pop(order, None)
        _save_order_comments(d)
    log.info("order-comment order=%s len=%d", order, len(comment))
    return jsonify({"ok": True})


@app.route("/api/orders")
def api_orders():
    """To-order list: forestshop 'Vybavuje sa' items joined to supplier reorder
    links, with the per-line 'ordered' state merged in. Degrades to [] on fetch
    error so the tab still renders."""
    try:
        csv_bytes = _orders_csv_cached()
    except Exception as e:  # noqa: BLE001 — degrade to empty list, log the cause
        log.warning("orders fetch failed: %r", e)
        return jsonify({"orders": [], "error": str(e)})
    rows = build_to_order_rows(csv_bytes, PRODUCTS, _load_decisions(), CODE2PAIR,
                               _load_variant_links())
    ordered = _load_ordered()
    waiting = _load_waiting()
    instock = _load_instock()
    unavail = _load_unavailable()
    pairings = _load_order_pairings()
    assigns = _load_supplier_assign()
    comments = _load_order_comments()                # #101 — per-order manager comment
    grube = _load_grube_codes()                      # loaded once per request
    for r in rows:
        r["ordered"] = bool(ordered.get(r["key"]))
        r["comment"] = comments.get(r["orderCode"], "")   # per-ORDER note (our side)
        r["waiting"] = bool(waiting.get(r["key"]))   # 'čaká sa' — deferred active line
        r["instock"] = bool(instock.get(r["key"]))         # 'skladom' — máme/naskladnené
        r["unavailable"] = bool(unavail.get(r["key"]))     # 'nedostupné' — u dodávateľa
        # supplierUrl stays the reviewed-decision link (read-only); pairUrl is the
        # inline-entered one (editable on the tab). A row is "paired" if either is set.
        r["pairUrl"] = pairings.get(r["itemCode"], "")
        # supplier manually assigned for an order line that arrived without one — the
        # tab groups by (assignedSupplier OR supplier), so this regroups the row.
        r["assignedSupplier"] = assigns.get(r["itemCode"], "")
        # GRUBE per-size code chip + .de link (empty for every non-GRUBE / unmatched row)
        _attach_grube(r, grube)
    return jsonify({"orders": rows,
                    "bad_status_config": bool(_order_statuses_state()[1])})


@app.route("/static/<path:p>")
def static_files(p):
    return send_from_directory("static", p)


# --------------------------------------------------------------------------- #
# n8n → Shoptet auto-import (vypredané → skladom)
# --------------------------------------------------------------------------- #
def _import_token():
    """Bearer token for the import endpoint, from the gitignored creds file
    (key N8N_IMPORT_TOKEN). None if not configured → endpoint refuses all calls."""
    return _cred("N8N_IMPORT_TOKEN")


MAX_IMPORT_BYTES = 5 * 1024 * 1024   # restock CSVs are a few kB; cap the in-memory read


def _safe_unlink(*paths):
    for p in paths:
        try:
            os.remove(p)
        except OSError:
            pass


def _client_ip():
    """Real caller IP behind the Cloudflare tunnel (so the unauthorized-attempt
    log is useful, not just the tunnel/local address)."""
    return (request.headers.get("CF-Connecting-IP")
            or request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            or request.remote_addr)


def run_import(csv_path, dry_run=False, timeout=300):
    """Run the existing careful import script as a subprocess (catalog backup +
    safe-mode + result read-back). Returns (returncode, stdout, stderr). Started in
    its own session so a timeout kills the WHOLE group (the Playwright/Chromium it
    spawns too), never an orphaned browser mid-import. `timeout` scales with the CSV
    size — a few thousand pairing rows legitimately take longer than a small restock.
    Stubbable in tests."""
    cmd = [sys.executable, IMPORT_SCRIPT, "--file", csv_path, "--yes"]
    if dry_run:
        cmd.append("--dry-run")
    # PYTHONIOENCODING pins how the CHILD ENCODES its stdout; `encoding=` below only
    # says how WE DECODE it. Without the pin the child follows the box's locale, so on
    # a non-UTF-8 box the result marker arrives mojibake'd ('V?SLEDOK:'), the slice is
    # empty, processed=None and EVERY chunk is booked failed/unreadable — the whole
    # nightly push dies on one character. Both ends must agree; only then does the
    # non-ASCII marker survive the pipe.
    env = {**os.environ, "PYTHONPATH": os.path.join(ROOT, "src"),
           "PYTHONIOENCODING": "utf-8"}
    # decode the child's output as UTF-8 EXPLICITLY — never the box's locale. The
    # result is read by slicing on the non-ASCII marker 'VÝSLEDOK:' (parse_result_stdout);
    # under a non-UTF-8 locale that marker would mojibake, every slice would come back
    # empty and #257 would silently regress (a partially accepted chunk → 'failed').
    p = subprocess.Popen(cmd, cwd=ROOT, env=env, text=True,
                         encoding="utf-8", errors="replace",
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         start_new_session=True)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        p.communicate()
        raise
    return p.returncode, out, err


# #156: Shoptet's server-side processing of a large import CSV genuinely takes longer
# than the browser's import-result redirect wait (scripts/shoptet_import.py:296,
# page.wait_for_url timeout=120000) — the nightly 415-product / 1195-row pairings push
# overran 120s and failed (Timeout 120000ms). Root-cause fix (not a bigger timeout —
# no-timeout-band-aids): split a large upload into chunks of <= IMPORT_CHUNK_ROWS rows,
# import each chunk by its own careful run_import, so every chunk completes well inside
# the timeout and the whole push is reliable + resumable. 300 leaves ~4x headroom over
# the ~120s-for-1195-rows point where it failed (a chunk of 300 processes in ~30s).
IMPORT_CHUNK_ROWS = 300


def _write_import_csv(header, rows, prefix, csv_safe=False):
    """Write ONE import CSV in the canonical Shoptet dialect (utf-8-sig BOM, ';',
    CRLF) and return its path. csv_safe applies the per-cell formula-injection guard
    (_csv_safe) used by the supplier write-back sink. Caller owns unlinking."""
    os.makedirs(OUT, exist_ok=True)
    out_fd, out_path = tempfile.mkstemp(prefix=prefix, suffix=".csv", dir=OUT)
    with os.fdopen(out_fd, "w", encoding="utf-8-sig", newline="") as f:
        w = writer.shoptet_writer(f)
        w.writerow(header)
        if csv_safe:
            w.writerows([_csv_safe(c) for c in row] for row in rows)
        else:
            w.writerows(rows)
    return out_path


def _import_rows_chunked(all_rows, header, dry, prefix, csv_safe=False, timeout=900):
    """Import ``all_rows`` into Shoptet in chunks of <= IMPORT_CHUNK_ROWS rows — one
    careful ``run_import`` per chunk (#156: a single large import overran the browser
    redirect timeout). Chunks run SEQUENTIALLY; the FIRST failing chunk STOPS the batch
    (the remaining rows are left for the next run — the caller's uploaded-state gating
    on ``success_codes`` keeps the push idempotent + resumable). The caller MUST hold
    ``_import_lock`` across this call and release it in a ``finally``. Never raises
    ``TimeoutExpired`` — a chunk timeout is caught and treated as a failed chunk, so the
    lock is always released and no stuck import-lock is left behind (the 21:03 cascade).

    A chunk Shoptet PARTIALLY accepted ('Spracované: 35. Upravené: 31. Zlyhanie
    variantov: 2.' — every submitted row processed, a few variants rejected) is NOT a
    failed chunk: those rows really did land in the eshop. It no longer stops the batch
    and is no longer booked as "0 rows imported" (#257 — that all-or-nothing accounting
    discarded 31 genuinely written rows a night and froze uploaded_pairings.json since
    2026-07-22). Its codes go to ``partial_codes``, NOT to ``success_codes``: the Shoptet
    log reports aggregate counts only and never says WHICH rows failed, so no individual
    row of that chunk may be claimed uploaded on the strength of the log alone (the
    caller proves them from the eshop's own export instead — _export_row_verdicts).

    Returns a dict:
      ok             True iff EVERY chunk was fully clean (dry-run all-ok counts as ok)
      success_codes  set of r[0] for every row in a fully SUCCESSFUL chunk
      partial_codes  set of r[0] for every row in a PARTIALLY accepted chunk (some of
                     them landed — which ones is unknowable from the log)
      partial        True iff any chunk was partially accepted
      partial_failed how many rows Shoptet rejected across the partial chunks
      chunks_total / chunks_ok / chunks_partial / rows_ok
      processed / updated / failed   summed over completed chunks (None-safe)
      error_detail   first hard-failing chunk's Shoptet error line, if any
      rc             0 iff every chunk was clean, else the first non-clean chunk's rc
      stdout_tail / err   from the LAST attempted chunk (surfaced upstream)
    """
    chunks = [all_rows[i:i + IMPORT_CHUNK_ROWS]
              for i in range(0, len(all_rows), IMPORT_CHUNK_ROWS)]
    success_codes, partial_codes = set(), set()
    agg = {"processed": 0, "updated": 0, "failed": 0}
    seen = {"processed": False, "updated": False, "failed": False}
    rc, error_detail, stdout_tail, err_tail = 0, None, "", ""
    chunks_ok = chunks_partial = rows_ok = rows_partial = partial_failed = 0
    unreadable = False
    for i, chunk in enumerate(chunks, 1):
        chunk_path = _write_import_csv(header, chunk, prefix, csv_safe=csv_safe)
        try:
            crc, out, err = run_import(chunk_path, dry_run=dry, timeout=timeout)
        except subprocess.TimeoutExpired:
            log.error("import %schunk %d/%d timed out — killed import group",
                      prefix, i, len(chunks))
            rc, err_tail = 1, "import timeout"
            error_detail = error_detail or "import timeout"
            # the CSV was submitted and Shoptet was very likely still processing it —
            # exactly the "we cannot read our own answer" case, NOT "nothing landed"
            unreadable = True
            break
        finally:
            _safe_unlink(chunk_path)
        # ONLY the script's own result slice — the raw stdout starts with an echo of
        # the baseline Log entry, whose 'Spracované: N' parse_import_log would find
        # FIRST and report as this run's result (#196/#257).
        parsed = parse_result_stdout(out)
        stdout_tail, err_tail = (out or "")[-800:], (err or "")[-400:]
        outcome = chunk_outcome(crc, parsed, len(chunk))
        if outcome == "failed":
            rc, error_detail = crc, parsed.get("error_detail")
            # an UNREADABLE result says nothing about what landed — the rows very
            # likely DID reach the eshop and only the read-back failed
            unreadable = parsed.get("processed") is None and not error_detail
            log.error("import %schunk %d/%d FAILED rc=%s", prefix, i, len(chunks), crc)
            break
        for k in agg:
            v = parsed.get(k)
            if v is not None:
                agg[k] += v
                seen[k] = True
        if outcome == "partial":
            # rc is kept non-zero so the run is reported as not-fully-ok, but the
            # batch CONTINUES — the remaining chunks are independent uploads.
            rc = rc or crc
            chunks_partial += 1
            rows_partial += len(chunk)
            partial_failed += parsed.get("failed") or 0
            partial_codes.update(r[0] for r in chunk)
            log.warning("import %schunk %d/%d PARTIAL processed=%s failed=%s — "
                        "riadky sa potvrdia z exportu, nie z logu",
                        prefix, i, len(chunks), parsed.get("processed"),
                        parsed.get("failed"))
            continue
        chunks_ok += 1
        rows_ok += len(chunk)
        success_codes.update(r[0] for r in chunk)
        log.info("import %schunk %d/%d OK processed=%s",
                 prefix, i, len(chunks), parsed.get("processed"))
    return {
        "ok": rc == 0, "success_codes": success_codes,
        "partial_codes": partial_codes, "partial": chunks_partial > 0,
        "partial_failed": partial_failed, "chunks_partial": chunks_partial,
        "chunks_total": len(chunks), "chunks_ok": chunks_ok, "rows_ok": rows_ok,
        "rows_partial": rows_partial,
        "processed": agg["processed"] if seen["processed"] else None,
        "updated": agg["updated"] if seen["updated"] else None,
        "failed": agg["failed"] if seen["failed"] else None,
        "error_detail": error_detail, "rc": rc, "unreadable": unreadable,
        "stdout_tail": stdout_tail, "err": err_tail,
    }


def _chunk_error_msg(res, total_rows, confirms_from_export=False):
    """ONE message for a chunked import that did not finish fully clean (the same
    text every caller used to build inline — NEkopíruj logiku). Two distinct cases,
    because they mean opposite things to the manager:

      * a chunk HARD-failed and stopped the batch → which chunk + how many rows
        still made it (the rest are retried next run);
      * every chunk ran but Shoptet REJECTED some rows (#257 'Zlyhanie variantov')
        → the rest genuinely landed; which ones is only provable from the eshop's
        own export, so those rows are re-sent until the export confirms them.

    `confirms_from_export` must be set ONLY by a caller that really reconciles
    against the catalog export (today: the pairings push). The other write paths
    (supplier / externalCode / restock / stock / raw n8n import) simply re-send, and
    telling the manager their rows "will be confirmed from the export" would be a
    promise nothing keeps.
    """
    done = res["chunks_ok"] + res.get("chunks_partial", 0)
    if done < res["chunks_total"]:
        where = f"časti {done + 1}/{res['chunks_total']}"
        count = f"{res['rows_ok']}"
        if res.get("rows_partial"):
            # rows_ok counts only fully clean chunks; a preceding partial chunk did
            # write most of its rows, so report it instead of understating the push.
            # Clamped: 'Zlyhanie variantov: N' is an aggregate Shoptet count and may
            # exceed the rows we sent if it ever counts VARIANTS rather than rows —
            # a NEGATIVE number of accepted rows would be nonsense to the manager.
            accepted = max(0, res["rows_partial"] - res.get("partial_failed", 0))
            count += f" (+{accepted} čiastočne prijatých)"
        detail = f": {res['error_detail']}" if res.get("error_detail") else ""
        if res.get("unreadable"):
            # NOT the same as "nothing was imported": the rows WERE submitted and very
            # likely landed, we just could not read/attribute Shoptet's own answer (an
            # unattributable Log entry — or a TIMEOUT, where Shoptet was still chewing
            # on a CSV it had already accepted)
            return (f"výsledok {where} sa nepodarilo prečítať — riadky mohli prejsť, "
                    f"over Log v Shoptete (potvrdených {count} z {total_rows} riadkov)"
                    + detail)
        return (f"import zlyhal na {where} "
                f"(naimportované {count} z {total_rows} riadkov){detail}")
    tail = (" (zvyšok sa naimportoval; potvrdí sa z exportu eshopu)"
            if confirms_from_export else " (zvyšok sa naimportoval)")
    return (f"Shoptet odmietol {res.get('partial_failed', 0)} z {total_rows} riadkov"
            + tail)


def _export_note_index() -> tuple[dict, set]:
    """ONE STREAMING pass over the on-disk catalog export (data/products.csv,
    refreshed hourly by „Sync zo Shoptetu") → two facts about the LIVE eshop:

      • notes = {variant code: internalNote} — the ONLY per-row proof that a reorder
        link really reached the eshop: the Shoptet import log reports aggregate
        counts only ('Spracované: 35. Upravené: 31. Zlyhanie variantov: 2.') and
        never says WHICH rows failed;
      • codes = EVERY variant code the catalog carries — a code that is NOT in here
        does not exist in the eshop at all, so any row addressing it is rejected on
        every import for ever (#270).

    A missing/empty export yields ({}, set()) — nothing is then confirmed and
    nothing is held back, i.e. every row is (re-)sent, which is the safe direction
    for this write (unlike the supplier write-back, which must fail CLOSED — there
    an empty export could CLOBBER a real eshop value, here it only means a harmless
    idempotent re-upload).

    Takes no pre-read text on purpose: the export is read HERE (by the caller that
    has just checked its AGE), never handed in — #272
    made this the streaming path precisely so nobody ever needs to pass ~57 MB
    around."""
    csv.field_size_limit(10**9)
    notes, codes, conflicting = {}, set(), set()
    for r in csv.DictReader(_iter_export_lines(), delimiter=";"):
        code = (r.get("code") or "").strip()
        if not code:
            continue
        codes.add(code)
        val = (r.get("internalNote") or "").strip()
        if code in notes and notes[code] != val:
            # the catalog holds duplicate products sharing variant codes (see
            # import_builder.link_rows) — if two rows disagree about a code, neither
            # proves anything, so the code must never count as confirmed
            conflicting.add(code)
        notes[code] = val
    for code in conflicting:
        notes.pop(code, None)
    return notes, codes


# How old the catalog export may be and still PROVE a row landed. „Sync zo Shoptetu"
# rewrites data/products.csv hourly, so 6 h tolerates a few missed syncs and still
# refuses an export from a sync that has been off/broken for half a day.
EXPORT_MAX_AGE_S = 6 * 3600

# How many „the eshop has no such code" rows the nightly result lists by name (#270).
# The count is always exact; the list is what the tab renders, and the manager fixes
# them one by one — a runaway list must not bloat automations.json.
MISSING_CODES_SHOWN = 50

# The catalogue is ~14 000 variant codes. An export carrying FAR fewer is not a small
# catalogue, it is a broken feed (a changed export pattern, a filter left on, a
# truncated download) — and believing it would accuse codes the eshop really has, and
# withhold their rows. A floor two orders of magnitude below reality never fires on a
# genuine catalogue, and (unlike a RATIO of the batch) it cannot disarm itself once the
# batch consists only of the doomed rows.
EXPORT_MIN_CODES = 1000

# …but 1000 against a ~14 000-code catalogue leaves a WIDE band of false confidence:
# a truncated export carrying 1200 codes cleared it, and the app then declared the
# other ~12 800 codes „missing from the eshop" — holding their rows and accusing them
# on the automation card (#277: measured, 12 866 such codes). The real floor is a
# RATIO of how big the catalogue actually is.
#
# The reference has to be its OWN persisted store: `len(CODE2PAIR)` is rebuilt from
# the SAME export, so a broken feed would poison the reference too, and a ratio of the
# BATCH disarms itself once the batch is only the doomed rows (#270). So: the largest
# code count seen in the last EXPORT_WATERMARK_WINDOW_DAYS, kept in daily buckets. A
# bad export can only fail to RAISE it (the buckets fold with max()), never lower it.
EXPORT_WATERMARK = _store("export_watermark.json")
EXPORT_WATERMARK_WINDOW_DAYS = 7
EXPORT_WATERMARK_RATIO = 0.5
# …and an UPPER bound on a single reading (PR #280 review). The window bounds the
# watermark in TIME but nothing bounded it in SIZE, so ONE implausible observation
# (a duplicated feed, a parse anomaly) raised the floor above reality and refused the
# HEALTHY export for a full 7 days: measured, one reading of 50 000 gave floor 25 000
# and locked out the real 14 066-code catalogue.
#
# 1.5 is chosen against EXPORT_WATERMARK_RATIO (0.5): a clamped reading yields a floor
# of 0.75 × the previous watermark, so an export at (or slightly below) the size we
# already believe still clears it — a growth cap of 2.0 would put the floor exactly AT
# the old watermark and refuse a catalogue that merely lost a few products.
#
# It bounds one reading, not a trend: honest repeated readings compound 1.5× per sync,
# so a genuine doubling is fully absorbed within a few hourly syncs with no human
# action. That is deliberate — it is the same „recovery is by TIME" property the window
# has, and for the same reason (repetition must not be able to shortcut a gate).
EXPORT_WATERMARK_MAX_GROWTH = 1.5
# How much smaller than what we already hold a DOWNLOAD may be before we refuse to
# swap it in (bytes vs bytes — never lines vs codes: multi-line HTML descriptions make
# a line count far exceed the code count, so a ratio calibrated for codes would reject
# perfectly healthy exports).
EXPORT_FETCH_MIN_RATIO = 0.5


def _watermark_days(state) -> dict:
    """The {day: count} buckets, defensively typed. Junk buckets are dropped rather
    than raising: this store is DERIVED state that the next sync rebuilds, so the safe
    degradation is „we do not know the catalogue size" (→ the absolute floor, i.e. the
    pre-#277 behaviour), never taking the nightly push down over a cache file."""
    days = state.get("days") if isinstance(state, dict) else None
    if not isinstance(days, dict):
        return {}
    return {k: v for k, v in days.items()
            if isinstance(k, str) and isinstance(v, int)
            and not isinstance(v, bool) and v > 0}


def _watermark_window(today=None) -> tuple:
    """(oldest day still inside the window, today) — both ISO, INCLUSIVE.

    Bounded from BOTH sides on purpose. A bucket dated in the FUTURE (a corrupt write,
    a clock skew) compared only against the lower cutoff would sit inside the window
    for ever, so a stale high reading would keep the floor high and the supplier
    write-back blocked with no way out — the same trap the playbook already records
    for `at` (posta terminal cache) and `claimed_at` (the reminder claim)."""
    today = today or datetime.now(timezone.utc).date()
    return (today - timedelta(days=EXPORT_WATERMARK_WINDOW_DAYS - 1)).isoformat(), \
        today.isoformat()


def _export_watermark(today=None) -> int:
    """The largest catalogue size observed inside the window, or 0 when we have never
    observed one (a fresh deploy, or a window that has aged out entirely)."""
    lo, hi = _watermark_window(today)
    live = [v for k, v in _watermark_days(_read_json_store(EXPORT_WATERMARK, {})).items()
            if lo <= k <= hi]
    return max(live) if live else 0


def _export_watermark_observe(codes: int, today=None) -> int:
    """Record `codes` as today's reading and prune the window. Called from EXACTLY one
    place — `run_shoptet_sync`, on the index just rebuilt from freshly downloaded bytes
    (the freshest ground truth there is). Deliberately NOT hidden inside
    `_export_note_index`/`_export_supplier_index`: a write on a read path would turn
    „read the export" into a mutation, and would let a STALE on-disk export keep
    re-asserting the old size for ever — which is precisely what would break the
    self-healing below.

    The day bucket keeps the LARGEST reading, so one bad hour cannot overwrite the good
    reading taken an hour earlier."""
    lo, hi = _watermark_window(today)
    with _lock:
        days = _watermark_days(_read_json_store(EXPORT_WATERMARK, {}))
        days = {k: v for k, v in days.items() if lo <= k <= hi}
        if codes > 0:
            # CLAMP a single reading to a bounded multiple of what we already believe
            # (#280 review): one implausible observation must not raise the floor above
            # reality and refuse the healthy export for the whole window. Nothing to
            # clamp against on a fresh deploy (or an aged-out window) → adopt as seen,
            # or the very first sync would understate the catalogue.
            current = max(days.values()) if days else 0
            capped = int(codes)
            if current > 0:
                capped = min(capped, int(current * EXPORT_WATERMARK_MAX_GROWTH))
                if capped < int(codes):
                    log.warning("export watermark: reading %d clamped to %d (max %.1f× "
                                "the %d we already believe) — one implausible reading "
                                "must not lock out the healthy export",
                                int(codes), capped, EXPORT_WATERMARK_MAX_GROWTH, current)
            days[hi] = max(days.get(hi, 0), capped)
        _atomic_write_json(EXPORT_WATERMARK, {"days": days}, indent=None)
    return max(days.values()) if days else 0


def _export_min_codes(today=None) -> int:
    """How many codes an export must carry to be believed — ONE threshold for BOTH
    gates (`_export_row_verdicts`'s trust check and `_do_upload_suppliers`'s write
    check), so they can never drift apart (playbook, TEST-PASCA 2).

    Recovery is by TIME and is bounded in both directions:
      • no watermark yet (fresh deploy, corrupt store, a window that aged out) → the
        absolute floor, i.e. exactly the pre-#277 behaviour: the first sync can never
        be blocked out of the box;
      • a catalogue that GENUINELY shrinks below the ratio is accepted with no human
        action once the old readings leave the window (≤ EXPORT_WATERMARK_WINDOW_DAYS).
        Until then the state is SAFE, not lossy: supplier assignments are HELD (never
        recorded uploaded) and the pairings half simply falls back to sending its rows.
    Repetition cannot shorten that on purpose — a persistently broken feed produces the
    same repeated reading as a genuine shrink, so „N agreeing observations" would accept
    the very thing this gate exists to reject."""
    return max(EXPORT_MIN_CODES, int(EXPORT_WATERMARK_RATIO * _export_watermark(today)))


def _missing_report(codes, values) -> dict:
    """The „eshop taký kód nemá" block both halves of the nightly push return (#270):
    an exact count plus the first MISSING_CODES_SHOWN codes with the value we wanted
    to write (the reorder URL for pairings, the supplier name for the write-back), so
    the manager can act on the code instead of guessing which rows Shoptet refused."""
    return {"missing_count": len(codes),
            "missing_in_eshop": [{"code": c, "value": (values.get(c) or "").strip()}
                                 for c in codes[:MISSING_CODES_SHOWN]]}


def _export_age_s():
    """Age of the on-disk catalog export in seconds, or None when it cannot be stat'd
    (missing file / a test that patches the reader). Unknown age never BLOCKS: with no
    file at all `_iter_export_lines` already yields nothing → nothing is confirmed."""
    try:
        return max(0.0, time.time() - os.path.getmtime(SRC))
    except OSError:
        return None


def _export_row_verdicts(rows, note_col=2) -> dict:
    """What the eshop's own catalog export says about `rows` (code;pairCode;
    internalNote) — BOTH verdicts from ONE streaming pass:

      confirmed  the eshop ALREADY carries the row exactly as we would write it —
                 proven uploaded, so it needs neither a re-upload nor a guess. This
                 is what un-freezes the nightly push (#257): rows Shoptet accepted
                 inside a partially-rejected batch are credited on the next run from
                 the eshop's own export instead of being re-sent forever.
      absent     the eshop's catalogue does not carry that variant code AT ALL, so
                 the row can NEVER import — Shoptet rejects it on every single run
                 („Zlyhanie variantov: 2", the same two rows every night since
                 24. 7. 2026). Holding them back is what stops the endless nightly
                 rejection; the caller LISTS them for the manager instead (#270).

    Holding a row back is deliberately NOT recorded as uploaded and is bounded and
    self-healing: a code that reappears in the catalogue (the manager fixes it, or a
    freak export was wrong) is simply sent on the next run.

    A confirmed row is NOT sent at all and IS recorded uploaded, so this credit is only
    as trustworthy as the export it reads. data/products.csv is refreshed by a SEPARATE
    hourly automation; while that sync is off/broken the file keeps its last contents,
    and a code cleared or changed in the eshop since then would be credited from stale
    bytes and silently never re-written. Refuse to credit anything from an export older
    than EXPORT_MAX_AGE_S and fall back to actually SENDING those rows (idempotent —
    the same URL is simply written again), mirroring the supplier write-back's
    fail-closed stance on an unusable export.

    The export is read HERE, never handed in: a `notes=` parameter (no caller ever
    used one) would be the single entry point through which the natural future
    optimisation „read the export once, pass it down" could credit rows from bytes
    whose age was never checked — the guard below can only hold while the read and
    the freshness check stay in the same place. The same guard now protects the
    `absent` verdict, which is a WRITE condition too (it withholds a row).

    `note_col=None` (#299 — the combined-import verdict pass over the WHOLE pending
    table, which has no single fixed column layout: every row can carry a different
    set of queued fields) skips the `confirmed` comparison entirely — there is no
    single column that means "internalNote" across a header built from `sorted(cols)`
    over an arbitrary field set — and returns only `absent`. Never index `r[note_col]`
    when it is `None`; every other caller keeps the default `2` and is unaffected."""
    none = {"confirmed": set(), "absent": set()}
    age = _export_age_s()
    if age is not None and age > EXPORT_MAX_AGE_S:
        log.warning("export je starý %.1f h (limit %.1f h) — nepotvrdzujem z neho "
                    "žiadne riadky ani nezadržiavam žiadne, radšej ich pošlem znova",
                    age / 3600, EXPORT_MAX_AGE_S / 3600)
        return none
    notes, codes = _export_note_index()
    floor = _export_min_codes()
    if len(codes) < floor:
        # no export, an export with no product rows, or an implausibly small one: we
        # do not know the catalogue, so nothing may be credited AND nothing may be
        # called missing
        if codes:
            log.warning("export nesie len %d kódov (limit %d) — vyzerá neúplne, "
                        "nepotvrdzujem ani nezadržiavam nič", len(codes), floor)
        return none
    if note_col is None:
        confirmed = set()
    else:
        confirmed = {r[0] for r in rows
                     if r[0] in notes and notes[r[0]] == (r[note_col] or "").strip()}
    absent = {r[0] for r in rows if r[0] not in codes}
    return {"confirmed": confirmed, "absent": absent}


@app.route("/api/n8n/shoptet-import", methods=["POST"])
def n8n_shoptet_import():
    """n8n posts a restock CSV (multipart 'file', or raw body); we whitelist it to
    the safe restock columns and run the careful Shoptet import. Bearer-auth'd.
    Pass dry_run=1 (form/query) to reach the import form without changing anything."""
    token = _import_token()
    auth = request.headers.get("Authorization", "")
    expected = f"Bearer {token}".encode() if token else b""
    # compare bytes — a non-ASCII Authorization header must 401, not raise (latin-1
    # is how WSGI decodes the header; compare_digest rejects non-ASCII str args)
    if not token or not hmac.compare_digest(auth.encode("latin-1", "ignore"), expected):
        log.warning("n8n import: unauthorized call from %s", _client_ip())
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    f = request.files.get("file")
    raw = f.read() if f else request.get_data()
    if not raw:
        log.warning("n8n import: empty body")
        return jsonify({"ok": False, "error": "empty body"}), 400
    if len(raw) > MAX_IMPORT_BYTES:
        log.warning("n8n import: payload too large (%d B)", len(raw))
        return jsonify({"ok": False, "error": "payload too large"}), 413

    os.makedirs(OUT, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    # unique names (mkstemp) so two same-second calls never clobber each other's
    # file while a subprocess is reading it
    raw_fd, raw_path = tempfile.mkstemp(prefix=f"n8n_restock_{ts}_", suffix="_raw.csv", dir=OUT)
    out_fd, out_path = tempfile.mkstemp(prefix=f"n8n_restock_{ts}_", suffix=".csv", dir=OUT)
    os.close(out_fd)
    with os.fdopen(raw_fd, "wb") as w:
        w.write(raw)
    try:
        row_count = import_builder.sanitize_csv(raw_path, out_path)
    except (ValueError, UnicodeDecodeError) as e:
        log.warning("n8n import: bad CSV: %s", e)
        _safe_unlink(raw_path, out_path)
        return jsonify({"ok": False, "error": str(e)}), 400
    finally:
        _safe_unlink(raw_path)   # sanitized file is the audit record; raw is transient
    if row_count == 0:
        log.info("n8n import: 0 restock rows — nothing to import")
        _safe_unlink(out_path)
        return jsonify({"ok": True, "rows": 0, "message": "no restock rows"}), 200

    # #158: chunk like #156 — a large restock feed can overrun the browser redirect
    # timeout just like the pairings/suppliers pushes did. out_path is already the
    # sanitized audit file in canonical RESTOCK_COLS order; read its rows back
    # instead of re-parsing the raw upload.
    with open(out_path, encoding="utf-8-sig", newline="") as f:
        rows_data = list(csv.reader(f, delimiter=";"))[1:]

    dry = str(request.values.get("dry_run", "")).lower() in ("1", "true", "yes")
    if not _import_lock.acquire(blocking=False):
        log.warning("n8n import: another import already running")
        _safe_unlink(out_path)
        return jsonify({"ok": False, "error": "import already running"}), 409
    log.info("n8n import: %d rows (chunks of %d), dry_run=%s, audit=%s",
             row_count, IMPORT_CHUNK_ROWS, dry, out_path)
    try:
        res = _import_rows_chunked(rows_data, import_builder.RESTOCK_COLS, dry,
                                   prefix=f"n8n_restock_{ts}_chunk_", timeout=900)
    finally:
        _import_lock.release()

    err_msg = ""
    if not res["ok"]:
        err_msg = _chunk_error_msg(res, row_count)
    result = {"ok": res["ok"], "exit_code": res["rc"], "rows": row_count, "dry_run": dry,
              "processed": res["processed"], "updated": res["updated"],
              "failed": res["failed"], "error_detail": res["error_detail"], "error": err_msg,
              "chunks_total": res["chunks_total"], "chunks_ok": res["chunks_ok"],
              "stdout_tail": res["stdout_tail"]}
    log.info("n8n import: rc=%s chunks=%d/%d processed=%s updated=%s failed=%s",
             res["rc"], res["chunks_ok"], res["chunks_total"],
             res["processed"], res["updated"], res["failed"])
    if not res["ok"]:
        log.error("n8n import FAILED rc=%s chunks_ok=%d/%d stderr=%s",
                  res["rc"], res["chunks_ok"], res["chunks_total"], (res["err"] or "")[-400:])
    return jsonify(result), (200 if res["ok"] else 502)


# --------------------------------------------------------------------------- #
# #299 — the ONE table between our decisions and the eshop. Producers queue the
# field values they want; the hourly `shoptet_upload` cycle turns the whole table
# into a single import. protect=True: a queued change that is lost never reaches
# the eshop and nothing notices, which is exactly the silent loss the table exists
# to end.
# --------------------------------------------------------------------------- #
PENDING_SHOPTET = _store("pending_shoptet.json")


def _load_pending() -> dict:
    return _read_json_store(PENDING_SHOPTET, {})


def _save_pending(d: dict, *, prev: dict | None = None) -> None:
    """`prev=` names the read `d` was DERIVED from (#299 Task 6): `queue_fields`
    always GROWS or holds the count (an existing code only ever gains fields), so
    the shrink-guard's `new >= was` skip covers every queueing write without it.
    `settle` is the one caller that legitimately SHRINKS the table (confirmed rows
    drop out) — it hands back a brand-new dict, not the object `_load_pending`
    read, so without `prev=` naming that read the guard cannot tell this shrink
    apart from an unrelated smaller map and correctly refuses it (store-prune.md
    §3: "ak staviaš NOVÚ mapu, musíš pridať prev=d0")."""
    _atomic_write_json(PENDING_SHOPTET, d, protect=True, prev=prev)


def queue_shoptet_fields(source, header, rows, credit_group=None,
                         credit_value=None) -> int:
    """Queue rows for the next hourly upload. Returns how many field values landed."""
    if not rows:
        return 0
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    with _lock:
        # A table we could not read is NOT an empty table — queueing on top of the
        # default would silently drop everything already waiting. That refusal is
        # NOT hand-rolled here: `_atomic_write_json(protect=True)` in `_save_pending`
        # already refuses the wipe, quarantines the corrupt file and raises
        # StoreWipeRefused, which the app turns into a 503 with instructions. A
        # hand-rolled `raise` in front of it short-circuits exactly that machinery
        # (no quarantine, wrong exception type, and an empty file — a real
        # post-crash shape — would brick queueing for ever).
        pending = _read_json_store(PENDING_SHOPTET, {})
        pending, n = shoptet_outbox.queue_fields(
            pending, source, header, rows,
            credit_group=credit_group, credit_value=credit_value, now=now)
        if not n:
            return 0          # nothing queued → never rewrite a protected store
        _save_pending(pending)
    log.info("outbox: %s zaradil %d polí (%d kódov)", source, n, len(rows))
    return n


# --------------------------------------------------------------------------- #
# n8n → nightly upload of worker pairings (reorder links → eshop internalNote)
# --------------------------------------------------------------------------- #
PAIRINGS_STATE = _store("uploaded_pairings.json")


def _load_uploaded():
    """{key: url} of pairings already uploaded — so the nightly job only sends new
    or changed ones. Missing/corrupt → empty (treat everything as new)."""
    # always a {key: url} map — a stray JSON array could repeat a key and break the
    # "total_uploaded never exceeds total_products" invariant in _pairing_summary
    return _read_json_store(PAIRINGS_STATE, {})


def _save_uploaded(d):
    _atomic_write_json(PAIRINGS_STATE, d, protect=True)


# Public URL of the review web — handed to the n8n notifier so the single summary
# Discord message can link straight to the pairing app.
PUBLIC_URL = os.environ.get("WEBREVIEW_PUBLIC_URL", "https://parovanie-forestshop.newlevel.media")


def _pairing_summary(uploaded):
    """Totals for the n8n summary notification: how many pairings are uploaded to the
    eshop in total, how many of our products still have none, and the review link.
    ``uploaded`` is the post-run map so ``total_uploaded`` already includes this run.
    Only keys still present in the current review set count, so a product removed
    since its upload can't push the ratio past total (e.g. avoid "Spolu 105 / 100")."""
    valid = {p.get("key") for p in PRODUCTS}
    total = len(valid)  # distinct product keys (de-dups), same unit as `up` below
    up = sum(1 for k in uploaded if k in valid)
    return {"total_products": total, "total_uploaded": up,
            "remaining": max(0, total - up), "review_url": PUBLIC_URL}


def _do_upload_pairings(dry):
    """Core of the nightly pairings upload — the SINGLE place the pairing-upload
    logic lives (NEkopíruj logiku). Reads the review decisions AND the inline
    order_pairings (codes pasted directly on a to-order line, OUTSIDE the review
    set — #38), builds ONE combined link import (code;pairCode;internalNote) for
    whatever of either source is not yet uploaded, runs the careful import, records
    what went up, and returns (result, status) for the caller to serialize. Shared
    by the n8n HTTP endpoint (below) and the in-app „Párovania → eshop" automation
    (#109) — no auth / Flask request access here. Visibility/stock are NOT touched
    — the morning restock job turns a product on once the supplier has it in stock.

    order_pairings entries are tracked under a distinct `order:<code>` namespace in
    the SAME uploaded_pairings.json state (a review key is always `SUPPLIER|pairCode`
    — never collides) and are excluded from THIS run's order rows when their code is
    already covered by a reviewed decision — a reviewed decision is authoritative,
    and Shoptet aborts the whole import on a duplicate code."""
    dec = _load_decisions()
    uploaded = _load_uploaded()
    new_keys = import_builder.new_pairing_keys(dec, uploaded)
    by_key = {p.get("key"): p for p in PRODUCTS}
    products = [{"name": by_key.get(k, {}).get("name", ""),
                 "our_url": by_key.get(k, {}).get("our_url", ""),
                 "supplier_url": dec[k].get("url", "")} for k in new_keys]

    order_pairings = _load_order_pairings()
    new_order_codes = import_builder.new_order_pairing_keys(order_pairings, uploaded)

    if not new_keys and not new_order_codes:
        log.info("n8n pairings: 0 new pairings")
        return {"ok": True, "count": 0, "products": [], "order_count": 0, "order_blocked": 0,
                "missing_count": 0, "missing_in_eshop": [],
                **_pairing_summary(uploaded)}, 200

    rows = import_builder.link_rows(PRODUCTS, {k: dec[k] for k in new_keys}, CODE2PAIR)
    # The exclusion is about OWNERSHIP, not about what this run happens to ship: a
    # reviewed decision outranks an inline pairing permanently, so it must be built
    # from ALL decisions exactly as the manual zip does (app.py /api/import), not from
    # THIS run's new keys. Built from `rows` it went EMPTY the night after a correction
    # shipped (the decision is no longer "new"), the stale `order_pairings[code]` was
    # emitted, and the eshop's internalNote was reverted — permanently, because the
    # code is then recorded uploaded and never retried, while the tab, /api/orders and
    # /api/import all kept showing the corrected link.
    owned_codes = {r[0] for r in import_builder.link_rows(
        PRODUCTS, dec, CODE2PAIR, _load_variant_links())}
    order_rows = import_builder.order_pairing_rows(
        {c: order_pairings[c] for c in new_order_codes}, CODE2PAIR,
        exclude_codes=owned_codes)
    all_rows = rows + order_rows
    if not all_rows:
        log.warning("n8n pairings: %d new keys + %d new order codes but 0 import rows",
                    len(new_keys), len(new_order_codes))
        # paired but un-uploadable — every decision key has 0 variant codes (blocked
        # below the fold). order_pairings CAN reach this branch on its own: a code
        # already owned by a reviewed decision (uploaded on an earlier night, so not
        # in `rows`) is excluded here too, which is precisely the point — it stays
        # "new" and blocked forever rather than reverting the eshop.
        return {"ok": True, "count": 0, "products": products,
                "order_count": 0, "order_blocked": len(new_order_codes),
                "message": "no import rows", "blocked": len(new_keys),
                "missing_count": 0, "missing_in_eshop": [],
                **_pairing_summary(uploaded)}, 200

    # surface a real data inconsistency: the same variant code paired to two different
    # supplier URLs (a code can hold only one link, so first-wins drops the rest)
    code_urls = {}
    for k in new_keys:
        for c in by_key.get(k, {}).get("variant_codes", []):
            code_urls.setdefault(c, set()).add((dec[k].get("url") or "").strip())
    conflicts = [c for c, u in code_urls.items() if len(u) > 1]
    if conflicts:
        log.warning("n8n pairings: %d codes paired to conflicting URLs (first wins): %s",
                    len(conflicts), conflicts[:10])

    # Not every key in new_keys necessarily lands a row: a product can have zero
    # variant codes, or ALL its codes can be the "seen"-deduped loser of an earlier
    # key sharing the same code (link_rows keeps only the first writer per code).
    # `uploadable_keys` = keys that got at least one code written to the CSV; a key
    # with none is `blocked` (surfaced, not silently dropped #49). Whether an
    # uploadable key is actually RECORDED uploaded depends on its chunk succeeding
    # (below).
    written_codes = {r[0] for r in rows}
    uploadable_keys = [k for k in new_keys
                       if written_codes & set(by_key.get(k, {}).get("variant_codes") or [])]
    blocked_keys = [k for k in new_keys if k not in uploadable_keys]
    if blocked_keys:
        log.warning("n8n pairings: %d of %d keys generated no row (codes missing/deduped): %s",
                    len(blocked_keys), len(new_keys), blocked_keys[:10])

    # An order_pairings code gets no row only when order_pairing_rows excluded it —
    # i.e. its code is owned by a reviewed decision (from ANY night, not just this
    # one). It stays "new" (never marked order:<code>) so it's retried if the
    # decision ever goes away.
    order_written_codes = {r[0] for r in order_rows}
    blocked_order_codes = [c for c in new_order_codes if c not in order_written_codes]
    if blocked_order_codes:
        log.warning("n8n pairings: %d order_pairings codes already covered by a reviewed decision: %s",
                    len(blocked_order_codes), blocked_order_codes[:10])

    # #257: the eshop's own export is the ONLY per-row proof that a link landed. A
    # row whose internalNote already IS what we would send is confirmed uploaded —
    # it is recorded and NOT sent again. That is what un-freezes the nightly push:
    # rows Shoptet accepted inside a partially-rejected batch (whose identity the
    # import log never reveals) get credited on the next run instead of being
    # rebuilt and re-sent every night forever.
    # #270: the mirror image — a code the catalogue does NOT carry at all can never
    # import (Shoptet rejects that row on every run, for ever). Hold it back and LIST
    # it with the URL we tried to write, so the manager can fix the code instead of
    # reading a bare „Shoptet odmietol 2 riadkov" every morning. Never credited, so
    # the moment the code appears in the catalogue it is written.
    verdicts = _export_row_verdicts(all_rows)
    confirmed = verdicts["confirmed"]
    # (the two sets are disjoint by construction — confirmed ⊆ notes ⊆ codes, absent is
    # „not in codes" — the subtraction is belt-and-braces against a future verdict change)
    absent = verdicts["absent"] - confirmed
    send_rows = [r for r in all_rows if r[0] not in confirmed and r[0] not in absent]
    missing = _missing_report(sorted(absent), {r[0]: r[2] for r in all_rows})
    if confirmed:
        log.info("n8n pairings: %d z %d riadkov už eshop má presne tak, ako by sme "
                 "ich poslali — potvrdené z exportu, neposielam znova",
                 len(confirmed), len(all_rows))
    if absent:
        # A review key with SEVERAL variant codes, one of them absent, can never be
        # recorded uploaded (its landed codes get confirmed, the absent one never
        # enters `success`, and a key is credited only when ALL its written codes did
        # — the #49 rule). It therefore stays „new" and reports +0 nových every night
        # until the code is fixed. That is the safe direction, and the card names the
        # exact offending code below, which is what the manager has to act on.
        log.warning("n8n pairings: %d kódov eshop v katalógu vôbec nemá — "
                    "neposielam ich (Shoptet by ich zakaždým odmietol): %s",
                    len(absent), sorted(absent)[:10])

    if not _import_lock.acquire(blocking=False):
        log.warning("n8n pairings: another import already running")
        return {"ok": False, "error": "import already running"}, 409
    log.info("n8n pairings: %d products, %d order codes, %d rows to send of %d "
             "(chunks of %d), dry_run=%s", len(new_keys), len(new_order_codes),
             len(send_rows), len(all_rows), IMPORT_CHUNK_ROWS, dry)
    try:
        # #156: import in chunks so no single large import overruns the browser
        # redirect timeout. A HARD-failing chunk stops the batch; a partially
        # accepted one does not (#257) — its rows are simply not credited from the
        # log, they wait for the export to confirm them.
        res = _import_rows_chunked(send_rows, import_builder.LINK_HEADER, dry,
                                   prefix="import_links_", timeout=900)
        # `partial` already forces a non-zero rc (so res["ok"] is False); the explicit
        # term keeps that invariant local — a partially rejected push is never "ok".
        ok = res["ok"] and not res["partial"]
        success = res["success_codes"] | confirmed
        if res["partial_codes"]:
            log.warning("n8n pairings: %d riadkov čaká na potvrdenie z exportu "
                        "(Shoptet ich prijal v čiastočne odmietnutej dávke): %s",
                        len(res["partial_codes"]), sorted(res["partial_codes"])[:10])
        # A decision key is recorded uploaded only when EVERY one of its written codes
        # landed in a SUCCESSFUL chunk — a key straddling the failed boundary stays
        # "new" (re-uploading its done codes next run is idempotent, same URL; marking
        # it done would lose its un-uploaded codes, the #49 class). On partial failure
        # this records the successful chunks so the next run only retries the rest
        # (resumable), never all-or-nothing.
        uploaded_keys = [
            k for k in uploadable_keys
            if (written_codes & set(by_key.get(k, {}).get("variant_codes") or [])) <= success]
        uploaded_order_codes = [c for c in new_order_codes
                                if c in order_written_codes and c in success]
        if not dry:
            done = {k: (dec[k].get("url") or "").strip()      # keys fully imported OK
                    for k in uploaded_keys}
            done.update({f"order:{c}": (order_pairings[c] or "").strip()
                         for c in uploaded_order_codes})      # order codes imported OK
            uploaded = _record_uploaded(_load_uploaded, _save_uploaded, done)
    finally:
        _import_lock.release()

    err_msg = ""
    if not ok:                               # clear, tab-surfaced message: which chunk + progress
        err_msg = _chunk_error_msg(res, len(send_rows), confirms_from_export=True)
    result = {"ok": ok, "exit_code": res["rc"], "count": len(uploaded_keys),
              "rows": len(all_rows), "rows_sent": len(send_rows),
              "confirmed_in_export": len(confirmed),
              "partial": res["partial"], "rejected": res["partial_failed"],
              "dry_run": dry, "processed": res["processed"],
              "updated": res["updated"], "failed": res["failed"],
              "error_detail": res["error_detail"], "error": err_msg,
              "chunks_total": res["chunks_total"], "chunks_ok": res["chunks_ok"],
              "products": products, "stdout_tail": res["stdout_tail"],
              "blocked": len(blocked_keys),
              **missing,
              "order_count": len(uploaded_order_codes),
              "order_blocked": len(blocked_order_codes),
              **_pairing_summary(uploaded)}
    log.info("n8n pairings: rc=%s chunks=%d/%d processed=%s products=%d order_codes=%d "
             "confirmed_from_export=%d rejected=%d",
             res["rc"], res["chunks_ok"], res["chunks_total"], res["processed"],
             len(uploaded_keys), len(uploaded_order_codes), len(confirmed),
             res["partial_failed"])
    if not ok:
        log.error("n8n pairings FAILED rc=%s chunks_ok=%d/%d stderr=%s",
                  res["rc"], res["chunks_ok"], res["chunks_total"], (res["err"] or "")[-400:])
    return result, (200 if ok else 502)


@app.route("/api/n8n/upload-pairings", methods=["POST"])
def n8n_upload_pairings():
    """n8n's nightly caller: Bearer-auth then delegate to _do_upload_pairings.
    dry_run=1 reaches the import without changing anything."""
    token = _import_token()
    auth = request.headers.get("Authorization", "")
    expected = f"Bearer {token}".encode() if token else b""
    if not token or not hmac.compare_digest(auth.encode("latin-1", "ignore"), expected):
        log.warning("n8n pairings: unauthorized call from %s", _client_ip())
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    dry = str(request.values.get("dry_run", "")).lower() in ("1", "true", "yes")
    result, status = _do_upload_pairings(dry)
    return jsonify(result), status


# --------------------------------------------------------------------------- #
# n8n → nightly upload of assigned supplier names (→ eshop `supplier` field)
# --------------------------------------------------------------------------- #
SUPPLIERS_STATE = _store("uploaded_suppliers.json")


def _load_uploaded_suppliers():
    """{code: supplier} already written back to the eshop — so the nightly job only
    sends new or changed assignments. Missing/corrupt → empty (everything is new).
    Always a dict (a stray array could repeat a code and break the summary invariant)."""
    return _load_uploaded_suppliers_state()[0]


def _load_uploaded_suppliers_state() -> tuple:
    """`({code: supplier}, from_disk)` — the same map, plus whether it really came off
    disk (PR #295 review).

    Degrading to `{}` is right for SENDING (an unknown record means „send it again",
    which is idempotent) and catastrophic for DELETING: #215 condemns an assignment on
    `c not in uploaded`, so a missing or corrupt record condemns the whole store at once.
    The removal therefore asks for this flag; everything else keeps using the plain
    loader."""
    return _read_json_store_state(SUPPLIERS_STATE, {})


def _save_uploaded_suppliers(d):
    _atomic_write_json(SUPPLIERS_STATE, d, protect=True)


def _supplier_summary(uploaded, assigns):
    """Totals for the n8n summary notification: assigned codes, how many are already
    written back (uploaded value still matches the current assignment), how many remain.
    A changed name counts as remaining (uploaded != current), matching new_supplier_keys."""
    valid = {c for c, s in assigns.items() if (c or "").strip() and (s or "").strip()}
    total = len(valid)
    up = sum(1 for c in valid if uploaded.get(c) == assigns.get(c))
    return {"total_assigned": total, "total_uploaded": up,
            "remaining": max(0, total - up), "review_url": PUBLIC_URL}


def _export_supplier_index() -> tuple[set, set, bool]:
    """ONE STREAMING pass over the catalog export for the supplier write-back (#272)
    → (codes whose product ALREADY carries its own `supplier`, EVERY code the export
    lists, whether the export had any content at all).

    The second and third values are what the write-back's fail-closed gate needs: an
    export that is missing/empty (no bytes at all) OR implausibly small (fewer than
    EXPORT_MIN_CODES codes — a broken feed) means "we cannot tell which suppliers are
    protected", which must BLOCK the write. The caller applies both, so a header-only
    export no longer passes on "it had bytes" alone (PR #276 review)."""
    csv.field_size_limit(10**9)
    own, codes, seen_content = set(), set(), False
    lines = _iter_export_lines()

    def _tap():
        nonlocal seen_content
        for line in lines:
            if line.strip():
                seen_content = True
            yield line

    for r in csv.DictReader(_tap(), delimiter=";"):
        code = (r.get("code") or "").strip()
        if not code:
            continue
        codes.add(code)
        if (r.get("supplier") or "").strip():
            own.add(code)
    return own, codes, seen_content


def _do_upload_suppliers(dry):
    """Core of the nightly supplier write-back — the SINGLE place the logic lives
    (NEkopíruj logiku). Reads the supplier assignments, builds code;pairCode;supplier
    for only the codes not yet uploaded (or whose name changed), runs the careful
    import, records what went up, and returns (result, status). Shared by the n8n
    HTTP endpoint (below) and the in-app „Párovania → eshop" automation (#109) — no
    auth / Flask request access here. Touches ONLY the `supplier` column —
    links/state/prices are left untouched. Codes whose product ALREADY has its own
    supplier in the current export are excluded, so a stale assignment never clobbers
    a real eshop supplier (BUG 1)."""
    assigns = _load_supplier_assign()
    # …and WHETHER that record really came off disk. The #215 removal below stands on
    # `c not in uploaded`, so a degraded read („{}") would condemn every assignment at
    # once (PR #295 review). Sending is unaffected — re-sending a value is idempotent.
    uploaded, uploaded_on_disk = _load_uploaded_suppliers_state()
    new_codes = import_builder.new_supplier_keys(assigns, uploaded)
    products = [{"code": c, "supplier": assigns[c]} for c in new_codes]
    if not new_codes:
        log.info("n8n suppliers: 0 new assignments")
        # `obsolete_removed` / `obsolete_held` are part of the shape on EVERY path, so a
        # caller never has to tell „nothing was removed" from „this build does not report
        # it" — nor „nothing was obsolete" from „we refused to judge"
        return {"ok": True, "count": 0, "products": [], "obsolete_removed": [],
                "obsolete_held": [],
                "missing_count": 0, "missing_in_eshop": [],
                **_supplier_summary(uploaded, assigns)}, 200

    # BUG 1 safety — FAIL CLOSED on an UNUSABLE export. The exclusion guard below needs
    # the catalog export to know which codes already carry their own eshop supplier.
    # With no usable export it cannot tell, so it would fall open (exclude nothing) and
    # a stale assignment could clobber a real supplier in the LIVE eshop. Refuse to
    # write anything: hold all new assignments (blocked), touch nothing. Holding is safe
    # and self-healing — the next run with a good export sends them; falling open is an
    # irreversible overwrite of live catalog data.
    #
    # „Unusable" is the SAME bar the (merely reporting) missing-code verdict below uses,
    # deliberately: an export with a handful of codes out of a ~14 000-code catalogue is
    # a broken feed (truncated download, a filter left on, a Shoptet-side subset), and it
    # yields a nearly empty `own_supplier` — i.e. it silently claims that almost NO code
    # is protected. Trusting the weaker "any bytes at all" signal for the DANGEROUS half
    # while demanding EXPORT_MIN_CODES for the cosmetic one was the PR #276 review's
    # IMPORTANT 1 (pinned: test_an_implausibly_small_export_blocks_the_supplier_write_back).
    # A PLAUSIBLE-but-partial export that merely lacks a given code is still fine — that
    # code is simply not excluded and gets written (unchanged behaviour, PR #213).
    #
    # FRESHNESS belongs to this SAME gate (PR #280 review, MUST FIX 1). It used to sit
    # further down, inside `export_trusted`, where it decided only whether `missing_codes`
    # was computed — never whether the upload ran. So a stale export SUPPRESSED THE HOLD
    # BUT NOT THE WRITE: measured on dev, an export 6 h + 1 s old sent the very
    # catalogue-absent code #275 exists to hold (['9/Z', '777', 'FOREST']), i.e. #275's
    # fix evaporated in any window where the hourly sync had been down before the 21:00
    # push. And the clobber guard above ran on the SAME stale `own_supplier` bytes, so an
    # assignment could overwrite a supplier a colleague had set in Shoptet meanwhile.
    # An old export is exactly as unusable as a small one, so it gets the same answer:
    # write nothing, hold everything. Suppressing only the hold was the fail-OPEN.
    #
    # Unknown age (`None` — the file cannot be stat'd, or a test patches the reader) never
    # blocks, per the documented contract: with no file at all the index above yields
    # nothing and `export_present` already refuses.
    own_supplier, export_codes, export_present = _export_supplier_index()
    min_codes = _export_min_codes()          # #277: ONE threshold, shared with the verdicts
    export_age = _export_age_s()
    export_stale = export_age is not None and export_age > EXPORT_MAX_AGE_S
    if not export_present or len(export_codes) < min_codes or export_stale:
        # WHY the upload was blocked, in machine-readable terms — the card used to read
        # „N zablokovaných (chýbajú kódy)" for every one of these, which names the wrong
        # cause (nothing is missing; the export is not believable). #277 widens the
        # blocking band from <1000 to <7033 codes, so the manager WILL meet this state.
        reason = ("stale" if export_stale
                  else "missing" if not export_present else "small")
        log.warning("n8n suppliers: export not believable (reason=%s, %d codes < %d, "
                    "age=%s h) — supplier upload BLOCKED (%d new assignments held, "
                    "no eshop write)", reason, len(export_codes), min_codes,
                    f"{export_age / 3600:.1f}" if export_age is not None else "?",
                    len(new_codes))
        return {"ok": True, "count": 0, "products": products,
                "message": "export unavailable — upload blocked (fail-closed)",
                "blocked": len(new_codes),
                "obsolete_removed": [], "obsolete_held": [],
                "gate_blocked": {"reason": reason, "codes": len(export_codes),
                                 "min_codes": min_codes,
                                 "age_h": (round(export_age / 3600, 1)
                                           if export_age is not None else None),
                                 "max_age_h": round(EXPORT_MAX_AGE_S / 3600, 1)},
                "missing_count": 0, "missing_in_eshop": [],
                **_supplier_summary(uploaded, assigns)}, 200

    # #215 — CLEAN UP the assignments the eshop has OVERTAKEN.
    #
    # BUG 1 excludes a code whose product already carries its own supplier, and that is
    # right: a per-product assignment exists to FILL IN a missing supplier, so once the
    # eshop has one the assignment is by definition out of date. But excluding it was all
    # that ever happened to it — it was never written, therefore never recorded as
    # uploaded, therefore came back as „new" on every single nightly run (a warning every
    # night, for ever), and it would FIRE the day the manager deliberately CLEARED that
    # supplier in the eshop, because nothing here can tell „never had one" from „just
    # deleted it". So it is removed.
    #
    # It removes the MANAGER'S OWN INPUT, so it obeys the same discipline as the flag prune
    # (`.claude/rules/store-prune.md`):
    #   * POSITIVE evidence on both axes — the code IS in the export (presence) AND the
    #     export shows it carrying its own supplier (state). „I do not see a supplier" is
    #     never evidence of anything;
    #   * FAIL-CLOSED on the source — reaching this line already proves the export is
    #     present, plausible (>= `_export_min_codes()`) and FRESH, because the gate above
    #     returns otherwise. There is deliberately no second, weaker check here;
    #   * only for codes WE NEVER WROTE BACK (`c not in uploaded`). This one is not
    #     belt-and-braces, it is the whole correctness of the rule: `new_supplier_keys`
    #     also returns a code whose name the manager has just EDITED, and for that code the
    #     export legitimately shows „its own supplier" — the OLD value WE put there. The
    #     export index keeps only the code, not the value, so „it has a supplier" cannot
    #     tell the two apart. #215 is about an assignment that was NEVER written back
    #     (blocked from the first run and every run after); removing an edited one would
    #     delete the manager's correction overnight and leave the old name live in the shop;
    #   * …and therefore ONLY on a record we REALLY READ (`uploaded_on_disk`, PR #295
    #     review). `_read_json_store` answers `{}` for a missing, corrupt or wrong-type
    #     file exactly as it does for an empty one, and `{}` makes „never written back"
    #     true of EVERY code — the whole store condemned in one run, on no evidence at
    #     all. `uploaded_suppliers.json` does not exist on the live box today, so this is
    #     one export appearance away, not a thought experiment. Absence is not evidence
    #     (store-prune §1), so without the record nothing is judged: the candidates are
    #     reported as `obsolete_held` and reconsidered on the next run that can read it;
    #   * NEVER `missing_codes` (#275): „the catalogue does not carry this code" is a
    #     different hold with a different fate — it is self-healing and the code may appear
    #     tomorrow, so that assignment must still be there when it does;
    #   * never on a DRY run, in-place `pop` under one `with _lock:`, no write at all when
    #     there is nothing to remove, and the concrete codes AND the values dropped go into
    #     the log — a count alone defends nothing three weeks later.
    obsolete = sorted(set(new_codes) & own_supplier & export_codes - set(uploaded))
    obsolete_removed, obsolete_held = [], []
    if obsolete and not uploaded_on_disk:
        # Fail-CLOSED and LOUD (automation-health §3): a permanent hold that nobody can
        # see is the silent death this rule exists to prevent, and the fix is a one-line
        # one — the file has to be there before we may delete on its silence.
        obsolete_held, obsolete = obsolete, []
        log.error("n8n suppliers: %d priradení vyzerá neaktuálne, ale evidenciu "
                  "nahratých dodávateľov (%s) sa nepodarilo prečítať — NEMAŽE SA NIČ, "
                  "lebo bez nej sa „nikdy sme to nenahrali\" nedá odlíšiť od „manažér "
                  "práve zmenil meno dodávateľa\": %s",
                  len(obsolete_held), SUPPLIERS_STATE, ", ".join(obsolete_held[:10]))
    if obsolete and not dry:
        try:
            with _lock:
                live = _load_supplier_assign()
                dropped = {c: live[c] for c in obsolete if c in live}
                if dropped:
                    for c in dropped:
                        live.pop(c, None)
                    _save_supplier_assign(live)
            # report what was ACTUALLY dropped, not what we set out to drop: a code that
            # vanished meanwhile must not be named as removed by us
            obsolete_removed = sorted(dropped)
            if dropped:
                log.info("n8n suppliers: %d priradení zmazaných ako neaktuálne — eshop už "
                         "pri tých kódoch má vlastného dodávateľa: %s", len(dropped),
                         ", ".join(f"{c}={v}" for c, v in sorted(dropped.items())))
        except (StoreLockTimeout, StoreWipeRefused, OSError, ValueError) as e:  # noqa: BLE001
            # HOUSEKEEPING, so it is wrapped like housekeeping (store-prune §3): a
            # concurrent click invalidates the read receipt and `_save_supplier_assign`
            # raises — unwrapped, that would abort the whole nightly write-back before a
            # single import row is built, and the manager's assignments would stop going up
            # entirely. Nothing was removed; the next run tries again.
            log.error("n8n suppliers: upratanie neaktuálnych priradení zlyhalo (%r) — "
                      "nemaže sa nič, beh pokračuje", e)
            obsolete_removed = []
    elif obsolete:
        log.info("n8n suppliers: %d priradení je neaktuálnych (eshop má vlastného "
                 "dodávateľa), suchý beh ich nemaže: %s", len(obsolete),
                 ", ".join(obsolete))
    if obsolete_removed:
        # A removed assignment is no longer one: it must leave THIS run's view of the work
        # too, or the summary would go on counting it as waiting to go up and the row
        # builder below would look up a key that is no longer in the store.
        assigns = _load_supplier_assign()
        gone = set(obsolete_removed)
        new_codes = [c for c in new_codes if c not in gone]
        products = [p for p in products if p["code"] not in gone]

    # #270/#275: the same "the eshop has no such code" rows that doom the pairings push
    # also sit here (145/3XL is both an inline pairing and a supplier assignment). Such
    # a row can NEVER import — Shoptet rejects it on every single run, so it is never
    # recorded in uploaded_suppliers.json, stays „new" for ever and turns the whole
    # nightly run red every night. It is therefore HELD BACK here exactly as the
    # pairings half has held it since #270, and reported by name with the value we
    # wanted to write.
    #
    # This does NOT reverse PR #213: that decision was that a present-but-partial export
    # must never DROP a legitimate fill-in assignment, and holding is not dropping. The
    # assignment stays in supplier_assignments.json, is never credited as uploaded, and
    # goes up on the first run after the code appears in the catalogue — bounded and
    # self-healing, while sending it costs a red run every night for ever.
    #
    # Withholding a row is a WRITE condition, so it may only stand on bytes we believe:
    # the same gates as the pairings verdict. BOTH of those gates — the #277 ratio floor
    # AND freshness — now stand together above and block the whole upload, so reaching
    # this line already PROVES the export is believable. The hold is therefore
    # unconditional: there is no longer an „untrusted" branch that would suppress it while
    # letting the write through (the fail-open the PR #280 review found).
    missing_codes = sorted(c for c in new_codes if c not in export_codes)
    if missing_codes:
        log.warning("n8n suppliers: %d kódov eshop v katalógu vôbec nemá — "
                    "zadržiavam ich (Shoptet by ich zakaždým odmietol): %s",
                    len(missing_codes), missing_codes[:10])

    # Two exclusions, one pass:
    #   own_supplier   BUG 1 — never overwrite a supplier the eshop already has
    #   missing_codes  #275  — never send a code the catalogue does not carry
    rows = import_builder.supplier_rows(
        {c: assigns[c] for c in new_codes}, CODE2PAIR,
        exclude_codes=own_supplier | set(missing_codes))
    if not rows:
        log.warning("n8n suppliers: %d new codes but 0 import rows "
                    "(%d already have their own eshop supplier, %d held as absent "
                    "from the catalogue)",
                    len(new_codes), len(own_supplier & set(new_codes)), len(missing_codes))
        return {"ok": True, "count": 0, "products": products,
                "message": "no import rows", "blocked": len(new_codes),
                "obsolete_removed": obsolete_removed, "obsolete_held": obsolete_held,
                **_missing_report(missing_codes, assigns),
                **_supplier_summary(uploaded, assigns)}, 200

    # supplier_rows is 1:1 with codes (no product→variant indirection), but codes with
    # their own eshop supplier — and, since #275, codes the catalogue does not carry —
    # are excluded, so written_codes ⊆ new_codes. A held code therefore never enters
    # `success` and so is never recorded uploaded: it is simply retried next run.
    written_codes = {r[0] for r in rows}

    if not _import_lock.acquire(blocking=False):
        log.warning("n8n suppliers: another import already running")
        # this return sits AFTER the cleanup above, so it is the one path where a removal
        # may already have happened — it has to say so like every other
        return {"ok": False, "error": "import already running",
                "obsolete_removed": obsolete_removed,
                "obsolete_held": obsolete_held}, 409
    log.info("n8n suppliers: %d codes, %d rows (chunks of %d), dry_run=%s",
             len(new_codes), len(rows), IMPORT_CHUNK_ROWS, dry)
    try:
        # #156: chunked import (formula-injection guard applied per cell — supplier
        # name is free text). FIRST failing chunk stops the batch; success_codes are
        # the codes imported by a successful chunk (partial progress → resumable).
        res = _import_rows_chunked(rows, import_builder.SUPPLIER_HEADER, dry,
                                   prefix="import_suppliers_", csv_safe=True, timeout=900)
        ok = res["ok"]
        success = res["success_codes"]
        uploaded_codes = [c for c in new_codes if c in written_codes and c in success]
        if not dry:                          # record only codes that imported OK
            uploaded = _record_uploaded(
                _load_uploaded_suppliers, _save_uploaded_suppliers,
                {c: (assigns[c] or "").strip() for c in uploaded_codes})
    finally:
        _import_lock.release()

    err_msg = ""
    if not ok:
        err_msg = _chunk_error_msg(res, len(rows))
    result = {"ok": ok, "exit_code": res["rc"], "count": len(uploaded_codes),
              "rows": len(rows), "dry_run": dry, "processed": res["processed"],
              "updated": res["updated"], "failed": res["failed"],
              "error_detail": res["error_detail"], "error": err_msg,
              "chunks_total": res["chunks_total"], "chunks_ok": res["chunks_ok"],
              "products": products, "stdout_tail": res["stdout_tail"],
              "obsolete_removed": obsolete_removed, "obsolete_held": obsolete_held,
              **_missing_report(missing_codes, assigns),
              **_supplier_summary(uploaded, assigns)}
    log.info("n8n suppliers: rc=%s chunks=%d/%d processed=%s codes=%d",
             res["rc"], res["chunks_ok"], res["chunks_total"], res["processed"], len(uploaded_codes))
    if not ok:
        log.error("n8n suppliers FAILED rc=%s chunks_ok=%d/%d stderr=%s",
                  res["rc"], res["chunks_ok"], res["chunks_total"], (res["err"] or "")[-400:])
    return result, (200 if ok else 502)


@app.route("/api/n8n/upload-suppliers", methods=["POST"])
def n8n_upload_suppliers():
    """n8n's nightly caller: Bearer-auth then delegate to _do_upload_suppliers.
    dry_run=1 reaches the import without changing anything."""
    token = _import_token()
    auth = request.headers.get("Authorization", "")
    expected = f"Bearer {token}".encode() if token else b""
    if not token or not hmac.compare_digest(auth.encode("latin-1", "ignore"), expected):
        log.warning("n8n suppliers: unauthorized call from %s", _client_ip())
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    dry = str(request.values.get("dry_run", "")).lower() in ("1", "true", "yes")
    result, status = _do_upload_suppliers(dry)
    return jsonify(result), status


# --------------------------------------------------------------------------- #
# n8n → nightly upload of GRUBE per-size externalCodes (→ eshop `externalCode`
# field). #62 — the nightly cron follow-up to the MVP manual zip. Mirrors the
# supplier write-back exactly (own incremental store, own header, chunked import).
# --------------------------------------------------------------------------- #
EXTERNALCODES_STATE = _store("uploaded_externalcodes.json")


def _load_uploaded_externalcodes():
    """{code: itemId} already written back to the eshop — so the nightly job only
    sends new or changed itemIds. Missing/corrupt → empty (everything is new).
    Always a dict (a stray array could repeat a code and break the summary invariant)."""
    return _read_json_store(EXTERNALCODES_STATE, {})


def _save_uploaded_externalcodes(d):
    _atomic_write_json(EXTERNALCODES_STATE, d, protect=True)


def _externalcode_summary(uploaded, grube_codes):
    """Totals for the tab/notification: GRUBE codes with a valid (numeric) itemId,
    how many are already written back (uploaded value still matches the current
    itemId), how many remain. A changed itemId counts as remaining (uploaded !=
    current), matching new_externalcode_keys. Only numeric-itemId codes count — a
    non-numeric one is never uploadable, so it must not inflate the total."""
    valid = {c: str(i.get("itemId", "")).strip() for c, i in grube_codes.items()
             if str(i.get("itemId", "")).strip().isdigit()}
    total = len(valid)
    up = sum(1 for c, iid in valid.items() if uploaded.get(c) == iid)
    return {"total_codes": total, "total_uploaded": up,
            "remaining": max(0, total - up), "review_url": PUBLIC_URL}


def _do_upload_externalcodes(dry):
    """Core of the nightly GRUBE externalCode write-back — the SINGLE place the logic
    lives (NEkopíruj logiku). Reads the durable grube_codes.json store, builds
    code;pairCode;externalCode for only the codes not yet uploaded (or whose itemId
    changed), runs the careful chunked import, records what went up, and returns
    (result, status). Shared by the n8n HTTP endpoint (below) and the in-app „GRUBE
    kódy → eshop" automation (#62) — no auth / Flask request access here. Touches ONLY
    the `externalCode` column (own file → a present-but-empty cell can't wipe
    internalNote/state/prices). The externalCode is the grube per-size `itemId`, which
    MUST be purely numeric — the numeric guard lives in both new_externalcode_keys and
    externalcode_rows (a non-numeric cell is junk / a possible formula-injection lead,
    dropped, never an empty cell that would WIPE the existing externalCode). GRUBE-only
    is guaranteed by the source store (grube_codes.json is built only for GRUBE)."""
    grube = _load_grube_codes()
    uploaded = _load_uploaded_externalcodes()
    new_codes = import_builder.new_externalcode_keys(grube, uploaded)
    products = [{"code": c, "externalCode": str(grube[c].get("itemId", "")).strip()}
                for c in new_codes]
    if not new_codes:
        log.info("n8n externalcode: 0 new codes")
        return {"ok": True, "count": 0, "products": [],
                **_externalcode_summary(uploaded, grube)}, 200

    rows = import_builder.externalcode_rows({c: grube[c] for c in new_codes}, CODE2PAIR)
    if not rows:
        log.warning("n8n externalcode: %d new codes but 0 import rows", len(new_codes))
        return {"ok": True, "count": 0, "products": products,
                "message": "no import rows", "blocked": len(new_codes),
                **_externalcode_summary(uploaded, grube)}, 200

    # externalcode_rows is 1:1 with codes (no product→variant indirection) and applies
    # the same numeric guard, so every new code that survived new_externalcode_keys has
    # exactly one row — written_codes == set(new_codes).
    written_codes = {r[0] for r in rows}

    if not _import_lock.acquire(blocking=False):
        log.warning("n8n externalcode: another import already running")
        return {"ok": False, "error": "import already running"}, 409
    log.info("n8n externalcode: %d codes, %d rows (chunks of %d), dry_run=%s",
             len(new_codes), len(rows), IMPORT_CHUNK_ROWS, dry)
    try:
        # #156: chunked import (formula-injection guard applied per cell — belt-and-
        # braces alongside the numeric itemId guard). FIRST failing chunk stops the
        # batch; success_codes are the codes imported by a successful chunk (partial
        # progress → resumable).
        res = _import_rows_chunked(rows, import_builder.EXTERNALCODE_HEADER, dry,
                                   prefix="import_externalcode_", csv_safe=True, timeout=900)
        ok = res["ok"]
        success = res["success_codes"]
        uploaded_codes = [c for c in new_codes if c in written_codes and c in success]
        if not dry:                          # record only codes that imported OK
            uploaded = _record_uploaded(
                _load_uploaded_externalcodes, _save_uploaded_externalcodes,
                {c: str(grube[c].get("itemId", "")).strip() for c in uploaded_codes})
    finally:
        _import_lock.release()

    err_msg = ""
    if not ok:
        err_msg = _chunk_error_msg(res, len(rows))
    result = {"ok": ok, "exit_code": res["rc"], "count": len(uploaded_codes),
              "rows": len(rows), "dry_run": dry, "processed": res["processed"],
              "updated": res["updated"], "failed": res["failed"],
              "error_detail": res["error_detail"], "error": err_msg,
              "chunks_total": res["chunks_total"], "chunks_ok": res["chunks_ok"],
              "products": products, "stdout_tail": res["stdout_tail"],
              **_externalcode_summary(uploaded, grube)}
    log.info("n8n externalcode: rc=%s chunks=%d/%d processed=%s codes=%d",
             res["rc"], res["chunks_ok"], res["chunks_total"], res["processed"], len(uploaded_codes))
    if not ok:
        log.error("n8n externalcode FAILED rc=%s chunks_ok=%d/%d stderr=%s",
                  res["rc"], res["chunks_ok"], res["chunks_total"], (res["err"] or "")[-400:])
    return result, (200 if ok else 502)


@app.route("/api/n8n/upload-externalcode", methods=["POST"])
def n8n_upload_externalcode():
    """n8n's nightly caller: Bearer-auth then delegate to _do_upload_externalcodes.
    dry_run=1 reaches the import without changing anything."""
    token = _import_token()
    auth = request.headers.get("Authorization", "")
    expected = f"Bearer {token}".encode() if token else b""
    if not token or not hmac.compare_digest(auth.encode("latin-1", "ignore"), expected):
        log.warning("n8n externalcode: unauthorized call from %s", _client_ip())
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    dry = str(request.values.get("dry_run", "")).lower() in ("1", "true", "yes")
    result, status = _do_upload_externalcodes(dry)
    return jsonify(result), status


# --------------------------------------------------------------------------- #
# n8n → nightly upload of per-size SPLIT links (→ eshop `internalNote` field, per
# variant). #192 — the nightly cron follow-up to the MVP manual zip for the #174
# „✂ Rozdeliť na veľkosti" per-size supplier links. DISTINCT write path from the
# pairings push: a `split` decision carries NO decision URL (its links live in
# variant_links.json, keyed per variant code), so the nightly pairings job never
# picks it up. Mirrors the supplier/externalcode write-back (own incremental store,
# reuses import_builder.link_rows for the rows — no duplicated logic).
# --------------------------------------------------------------------------- #
VARIANT_LINKS_STATE = _store("uploaded_variant_links.json")


def _load_uploaded_variant_links():
    """{variant_code: url} already written back to the eshop — so the nightly job only
    sends new or changed split links. Missing/corrupt → empty (everything is new).
    Always a dict (a stray array could repeat a code and break the summary invariant)."""
    return _read_json_store(VARIANT_LINKS_STATE, {})


def _save_uploaded_variant_links(d):
    _atomic_write_json(VARIANT_LINKS_STATE, d, protect=True)


def _variant_link_summary(uploaded, vlinks, split_codes):
    """Totals for the tab/notification: split-linked variant codes with a valid
    http(s) URL, how many are already written back (uploaded url still matches the
    current one), how many remain. A changed URL counts as remaining (uploaded !=
    current), matching new_variant_link_keys. Only split + http(s) codes count — a
    stale link on a no-longer-split product, or a non-http URL, is never uploadable so
    it must not inflate the total."""
    valid = {c: (u or "").strip() for c, u in vlinks.items()
             if c in split_codes and (u or "").strip().startswith(("http://", "https://"))}
    total = len(valid)
    up = sum(1 for c, u in valid.items() if uploaded.get(c) == u)
    return {"total_codes": total, "total_uploaded": up,
            "remaining": max(0, total - up), "review_url": PUBLIC_URL}


def _do_upload_variant_links(dry):
    """Core of the nightly per-size split-link write-back (#192) — the SINGLE place
    the logic lives (NEkopíruj logiku). Reads the durable variant_links.json store +
    the live split decisions, builds code;pairCode;internalNote rows for only the split
    variants not yet uploaded (or whose URL changed) via import_builder.link_rows (the
    SAME row builder the manual zip uses — GRUBE .de normalization + per-variant
    skip-empty included), runs the careful chunked import, records what went up, and
    returns (result, status). Shared by the n8n HTTP endpoint (below) and the in-app
    „Veľkostné linky → eshop" automation (#192) — no auth / Flask request access here.
    Touches ONLY the `internalNote` column (LINK_HEADER → a present-but-empty cell
    can't wipe state/prices; and a variant with no stored link is skipped by link_rows,
    never an empty cell that would WIPE the existing link). A non-http(s) URL is dropped
    in new_variant_link_keys (fail-safe — never reaches the live eshop). Only `split`
    decisions are passed to link_rows, so good/manual links (already pushed by
    „Párovania → eshop") are never re-written here."""
    vlinks = _load_variant_links()
    uploaded = _load_uploaded_variant_links()
    dec = _load_decisions()
    by_key = {p.get("key"): p for p in PRODUCTS}
    split_dec = {k: d for k, d in dec.items() if d.get("status") == "split"}
    split_codes = {c for k in split_dec
                   for c in (by_key.get(k, {}).get("variant_codes") or [])}
    new_codes = import_builder.new_variant_link_keys(vlinks, split_codes, uploaded)
    products = [{"code": c, "url": (vlinks.get(c) or "").strip()} for c in new_codes]
    if not new_codes:
        log.info("n8n variant-links: 0 new split links")
        return {"ok": True, "count": 0, "products": [],
                **_variant_link_summary(uploaded, vlinks, split_codes)}, 200

    # Build rows via link_rows (the manual-zip builder). Passing ONLY split_dec keeps
    # good/manual links out (they go via _do_upload_pairings); passing ONLY the new
    # codes' variant_links keeps THIS run incremental (link_rows skips a variant with no
    # link, so an unchanged/old code produces no row).
    rows = import_builder.link_rows(PRODUCTS, split_dec, CODE2PAIR,
                                    {c: vlinks[c] for c in new_codes})
    if not rows:
        log.warning("n8n variant-links: %d new codes but 0 import rows", len(new_codes))
        return {"ok": True, "count": 0, "products": products,
                "message": "no import rows", "blocked": len(new_codes),
                **_variant_link_summary(uploaded, vlinks, split_codes)}, 200

    # A new code can miss a row: link_rows dedups per variant code (first-wins across
    # duplicate-catalog products), so a code shared with an earlier split product is a
    # "seen"-loser → blocked (surfaced, not silently dropped #49). uploadable == codes
    # that actually got a row.
    written_codes = {r[0] for r in rows}
    blocked = [c for c in new_codes if c not in written_codes]
    if blocked:
        log.warning("n8n variant-links: %d of %d codes generated no row (deduped): %s",
                    len(blocked), len(new_codes), blocked[:10])

    if not _import_lock.acquire(blocking=False):
        log.warning("n8n variant-links: another import already running")
        return {"ok": False, "error": "import already running"}, 409
    log.info("n8n variant-links: %d codes, %d rows (chunks of %d), dry_run=%s",
             len(new_codes), len(rows), IMPORT_CHUNK_ROWS, dry)
    try:
        # #156: chunked import. csv_safe per cell (belt-and-braces — the URL is already
        # http(s)-guarded so the '-prefix never actually fires, but the nightly sink
        # must not be weaker than the manual zip). FIRST failing chunk stops the batch;
        # success_codes are the codes imported by a successful chunk (partial → resumable).
        res = _import_rows_chunked(rows, import_builder.LINK_HEADER, dry,
                                   prefix="import_variant_links_", csv_safe=True, timeout=900)
        ok = res["ok"]
        success = res["success_codes"]
        uploaded_codes = [c for c in new_codes if c in written_codes and c in success]
        if not dry:                          # record only codes that imported OK
            uploaded = _record_uploaded(
                _load_uploaded_variant_links, _save_uploaded_variant_links,
                {c: (vlinks[c] or "").strip() for c in uploaded_codes})
    finally:
        _import_lock.release()

    err_msg = ""
    if not ok:
        err_msg = _chunk_error_msg(res, len(rows))
    result = {"ok": ok, "exit_code": res["rc"], "count": len(uploaded_codes),
              "rows": len(rows), "dry_run": dry, "processed": res["processed"],
              "updated": res["updated"], "failed": res["failed"],
              "error_detail": res["error_detail"], "error": err_msg,
              "chunks_total": res["chunks_total"], "chunks_ok": res["chunks_ok"],
              "products": products, "stdout_tail": res["stdout_tail"],
              "blocked": len(blocked),
              **_variant_link_summary(uploaded, vlinks, split_codes)}
    log.info("n8n variant-links: rc=%s chunks=%d/%d processed=%s codes=%d",
             res["rc"], res["chunks_ok"], res["chunks_total"], res["processed"], len(uploaded_codes))
    if not ok:
        log.error("n8n variant-links FAILED rc=%s chunks_ok=%d/%d stderr=%s",
                  res["rc"], res["chunks_ok"], res["chunks_total"], (res["err"] or "")[-400:])
    return result, (200 if ok else 502)


@app.route("/api/n8n/upload-variant-links", methods=["POST"])
def n8n_upload_variant_links():
    """n8n's nightly caller: Bearer-auth then delegate to _do_upload_variant_links.
    dry_run=1 reaches the import without changing anything."""
    token = _import_token()
    auth = request.headers.get("Authorization", "")
    expected = f"Bearer {token}".encode() if token else b""
    if not token or not hmac.compare_digest(auth.encode("latin-1", "ignore"), expected):
        log.warning("n8n variant-links: unauthorized call from %s", _client_ip())
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    dry = str(request.values.get("dry_run", "")).lower() in ("1", "true", "yes")
    result, status = _do_upload_variant_links(dry)
    return jsonify(result), status


# --------------------------------------------------------------------------- #
# In-app automations (#93): generic runner + the Pošta SK uncollected-shipments
# automation. New automations (#105-#111) register themselves in AUTOMATIONS_REG
# below — the runner, endpoints and sidebar section are shared.
# --------------------------------------------------------------------------- #
AUTOMATIONS_STATE = _store("automations.json")
POSTA_STATE = _store("posta_uncollected.json")
# How long a cached TERMINAL tracking verdict is trusted before one re-verification (#222).
POSTA_TERMINAL_RECHECK_DAYS = 7
ORDERS_REMINDER_STATE = _store("orders_reminder.json")   # #105 dedup + display


class DedupStoreCorrupt(RuntimeError):
    """An unreadable DEDUP/ESCALATION store — the two customer-mail automations refuse to run.

    Every other store in this app uses the „SAFE loader" pattern (unparseable → `{}`) because
    losing a display flag is cosmetic. These two are different in kind: the file IS the proof of
    which customers have already been mailed. Degrade a partial write to `{}` and the very next
    `_claim` / `_persist_done` / escalation bump persists a brand-new ONE-entry map — the whole
    dedup history is gone, so every open order gets a SECOND reminder and every parcel at the post
    office a duplicate escalation (#225). So they fail CLOSED: the corrupt bytes are copied aside,
    the run aborts having mailed nobody, and a human repairs the file from that copy. Rather not
    send than send twice. A MISSING file is NOT corruption — it is a legitimate first run (nothing
    was ever sent, so there is nothing to lose) and must never be blocked.
    """


# Quarantine is idempotent per (path, content) for the LIFETIME OF THE PROCESS: a corrupt store is
# read on every display request too (the tab polls while a run is in flight), and re-scanning +
# re-reading every existing backup on each of those reads is pure waste. The digest memo also makes
# the scan-then-create sequence safe under the app's threads — it runs under its OWN small lock,
# never the global `_lock` (whose holder is frequently the caller of this helper).
_quarantine_lock = threading.Lock()
_quarantined: dict = {}                  # path -> (sha256 of the corrupt bytes, backup path)


def _quarantine_corrupt_store(path: str) -> str:
    """Preserve an unreadable store as `<path>.corrupt-<ts>` and return that path (or "").

    The ORIGINAL is deliberately left in place: moving it away would make the very next load see
    „no file" = a legitimate first run, and the automation would happily start a fresh EMPTY dedup
    store — exactly the silent wipe this whole guard exists to prevent. The file therefore keeps
    failing loudly until a human fixes it.

    Backups are de-duplicated by CONTENT, so a daily automation hitting the same corrupt file does
    not leave one copy per run; a DIFFERENT corruption later still gets its own copy.
    """
    # Resolve FIRST: the memo below is keyed by path, and a `_StorePath` hashes from the CURRENT
    # OUT — keyed by the object, every repointed data dir would leave an entry nobody can look
    # up again (PR #265 review). Callers pass either form.
    path = os.fspath(path)
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:  # noqa: BLE001 — the caller is already failing closed; best effort only
        log.error("dedup store %s sa nepodarilo zazálohovať (%r)", path, e)
        return ""
    digest = hashlib.sha256(raw).hexdigest()
    with _quarantine_lock:
        seen = _quarantined.get(path)
        if seen and seen[0] == digest:
            return seen[1]                             # these exact bytes are already preserved
        folder = os.path.dirname(path) or "."
        prefix = os.path.basename(path) + ".corrupt-"
        try:
            for name in sorted(os.listdir(folder)):
                if not name.startswith(prefix):
                    continue
                with open(os.path.join(folder, name), "rb") as f:
                    if f.read() == raw:                # …or were preserved by an earlier process
                        _quarantined[path] = (digest, os.path.join(folder, name))
                        return os.path.join(folder, name)
        except OSError as e:  # noqa: BLE001 — an unreadable folder must not hide the real error
            log.warning("dedup store %s: existujúce zálohy sa nepodarilo prezrieť (%r)", path, e)
        dest = f"{path}.corrupt-{datetime.now().strftime('%Y%m%dT%H%M%S%f')}"
        try:
            fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(raw)
        except OSError as e:  # noqa: BLE001 — full disk / permissions; the original still stands
            log.error("dedup store %s: zálohu %s sa nepodarilo zapísať (%r)", path, dest, e)
            return ""
        _quarantined[path] = (digest, dest)
    return dest


def _load_dedup_store(path: str, label: str, dedup_keys: tuple = ()) -> dict:
    """Strict loader for a dedup/escalation store — see DedupStoreCorrupt.

    Missing file → `{}` (first run). Unparseable, not a dict, or carrying a `dedup_keys` entry
    that is present but is NOT a dict → a copy is preserved and DedupStoreCorrupt is raised. Any
    other OSError (permissions…) propagates, which is fail-closed too.

    `dedup_keys` names the maps whose loss means a DUPLICATE CUSTOMER MAIL — `orders` here,
    `escalation` there. Validating only the outer dict was not enough: `{"orders": null}` parses
    AND is a dict, so the run read „nobody was ever mailed", persisted that, and re-mailed
    everyone on the next pass (PR #228 review — the very wipe this guard exists to stop, one
    level down). A key that is simply ABSENT stays fine: that is a first run.

    Deliberately NOT listed: `posta_uncollected.json`'s `terminal` cache. It only ever saves an
    API call, so losing it cannot duplicate a mail — while failing the run on it WOULD silence a
    genuine customer notification. Fail-closed applies to the record of what was SENT, nothing
    else.
    """
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except FileNotFoundError:
        return {}
    except ValueError as e:
        # ValueError, not JSONDecodeError: these stores are written with ensure_ascii=False and
        # are full of Slovak text, so a write cut mid-character raises UnicodeDecodeError — the
        # single most likely real truncation. It used to escape the loader entirely (no
        # quarantine copy, a raw traceback in last_error, a 500 on the tab).
        raise DedupStoreCorrupt(_corrupt_msg(path, label)) from e
    if not isinstance(d, dict):
        # parses, but is not a store — the old `else {}` made this the second silent-wipe path
        raise DedupStoreCorrupt(_corrupt_msg(path, label))
    for key in dedup_keys:
        if key in d and not isinstance(d[key], dict):
            raise DedupStoreCorrupt(_corrupt_msg(path, label))
    _note_store_read(path, d)   # #261 — these stores are write-guarded like the rest
    return d


def _corrupt_msg(path: str, label: str) -> str:
    backup = _quarantine_corrupt_store(path)
    log.error("orders/posta: POŠKODENÁ evidencia %s (%s) — automatizácia neposiela nič, "
              "aby zákazníci nedostali duplicitné maily; kópia: %s", label, path, backup or "-")
    return (f"Poškodená evidencia {label} (data/out/{os.path.basename(path)}) — "
            "neposielam žiadne e-maily, hrozili by duplicitné maily zákazníkom. "
            + (f"Oprav súbor podľa zálohy {os.path.basename(backup)}. " if backup
               else "Oprav súbor. ")
            # NEVER suggest deleting it: a missing file is a legitimate first run, so deleting is
            # exactly the wipe this guard prevents — every customer would be mailed again.
            + "POZOR: NEMAŽ ho — prázdna evidencia znamená, že každý zákazník dostane mail znova.")


@app.errorhandler(StoreWipeRefused)
@app.errorhandler(StoreLockTimeout)
def _handle_store_write_refused(e):
    """A refused write / a store lock we could not take answers 503 with a Slovak
    „what to do", never a bare 500 — a 500 reads as a transient glitch and invites the
    manager to click again, which is exactly the wrong reaction to either of these."""
    return jsonify({"ok": False, "error": str(e)}), 503


@app.errorhandler(AutomationStateCorrupt)
def _handle_automation_state_corrupt(e: AutomationStateCorrupt):
    """An unreadable automations.json fails CLOSED (#265 second review, C4) — it must
    not pretend no automations are set up. The tab therefore answers 503 with what to
    repair, exactly like a refused store write, instead of a 500 that reads transient."""
    return jsonify({"ok": False, "error": str(e)}), 503


@app.errorhandler(DedupStoreCorrupt)
def _handle_dedup_store_corrupt(e: DedupStoreCorrupt):
    """Any endpoint that touches a dedup store fails closed with a 503 that says what to FIX —
    a bare 500 would look like a transient glitch and invite the manager to keep clicking.
    Registered app-wide on purpose: a future endpoint reaching one of these stores inherits it."""
    return jsonify({"ok": False, "error": str(e)}), 503


def _load_posta_state() -> dict:
    """Fail-CLOSED (#225): a corrupt escalation store aborts the run instead of restarting every
    escalation at count 0. Display-only callers use _load_posta_state_display()."""
    return _load_dedup_store(POSTA_STATE, "nevyzdvihnutých zásielok", ("escalation",))


def _load_posta_state_display() -> tuple:
    """Read-only DISPLAY variant → `(state, corrupt)`. A corrupt store must never 500 the tab —
    but it must not render as a CLEAN, EMPTY one either: that is indistinguishable from a quiet
    day, so a corruption appearing between runs would stay invisible while the automation
    silently stops mailing. The flag is what the tab turns into a visible warning."""
    try:
        return _load_posta_state(), False
    except DedupStoreCorrupt:
        return {}, True


def _save_posta_state(d: dict) -> None:
    # ("escalation",) — the record of who was already e-mailed is that NESTED map; the
    # outer dict keeps the same key set on every write, so guarding only it is inert.
    _atomic_write_json(POSTA_STATE, d, mode=0o600, protect=("escalation",))


def _fetch_tracking(pkg: str) -> dict:
    """Pošta SK tracking for one package — 3 tries (n8n: retryOnFail maxTries=3,
    3s between), 60s timeout. Raises after the last failure so the run records
    the shipment under errors instead of silently skipping it."""
    url = posta_uncollected.TRACKING_API.format(q=quote(pkg))
    for attempt in range(1, 4):
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=60)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001 — retried; the last failure propagates
            log.warning("posta: tracking %s attempt %d/3 failed: %r", pkg, attempt, e)
            if attempt == 3:
                raise
            time.sleep(3)
    raise RuntimeError("unreachable")


def run_posta_uncollected() -> dict:
    """One check run (daily 09:00 or 'Spustiť teraz'): shipments from the app's
    orders export → Pošta SK tracking per shipment → escalation e-mails to
    customers per the n8n cadence → full display state for the tab persisted
    to data/out/posta_uncollected.json. Returns the summary the runner stores."""
    # The escalation store is read FIRST — before the orders export and long before the first
    # Pošta SK round-trip. It is a free local check that can disqualify the whole run (#225: a
    # corrupt store means we cannot know who was already notified, so nothing may be sent), and
    # the „lacné diskvalifikátory pred drahými volaniami" rule puts it ahead of the paid work.
    with _lock:
        st0 = _load_posta_state()
        esc = dict(st0.get("escalation") or {})
        # #222 — packageNumber -> {state, at} for shipments already in a FINAL tracking state.
        # Nothing about them can change, so they are skipped entirely on later runs instead of
        # costing another sequential API round-trip (up to 180 s each on a bad day).
        # …tolerated, unlike `escalation`: this cache only ever saves an API call, so garbage in
        # it can never duplicate a mail — it just costs a real check. `dict(...)` on a non-dict
        # would have raised and taken the whole run (and the customer's notification) with it.
        term_cache = dict(st0.get("terminal") or {}) if isinstance(
            st0.get("terminal"), dict) else {}
    csv_bytes = _orders_csv_cached()
    # ONE `today` for the whole run, passed to both readers of the export. They are documented as
    # counting the same set of orders, so they must not be able to straddle midnight between two
    # calls that each defaulted to date.today() on their own.
    today = datetime.now().date()
    today_iso = today.isoformat()
    # #296 — the two status names this automation used to carry as literals now come from the
    # manager's configuration, and „dispatched" is derived rather than given a box of its own.
    # …with the REASON (PR #298 review, A2): an unusable configuration makes every set below a
    # built-in default, and these mails go to real customers. Same answer the prune and the
    # reminders give — count, render, do NOT act (automation-health §3).
    cancelled_statuses, dispatched_statuses, status_reason = _posta_statuses()
    bad_status_config = bool(status_reason)
    if bad_status_config:
        log.error("posta: nastavenie stavov objednávok sa nedá použiť (%s) — NEPOSIELAM "
                  "žiadne upozornenia zákazníkom, lebo appka práve beží na PREDVOLENÝCH "
                  "názvoch stavov a nevie, ktoré objednávky sú zrušené; zásielky nechávam "
                  "vypísané na karte a čakám na opravu zoznamov na karte „Sync zo Shoptetu“",
                  status_reason)
    shipments = posta_uncollected.shipments_from_orders_csv(
        csv_bytes, today, cancelled_statuses=cancelled_statuses)
    uncollected, invalid, errors = [], [], []
    sent = failed = api_skipped = blocked = 0
    # Is the SOURCE still alive (#282)? `checked` above only ever counts orders that DID carry a
    # package number, so an export that stops carrying them reads as a calm day. This is the one
    # stat that can tell those two apart. Pure counting over the same export — no send, no API.
    coverage = posta_uncollected.source_coverage(
        csv_bytes, today, cancelled_statuses=cancelled_statuses,
        dispatched_statuses=dispatched_statuses)
    if coverage["dispatched_status_unknown"]:
        # The alarm's own blind spot, logged rather than assumed away: every count above hangs off
        # the dispatched status NAMES — configuration since #296, but a name can still be edited
        # WRONG. Orders in the window of which NOT ONE is recognised as dispatched means that
        # vocabulary moved — and this alarm would then sit green forever, exactly like the
        # automation it was built to watch.
        # `eligible_orders` and not `missing_package + dispatched_orders`: that sum degenerates
        # to „orders WITHOUT a number", and a window whose orders all carry one reported „v okne
        # je 0 objednávok, ale ANI JEDNA…" — self-contradictory, and wrong about the only number
        # the reader can act on. The recognised COUNT goes with it since PR #298's review: the
        # branch no longer implies zero (1-4 survivors fire it too).
        log.error("%s", _dispatched_status_blind_message(
            coverage["eligible_orders"], dispatched_statuses,
            coverage["dispatched_orders"]))
    if coverage["degraded"]:
        log.error("posta: ZDROJ ZÁSIELOK JE DEGRADOVANÝ — %d z %d odoslaných objednávok v okne "
                  "nemá podacie číslo, posledné pribudlo pred %s dňami; automatizácia z nich "
                  "nevidí takmer nič a nikoho neupozorní",
                  coverage["dispatched_without_package"], coverage["dispatched_orders"],
                  coverage["days_since_last_package"]
                  if coverage["days_since_last_package"] is not None else "30+")
    # A cached terminal verdict is trusted for this long, then re-verified once. Cheap insurance:
    # it still removes ~6 of every 7 calls for a delivered parcel, while bounding ANY wrong or
    # freak reading to a week instead of the full 30-day source window.
    recheck_before = (today - timedelta(days=POSTA_TERMINAL_RECHECK_DAYS)).isoformat()
    for s in shipments:
        cached = term_cache.get(s["packageNumber"])
        if (isinstance(cached, dict) and cached.get("state")
                and cached.get("code") == s["code"]
                and recheck_before <= str(cached.get("at") or "") <= today_iso):
            # Delivered (at home or collected at the post office) — final, so there is nothing
            # left to check or e-mail. Four ways this deliberately falls through to a REAL
            # check instead: a garbage entry (not a dict, no state); a package number that now
            # belongs to a DIFFERENT order — tracking numbers are typed into Shoptet by hand, so
            # a reused/mistyped one must not let a stale „delivered" silence a genuinely
            # uncollected parcel; an entry older than POSTA_TERMINAL_RECHECK_DAYS, so one
            # wrong or freak reading self-heals within a week instead of sticking for the whole
            # 30-day window; and — the reason `at` is bounded from BOTH sides — a value that is
            # not a real past date at all. `at` is compared as a plain string, so anything
            # sorting above the cutoff („zzz" from a partial write, a future date after a clock
            # jump) would otherwise stay „fresh" for good, switching the weekly re-check net off
            # without a trace. Corruption, ambiguity and age all degrade to „check it", never to
            # „ignore it forever" — a cached verdict may only ever save an API call, never
            # silence a customer notification.
            api_skipped += 1
            continue
        esc_val = esc.get(s["code"], "")
        if esc_val != "" and not isinstance(esc_val, str):
            # The escalation value („<count>|<date>") is what says how many warnings this
            # customer already got. `parse_notified` degrades ANY non-string to (0, None), so a
            # partial write here used to restart the cadence and re-send warning #1 to someone
            # who already had it (PR #228 review). We cannot prove what was sent → we send
            # nothing and surface the shipment, exactly as #225 decided for the whole store.
            # Checked BEFORE the tracking call: a free local disqualifier comes first.
            log.error("posta: obj. %s (%s) má POŠKODENÝ záznam o odoslaných upozorneniach "
                      "(%r) — NEposielam nič, aby zákazník nedostal duplicitné upozornenie",
                      s["code"], s["packageNumber"], esc_val)
            errors.append({"orderCode": s["code"], "packageNumber": s["packageNumber"],
                           "error": "poškodený záznam o odoslaných upozorneniach — "
                                    "neposielam, over ručne"})
            continue
        try:
            tj = _fetch_tracking(s["packageNumber"])
        except Exception as e:  # noqa: BLE001 — recorded per shipment, run continues
            log.error("posta: tracking %s (obj. %s) FAILED after retries: %r",
                      s["packageNumber"], s["code"], e)
            errors.append({"orderCode": s["code"],
                           "packageNumber": s["packageNumber"], "error": str(e)})
            continue
        final = posta_uncollected.terminal_state(tj)
        if final:
            # `code` is part of the entry so the skip above can prove the cached verdict really
            # belongs to THIS order, not to an earlier one that used the same tracking number.
            term_cache[s["packageNumber"]] = {"state": final, "at": today_iso, "code": s["code"]}
        else:
            # We just looked, and it is NOT final — so any cached verdict for this parcel has
            # been DISPROVED (a stale entry we re-checked, or one left by a mistyped number).
            # Drop it rather than leave a wrong „delivered" sitting in the store.
            term_cache.pop(s["packageNumber"], None)
        r = posta_uncollected.evaluate_shipment(s, tj, esc.get(s["code"], ""))
        if r["invalid"]:
            # The exact class of package numbers that silently broke the n8n
            # workflow (13-14 digit numeric labels) — surfaced, never skipped.
            log.warning("posta: INVALID_FORMAT balík %s (obj. %s) — Pošta SK ho "
                        "nevie sledovať, treba preveriť ručne", r["packageNumber"], r["orderCode"])
            invalid.append({k: r[k] for k in (
                "orderCode", "packageNumber", "name", "admin_link")})
            continue
        if r["send"]:
            if bad_status_config:
                # Fail-CLOSED, and placed BEFORE the other two disqualifiers so each of them
                # keeps reporting its own gap truthfully (automation-health §3). It is not
                # counted as `emails_failed`: nothing was attempted and nothing will be
                # retried until the configuration is repaired, and inflating a failure counter
                # would hide the real cause behind a number that looks like an SMTP problem.
                blocked += 1
                mail_ok = False
            elif not r["email"]:
                log.error("posta: obj. %s (%s) nemá e-mail — upozornenie nemožno poslať",
                          r["orderCode"], r["packageNumber"])
                mail_ok = False
            else:
                # bcc omitted -> _send_mail_html defaults it to MAIL_BCC (#126); require_bcc
                # makes that BINDING for a real customer mail — no owner copy, no send.
                mail_ok = _send_mail_html(r["email"], r["email_subject"], r["email_body"],
                                          require_bcc=True)
            if mail_ok:
                esc[r["orderCode"]] = r["new_state_value"]
                sent += 1
                log.info("posta: email #%d for obj. %s (%s) sent to %s",
                         r["count"], r["orderCode"], r["packageNumber"], r["email"])
                # persist the bump IMMEDIATELY — a crash later in the run must
                # never lose a sent-mail record (that would double-send tomorrow)
                try:
                    with _lock:
                        st = _load_posta_state()
                        st.setdefault("escalation", {})[r["orderCode"]] = r["new_state_value"]
                        _save_posta_state(st)
                except Exception as e:  # noqa: BLE001 — full disk / permissions
                    # The mail ALREADY went out. Surface the order code loudly for manual
                    # follow-up and keep going — aborting here would leave the remaining
                    # shipments unchecked. NOTE: do NOT `continue` — the shipment still has to
                    # reach the „Nevyzdvihnuté" tab below (it is exactly the row this log tells
                    # the manager to check), and the in-memory `esc` bump above is re-persisted
                    # by the run's final save, so the record is not lost by this failure alone.
                    log.error("posta: mail pre obj. %s (%s) ODIŠIEL, ale okamžitý zápis stavu "
                              "ZLYHAL (%r) — ak zlyhá aj finálny zápis, hrozí duplicitný mail "
                              "v ďalšom behu; skontroluj ručne",
                              r["orderCode"], r["packageNumber"], e)
            else:
                if not bad_status_config:
                    failed += 1      # state NOT bumped → retried next run
                prev_count, prev_last = posta_uncollected.parse_notified(
                    esc.get(r["orderCode"], ""))
                r["count"] = prev_count
                r["last_sent"] = prev_last.isoformat() if prev_last else ""
                r["call_needed"] = prev_count >= posta_uncollected.MAX_EMAILS
        if r["uncollected"]:
            uncollected.append({k: r[k] for k in (
                "orderCode", "packageNumber", "name", "phone", "email",
                "office_name", "office_addr", "retained_till", "notified_since",
                "days_at_post", "count", "last_sent", "call_needed",
                "tracking_link", "admin_link")})
    # prune escalation state for orders that left the 30-day source window
    codes = {s["code"] for s in shipments}
    esc = {k: v for k, v in esc.items() if k in codes}
    # …and the terminal-tracking cache the same way, by package number — that keeps it bounded
    # by the source window itself, so it can never grow the way the dedup store did (#220).
    pkgs = {s["packageNumber"] for s in shipments}
    term_cache = {k: v for k, v in term_cache.items() if k in pkgs}
    if api_skipped:
        log.info("posta: %d zásielok preskočených — tracking už hlásil konečný stav "
                 "(doručené/vrátené), Pošta SK API sa pre ne nevolá", api_skipped)
    stats = {"checked": len(shipments), "uncollected": len(uncollected),
             "invalid": len(invalid), "errors": len(errors),
             "emails_sent": sent, "emails_failed": failed,
             # how many API round-trips the terminal cache saved this run (#222)
             "api_skipped": api_skipped,
             # „BCC vždy" is BINDING for these customer mails (require_bcc): with no MAIL_BCC not
             # one escalation goes out. Surfaced so the tab shows a dead automation as dead
             # instead of a healthy-looking run that quietly mailed nobody (ERROR log only).
             "bcc_missing": _mail_bcc() is None,
             # #282 — the same idea one step upstream: with no package numbers in the export there
             # is nothing to check at all, and `checked` shrinking towards zero looks exactly like
             # a quiet week. These four make the difference visible; `source_degraded` is what
             # turns the card red instead of leaving a green „✅ OK" over a blind automation.
             # …and a run that may not send anything is not a healthy run either (PR #298
             # review, A2). automation-health §3 is explicit that a new blind state signs up
             # to `source_degraded` rather than inventing a second flag `navError()` would
             # have to learn about; `status_config_broken` is the extra key that gives the
             # banner its text, and `emails_blocked` the number to put in it.
             "source_degraded": bool(coverage["degraded"] or bad_status_config),
             "status_config_broken": bad_status_config,
             "emails_blocked": blocked,
             "dispatched_orders": coverage["dispatched_orders"],
             "dispatched_without_package": coverage["dispatched_without_package"],
             "missing_package": coverage["missing_package"],
             "days_since_last_package": coverage["days_since_last_package"],
             "dispatched_status_unknown": coverage["dispatched_status_unknown"]}
    with _lock:
        # Re-read under the lock and update that map, rather than writing a brand-new dict:
        # `esc`/`term_cache` were read before minutes of Pošta SK round-trips, so this both
        # keeps whatever another writer added to the untouched keys AND makes the save a
        # genuine read-modify-write — which is what the shrink guard checks (PR #265 review:
        # a rebuilt dict carries no read receipt, and `escalation` really does shrink here).
        st = _load_posta_state()
        st.update({
            # `esc` is the start-of-run map MINUS the orders that left the source window
            # (pruned above) PLUS this run's bumps — so it deliberately CAN shrink. That is
            # legitimate only because `st` is the map we just read under this lock, which is
            # exactly what the shrink guard checks.
            "escalation": esc,
            "terminal": term_cache,
            "last_check": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "uncollected": uncollected, "invalid": invalid, "errors": errors,
            "stats": stats,
        })
        _save_posta_state(st)
    log.info("posta: run done %s", stats)
    return stats


def run_shoptet_sync() -> dict:
    """Hourly refresh (#119): re-pulls the forestshop orders export (bypassing the
    30-min ORDERS_MAXAGE cache window — an hourly GUARANTEED pull, not just
    "whenever someone opens Na objednanie") AND the full Shoptet catalog export
    (data/products.csv) AND the customer export (data/out/customers_cache.csv),
    then rebuilds the in-memory CODE2PAIR/CATALOG search
    index and resyncs each review card's price/stock snapshot
    (export_helpers.resync_current — the same logic scripts/resync_export.py
    runs manually). Passive READ-ONLY refresh as far as the manager's decision
    stores go (decisions/order_pairings/supplier_assignments — his live work),
    with ONE deliberate exception since #212: the per-line flag stores are pruned
    of keys whose ORDER the freshly downloaded export positively shows as closed.
    That prune only ever REMOVES keys the tab could never display again, never
    writes a value, and disarms itself on an implausible export
    (`_prune_orphan_line_flags`).

    Fetch-then-swap (temp file + atomic os.replace) throughout: a failed/partial
    fetch raises BEFORE anything on disk changes, so the runner's existing
    try/except (automation_runner._execute) records the error and the app keeps
    serving the previous cache/catalog/review data untouched — degrade, never
    crash, never a half-written file."""
    global PRODUCTS, CODE2PAIR, CODE2VARIANT, CATALOG, _NEDOSTUPNE_CAT, _CODE2URL

    orders_bytes = _fetch_orders_csv()
    _atomic_write_bytes(ORDERS_CACHE, orders_bytes, mode=0o600)

    # #212 — on the bytes we have JUST downloaded, which is the freshest ground truth
    # there is. Deliberately NOT on the read path `/api/orders`: a write there would turn
    # „read the export" into a mutation and would let a STALE on-disk copy keep deciding
    # what to delete (the same reason `_export_watermark_observe` is not hidden inside a
    # reader). Housekeeping, so it can never take the hourly refresh down with it — a
    # refused or failed prune leaves the stores exactly as they were and the sync goes on.
    try:
        prune = _prune_orphan_line_flags(orders_bytes)
    except (StoreLockTimeout, StoreWipeRefused, OSError, ValueError,
            csv.Error) as e:  # noqa: BLE001
        # `csv.Error` explicitly: it is neither ValueError nor OSError, so without it a
        # malformed export would escape and take the catalogue refresh, the review resync
        # and the customer export down with it, every hour, while this comment promised
        # the opposite. `_orders_by_openness` already catches it — this is the backstop.
        log.error("prune riadkových príznakov preskočený (%r) — sync pokračuje", e)
        prune = {"pruned": 0, "skipped": repr(e), "orders_seen": 0, "orders_open": 0,
                 "unknown_statuses": [], "open_statuses": [], "per_store": {}}

    # A REFUSED download is NON-FATAL (PR #280 review, MUST FIX 2). The refusal fires
    # only while the copy already on disk is fresh AND plausible — it exists to protect
    # exactly those bytes — so the right response is to keep using them, not to abandon
    # the whole refresh. Unguarded, it took the review-card price/stock resync and the
    # customer export down every hour until the on-disk export aged past
    # EXPORT_MAX_AGE_S, which was precisely the staleness that disarmed the supplier
    # hold: the two gates created each other's blind spot.
    #
    # Deliberately narrow: only ExportDownloadRefused. A network failure says nothing
    # about the on-disk copy, and a sync quietly running for a week on an old export
    # while reporting OK is worse than a red row — so that stays fatal.
    export_bytes, export_error = b"", None
    try:
        export_bytes = _fetch_export_csv()
        _atomic_write_bytes(SRC, export_bytes)
    except ExportDownloadRefused as e:
        export_error = str(e)              # already secret-sanitized by the guard
        log.warning("shoptet_sync: catalogue export download refused (non-fatal, "
                    "keeping the copy on disk): %s", export_error)

    # rebuild the in-memory search index from the fresh export — same single
    # cp1250-pass helper the app uses at startup, no restart needed.
    with _lock:
        # RE-READ review_data.json first (PR #265 review). It changes UNDER a running app:
        # the documented `scripts/add_supplier_review_data.py` appends a whole new supplier
        # while the service serves. `PRODUCTS` was read once at BOOT and `resync_current`
        # only mutates in place, so resyncing and saving that list would discard the new
        # supplier — and since #261 the writer refuses that shrink, which left this
        # automation failing every hour with no way back short of a restart. Re-reading
        # makes the append take effect without one AND makes the save below a legitimate
        # read-modify-write. A missing/unparsable file yields [] — keep what we are serving
        # rather than adopt an empty catalogue.
        fresh = _read_json_store(DATA, [])
        if fresh:
            PRODUCTS = fresh
        review_keys = ({p.get("pairCode") for p in PRODUCTS if p.get("pairCode")}
                       | {c for p in PRODUCTS for c in (p.get("variant_codes") or [])})
        CODE2PAIR, CODE2VARIANT, CATALOG = _load_catalog(SRC, review_keys)
        # the fresh export can change names/alternatives → drop the lazy caches so the
        # nedostupné resolver rebuilds them from the new products.csv on next tab open.
        _NEDOSTUPNE_CAT = None
        _CODE2URL = None
        # #277 — the ONE place the catalogue-size watermark is observed: the index we
        # have just rebuilt from freshly downloaded bytes. Feeding a persistent
        # max-over-7-days store is NOT the „compare against len(CODE2PAIR)" the ticket
        # rules out: a broken feed can only fail to raise the watermark, never lower it.
        #
        # SKIPPED when the download was refused: the index then comes from the OLD copy
        # on disk, and re-observing that would let a stale export keep re-asserting the
        # old size for ever — which is exactly what disables the ratio floor's
        # time-based self-healing (playbook: measure only from freshly downloaded bytes).
        if export_error is None:
            _export_watermark_observe(len(CODE2PAIR))

    rows = []
    # newline="" — see _load_catalog (#279). resync_current joins the export to
    # review_data.json on (supplier, NAME): a rewritten name breaks that join, so the
    # card silently goes `stale` and its price/stock stop refreshing.
    with open(SRC, encoding="cp1250", errors="replace", newline="") as f:
        for row in csv.DictReader(f, delimiter=";"):
            rows.append(row)
    with _lock:
        counts = resync_current(rows, PRODUCTS, set(config.SUPPLIERS))
        _save_products(PRODUCTS)

    # Customer export — fetched LAST and NON-FATAL: a customer-export hiccup must
    # never turn the whole sync red, because orders/catalog/review already landed
    # above and ARE the critical data (nothing consumes customers yet). On failure we
    # log + surface customers_error in the result, but the run still reports OK for the
    # critical refresh — the manager must not read a red status as "orders stale". Same
    # fetch-then-swap + secret-safe pattern.
    customers_bytes, customers_error = b"", None
    try:
        customers_bytes = _fetch_customers_csv()
        _atomic_write_bytes(CUSTOMERS_CACHE, customers_bytes, mode=0o600)
    except Exception as e:  # noqa: BLE001 — auxiliary source; never fail the whole sync
        customers_error = str(e)   # already secret-sanitized by _fetch_customers_csv
        log.warning("shoptet_sync: customer export refresh failed (non-fatal): %s",
                    customers_error)

    result = {
        "orders_bytes": len(orders_bytes),
        "catalog_products": len(CATALOG),
        "catalog_codes": len(CODE2PAIR),
        "review_synced": counts["synced"],
        "review_stale": counts["stale"],
        "customers_bytes": len(customers_bytes),
        # #212 — how many of the manager's per-line flag keys this run deleted. It is the
        # one thing in this automation that REMOVES his markings, so it is reported next
        # to the rest rather than living only in the log.
        "flags_pruned": prune["pruned"],
        # …and the numbers any refusal fired on. A refusal that returns a bare 0 tells the
        # operator „your export is wrong" with nothing to go and look at
        # (`.claude/rules/automation-health.md` §3, and store-prune §7).
        "flags_orders_seen": prune["orders_seen"],
        "flags_orders_open": prune["orders_open"],
        # the honest cost of the terminal-status allow-list: a status nobody taught it about
        # quietly stops being pruned, so the run names it instead of hiding it
        "flags_unknown_statuses": prune["unknown_statuses"],
        # #209 — the statuses this run treated as „being processed". The „nothing is open"
        # banner names THESE, so after a rename it stops sending the manager to look for a
        # literal the shop no longer uses.
        "flags_open_statuses": prune["open_statuses"],
    }
    if prune["skipped"]:
        result["flags_prune_skipped"] = prune["skipped"]
        # #293 — the refusal is SAFE but PERMANENT: `no-open-orders` / `no-status-column`
        # hold until the export is fixed, so the prune never runs once and the flag stores
        # grow exactly as they did before #212. Everything else in this run legitimately
        # succeeded, so `last_status` stays `ok` — which is precisely how the „quietly dead
        # automation" looks. It rides the SAME `source_degraded` flag #282 introduced (a run
        # that cannot read its own input has failed, whether or not it threw), because
        # `navError()` already lights the sidebar ⚠ from it; a second predicate would be one
        # more branch every future automation has to remember to hit.
        result["source_degraded"] = True
    if export_error:
        result["export_error"] = export_error
    if customers_error:
        result["customers_error"] = customers_error
    log.info("shoptet_sync: run OK %s", result)
    return result


# The order matters: pairings first (they define what a product IS), then the
# code/link producers, then the availability ones — so a product that gains a
# supplier link in the same cycle is already linked when it goes on sale.
CYCLE_PRODUCERS = ("parovania_eshop", "grube_externalcode", "split_links",
                   "restock_skladom", "stock_skladom")
# #299 review I2 — producers already switched from their OLD direct-to-eshop
# import onto `queue_shoptet_fields` (Tasks 8-10 add one each here, in the SAME
# commit that switches it). Until a producer is in here the cycle must NEVER
# run it: today ALL of CYCLE_PRODUCERS still write straight to the live eshop,
# so running them hourly would turn a 1x/day automation into 24x/day writes to
# forestshop.sk the moment a manager clicks ▶ Štart on "Sync do Shoptetu".
QUEUE_MIGRATED: tuple[str, ...] = ()
SECOND_SYNC_SKIP_WHEN_NOTHING_SENT = True
# #299 review N3 — shoptet_sync and shoptet_upload share the SAME 60-minute
# schedule, so their scheduler ticks synchronize: without this, the cycle's own
# PRE-import download re-fetches the 57 MB catalogue a second time in the same
# tick shoptet_sync already refreshed it on its own schedule. Skip the PRE-
# import download ONLY when the export is still fresh (younger than this) —
# an export that age can only have come from THIS tick's own shoptet_sync run,
# never a stale leftover, so nothing is lost by not re-downloading it. The
# POST-import download is NEVER skipped by this — that one is the whole point
# of "the database is reconciled right away" after an upload.
SHOPTET_UPLOAD_SKIP_PRESYNC_FRESHER_THAN_S = 15 * 60


def _enabled_automations() -> set:
    return {a["key"] for a in RUNNER.status() if a.get("enabled")}


def run_shoptet_upload() -> dict:
    """#299 — the hourly cycle: download → let the producers queue → ONE import →
    settle → download again.

    It writes nothing to the eshop itself: producers queue into pending_shoptet,
    and only rows the import log confirmed AND still hold their sent value are
    credited and dropped (see `sent_fields` below — #299 review C1). The second
    download is skipped when nothing went up (most hours) — it would re-fetch the
    57 MB catalogue for no reason.

    Every step below waits for its automation to actually finish
    (`RUNNER.run_sync`, #299 review I3) — the download really is on disk before
    the export is read, and a queue-migrated producer really has queued before
    the pending table is read; `RUNNER.run_now`'s background thread only made
    that ordering look true. `resynced` counts the REAL outcome of each
    `run_sync` call (0, 1 or 2), never a hard-coded guess — a `shoptet_sync`
    already in flight from its own hourly schedule returns False and must not
    be reported as "downloaded".

    #299 review C1 — `pending` is read once here to build the import, then read
    AGAIN under `_lock` right before `settle`: a producer can legitimately queue
    a NEW value for the same code while the import is still running (up to 15
    min/chunk). `sent_fields` is what THIS import actually put on the wire, built
    from the very `rows` handed to `_import_rows_chunked` — never re-derived from
    `pending` — so `settle` can tell a field Shoptet confirmed from a field that
    merely happens to belong to a code Shoptet confirmed. A field that changed
    mid-flight is kept and goes out again next hour; its credit does not fire.

    #299 review N2 — `sent_credits` is the same kind of send-time snapshot as
    `sent_fields`, built from the SAME `pending` read that produced `rows`, and
    carries what a field's `credit` dict looked like when it was sent. A
    re-queue mid-import can leave a field's plain VALUE unchanged (so it is not
    dirty and IS confirmed) while writing a NEW `credit.value` — `settle` must
    still credit the value Shoptet actually saw, never whatever `fresh` holds.

    #299 review (Task 4 minor, deferred to Task 6 — M2): the drain reads the table
    through `_load_pending`, which DEGRADES a corrupt/unreadable file to `{}` —
    same as every other reader in this module. That degrade is safe HERE, on
    purpose, and deliberately NOT special-cased: the two `_load_pending()` calls
    below only ever decide what THIS run attempts to send; the strictness that
    matters is at the END of the cycle, where `_save_pending(settled)` runs
    UNCONDITIONALLY. `_atomic_write_json(protect=True)` re-reads the file straight
    off disk at write time (never trusts what an earlier read handed back), so a
    genuinely corrupt table still makes this run raise `StoreWipeRefused` out of
    this function — the exact same refusal `queue_shoptet_fields` gets from the
    same guard — instead of quietly completing a run that settled and credited
    nothing while looking like a clean, empty hour. Skipping that final write
    when `settled` happens to be empty (to avoid a "redundant" save) would throw
    this refusal away and turn a corrupt table into a silent no-op every single
    hour; that is exactly why it is never skipped.

    #299 review N3 — the PRE-import download is skipped when the on-disk export
    is already fresher than `SHOPTET_UPLOAD_SKIP_PRESYNC_FRESHER_THAN_S`: on the
    shared 60-minute schedule, shoptet_sync's own tick usually lands moments
    before this cycle's, so re-downloading here is a second 57 MB fetch of a
    catalogue that is already current. An unknown age (no export on disk, or a
    test double) is NEVER treated as fresh — the download always runs then, the
    same fail-safe direction `_export_age_s`/`_export_row_verdicts` already
    take. The POST-import download is untouched by this — it is what actually
    reconciles the database right after an upload."""
    with _shoptet_cycle_claim() as got:
        if not got:
            return {"ok": False, "error": "cyklus už beží", "queued": 0, "sent": 0,
                    "confirmed": 0, "blocked": 0, "stale_blocked": [],
                    "producers": {}, "resynced": 0,
                    "skipped_second_sync": True, "unconfirmed": 0}

        presync_age = _export_age_s()
        presync_fresh = (presync_age is not None
                          and presync_age < SHOPTET_UPLOAD_SKIP_PRESYNC_FRESHER_THAN_S)
        if presync_fresh:
            resynced = 0
            log.info("sync do Shoptetu: export je čerstvý (%.1f min, limit %.1f "
                      "min) — preskakujem predimportné stiahnutie",
                      presync_age / 60, SHOPTET_UPLOAD_SKIP_PRESYNC_FRESHER_THAN_S / 60)
        else:
            resynced = int(bool(RUNNER.run_sync("shoptet_sync")))
            log.info("sync do Shoptetu: predimportné stiahnutie spúšťam (vek "
                      "exportu %s)", "neznámy" if presync_age is None
                      else f"{presync_age / 60:.1f} min")

        enabled = _enabled_automations()
        producers = {}
        for key in CYCLE_PRODUCERS:
            if key not in QUEUE_MIGRATED or key not in enabled:
                continue
            producers[key] = bool(RUNNER.run_sync(key))

        # M5: build_import(pending) once — not once for the verdict pass and
        # once more for the actual send — and filter `absent` over the result
        # instead of re-walking the whole table a second time.
        pending = _load_pending()
        header, rows_all, _ = shoptet_outbox.build_import(pending)
        verdicts = _export_row_verdicts(rows_all, note_col=None) if rows_all else \
            {"confirmed": set(), "absent": set()}
        absent = verdicts["absent"]
        rows = [r for r in rows_all if r[0] not in absent]
        blocked = {c: "not-in-catalog" for c in sorted(pending) if c in absent}
        # What THIS import is about to put on the wire, per code+column — the
        # snapshot `settle` compares against later, never `pending` itself (C1).
        cols = header.split(";")[len(shoptet_outbox.KEY_COLUMNS):]
        sent_fields = {r[0]: dict(zip(cols, r[len(shoptet_outbox.KEY_COLUMNS):]))
                       for r in rows}
        # #299 review N2 — the credit VALUE settle() may award must come from
        # this same send-time snapshot, never from `fresh` below: a producer
        # that re-queues the SAME field value but a NEW credit_value mid-import
        # must not have that new value picked up, since Shoptet never saw it.
        # Built from THIS `pending` (the one that produced `rows`), never from
        # `fresh` — exactly like `sent_fields` above.
        sent_credits = {}
        for code, entry in pending.items():
            for col, field in (entry.get("fields") or {}).items():
                c = field.get("credit")
                if c:
                    sent_credits.setdefault(code, {})[col] = c

        # _import_rows_chunked's own contract (its docstring): the caller MUST hold
        # _import_lock across the call and release it in a `finally` — every other
        # of its 7 call sites in this module does exactly this, because it drives a
        # single shared browser automation that cannot run twice at once. Not in
        # the brief's original sketch; added here because skipping it would let a
        # manual "Spustiť teraz" of parovania_eshop/grube_externalcode/... (still on
        # their OLD direct-import path — #299's producer migration is a later task)
        # race this cycle's own import against the very same Shoptet session.
        res = None
        import_busy = False
        if rows:
            if not _import_lock.acquire(blocking=False):
                import_busy = True
                log.warning("sync do Shoptetu: iný import práve beží — táto vlna "
                           "sa preskakuje, riadky ostávajú v tabuľke na ďalší beh")
            else:
                try:
                    # M3: three of the five (still direct-import) producers this
                    # cycle will eventually replace already send csv_safe=True —
                    # the combined import must not lose that formula-injection guard.
                    res = _import_rows_chunked(rows, header, False,
                                               prefix="import_sync_", csv_safe=True,
                                               timeout=900)
                finally:
                    _import_lock.release()
        success = set(res["success_codes"]) if res else set()
        now = datetime.now().isoformat(timespec="seconds")
        with _lock:
            fresh = _load_pending()
            settled, credits = shoptet_outbox.settle(fresh, success, blocked,
                                                      sent_fields, sent_credits,
                                                      now=now)
            _save_pending(settled, prev=fresh)
        for store, entries in credits.items():
            _credit_producer(store, entries)

        sent, confirmed = len(rows), len(success)
        unconfirmed = sent - confirmed
        skipped = SECOND_SYNC_SKIP_WHEN_NOTHING_SENT and confirmed == 0
        if not skipped:
            resynced += int(bool(RUNNER.run_sync("shoptet_sync")))

        stale = shoptet_outbox.stale_blocked(settled)
        ok = (not import_busy) and (res is None or (res["ok"] and not res["partial"])) \
            and not stale
        if unconfirmed:
            if import_busy:
                log.error("sync do Shoptetu: %d riadkov čaká, lebo iný import práve "
                          "bežal — pôjdu v ďalšom hodinovom behu", unconfirmed)
            else:
                log.error("sync do Shoptetu: %d z %d riadkov Shoptet nepotvrdil — "
                          "ostávajú v tabuľke a pôjdu znova", unconfirmed, sent)
        if stale:
            log.error("sync do Shoptetu: %d kódov je zablokovaných 3 a viac behov "
                      "(eshop ich v katalógu nemá): %s", len(stale), stale[:10])
        error = ""
        if not ok:
            error = "iný import práve beží" if import_busy else \
                "nepotvrdené alebo zablokované riadky"
        return {"ok": ok, "queued": len(pending), "sent": sent,
                "confirmed": confirmed, "blocked": len(blocked),
                "stale_blocked": stale, "producers": producers,
                "resynced": resynced, "skipped_second_sync": skipped,
                "unconfirmed": unconfirmed, "error": error}


def _credit_producer(store: str, entries: dict) -> None:
    """Write a producer's uploaded-state for groups the import confirmed."""
    path = {"parovania_eshop": PAIRINGS_STATE}.get(store)
    if path is None:
        log.warning("outbox: neznámy kredit store %s (%d skupín)", store, len(entries))
        return
    _record_uploaded(lambda: _read_json_store(path, {}),
                     lambda d: _atomic_write_json(path, d, protect=True),
                     entries)


def run_parovania_eshop() -> dict:
    """Nightly push (daily 21:00) of the workers' NEW pairings (reorder links →
    internalNote) + newly assigned suppliers (→ supplier field) to the Shoptet
    eshop — the in-app migration of the n8n „Forestshop — Párovania → eshop"
    workflow (YuDugCCOnwejRfva, #109). Reuses the SAME careful upload path as the
    two n8n endpoints (_do_upload_pairings / _do_upload_suppliers — no Shoptet
    logic reimplemented). The write stays IDEMPOTENT: already-uploaded pairings/
    suppliers are skipped via uploaded_pairings.json / uploaded_suppliers.json,
    so a re-run never double-uploads. Records combined counts for the tab. Both
    steps run sequentially (mirroring the n8n chain); a step that completes with
    ok:false (import failed) or blocked is surfaced in the returned `status`
    without crashing the run. A genuine exception propagates to the runner, which
    records last_status='error' and keeps the app alive (degrade, never crash).

    Reads ONLY the manager's decision/assignment stores (what to push) — never
    modifies them; its own progress lives in the two uploaded_*.json state files."""
    pairings, _ps = _do_upload_pairings(dry=False)
    suppliers, _ss = _do_upload_suppliers(dry=False)

    def _blocked(d):
        return int(d.get("blocked") or 0)

    def _missing(d):
        return int(d.get("missing_count") or 0)

    p_ok = bool(pairings.get("ok"))
    s_ok = bool(suppliers.get("ok"))
    if not (p_ok and s_ok):
        status = "failed"          # an import (or lock/timeout) failed → red row
    elif (_blocked(pairings) or _blocked(suppliers)
            or _missing(pairings) or _missing(suppliers)):
        status = "blocked"         # paired but un-uploadable (missing codes) → orange row
    else:
        status = "ok"
    # NOTE: pairings["order_blocked"] deliberately does NOT feed this status — an
    # inline order_pairing code excluded because a reviewed decision already covers
    # it (#38) is expected/benign (the decision wins), not a data problem worth an
    # orange row. It's still surfaced in the tab's own "Inline páry" counters.

    result = {
        "status": status,
        "pairings": {
            "count": pairings.get("count", 0),
            "total_uploaded": pairings.get("total_uploaded", 0),
            "total_products": pairings.get("total_products", 0),
            "remaining": pairings.get("remaining", 0),
            "blocked": _blocked(pairings),
            # #38: inline order_pairings pushed in the SAME run (own namespace,
            # own counters — see _do_upload_pairings)
            "order_count": int(pairings.get("order_count") or 0),
            "order_blocked": int(pairings.get("order_blocked") or 0),
            # #270: codes the eshop's CATALOGUE does not carry at all — held back
            # instead of being rejected by Shoptet every night, and listed by name so
            # the manager can fix them (this is what turns the row orange above).
            "missing_count": _missing(pairings),
            "missing_in_eshop": pairings.get("missing_in_eshop") or [],
            # #257: how many rows the eshop already had exactly as we would write
            # them (proven from its export → credited, not re-sent), and how many
            # Shoptet REJECTED out of the ones we did send. Both were invisible
            # while a partially-accepted batch was booked as a plain failure.
            "confirmed_in_export": int(pairings.get("confirmed_in_export") or 0),
            "rejected": int(pairings.get("rejected") or 0),
            "partial": bool(pairings.get("partial")),
            "ok": p_ok,
            "error": pairings.get("error", ""),
        },
        "suppliers": {
            "count": suppliers.get("count", 0),
            "total_uploaded": suppliers.get("total_uploaded", 0),
            "total_assigned": suppliers.get("total_assigned", 0),
            "remaining": suppliers.get("remaining", 0),
            "blocked": _blocked(suppliers),
            # #270/#275: codes the eshop's CATALOGUE does not carry at all. Since #275
            # this half HOLDS them like the pairings half, instead of sending them for
            # Shoptet to refuse — which is what stops the run being red every night and
            # makes this term the one that (correctly) turns it orange instead.
            "missing_count": _missing(suppliers),
            "missing_in_eshop": suppliers.get("missing_in_eshop") or [],
            # #280 review: WHY the fail-closed gate blocked the write, so the card can
            # name the real cause. Every block used to render „chýbajú kódy", which is
            # the one thing it never is — nothing is missing, the export is simply not
            # believable (absent / too small / too old). None on a healthy run.
            "gate_blocked": suppliers.get("gate_blocked"),
            "ok": s_ok,
            "error": suppliers.get("error", ""),
        },
        "review_url": PUBLIC_URL,
    }
    log.info("parovania_eshop: run done status=%s pairings=%d suppliers=%d",
             status, result["pairings"]["count"], result["suppliers"]["count"])
    return result


def run_grube_externalcode() -> dict:
    """Nightly push (daily 03:30) of the GRUBE per-size externalCodes (grube itemId
    → the eshop `externalCode` field) — the in-app cron follow-up (#62) to the MVP
    manual zip. Reuses the SAME careful chunked import path as the n8n endpoint
    (_do_upload_externalcodes — no Shoptet logic reimplemented). The write stays
    IDEMPOTENT: an already-uploaded itemId is skipped via uploaded_externalcodes.json,
    so a re-run never re-pushes an unchanged itemId; only a NEW code or a CHANGED
    itemId goes up. A step that completes with ok:false (import failed) or blocked is
    surfaced in the returned `status` without crashing the run; a genuine exception
    propagates to the runner (records last_status='error', keeps the app alive).

    Reads ONLY the durable grube_codes.json store (built by scripts/build_grube_codes.py)
    — never modifies it; its own progress lives in uploaded_externalcodes.json.

    SEPARATE from „Párovania → eshop": that automation is ALREADY enabled on prod, and
    externalCode is a distinct write field — folding it in would auto-activate on the
    live eshop, breaking the #93 default-disabled-for-new-live-write contract. This one
    starts DISABLED on its own."""
    ext, _es = _do_upload_externalcodes(dry=False)
    e_ok = bool(ext.get("ok"))
    blocked = int(ext.get("blocked") or 0)
    if not e_ok:
        status = "failed"          # an import (or lock/timeout) failed → red row
    elif blocked:
        status = "blocked"         # numeric-itemId codes that produced no row → orange
    else:
        status = "ok"
    result = {
        "status": status,
        "externalcodes": {
            "count": ext.get("count", 0),
            "total_uploaded": ext.get("total_uploaded", 0),
            "total_codes": ext.get("total_codes", 0),
            "remaining": ext.get("remaining", 0),
            "blocked": blocked,
            "ok": e_ok,
            "error": ext.get("error", ""),
        },
        "review_url": PUBLIC_URL,
    }
    log.info("grube_externalcode: run done status=%s externalcodes=%d",
             status, result["externalcodes"]["count"])
    return result


def run_split_links() -> dict:
    """Nightly push (daily 03:45) of the per-size SPLIT links (#174 „✂ Rozdeliť na
    veľkosti") to the eshop `internalNote` field, per variant — the in-app cron
    follow-up (#192) to the MVP manual zip. Reuses the SAME careful chunked import path
    + row builder as the n8n endpoint (_do_upload_variant_links → link_rows — no Shoptet
    logic reimplemented). The write stays IDEMPOTENT: an already-uploaded URL is skipped
    via uploaded_variant_links.json, so a re-run never re-pushes an unchanged link; only
    a NEW split variant or a CHANGED URL goes up. A step that completes with ok:false
    (import failed) or blocked is surfaced in the returned `status` without crashing the
    run; a genuine exception propagates to the runner (records last_status='error',
    keeps the app alive).

    Reads ONLY the durable variant_links.json store + the live split decisions — never
    modifies them; its own progress lives in uploaded_variant_links.json.

    SEPARATE from „Párovania → eshop" (which is already enabled on prod): a split link
    is a distinct write (internalNote per variant via link_rows, keyed per variant
    code) that the pairings push never handles — folding it in would auto-activate on
    the live eshop, breaking the #93 default-disabled-for-new-live-write contract. This
    one starts DISABLED on its own."""
    vl, _vs = _do_upload_variant_links(dry=False)
    v_ok = bool(vl.get("ok"))
    blocked = int(vl.get("blocked") or 0)
    if not v_ok:
        status = "failed"          # an import (or lock/timeout) failed → red row
    elif blocked:
        status = "blocked"         # split codes that produced no row → orange
    else:
        status = "ok"
    result = {
        "status": status,
        "variantlinks": {
            "count": vl.get("count", 0),
            "total_uploaded": vl.get("total_uploaded", 0),
            "total_codes": vl.get("total_codes", 0),
            "remaining": vl.get("remaining", 0),
            "blocked": blocked,
            "ok": v_ok,
            "error": vl.get("error", ""),
        },
        "review_url": PUBLIC_URL,
    }
    log.info("split_links: run done status=%s variantlinks=%d",
             status, result["variantlinks"]["count"])
    return result


# --------------------------------------------------------------------------- #
# #106 — „Dodávateľský sklad": daily supplier availability/price scraper (in-app
# migration of the n8n „Forestshop — Dodávateľský scraper" 6kn7jzBXTjbmbiVa).
# For each product's supplier link (internalNote) it fetches the supplier page,
# extracts availability + price via a STATIC tier (JSON-LD → meta → text keywords)
# and, only when static can't decide, an OpenAI gpt-4o-mini fallback. Results land
# in data/out/supplier_stock.json — the input for #107/#108. Pure logic lives in
# parovanie.supplier_stock; this wires it to the network, OpenAI and the store.
# --------------------------------------------------------------------------- #
SUPPLIER_STOCK_STATE = _store("supplier_stock.json")
# Refetch only links not checked in the last N hours (skip fresh OK rows) — saves
# HTTP + paid LLM cost. Chosen < 24h so a daily run still re-checks everything once.
SUPPLIER_STOCK_MAX_AGE_H = 20.0
SUPPLIER_FETCH_DELAY_S = 1.0      # per-domain politeness gap (don't hammer one shop)
SUPPLIER_FETCH_TIMEOUT = 30      # s per supplier page
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_TIMEOUT = 60              # s per LLM call


def _load_supplier_stock() -> dict:
    try:
        with open(SUPPLIER_STOCK_STATE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_supplier_stock(d: dict) -> None:
    _atomic_write_json(SUPPLIER_STOCK_STATE, d, mode=0o600)


def _iter_export_lines():
    """The on-disk Shoptet catalog export (data/products.csv), cp1250-decoded,
    yielded LINE BY LINE — the reader the NIGHTLY PUSH uses (#272), so it never
    holds more than one line of the ~57 MB file.

    `newline=""` keeps every line terminator intact (and turns off newline
    translation), which is exactly what `csv.reader`/`csv.DictReader` need: a
    quoted field spanning several lines is reassembled from the yielded lines
    byte-for-byte, so parsing over this iterator gives the same values as parsing
    over `io.StringIO(whole_text)` did for every well-formed export. One deliberate
    difference: a BARE carriage return in an UNQUOTED field used to raise
    `_csv.Error: new-line character seen in unquoted field` (the whole nightly push
    died); it now splits the record, which is exactly why the csv docs mandate
    `newline=""`. It can only ADD codes, never invent an absent one. A missing file yields nothing and never
    raises — same contract as `_read_export_for_links`.

    Measured on the live 57.4 MB export: peak allocation 346.6 MB → 3.0 MB
    (max RSS 361 MB → 17.6 MB) at the same wall time (~1.4 s)."""
    try:
        f = open(SRC, encoding="cp1250", errors="replace", newline="")
    except FileNotFoundError:
        return
    with f:
        yield from f


def _read_export_for_links() -> str:
    """The WHOLE on-disk Shoptet catalog export as one cp1250-decoded string — the
    source of supplier links for the scraping/JOIN automations (#106/#107/#108),
    which genuinely need the full text. Refreshed hourly by the „Sync zo Shoptetu"
    automation and at startup; a missing file simply yields 0 links (never crashes).

    Deliberately NOT implemented on top of `_iter_export_lines`: `"".join(lines)`
    builds the whole line list first and would peak HIGHER than this single read.
    The nightly push must not call this at all — it streams (#272)."""
    try:
        with open(SRC, "rb") as f:
            return f.read().decode("cp1250", errors="replace")
    except FileNotFoundError:
        return ""


def _fetch_supplier_html(url: str) -> str:
    """GET one supplier product page (3 tries, exponential-ish backoff). Raises on
    the final failure so the run records THAT link as an error row and continues.
    The URL is a public product page (no secret) — safe to keep in error text."""
    for attempt in range(1, 4):
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=SUPPLIER_FETCH_TIMEOUT)
            r.raise_for_status()
            if not r.encoding or r.encoding.lower() == "iso-8859-1":
                r.encoding = r.apparent_encoding or r.encoding
            return r.text
        except Exception as e:  # noqa: BLE001 — retried; the last failure propagates
            log.warning("supplier_stock: fetch %s attempt %d/3 failed: %r", url, attempt, e)
            if attempt == 3:
                raise
            time.sleep(2 * attempt)
    raise RuntimeError("unreachable")


def _llm_extract(text: str, url: str) -> dict:
    """OpenAI gpt-4o-mini structured extraction of availability/price from page text.
    Requires OPENAI_API_KEY (data/.ai_env) — the caller only reaches here when the
    key is present. The key lives in the Authorization header (never the URL), so an
    error string carries no secret."""
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY nie je nastavený")
    payload = {"model": supplier_stock.LLM_MODEL,
               "messages": supplier_stock.build_llm_messages(text, url),
               "temperature": 0,
               "response_format": {"type": "json_object"}}
    r = requests.post(OPENAI_URL, json=payload, timeout=OPENAI_TIMEOUT,
                      headers={"Authorization": f"Bearer {key}",
                               "Content-Type": "application/json"})
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"]
    return supplier_stock.parse_llm_json(content)


def _politeness_wait(domain: str, last_ts: dict) -> None:
    """Sleep so consecutive fetches to the SAME supplier domain are ≥ the politeness
    gap apart (different domains never wait on each other)."""
    prev = last_ts.get(domain)
    if prev is not None and SUPPLIER_FETCH_DELAY_S > 0:
        gap = SUPPLIER_FETCH_DELAY_S - (time.monotonic() - prev)
        if gap > 0:
            time.sleep(gap)
    last_ts[domain] = time.monotonic()


def run_supplier_stock() -> dict:
    """One scraper run (daily 05:00 or „Spustiť teraz"): supplier links from the
    export → per link fetch + STATIC extraction (+ LLM fallback when static can't
    decide AND a key is configured) → upsert into data/out/supplier_stock.json.

    Cost-aware: recently-checked OK links are skipped (stale-skip); the LLM is
    called ONLY when static fails; per-domain politeness spreads same-shop hits.
    Robust: a failing fetch / LLM call for one link is recorded as an error row and
    the run continues — one bad supplier never crashes the run or the app. Reads
    ONLY its own store + the export; never touches the manager's decision stores."""
    csv_text = _read_export_for_links()
    links = supplier_stock.links_from_export(csv_text, config.SUPPLIERS)
    prev = {r.get("link"): r for r in (_load_supplier_stock().get("rows") or [])}
    now = datetime.now(timezone.utc).astimezone()
    have_key = bool(os.environ.get("OPENAI_API_KEY"))
    last_ts: dict[str, float] = {}
    rows = []
    stats = {"total": len(links), "checked": 0, "skipped": 0, "static": 0, "llm": 0,
             "available": 0, "unavailable": 0, "unknown": 0, "errors": 0, "llm_calls": 0}

    for lk in links:
        link = lk["link"]
        prow = prev.get(link)
        if supplier_stock.is_recently_checked(prow, now, SUPPLIER_STOCK_MAX_AGE_H):
            rows.append(prow)                       # keep the fresh result untouched
            stats["skipped"] += 1
            continue
        row = {"link": link, "supplier": lk.get("supplier", ""), "name": lk.get("name", ""),
               "codes": lk.get("codes", []), "product_count": lk.get("count", 0)}
        try:
            _politeness_wait(supplier_stock.host_of(link), last_ts)
            html = _fetch_supplier_html(link)
            static = supplier_stock.extract_static(html, link)
            available = static["available"]
            price = static["price"]
            currency = static["currency"]
            variants = static["variants"]
            avail_text = static["availabilityText"]
            extracted_by = static["extractedBy"]
            if supplier_stock.need_llm(static):
                if have_key:
                    llm = _llm_extract(supplier_stock.page_text(html), link)
                    stats["llm_calls"] += 1
                    extracted_by = "llm"
                    if llm["available"] is not None:
                        available = llm["available"]
                    if llm["price"] is not None:
                        price = llm["price"]
                    currency = llm["currency"] or currency
                    variants = llm["variants"] or variants
                    avail_text = llm["availabilityText"] or avail_text
                else:
                    # no key → don't call LLM; keep whatever static found, flag it
                    extracted_by = "static-only"
            row.update(ok=True, error="", available=available, price=price,
                       currency=currency, availabilityText=avail_text, variants=variants,
                       extractedBy=extracted_by, checkedAt=now.isoformat(timespec="seconds"))
            stats["checked"] += 1
            stats["llm" if extracted_by == "llm" else "static"] += 1
            if available is True:
                stats["available"] += 1
            elif available is False:
                stats["unavailable"] += 1
            else:
                stats["unknown"] += 1
        except Exception as e:  # noqa: BLE001 — per-link error recorded, run continues
            log.warning("supplier_stock: link %s FAILED: %r", link, e)
            row.update(ok=False, error=str(e)[:300], available=None, price=None,
                       currency="", availabilityText="", variants=[], extractedBy="error",
                       checkedAt=now.isoformat(timespec="seconds"))
            stats["errors"] += 1
        rows.append(row)

    _save_supplier_stock({"last_check": now.isoformat(timespec="seconds"),
                          "rows": rows, "stats": stats})
    log.info("supplier_stock: run done %s", stats)
    return stats


# --------------------------------------------------------------------------- #
# #107 — „Riziko výpadku": daily supply-risk report (in-app migration of the
# n8n workflow „Forestshop — Riziko výpadku" 7ujLZ4WDNphSgsuj). READ-ONLY /
# advisory — writes NOTHING to the eshop, ever. JOINS our catalog export
# against #106's ALREADY-SCRAPED data/out/supplier_stock.json (same
# internalNote link both automations share) — this automation does not scrape
# anything itself. Pure logic lives in parovanie.riziko_vypadku; this wires it
# to the store + the display/CSV endpoints.
# --------------------------------------------------------------------------- #
RIZIKO_STATE = _store("riziko_vypadku.json")


def _load_riziko() -> dict:
    try:
        with open(RIZIKO_STATE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_riziko(d: dict) -> None:
    _atomic_write_json(RIZIKO_STATE, d, mode=0o600)


def run_riziko_vypadku() -> dict:
    """One check run (daily ~06:15 or „Spustiť teraz"): join OUR catalog export
    (same on-disk data/products.csv #106 reads) against the „Dodávateľský sklad"
    scraper's LAST result (data/out/supplier_stock.json) — no network calls of
    its own. When the scraper has never run yet (no rows persisted), this
    surfaces has_supplier_data=False instead of silently flagging nothing as
    'no risk' (which would look like a false all-clear)."""
    with _lock:
        stock = _load_supplier_stock()
    supplier_rows = stock.get("rows") or []
    has_data = bool(supplier_rows)
    csv_text = _read_export_for_links()          # same cp1250 export reader as #106
    risks = riziko_vypadku.compute_risk(csv_text, supplier_rows) if has_data else []
    now = datetime.now(timezone.utc).astimezone()
    with _lock:
        _save_riziko({
            "last_check": now.isoformat(timespec="seconds"),
            "has_supplier_data": has_data,
            "supplier_last_check": stock.get("last_check", ""),
            "risks": risks,
        })
    stats = {"risks": len(risks), "has_supplier_data": has_data}
    log.info("riziko_vypadku: run done %s", stats)
    return stats


# --------------------------------------------------------------------------- #
# #108 — „Vypredané → Skladom": daily restock (in-app migration of the LIVE n8n
# workflow „Forestshop — Vypredané → Skladom v2" KN1BE18HLdM8mfTc). WRITES to the
# live eshop: JOINS our catalog export (Vypredané + visible products, state 2)
# against #106's ALREADY-SCRAPED supplier_stock.json and, for every product whose
# supplier now has FRESH confirmed stock, builds the restock rows (both availability
# fields → Skladom, visible, stock) and pushes them through the SAME careful Shoptet
# import path the n8n endpoint uses (import_builder.restock_rows → run_import →
# #23-hardened read-back). Detection logic lives in parovanie.restock_skladom; this
# wires it to the store + the import + the display endpoint.
# --------------------------------------------------------------------------- #
RESTOCK_STATE = _store("restock_skladom.json")


def _load_restock() -> dict:
    try:
        with open(RESTOCK_STATE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_restock(d: dict) -> None:
    _atomic_write_json(RESTOCK_STATE, d, mode=0o600)


def run_restock_skladom() -> dict:
    """One restock run (daily ~06:00 or „Spustiť teraz"): join OUR catalog export
    (Vypredané + visible products, state 2) against the „Dodávateľský sklad" scraper's
    LAST result (data/out/supplier_stock.json) and flip back to Skladom every product
    whose supplier now has FRESH confirmed stock — by building the restock import rows
    (both availability fields → Skladom, visible, stock 5 via
    import_builder.restock_rows) and pushing them through the SAME careful chunked
    Shoptet import path the n8n endpoints use (_import_rows_chunked → run_import →
    parse_import_log read-back — #158: a large restock batch has the same 120s
    browser-redirect-timeout risk #156 fixed for the pairings/suppliers pushes).
    WRITES to the live eshop.

    Safe by construction: no supplier data (scraper never ran) → flips NOTHING and
    surfaces has_supplier_data=False; a candidate needs ok+available+FRESH (checkedAt
    within 48h), so a stale or negative supplier confirmation never flips a product.
    Idempotent — only state-2 (Vypredané) products are candidates, so a product already
    Skladom is never re-flipped once the export refreshes. A failed import is detected
    via the #23-hardened read-back and recorded status='error', not a silent success
    (the run itself never raises on an import failure — it degrades to a red row, like
    parovania_eshop). Reads ONLY its own store + the export + supplier_stock; never
    touches the manager's decision stores."""
    with _lock:
        stock = _load_supplier_stock()
    supplier_rows = stock.get("rows") or []
    has_data = bool(supplier_rows)
    csv_text = _read_export_for_links()          # same cp1250 export reader as #106
    now = datetime.now(timezone.utc).astimezone()
    candidates = (restock_skladom.compute_candidates(csv_text, supplier_rows, now)
                  if has_data else [])
    rows = import_builder.restock_rows(candidates, CODE2PAIR)

    status = "ok"
    processed = updated = failed = None
    error_detail = ""
    if rows:
        if not _import_lock.acquire(blocking=False):
            log.warning("restock_skladom: iný import práve beží — beh preskočený")
            status, error_detail = "busy", "iný import práve beží"
        else:
            try:
                # #158: chunked import (like #156) so a large restock batch never
                # overruns the browser redirect timeout. A chunk that times out is
                # caught INSIDE _import_rows_chunked (never raises) and treated as
                # a failed chunk — the lock is always released.
                res = _import_rows_chunked(rows, import_builder.RESTOCK_COLS, False,
                                           prefix="restock_", timeout=900)
            finally:
                _import_lock.release()
            processed, updated, failed = res["processed"], res["updated"], res["failed"]
            if res["ok"]:
                status = "ok"
            else:
                status = "error"
                # _chunk_error_msg already carries res["error_detail"]; add the
                # stderr tail only when there is no Shoptet reason to show
                tail = "" if res["error_detail"] else (res["err"] or "")[-300:]
                error_detail = _chunk_error_msg(res, len(rows)) + (f": {tail}" if tail else "")
                log.error("restock_skladom: import FAILED rc=%s chunks_ok=%d/%d stderr=%s",
                          res["rc"], res["chunks_ok"], res["chunks_total"],
                          (res["err"] or "")[-400:])

    # `candidates` are always stored (what WOULD be flipped) so the tab shows them
    # even on a failed import; on success they ARE what was flipped.
    with _lock:
        _save_restock({
            "last_check": now.isoformat(timespec="seconds"),
            "has_supplier_data": has_data,
            "supplier_last_check": stock.get("last_check", ""),
            "status": status,
            "candidates": candidates,
            "processed": processed, "updated": updated, "failed": failed,
            "error_detail": error_detail,
        })
    stats = {"candidates": len(candidates), "imported_rows": len(rows),
             "status": status, "processed": processed, "updated": updated,
             "failed": failed, "has_supplier_data": has_data}
    log.info("restock_skladom: run done %s", stats)
    return stats


# --------------------------------------------------------------------------- #
# #98 — „Máme skladom → Skladom": auto-restock from Shoptet's OWN physical stock.
# Distinct from #108 restock_skladom (which triggers on a scraped SUPPLIER
# confirmation): this finds OUR products that physically HAVE stock (stock>0 — the
# green „máme" bars in the Shoptet admin) yet are still shown as Vypredané (state 2,
# visible), and flips them back to Skladom by importing the rows to Shoptet. It
# needs NO supplier data. Detection lives in parovanie.stock_skladom; the write is
# import_builder.skladom_rows (visible + both availability Skladom; the real stock
# is NEVER overwritten) through the SAME careful chunked import path.
# --------------------------------------------------------------------------- #
STOCK_SKLADOM_STATE = _store("stock_skladom.json")


def _load_stock_skladom() -> dict:
    try:
        with open(STOCK_SKLADOM_STATE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_stock_skladom(d: dict) -> None:
    _atomic_write_json(STOCK_SKLADOM_STATE, d, mode=0o600)


def run_stock_skladom() -> dict:
    """One auto-skladom run (daily or „Spustiť teraz"): read OUR catalog export and
    flip back to Skladom every product that PHYSICALLY has stock (stock>0) but is
    still shown as Vypredané (state 2, visible) — the manager's „máme skladom" bars
    in Shoptet — by building the rows (both availability fields → Skladom, visible,
    stock left untouched via import_builder.skladom_rows) and pushing them through
    the SAME careful chunked Shoptet import path the n8n endpoints use
    (_import_rows_chunked → run_import → #23-hardened read-back). WRITES to the live
    eshop.

    Needs no supplier data (unlike restock_skladom) — the trigger is Shoptet's own
    stock. Safe by construction: a conscious-off product (detailOnly/discontinued/
    hidden = state 3) or an already-Skladom one (state 1) is never a candidate, so a
    residual unit on a discontinued product is never re-listed and a live product is
    never re-flipped (idempotent). A failed import is detected via the #23-hardened
    read-back and recorded status='error', not a silent success (the run never raises
    on an import failure — it degrades to a red row, like parovania_eshop). Reads
    ONLY its own store + the export; never touches the manager's decision stores."""
    csv_text = _read_export_for_links()          # same cp1250 export reader as #106
    now = datetime.now(timezone.utc).astimezone()
    candidates = stock_skladom.compute_candidates(csv_text)
    rows = import_builder.skladom_rows(candidates, CODE2PAIR)

    status = "ok"
    processed = updated = failed = None
    error_detail = ""
    if rows:
        if not _import_lock.acquire(blocking=False):
            log.warning("stock_skladom: iný import práve beží — beh preskočený")
            status, error_detail = "busy", "iný import práve beží"
        else:
            try:
                res = _import_rows_chunked(rows, import_builder.SKLADOM_COLS, False,
                                           prefix="skladom_", timeout=900)
            finally:
                _import_lock.release()
            processed, updated, failed = res["processed"], res["updated"], res["failed"]
            if res["ok"]:
                status = "ok"
            else:
                status = "error"
                # _chunk_error_msg already carries res["error_detail"]; add the
                # stderr tail only when there is no Shoptet reason to show
                tail = "" if res["error_detail"] else (res["err"] or "")[-300:]
                error_detail = _chunk_error_msg(res, len(rows)) + (f": {tail}" if tail else "")
                log.error("stock_skladom: import FAILED rc=%s chunks_ok=%d/%d stderr=%s",
                          res["rc"], res["chunks_ok"], res["chunks_total"],
                          (res["err"] or "")[-400:])

    # `candidates` are always stored (what WOULD be flipped) so the tab shows them
    # even on a failed import; on success they ARE what was flipped.
    with _lock:
        _save_stock_skladom({
            "last_check": now.isoformat(timespec="seconds"),
            "status": status,
            "candidates": candidates,
            "processed": processed, "updated": updated, "failed": failed,
            "error_detail": error_detail,
        })
    stats = {"candidates": len(candidates), "imported_rows": len(rows),
             "status": status, "processed": processed, "updated": updated,
             "failed": failed}
    log.info("stock_skladom: run done %s", stats)
    return stats


# --------------------------------------------------------------------------- #
# #105 — „Pripomienky objednávok" (migrated from n8n „Forestshop orders",
# MnskuiOdu3i5GKlF). Daily: „Vybavuje sa" orders older than 4 days →
#   • NO internal note (shopRemark empty)  → RED „nikto sa jej nedotkol" alert
#     (was Discord red) — no e-mail.
#   • HAS a note → AI-classify the note (contacted vs not) → if NOT contacted,
#     send ONE reminder e-mail to the customer (max once per order, deduped via
#     data/out/orders_reminder.json), ORANGE „pripomienka odoslaná" (was Discord
#     orange); if contacted, just log skipped_contacted.
# SENDS real customer e-mails + costs OpenAI → starts DISABLED (#93 contract).
# --------------------------------------------------------------------------- #
def _load_orders_reminder() -> dict:
    """Fail-CLOSED (#225): a corrupt dedup store aborts the run instead of quietly starting a new
    empty one (which would re-mail every open order). Display-only callers use the _display()
    variant below. Fail-closed is the DEFAULT on purpose — a call site added later inherits the
    safe behaviour rather than the dangerous one."""
    return _load_dedup_store(ORDERS_REMINDER_STATE, "odoslaných pripomienok", ("orders",))


def _load_orders_reminder_display() -> tuple:
    """Read-only DISPLAY variant → `(state, corrupt)` — see _load_posta_state_display()."""
    try:
        return _load_orders_reminder(), False
    except DedupStoreCorrupt:
        return {}, True


def _save_orders_reminder(d: dict) -> None:
    # ("orders",) — see _save_posta_state: the dedup map is one level down.
    _atomic_write_json(ORDERS_REMINDER_STATE, d, mode=0o600, protect=("orders",))


def _classify_contacted(shop_remark: str) -> bool:
    """OpenAI gpt-4o-mini classification of an order's internal shop note (#105) → True when the
    customer was ALREADY contacted (skip the reminder), False when NOT (send it). Mirrors the
    #106 supplier-scraper OpenAI call pattern: requests.post, JSON-object output, key in the
    Authorization header (never the URL → no secret in any error text). Requires OPENAI_API_KEY —
    the caller only reaches here when the key is present."""
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY nie je nastavený")
    payload = {"model": supplier_stock.LLM_MODEL,
               "messages": orders_reminder.build_classifier_messages(shop_remark),
               "temperature": 0,
               "response_format": {"type": "json_object"}}
    r = requests.post(OPENAI_URL, json=payload, timeout=OPENAI_TIMEOUT,
                      headers={"Authorization": f"Bearer {key}",
                               "Content-Type": "application/json"})
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"]
    return orders_reminder.parse_classification(content)


# The order's dedup record can be in one of three states. Only the two TERMINAL ones are a
# result; 'sending' is a TRANSIENT claim a manual override takes right before its SMTP call so a
# double-click cannot e-mail the customer twice (BUG 2). A crash between claim and send must
# never lock the order forever, so a claim older than SENDING_CLAIM_TTL_S counts as abandoned.
REMINDER_TERMINAL_STATUSES = ("emailed", "skipped_contacted")
SENDING_CLAIM_TTL_S = 600          # 10 min — far above the ~20 s SMTP timeout

# _claim's two failure modes are NOT the same event and must not share a return value (M1):
# None = somebody else legitimately owns the order right now (a manual send, or a verdict that
# landed meanwhile) — an ordinary skip; this sentinel = the claim could not be WRITTEN (full
# disk / permissions), which means the run mailed nobody for a reason the manager has to fix.
# Collapsing both into None is what let a completely dead run report `errors: 0`.
_CLAIM_WRITE_FAILED = object()


def _reminder_is_terminal(entry) -> bool:
    """True for a finally-resolved order — never re-classified, never re-mailed.
    Tolerates a garbage entry (partial write): anything that is not a dict is 'not resolved',
    so the order is simply processed again rather than crashing the run."""
    return isinstance(entry, dict) and entry.get("status") in REMINDER_TERMINAL_STATUSES


def _mark_manual(row: dict, entry) -> dict:
    """Carry the RECORD's `manual` flag onto its DISPLAY row (#227).

    A row the manager resolved by hand and one the AI ruled on both land in `skipped`, but they
    mean opposite things — and the tab used to render the whole list under „AI usúdilo, že
    zákazník je už kontaktovaný". For a manual row the classifier often never ran at all (no
    internal note, no OPENAI_API_KEY, no MAIL_BCC), so that heading stated something that never
    happened. The flag is what lets the tab split the two.

    Unlike `pending` — a per-RUN note that every resolving path strips — `manual` is a property
    of the RECORD, so it is re-derived from the record on every rebuild rather than carried
    along: that keeps it correct no matter which path produced the row, and self-heals a row
    copied forward from before the flag existed."""
    if isinstance(entry, dict) and entry.get("manual"):
        row["manual"] = True
    else:
        row.pop("manual", None)
    return row


def _reminder_unreadable(orders, code: str) -> bool:
    """True when a record for `code` EXISTS in the map but cannot be read (a partial write left a
    string / number / list / null under it).

    Takes the MAP, not the value: `orders.get(code)` returns None both for „no record" (the
    normal case for every new order) and for a JSON `null` record — and those two must never be
    confused. Only key PRESENCE tells them apart.

    An unreadable record is NOT „no record" and must never be treated as one: the order got a
    record because something happened to it, so reading it as „never mailed" is what
    re-classified and re-MAILED a customer who had already been reminded (PR #228 review). #225
    settled the direction for these stores — we cannot prove the mail did NOT go out, so the
    automation does not send and hands the row to the manager instead. A manual override on that
    row is still allowed: that is an explicit human decision with the order on screen."""
    return (isinstance(orders, dict) and code in orders
            and not isinstance(orders[code], dict))


def _reminder_claim_active(entry, now=None) -> bool:
    """True while a manual send for this order is genuinely IN FLIGHT (fresh 'sending' claim).
    An unparseable, expired or future-dated claim returns False — abandoned claims must be
    re-claimable. The age is bounded from BOTH sides for the same reason the terminal tracking
    cache bounds its `at`: a timestamp AHEAD of now (a clock/TZ step backwards, a partial write)
    would otherwise read as a live claim until real time catches up, and then the manager's
    „▶ Poslať pripomienku" 409s while the run skips the order — the reminder is silently never
    sent, and the TTL that exists so a claim can never lock an order forever is defeated."""
    if not isinstance(entry, dict) or entry.get("status") != "sending":
        return False
    now = now or datetime.now(timezone.utc).astimezone()
    try:
        claimed = datetime.fromisoformat(entry.get("claimed_at") or "")
    except (ValueError, TypeError):
        return False
    if claimed.tzinfo is None:
        claimed = claimed.replace(tzinfo=now.tzinfo)
    return 0 <= (now - claimed).total_seconds() < SENDING_CLAIM_TTL_S


def run_orders_reminder() -> dict:
    """One check run (daily 08:00 or „Spustiť teraz"): „Vybavuje sa" orders >4d from the app's
    cached orders export → red (no note) / AI-classified (with note) → one reminder e-mail per
    not-yet-contacted order, max once per order (data/out/orders_reminder.json). Returns the
    summary the runner stores; the full red/orange snapshot is persisted for the tab.

    Robust: a failing OpenAI or SMTP call for ONE order is logged and skipped (that order is not
    recorded → retried next run), never crashing the scheduler. No key → the AI branch degrades
    gracefully (never e-mails blind). Reads ONLY its own store + the orders export; never touches
    the manager's decision / to-order stores.

    Incremental (#153): an already-terminal order (emailed / manually-contacted) whose date+note
    fingerprint is unchanged since the last run is NOT re-classified or re-mailed — its previous
    display row is carried forward as-is (days refreshed). See
    orders_reminder.partition_incremental for the exact correctness contract (a newly-eligible or
    not-yet-terminal order is always fully (re)processed).

    Duplicate-mail safety: the dedup record of every send is persisted IMMEDIATELY (a failed
    write is logged with the order code, re-applied by the final save, and the run continues),
    the final save re-reads the `orders` map from disk instead of writing back the start-of-run
    snapshot (so a manual override written mid-run is never discarded), and the run CLAIMS each
    order it is about to classify+mail — the same transient 'sending' record the manual override
    takes — so a click landing in its OpenAI+SMTP window is rejected instead of mailing the
    customer a second time. An order with no customer e-mail is surfaced in `no_email` WITHOUT
    an AI call — it can never be mailed, so classifying it would buy a paid OpenAI call on every
    run forever."""
    # The dedup store is read FIRST — before the orders export and long before any OpenAI/SMTP
    # call. It is a free local check that can disqualify the whole run (#225: an unreadable store
    # means we cannot know who was already reminded, so nothing may be sent), and the „lacné
    # diskvalifikátory pred drahými volaniami" rule puts it ahead of the paid work.
    with _lock:
        state = _load_orders_reminder()
        # `orders` is dict-or-absent (the loader raises on a non-dict MAP — losing it means
        # mailing everyone twice). A garbage value UNDER a single code does NOT crash the run
        # either, but it is not read as „never mailed" — see _reminder_unreadable.
        done = dict(state.get("orders") or {})                              # code -> {status,…}
    csv_bytes = _orders_csv_cached()
    # #209 — the SAME configured „being processed" set the to-order tab uses. It is one
    # notion, not four: a renamed status must not leave this automation silently mailing
    # nobody while the tab is empty for the same reason (automation-health.md §3).
    #
    # …and the REASON comes with it (PR #295 review). The loader's „unusable set → measured
    # default" is fail-CLOSED for the prune (it refuses to delete) and was fail-OPEN here:
    # a corrupt file restored the built-in `to_order` and re-armed customer e-mails on
    # exactly the statuses the manager may have narrowed it to exclude. This is a mail to a
    # real customer, so it gets the prune's answer, not the tab's: render, but do not act.
    status_sets, bad_status_config = _order_statuses_state()
    bad_status_config = bool(bad_status_config)
    orders = orders_reminder.select_orders(
        csv_bytes, statuses=status_sets["to_order"])
    if bad_status_config:
        log.error("orders_reminder: nastavenie stavov objednávok sa nedá použiť "
                  "(%s) — NEPOSIELAM žiadne pripomienky zákazníkom, aby nedostali mail "
                  "kvôli stavu, ktorý manažér zo zoznamu vyradil; v zozname je teraz %d "
                  "objednávok podľa PREDVOLENÝCH stavov", ORDER_STATUSES, len(orders))
    # Every code in the export (not just the >4d „Vybavuje sa" ones) — the window the dedup
    # store is pruned against at the final save (#220). Empty = export unreadable → no pruning.
    window_codes = orders_reminder.all_order_codes(csv_bytes)
    prev_fp = state.get("fingerprints") or {}
    prev_orange = {r["code"]: r for r in state.get("orange") or []}
    prev_skipped = {r["code"]: r for r in state.get("skipped") or []}
    # Only a FINALLY resolved order takes the incremental fast path — a transient 'sending'
    # claim (a manual override mid-flight) is not a result, so it must never look like one.
    to_process, already_seen, fingerprints = orders_reminder.partition_incremental(
        orders, prev_fp, {c for c, v in done.items() if _reminder_is_terminal(v)})
    have_key = bool(os.environ.get("OPENAI_API_KEY"))
    # „BCC vždy" is BINDING for these customer mails (require_bcc below): with no MAIL_BCC not a
    # single reminder goes out. Surfaced in the stats so the tab shows a dead automation as dead
    # instead of a healthy-looking run that quietly mailed nobody (only an ERROR line before).
    bcc_missing = _mail_bcc() is None
    now_iso = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    run_token = secrets.token_hex(8)     # identifies THIS run's claims (never release a foreign one)
    red, orange, skipped, no_email = [], [], [], []
    # Orders this run started on but could NOT finish (M2). Every such branch used to just
    # `continue`, and because the display lists are rebuilt from scratch on every run, the order
    # vanished from the tab for the whole day — while the override endpoint, which only searches
    # red/orange/skipped, answered 404 „objednávka sa v aktuálnom zozname nenašla" for the row
    # the manager still had on screen. The next run heals it, but the manager has no way to act
    # meanwhile — on exactly the orders that need a human most. Collected here and merged into
    # `skipped` (the list whose row carries the note AND the „▶ Poslať pripomienku" action).
    pending = []
    failed_writes = set()                # codes whose immediate dedup write did not reach disk
    emailed_now = skipped_now = ai_unavailable = errors = 0

    def _pending_row(o: dict, why: str) -> dict:
        row = {k: o[k] for k in ("code", "billFullName", "email", "itemName",
                                 "shopRemark", "days", "admin_link")}
        row["pending"] = why             # rendered on the row so the tab explains itself
        return row

    def _persist_done(code: str, entry: dict) -> None:
        # persist the dedup record IMMEDIATELY — a crash later in the run must never lose a
        # sent-mail record (that would double-send tomorrow), mirroring run_posta_uncollected.
        done[code] = entry
        try:
            with _lock:
                st = _load_orders_reminder()
                st.setdefault("orders", {})[code] = entry
                _save_orders_reminder(st)
        except Exception as e:  # noqa: BLE001 — full disk / permissions
            # The mail ALREADY went out but its record did not reach disk. Remember the code:
            # the final save re-reads `orders` from disk (the lost-update fix), so without a
            # re-apply this record would be silently DISCARDED and the next run would e-mail the
            # same customer again. Log it for manual follow-up and keep the run going — aborting
            # here would leave the remaining orders unprocessed too.
            failed_writes.add(code)
            log.error("orders_reminder: obj. %s vybavená (%s), ale zápis stavu ZLYHAL (%r) — "
                      "skúsim ho znova pri záverečnom uložení, skontroluj ručne",
                      code, entry.get("status"), e)

    def _claim(code: str, email: str):
        """Take the transient 'sending' claim BEFORE this run's own OpenAI+SMTP window. Without
        it the run is invisible on disk for the whole ~20 s round-trip, so a manual „▶ Poslať
        pripomienku" click landing in that window passes the 409 gate, claims, and mails — and
        the run mails too (PR #223 review).

        The claim is its OWN lock acquisition with its OWN re-read of the record inside (NOT a
        continuation of the caller's fresh per-order read — that lock is already released by the
        time we get here). That is what makes it correct: the state is re-checked under the same
        lock that writes the claim, so nothing can slip in between the check and the write. The
        caller's earlier read is only there to decide whether this order is worth working on.
        Returns the claim entry, None when someone else legitimately won the order meanwhile, or
        _CLAIM_WRITE_FAILED when the claim could not be written. A claim that did not reach disk
        is no protection at all, so the order is SKIPPED (retried next run) rather than mailed
        unclaimed: for a customer mail, sending nothing beats sending twice — but the caller
        counts that as an ERROR (M1), because a run whose claims all fail mails nobody and must
        never look like a healthy, quiet day on the tab."""
        entry = {"status": "sending",
                 # a FRESH timestamp per claim — `now_iso` is the start of the run, and a long
                 # run would hand out claims that already look expired
                 "claimed_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                 "claim": run_token, "email": email, "run": True}
        try:
            with _lock:
                st = _load_orders_reminder()
                cur_map = st.get("orders") or {}
                cur = cur_map.get(code)
                # …_reminder_unreadable too: defence in depth. The caller already skips such an
                # order, but a claim must never overwrite a record we could not read.
                if (_reminder_claim_active(cur) or _reminder_is_terminal(cur)
                        or _reminder_unreadable(cur_map, code)):
                    return None
                st.setdefault("orders", {})[code] = entry
                _save_orders_reminder(st)
        except Exception as e:  # noqa: BLE001 — full disk / permissions; never crash the run
            log.error("orders_reminder: nárok na obj. %s sa nepodarilo zapísať (%r) — "
                      "objednávku preskakujem, skúsim ju v ďalšom behu", code, e)
            return _CLAIM_WRITE_FAILED
        done[code] = entry
        return entry

    def _release(code: str, prev) -> None:
        """Give the order back after OUR claim produced no result (a failed classification or
        send) — only when the claim on disk is still ours, so a concurrent manual override that
        resolved the order meanwhile is never clobbered. Without this a transient failure would
        lock the order until the TTL lapses."""
        try:
            with _lock:
                st = _load_orders_reminder()
                cur = (st.get("orders") or {}).get(code)
                if not isinstance(cur, dict) or cur.get("claim") != run_token:
                    return
                if isinstance(prev, dict):
                    st["orders"][code] = prev
                else:
                    st.get("orders", {}).pop(code, None)
                _save_orders_reminder(st)
        except Exception as e:  # noqa: BLE001 — full disk / permissions; the TTL is the backstop
            log.error("orders_reminder: uvoľnenie nároku na obj. %s zlyhalo (%r) — vyprší "
                      "sám o %s s", code, e, SENDING_CLAIM_TTL_S)
        if isinstance(prev, dict):
            done[code] = prev
        else:
            done.pop(code, None)

    # unchanged + already-terminal orders: reuse the last computed display row (days refreshed)
    # — no re-classification, no OpenAI/SMTP call, no CSV-field rebuild (the incremental fast path).
    for o in already_seen:
        code = o["code"]
        entry = done.get(code) or {}
        status = entry.get("status")
        prev_row = (prev_orange if status == "emailed" else prev_skipped).get(code)
        row = dict(prev_row) if prev_row else {k: o[k] for k in
                                               ("code", "billFullName", "email", "itemName",
                                                "shopRemark", "days", "admin_link")}
        row["days"] = o["days"]
        _mark_manual(row, entry)        # who resolved it (#227) — re-derived, never assumed
        # An order only reaches the fast path once it is TERMINAL, so a „run could not finish
        # it" note from an earlier run is stale by definition — carried forward it would warn
        # about a resolved order forever (found in the PR #224 adversarial review).
        row.pop("pending", None)
        (orange if status == "emailed" else skipped).append(row)

    for o in to_process:
        code = o["code"]
        # Decide from the CURRENT record on disk, never from `done` (the start-of-run
        # snapshot): this run spends minutes in OpenAI + SMTP, and an override the manager
        # made in that window — resolving the order, or claiming it for a manual send — must
        # be seen here, or the run mails a customer who was just handled. This is the READ
        # side of the same lost update whose WRITE side the final save fixes.
        # It sits ABOVE the no-note (RED) branch on purpose: an order resolved mid-run must not
        # be re-listed as unhandled either, or the manager's next click on that row 409s.
        with _lock:
            orders_now = _load_orders_reminder().get("orders") or {}
        prev = orders_now.get(code)
        if prev is not None:
            done[code] = prev                       # keep the snapshot consistent with disk
        if _reminder_unreadable(orders_now, code):
            # A record exists but is garbage → we cannot prove this customer was NOT already
            # reminded, so we do not remind them (#225's rule, applied per RECORD). Surfaced as
            # pending so the row stays on the tab WITH „▶ Poslať pripomienku" — the manager
            # decides, the automation does not guess.
            log.error("orders_reminder: obj. %s má POŠKODENÝ záznam v evidencii (%r) — "
                      "pripomienku NEposielam (mohla by byť druhá), skontroluj ručne",
                      code, prev)
            errors += 1
            pending.append(_pending_row(
                o, "poškodený záznam v evidencii — neposielam, aby zákazník nedostal "
                   "druhý mail; over ručne"))
            continue
        if _reminder_claim_active(prev):
            # a manual override is talking to SMTP for this order RIGHT NOW — don't race it
            log.info("orders_reminder: obj. %s má rozrobené ručné odoslanie — preskakujem", code)
            pending.append(_pending_row(o, "práve prebieha ručné odoslanie"))
            continue
        if _reminder_is_terminal(prev):             # already resolved — reflect its status
            row = {k: o[k] for k in ("code", "billFullName", "email", "itemName",
                                     "shopRemark", "days", "admin_link")}
            row["sent_date"] = prev.get("date", "")
            _mark_manual(row, prev)                 # …and BY WHOM (#227)
            (orange if prev.get("status") == "emailed" else skipped).append(row)
            continue
        if not o["has_note"]:
            red.append({k: o[k] for k in ("code", "billFullName", "phone", "email",
                                          "itemName", "days", "admin_link")})
            continue
        if not o["email"]:
            # No address → the reminder can NEVER be sent → the order never becomes terminal.
            # Classifying it would burn a paid OpenAI call on EVERY run, forever, for nothing;
            # surface it instead so the manager can fill the address in (#BUG 4).
            log.warning("orders_reminder: obj. %s nemá e-mail — pripomienku nemožno poslať, "
                        "AI klasifikáciu preskakujem (doplň e-mail v Shoptete)", code)
            no_email.append({k: o[k] for k in ("code", "billFullName", "phone", "email",
                                               "itemName", "shopRemark", "days", "admin_link")})
            continue
        # Both remaining gates are CONFIG gaps and both are free to check, so they come before
        # the claim and the paid OpenAI call. The key is checked first purely so `ai_unavailable`
        # stays truthful: ordering it after the BCC gate would report 0 „AI nedostupné" whenever
        # BCC also happened to be missing, hiding a second config gap behind the first.
        if not have_key:
            ai_unavailable += 1
            log.warning("orders_reminder: obj. %s má poznámku, ale OPENAI_API_KEY nie je "
                        "nastavený — AI nedostupné, pripomienku neposielam (skúsim ďalší beh)", code)
            pending.append(_pending_row(
                o, "AI klasifikácia nedostupná — chýba OPENAI_API_KEY"))
            continue                                # do NOT record → retried when key is present
        if bcc_missing:
            # „BCC vždy" is BINDING for this customer mail (require_bcc below), so the send WILL
            # be refused — and the order never becomes terminal, so it would be claimed and
            # classified again on every run, forever, for a mail that can never go out. That is
            # exactly the „drahé volanie až po lacných diskvalifikátoroch" rule: a missing
            # config line is free to check, an OpenAI call is not. The tab already renders the
            # bcc_missing warning; the row below says which orders are waiting on it (M3).
            log.warning("orders_reminder: obj. %s — MAIL_BCC nie je nastavené (data/.mail_env), "
                        "pripomienku nemožno odoslať; AI klasifikáciu preskakujem", code)
            pending.append(_pending_row(
                o, "chýba MAIL_BCC v data/.mail_env — pripomienka sa neodošle"))
            continue                                # no claim, no OpenAI call, nothing recorded
        if bad_status_config:
            # PR #295 review — the third config gate, and the only one whose failure would
            # mail the WRONG PEOPLE rather than nobody. It is deliberately last of the
            # three so `ai_unavailable` and `bcc_missing` keep reporting their own gaps
            # honestly; like them it costs nothing, takes no claim and buys no OpenAI call.
            pending.append(_pending_row(
                o, "nastavenie stavov objednávok sa nedá prečítať — pripomienky sa "
                   "neposielajú, kým sa to neopraví (karta „Stavy objednávok\")"))
            continue
        # From here the run talks to OpenAI + SMTP for this order — claim it first so a manual
        # send clicked in that window is rejected (409) instead of mailing the customer twice.
        claimed = _claim(code, o["email"])
        if claimed is _CLAIM_WRITE_FAILED:
            errors += 1                             # a run that claims nothing mails nobody (M1)
            pending.append(_pending_row(
                o, "nárok na odoslanie sa nepodarilo zapísať — skúsim v ďalšom behu"))
            continue
        if claimed is None:
            log.info("orders_reminder: obj. %s si medzitým vzal niekto iný — preskakujem", code)
            pending.append(_pending_row(o, "objednávku si medzitým vzal niekto iný"))
            continue
        try:
            contacted = _classify_contacted(o["shopRemark"])
        except Exception as e:  # noqa: BLE001 — recorded per order, run continues
            errors += 1
            _release(code, prev)                    # claim must not outlive the attempt
            log.error("orders_reminder: klasifikácia obj. %s zlyhala: %r", code, e)
            pending.append(_pending_row(o, "AI klasifikácia zlyhala — skúsim v ďalšom behu"))
            continue
        base = {"name": o["billFullName"], "email": o["email"],
                "itemName": o["itemName"], "note": o["shopRemark"], "date": now_iso}
        if contacted:
            _persist_done(code, {**base, "status": "skipped_contacted"})
            skipped_now += 1
            log.info("orders_reminder: obj. %s — AI: zákazník už kontaktovaný, skipped_contacted", code)
            row = {k: o[k] for k in ("code", "billFullName", "email", "itemName",
                                     "shopRemark", "days", "admin_link")}
            row["sent_date"] = now_iso
            skipped.append(row)
            continue
        subject, html = orders_reminder.build_reminder_email(o["billFullName"], code)
        # bcc omitted → _send_mail_html defaults it to MAIL_BCC (the „BCC vždy" convention #105);
        # require_bcc makes it BINDING for a real customer mail — no owner copy, no send.
        if _send_mail_html(o["email"], subject, html, require_bcc=True):
            _persist_done(code, {**base, "status": "emailed"})
            emailed_now += 1
            log.info("orders_reminder: obj. %s — pripomienka odoslaná zákazníkovi %s", code, o["email"])
            row = {k: o[k] for k in ("code", "billFullName", "email", "itemName",
                                     "shopRemark", "days", "admin_link")}
            row["sent_date"] = now_iso
            orange.append(row)
        else:
            errors += 1                             # SMTP failed → not recorded → retried next run
            _release(code, prev)                    # claim must not outlive the attempt
            # „mail neodišiel" is a guarantee, not a hedge: _smtp_deliver reports success once
            # sendmail() returns (a failing quit() after delivery is NOT a failure) and treats a
            # rejected CUSTOMER address as the only send failure — so False here means the
            # customer really did not get it, and clicking „▶ Poslať pripomienku" is safe.
            pending.append(_pending_row(
                o, "odoslanie e-mailu zlyhalo (mail neodišiel) — skúsim v ďalšom behu"))

    with _lock:
        # `done` is a snapshot taken at the START of the run and this run spends minutes in
        # OpenAI + SMTP — writing it back wholesale would DISCARD every dedup record the
        # manager's overrides wrote meanwhile (lost update → that customer gets a duplicate
        # mail on the next run). `_persist_done` already wrote this run's own records
        # immediately, so the on-disk `orders` map is the authoritative one: re-read it and
        # replace only the DISPLAY fields.
        st = _load_orders_reminder()
        # `orders` is guaranteed dict-or-absent by the loader's dedup_keys guard — a non-dict map
        # raises long before this point, so the fallback below can only fire when the map is
        # ABSENT (the file vanished mid-run: deleted by hand, a cleanup job, a botched restore).
        # It MUST fall back to `dict(done)` and never to `{}`: `done` is the start-of-run snapshot
        # PLUS every record this run persisted, so it is never less complete than a missing map,
        # while `{}` would write an empty dedup history back and make the next run re-mail every
        # customer served so far (PR #228 review F3, regression test in
        # test_a_store_that_vanishes_mid_run_keeps_the_dedup_history).
        orders_map = st.get("orders") or dict(done)
        # …but a record whose immediate write FAILED never reached that map, so re-apply it ON
        # TOP — otherwise the lost-update fix silently throws away the proof that a mail really
        # was sent, and the next run re-sends it. Only a record that is still non-terminal on
        # disk (typically our own 'sending' claim, or nothing at all) is overwritten: a
        # concurrent override that reached a terminal verdict meanwhile keeps winning.
        for c in failed_writes:
            if c in done and not _reminder_is_terminal(orders_map.get(c)):
                orders_map[c] = done[c]

        # …and only now bound the map (#220): it used to be written back whole on every run, so
        # it only ever grew. Pruning happens AFTER the re-apply above so a just-written record
        # is judged on its own merits, and it never touches a code the export still carries —
        # dropping one of those is what would re-mail a customer. See orders_reminder.prune_done
        # for the full rule set, including the deliberate REOPEN decision (a reopened order
        # keeps its record and is not reminded twice; a manual send from the tab still works).
        orders_map, pruned_codes = orders_reminder.prune_done(orders_map, window_codes)
        if pruned_codes:
            # Never drop a dedup record silently: if a customer ever gets a second reminder,
            # this line is the only place that can show whether pruning was the cause.
            log.info("orders_reminder: z dedup evidencie vypadlo %d starých objednávok "
                     "(mimo exportu a staršie než %d dní): %s", len(pruned_codes),
                     orders_reminder.DEDUP_RETENTION_DAYS, ", ".join(sorted(pruned_codes)[:20]))

        # Display lists are computed from the start-of-run snapshot and written wholesale, so an
        # order the manager resolved WHILE the run was working would come back onto the tab as
        # unhandled — and their next click on it would 409. Move any such row to the list its
        # new status belongs to. (`no_email` rows are not reachable from the override endpoint
        # today; filtering them too costs nothing and keeps the two lists consistent.)
        resolved = {c: v for c, v in orders_map.items() if _reminder_is_terminal(v)}

        def _relocate(rows):
            keep = []
            for r in rows:
                ent = resolved.get(r.get("code"))
                if ent is None:
                    keep.append(r)
                    continue
                dest = orange if ent.get("status") == "emailed" else skipped
                if r.get("code") not in {x.get("code") for x in dest}:
                    # drop `pending`: the order IS resolved now, so carrying the „run could not
                    # finish it" note across would leave a permanent warning on a finished row
                    # (and keep inflating the „z toho N nedokončených" heading every run).
                    # `manual` is the opposite kind of field — a property of the RECORD — so it
                    # is (re)applied from the entry that resolved the order (#227).
                    dest.append(_mark_manual(
                        {k: v for k, v in r.items() if k != "pending"}
                        | {"sent_date": ent.get("date", "")}, ent))
            return keep

        red = _relocate(red)
        no_email = _relocate(no_email)
        # Orders the run could not finish (M2) join `skipped` — the list whose row shows the
        # note AND offers „▶ Poslať pripomienku", so the manager can finish the job by hand.
        # Through _relocate first: one of them may have been resolved by an override in the very
        # window that made the run give up, and then it belongs in orange/skipped by its status.
        skipped.extend(_relocate(pending))

        stats = {"orders_4d": len(orders), "no_note": len(red),
                 "with_note": len(orders) - len(red),
                 "emailed_now": emailed_now, "emailed_total": 0,
                 "skipped_now": skipped_now, "ai_unavailable": ai_unavailable,
                 "no_email": len(no_email), "errors": errors,
                 "bcc_missing": bcc_missing,
                 # PR #295 review — a run that cannot read its own configuration has
                 # FAILED even though it did not throw. `bad_status_config` names the
                 # cause for the card's banner; `source_degraded` is the EXISTING flag the
                 # ⚠ nav badge already reads, so the signal reaches the side menu without a
                 # second path every future automation would have to remember
                 # (automation-health §3, the `autoByKey('posta')` lesson).
                 "bad_status_config": bad_status_config,
                 "source_degraded": bad_status_config}
        stats["emailed_total"] = sum(1 for v in orders_map.values()
                                     if isinstance(v, dict) and v.get("status") == "emailed")
        st.update({
            "orders": orders_map,
            "last_check": now_iso,
            "red": red, "orange": orange, "skipped": skipped, "no_email": no_email,
            "stats": stats,
            "fingerprints": fingerprints,   # #153 — incremental-run cache for the next run
        })
        _save_orders_reminder(st)
    log.info("orders_reminder: run done %s", stats)
    return stats


# --------------------------------------------------------------------------- #
# #135 — "our_images: periodicky validovať/čistiť mŕtve forestshop-CDN URL":
# periodic HTTP validation of OUR OWN review-card image URLs (our_images —
# cdn.myshoptet.com product photos). A genuinely dead URL handed straight to
# the browser makes Chrome log "Failed to load resource" to the console
# regardless of the #50/#74 onerror placeholder (that handler hides the
# broken image visually but can't suppress the browser's own network-error
# log). The only real fix is to never SERVE a dead URL in the first place —
# this automation only MAINTAINS the persistent per-URL health cache
# (data/out/image_health.json); /api/products applies it at REQUEST time
# (image_health.clean_products) so a review card never renders a URL
# confirmed dead. review_data.json itself is never rewritten by this run.
# --------------------------------------------------------------------------- #
IMAGE_HEALTH_STATE = _store("image_health.json")
# Short timeout (mirrors /api/images' 8s fast-fail #74) — a hung supplier/CDN
# must not tie up the whole run; it's simply recorded as a failed check.
IMAGE_HEALTH_TIMEOUT = 8


def _load_image_health() -> dict:
    try:
        with open(IMAGE_HEALTH_STATE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_image_health(d: dict) -> None:
    _atomic_write_json(IMAGE_HEALTH_STATE, d, indent=None, mode=0o600)


def _check_image_url(url: str) -> bool:
    """Is `url` still a live image? HEAD first (cheap, no body transfer); a
    host that doesn't support HEAD (405/501 — some CDNs) gets a byte-range
    GET fallback instead of being marked dead on a technicality. ANY
    exception (timeout, DNS failure, connection reset, TLS error, …) is
    treated as NOT ok — a failed check IS the dead-image signal, never a
    crash (mirrors the try/except-degrade shape of every other automation's
    per-item network call)."""
    try:
        r = requests.head(url, headers={"User-Agent": UA},
                          timeout=IMAGE_HEALTH_TIMEOUT, allow_redirects=True)
        if r.status_code in (405, 501):
            r = requests.get(url, headers={"User-Agent": UA, "Range": "bytes=0-0"},
                             timeout=IMAGE_HEALTH_TIMEOUT, stream=True, allow_redirects=True)
        return r.ok
    except Exception as e:  # noqa: BLE001 — per-URL error is the dead signal; run continues
        log.warning("image_health: check failed url=%s: %r", url, e)
        return False


def run_image_health() -> dict:
    """One validation run (daily or "Spustiť teraz"): collect every
    our_images URL currently in review_data.json (PRODUCTS), HEAD-check the
    ones due (image_health.needs_check skips a URL confirmed healthy within
    the freshness window, but ALWAYS retries one whose last result was a
    failure), and update the persistent per-URL cache — 2 CONSECUTIVE
    failures before an image is treated as dead (image_health.DEAD_AFTER_FAILS),
    so a transient CDN blip never wipes a genuinely-good image. Cache entries
    for URLs no longer referenced by any product are pruned (catalog drift)
    so the store doesn't grow forever. WRITES NOTHING to review_data.json or
    any manager decision store — /api/products is what filters, at request
    time, from this cache alone."""
    with _lock:
        products_snapshot = list(PRODUCTS)
    urls = image_health.collect_image_urls(products_snapshot)
    cache = dict(_load_image_health().get("cache") or {})
    now = datetime.now(timezone.utc).astimezone()
    checked = skipped = ok_n = fail_n = 0
    for url in urls:
        if not image_health.needs_check(url, cache, now):
            skipped += 1
            continue
        ok = _check_image_url(url)
        image_health.record_result(cache, url, ok, now)
        checked += 1
        if ok:
            ok_n += 1
        else:
            fail_n += 1
    live = set(urls)
    cache = {u: e for u, e in cache.items() if u in live}
    _, cleaned_images = image_health.clean_products(products_snapshot, cache)
    dead_urls = sum(1 for e in cache.values() if image_health.is_dead(e))
    stats = {"total_urls": len(urls), "checked": checked, "skipped": skipped,
             "ok": ok_n, "failed": fail_n, "dead_urls": dead_urls,
             "cleaned_images": cleaned_images}
    with _lock:
        _save_image_health({"cache": cache, "stats": stats,
                            "last_check": now.isoformat(timespec="seconds")})
    log.info("image_health: run done %s", stats)
    return stats


# --------------------------------------------------------------------------- #
# „Poľovnícke výstavy" (#111) — the 3 background chains, migrated from n8n. All
# default-OFF (#93). Chain A rozposiela otázky (SEND), chains B/D čítajú IMAP a
# posúvajú stav. Chain C (Ideme) is the manual /api/vystava/ideme button, not here.
# --------------------------------------------------------------------------- #
def run_vystavy_otazka() -> dict:
    """Chain A (daily 06:00, default OFF): send the intro question to every výstava
    that is Nová (status ""), sposob=email, has an e-mail, and whose kedy_riesit ==
    the current sk-SK month name. Advances status→otazka + stores the msgid + feed.
    SMTP runs OUTSIDE the store lock; each result is persisted under the lock with a
    re-check (a manual edit meanwhile must not be clobbered)."""
    month = _sk_month_now()
    with _lock:
        all_vystavy = _load_vystavy()
        snapshots = [dict(v) for v in all_vystavy
                     if v.get("status") == VY_NEW and v.get("sposob") == "email"
                     and (v.get("email") or "").strip()
                     and (v.get("kedy_riesit") or "").strip().casefold() == month]
    # výstavy skipped this run (wrong month / not-new / pdf / no e-mail) — the spec
    # summary shape is {poslane, preskocene} (design.md:145); the rest is a superset.
    preskocene = len(all_vystavy) - len(snapshots)
    poslane = zlyhane = 0
    for snap in snapshots:
        subject, mbody = _vy_otazka_mail(snap)
        msgid = _send_vystava_mail(snap["email"].strip(), subject, mbody)   # outside lock
        if not msgid:
            zlyhane += 1
            continue
        with _lock:
            vystavy = _load_vystavy()
            v = _vy_find(vystavy, snap["id"])
            if not v or v.get("status") != VY_NEW:
                continue                              # changed meanwhile → skip
            v["status"] = VY_OTAZKA
            v["email_otazka_msgid"] = msgid
            v["email_datum"] = _now_iso()
            _vy_feed(v, "otazka_poslana",
                     f"Automaticky poslaná otázka organizátorovi ({snap['email'].strip()}).")
            _save_vystavy(vystavy)
            poslane += 1
    result = {"poslane": poslane, "preskocene": preskocene, "zlyhane": zlyhane,
              "mesiac": month, "kandidati": len(snapshots)}
    log.info("vystavy_otazka: %s", result)
    return result


def _vystavy_check_replies(awaited_status: str, msgid_field: str,
                           new_status: str, feed_typ: str) -> int:
    """Shared body of chains B/D: fetch the inbox (OUTSIDE the lock — it is I/O), match
    replies to výstavy waiting in `awaited_status`, advance each to `new_status` and feed
    the trimmed reply excerpt. Returns the number of state advances."""
    msgs = vystavy_imap.fetch_inbox()          # outside the lock (network)
    najdene = 0
    with _lock:
        vystavy = _load_vystavy()
        for vid, excerpt in vystavy_imap.match_reply(msgs, vystavy, awaited_status, msgid_field):
            v = _vy_find(vystavy, vid)
            if not v or v.get("status") != awaited_status:
                continue
            v["status"] = new_status
            v["email_datum"] = _now_iso()
            _vy_feed(v, feed_typ,
                     f"Prišla odpoveď: {excerpt}" if excerpt else "Prišla odpoveď od organizátora.")
            najdene += 1
        if najdene:
            _save_vystavy(vystavy)
    return najdene


def run_vystavy_odpoved_otazka() -> dict:
    """Chain B (daily 09:00, default OFF): IMAP-check replies to the sent question. A
    reply threaded on a výstava's email_otazka_msgid advances it otazka → akcia bude."""
    result = {"najdene": _vystavy_check_replies(
        VY_OTAZKA, "email_otazka_msgid", VY_AKCIA, "odpoved_otazka")}
    log.info("vystavy_odpoved_otazka: %s", result)
    return result


def run_vystavy_odpoved_prihlaska() -> dict:
    """Chain D (daily 09:30, default OFF): IMAP-check replies to the sent application. A
    reply threaded on email_ziadost_msgid advances poziadane → odpovedane od organizatora."""
    result = {"najdene": _vystavy_check_replies(
        VY_POZIADANE, "email_ziadost_msgid", VY_HOTOVO, "odpoved_prihlaska")}
    log.info("vystavy_odpoved_prihlaska: %s", result)
    return result


# Plain-language "what it does + when it runs" for each automation (#173) — shown
# in its tab so the manager doesn't have to guess from the name alone. Written from
# the actual run_<key>() behavior (docstrings above), not from the name/schedule —
# keep this in sync when a run_<key>() behavior changes.
AUTOMATION_DESCRIPTIONS = {
    "posta_uncollected":
        "Denne o 9:00 skontroluje sledovacie čísla zásielok na Pošte SK, nájde "
        "nevyzdvihnuté balíky a postupne posiela zákazníkom upozorňovacie e-maily.",
    "shoptet_sync":
        "Každú hodinu stiahne objednávky (posledných 90 dní) a celý katalóg zo "
        "Shoptetu a podľa toho prestaví vyhľadávací index aj naše ceny/sklad na "
        # #212 corrected `run_shoptet_sync`'s docstring but not THIS string, which is the
        # one the manager actually reads on the card — and it promised the very thing the
        # prune stopped being true: it does now remove his markings, just only the ones the
        # tab could never show him again.
        "review kartách. Zmaže pritom značky pri riadkoch tých objednávok, ktoré sú už "
        "vybavené a na tabe sa nedajú zobraziť — nič iné z tvojej práce nemení.",
    "shoptet_upload":
        # #299 review I2: zapisovanie automatizácií do tabuľky čakajúcich zmien
        # ešte len pribúda (Tasky 8-10, jedna za druhou) — dnes tabuľka zostáva
        # prázdna a tento popis nesmie tvrdiť, že ju niekto napĺňa, kým to tak
        # naozaj nie je.
        "Každú hodinu stiahne čerstvý stav zo Shoptetu, jedným importom nahrá do "
        "eshopu všetko, čo je zapísané v tabuľke čakajúcich zmien, a potom stav "
        "stiahne znova. Nahraté označí až vtedy, keď to Shoptet potvrdí; čo eshop "
        "v katalógu nemá, ostane čakať a je to tu vidieť. Automatizácie do tejto "
        "tabuľky zatiaľ nezapisujú — prechádzajú na ňu postupne, jedna po druhej.",
    "parovania_eshop":
        "Denne o 21:00 nahrá nové napárované produkty a doplnených dodávateľov do "
        "Shoptet eshopu — zapíše doobjednávacie odkazy do poznámky produktu.",
    "grube_externalcode":
        "Denne o 3:30 nahrá do Shoptet eshopu kódy dodávateľa GRUBE pre jednotlivé "
        "veľkosti (do poľa externalCode). Nahrá len nové alebo zmenené kódy — čo už "
        "raz nahrala, znova neposiela.",
    "split_links":
        "Denne o 3:45 nahrá do Shoptet eshopu odkazy na jednotlivé veľkosti pri "
        "produktoch rozdelených na veľkosti (do internej poznámky produktu). Nahrá "
        "len nové alebo zmenené odkazy — čo už raz nahrala, znova neposiela.",
    "dodavatelsky_sklad":
        "Denne o 5:00 prejde weby dodávateľov (pri nejasnej dostupnosti pomôže AI) "
        "a zistí, čo majú skladom a za akú cenu.",
    "riziko_vypadku":
        "Denne o 6:15 porovná náš sklad s dodávateľským skladom (dáta z "
        "automatizácie „Dodávateľský sklad“) a upozorní na produkty, ktoré máme "
        "skladom, ale dodávateľ ich už nemá — hrozí výpadok.",
    "restock_skladom":
        "Denne o 6:00 nájde produkty, ktoré máme označené ako Vypredané, ale "
        "dodávateľ ich má opäť skladom, a rovno ich naskladní naspäť v eshope.",
    "stock_skladom":
        "Denne o 6:45 nájde produkty, ktoré fyzicky máme na sklade (Shoptet ukazuje "
        "kusy skladom), ale zákazníkom sa stále zobrazujú ako Vypredané, a rovno ich "
        "prepne na Skladom. Nedotýka sa produktov, ktoré ste vedome ukončili.",
    "orders_reminder":
        "Denne o 8:00 skontroluje objednávky vo vybavovaní dlhšie ako 4 dni — bez "
        "poznámky len upozorní (žiadny mail), s poznámkou AI vyhodnotí, či bol "
        "zákazník kontaktovaný, a ak nie, pošle mu pripomienkový e-mail (max. raz "
        "na objednávku).",
    "image_health":
        "Pravidelne overí, či produktové fotky na našich kartách ešte fungujú, a "
        "mŕtve odkazy skryje z karty (samo sa opraví, keď fotka zase ožije).",
    "vystavy_otazka":
        "Denne o 6:00 rozpošle úvodnú otázku organizátorom výstav, ktorých mesiac "
        "riešenia je práve teraz — spýta sa, či sa podujatie tento rok koná.",
    "vystavy_odpoved_otazka":
        "Denne o 9:00 skontroluje e-mailovú schránku, či organizátor odpovedal na "
        "úvodnú otázku, a ak áno, posunie výstavu do stavu „čaká na rozhodnutie“.",
    "vystavy_odpoved_prihlaska":
        "Denne o 9:30 skontroluje e-mailovú schránku, či organizátor potvrdil "
        "prihlášku, a ak áno, označí výstavu ako potvrdenú.",
}

AUTOMATIONS_REG = [
    Automation(key="posta_uncollected",
               name="Nevyzdvihnuté zásielky — Pošta SK",
               schedule={"daily_at": "09:00", "tz": "Europe/Bratislava"},
               run_fn=run_posta_uncollected),
    # #119 — hourly guaranteed refresh of the orders export + full catalog export.
    # SAFETY (#93 contract): starts DISABLED like every automation; the manager
    # clicks ▶ Štart. Passive/read-only (no e-mails, no customer side-effects),
    # so it is SAFE to enable immediately once deployed — the deploy itself just
    # never auto-enables anything on its own.
    Automation(key="shoptet_sync",
               name="Sync zo Shoptetu",
               schedule={"interval_minutes": 60, "tz": "Europe/Bratislava"},
               run_fn=run_shoptet_sync),
    # #299 — the write-side counterpart of shoptet_sync. Starts DISABLED (#93):
    # it pushes to the live eshop, so the manager turns it on himself.
    Automation(key="shoptet_upload",
               name="Sync do Shoptetu",
               schedule={"interval_minutes": 60, "tz": "Europe/Bratislava"},
               run_fn=run_shoptet_upload),
    # #109 — nightly push of new pairings + assigned suppliers to the Shoptet
    # eshop (migrated from n8n YuDugCCOnwejRfva). SAFETY (#93 contract): starts
    # DISABLED — this one WRITES to the live production eshop, so it runs ONLY
    # after the manager clicks ▶ Štart; a deploy never auto-pushes on its own.
    Automation(key="parovania_eshop",
               name="Párovania → eshop",
               schedule={"daily_at": "21:00", "tz": "Europe/Bratislava"},
               run_fn=run_parovania_eshop),
    # #62 — nightly push of GRUBE per-size externalCodes (grube itemId → eshop
    # `externalCode`), the cron follow-up to the MVP manual zip. SAFETY (#93
    # contract): starts DISABLED — this one WRITES to the live production eshop, so
    # it runs ONLY after the manager clicks ▶ Štart; a deploy never auto-pushes on
    # its own. DELIBERATELY a separate automation (not folded into „Párovania →
    # eshop", which is already enabled on prod) so enabling the externalCode write
    # stays an explicit opt-in. Scheduled 03:30, well clear of the 21:00 pairings
    # push and the morning restock window.
    Automation(key="grube_externalcode",
               name="GRUBE kódy → eshop",
               schedule={"daily_at": "03:30", "tz": "Europe/Bratislava"},
               run_fn=run_grube_externalcode),
    # #192 — nightly push of the per-size SPLIT links (#174 „✂ Rozdeliť na veľkosti":
    # a product whose supplier lists a different URL per size) to the eshop
    # internalNote, per variant. SAFETY (#93 contract): starts DISABLED — this one
    # WRITES to the live production eshop, so it runs ONLY after the manager clicks
    # ▶ Štart; a deploy never auto-pushes on its own. DELIBERATELY a separate
    # automation (not folded into „Párovania → eshop", which is already enabled on
    # prod) — a split decision carries no decision URL (its links live in
    # variant_links.json per variant), so the pairings push never handles it; enabling
    # the split-link write stays an explicit opt-in. Scheduled 03:45, just after the
    # 03:30 GRUBE externalCode push.
    Automation(key="split_links",
               name="Veľkostné linky → eshop",
               schedule={"daily_at": "03:45", "tz": "Europe/Bratislava"},
               run_fn=run_split_links),
    # #106 — daily supplier availability/price scraper. SAFETY (#93 contract):
    # starts DISABLED — a run makes MANY external HTTP calls AND costs money via
    # OpenAI, so it runs ONLY after the manager clicks ▶ Štart; a deploy never
    # scrapes or spends on its own.
    Automation(key="dodavatelsky_sklad",
               name="Dodávateľský sklad",
               schedule={"daily_at": "05:00", "tz": "Europe/Bratislava"},
               run_fn=run_supplier_stock),
    # #107 — daily supply-risk report (products we still show as Skladom but our
    # supplier has sold out). SAFETY (#93 contract): starts DISABLED like every
    # other automation, for consistency — even though it is purely READ-ONLY /
    # advisory (no e-mail, no eshop write, no cost) it runs ONLY after the manager
    # clicks ▶ Štart, same as shoptet_sync; a deploy never auto-enables anything.
    Automation(key="riziko_vypadku",
               name="Riziko výpadku",
               schedule={"daily_at": "06:15", "tz": "Europe/Bratislava"},
               run_fn=run_riziko_vypadku),
    # #108 — daily restock (products WE show as Vypredané but our supplier has stock
    # again → flip back to Skladom). SAFETY (#93 contract): starts DISABLED — this one
    # WRITES to the live production eshop, so it runs ONLY after the manager clicks
    # ▶ Štart; a deploy never auto-restocks on its own. Scheduled after the 05:00
    # supplier scrape (fresh data) and the 06:15 riziko report brackets it.
    Automation(key="restock_skladom",
               name="Vypredané → Skladom",
               schedule={"daily_at": "06:00", "tz": "Europe/Bratislava"},
               run_fn=run_restock_skladom),
    # #98 — daily auto-skladom from Shoptet's OWN physical stock (products we HAVE
    # stock of but that still show Vypredané → flip to Skladom). SAFETY (#93
    # contract): starts DISABLED — this one WRITES to the live production eshop, so
    # it runs ONLY after the manager clicks ▶ Štart; a deploy never auto-restocks on
    # its own. Scheduled 06:45, after the hourly export sync + the 06:00 restock.
    Automation(key="stock_skladom",
               name="Máme skladom → Skladom",
               schedule={"daily_at": "06:45", "tz": "Europe/Bratislava"},
               run_fn=run_stock_skladom),
    # #105 — daily „Vybavuje sa" >4d orders → red (no note) / AI-classified reminder e-mail
    # (with note). SAFETY (#93 contract): starts DISABLED — it SENDS real customer e-mails AND
    # costs money via OpenAI, so it runs ONLY after the manager clicks ▶ Štart; a deploy never
    # e-mails or spends on its own. Scheduled 08:00 like the original n8n workflow.
    Automation(key="orders_reminder",
               name="Pripomienky objednávok",
               schedule={"daily_at": "08:00", "tz": "Europe/Bratislava"},
               run_fn=run_orders_reminder),
    # #135 — periodic our_images (our own product photo) HEAD validation. SAFETY
    # (#93 contract): starts DISABLED for consistency with every other automation
    # (like riziko_vypadku/shoptet_sync it is READ-ONLY / advisory — it makes
    # external HTTP HEAD calls against our own forestshop CDN but writes nothing
    # to the eshop or any manager store), so it runs only after ▶ Štart.
    Automation(key="image_health",
               name="Kontrola obrázkov",
               schedule={"daily_at": "04:30", "tz": "Europe/Bratislava"},
               run_fn=run_image_health),
    # #111 — „Poľovnícke výstavy": the 3 background chains migrated from n8n. SAFETY
    # (#93 contract): all start DISABLED — chain A SENDS real e-mails to organizers,
    # chains B/D read the live IMAP mailbox and advance state, so they run ONLY after
    # the manager opts in; a deploy never e-mails or reads mail on its own. These have
    # NO nav tab (per spec) — their effect shows on the „Poľovnícke výstavy" work tab;
    # the manager's primary controls are the per-výstava manual buttons.
    Automation(key="vystavy_otazka",
               name="Výstavy: rozposlať otázky",
               schedule={"daily_at": "06:00", "tz": "Europe/Bratislava"},
               run_fn=run_vystavy_otazka),
    Automation(key="vystavy_odpoved_otazka",
               name="Výstavy: kontrola odpovedí na otázku",
               schedule={"daily_at": "09:00", "tz": "Europe/Bratislava"},
               run_fn=run_vystavy_odpoved_otazka),
    Automation(key="vystavy_odpoved_prihlaska",
               name="Výstavy: kontrola odpovedí na prihlášku",
               schedule={"daily_at": "09:30", "tz": "Europe/Bratislava"},
               run_fn=run_vystavy_odpoved_prihlaska),
]
# lock=_lock: the automation state file is a store like any other — it must be
# serialised across processes too (#264), not just across threads.
RUNNER = AutomationRunner(AUTOMATIONS_STATE, AUTOMATIONS_REG, lock=_lock)

# #262 — ONE scheduler per data dir. A throwaway instance booted on another port for
# a screenshot (see the playbook recipe) was never killed and ran four days beside the
# real service with its scheduler enabled: two runners racing the same nightly jobs
# over the same data/out, unlogged — the customer-mail automations were one release
# away from mailing everybody twice. The claim below is an flock held for the whole
# process lifetime, so a crashed instance releases it automatically (no stale pidfile).
SCHEDULER_CLAIM = _store(".scheduler.lock")
_scheduler_claim_fd = None


def _scheduler_enabled() -> bool:
    """False only when WEBREVIEW_NO_SCHEDULER is explicitly set (empty/`0` = on).

    The throwaway preview instance boots with it: with no scheduler the process never
    fires anything BY ITSELF — no 09:00 customer mails, no 21:00 eshop write, no paid
    05:00 scrape — however long it is forgotten. (An explicit „⚡ Spustiť teraz" click
    by someone who opens ITS port still runs; the flag removes the unattended timer,
    not the logged-in human.)"""
    return os.environ.get("WEBREVIEW_NO_SCHEDULER", "").strip() in ("", "0")


def _claim_scheduler() -> bool:
    """Take the exclusive cross-process scheduler claim for this data dir."""
    global _scheduler_claim_fd
    p = os.fspath(SCHEDULER_CLAIM)
    fd = os.open(p, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        try:
            with open(p, encoding="utf-8") as f:
                holder = f.read().strip()
        except OSError:
            holder = ""
        os.close(fd)
        log.error("automation scheduler NOT started: another instance already holds %s "
                  "(%s). Two schedulers over one data dir race the nightly jobs and can "
                  "mail a customer twice (#262) — stop the other instance first.",
                  p, holder or "holder unknown")
        return False
    os.ftruncate(fd, 0)
    os.write(fd, f"pid={os.getpid()} port={os.environ.get('WEBREVIEW_PORT', '8801')} "
                 f"started={datetime.now().isoformat(timespec='seconds')}\n".encode())
    _scheduler_claim_fd = fd   # never closed — the claim lasts as long as this process
    return True


# What this instance INTENDED: "running" | "blocked" (another instance holds the claim)
# | "off" (WEBREVIEW_NO_SCHEDULER). Reported (derived, see `_scheduler_state`) by
# /api/automations and rendered as a banner: the boot log line used to be the ONLY
# trace, while the tab kept showing every enabled automation with a healthy future
# „Ďalší beh" (it comes from the persisted state file) although nothing would ever fire —
# a silent business failure of exactly the kind the store guard exists to prevent, one
# level up (PR #265 review). Starts "off": until _start_scheduler() runs, nothing schedules.
SCHEDULER_INTENT = "off"


def _scheduler_state() -> str:
    """What is REALLY the case, not what boot intended (PR #265 second review).

    An intent assigned once at boot cannot notice the loop thread dying, so a runner
    that is gone kept reporting „running" forever — the same healthy-looking tab over
    an idle scheduler that the banner exists to prevent. "dead" is the honest answer
    and the banner says so."""
    if SCHEDULER_INTENT != "running":
        return SCHEDULER_INTENT
    return "running" if RUNNER.is_alive() else "dead"


def _start_scheduler() -> bool:
    """Start the automation runner iff this instance may and can own it."""
    global SCHEDULER_INTENT
    if not _scheduler_enabled():
        log.warning("automation scheduler DISABLED (WEBREVIEW_NO_SCHEDULER) — nothing "
                    "will run on a timer in this instance (manual runs still work)")
        SCHEDULER_INTENT = "off"
        return False
    if not _claim_scheduler():
        SCHEDULER_INTENT = "blocked"
        return False
    RUNNER.start()
    SCHEDULER_INTENT = "running"
    return True


# #299 — the whole download → let the automations queue changes → one upload →
# download-again cycle holds ONE claim, so a standalone hourly download cannot land
# in the middle of it and hand the drain a catalogue that changed under its feet.
# Same flock shape as SCHEDULER_CLAIM above — but that one is held for the whole
# process lifetime, while THIS claim must be released the moment the cycle ends
# (success or exception), so the next hourly run can take it. Hence the context
# manager, not a boot-time `_claim_*() -> bool` function.
CYCLE_CLAIM = _store(".shoptet_cycle.lock")


def _cycle_busy() -> bool:
    """True while some process is inside the upload cycle."""
    p = os.fspath(CYCLE_CLAIM)
    fd = os.open(p, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return True
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


@contextlib.contextmanager
def _shoptet_cycle_claim():
    """Take the exclusive cross-process claim for the whole Shoptet upload cycle.

    Yields `True` when the claim was taken (the caller owns the cycle) or `False`
    when another process already holds it (the caller must skip this run, never
    proceed anyway). The claim is ALWAYS released when the `with` block exits —
    including on an exception — so a crashed cycle never wedges the next hourly
    run open forever.

    #299 review (Task 5 minor, deferred to Task 6): opening the claim file itself
    can fail — a full disk, wrong permissions, a missing/unwritable data dir — and
    that is NOT the same event as another process already holding the claim. Left
    unguarded, an hourly `os.open` failure would raise straight out of this
    generator and crash the WHOLE automation (recorded as a hard error) instead of
    the same clean "skip this run, try again next hour" the busy-claim branch
    already gives. Same yield-`False` shape, so a caller cannot tell the two apart
    from the return value alone — that is fine, since both mean exactly the same
    thing to the caller (skip this hour); the log line is what tells them apart."""
    p = os.fspath(CYCLE_CLAIM)
    try:
        fd = os.open(p, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as e:
        log.error("sync do Shoptetu: claim súbor %s sa nedá otvoriť (%r) — cyklus "
                  "preskakujem, o hodinu znova", p, e)
        yield False
        return
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            log.warning("sync do Shoptetu: cyklus už beží inde, preskakujem")
            yield False
            return
        os.ftruncate(fd, 0)
        os.write(fd, f"pid={os.getpid()} "
                     f"started={datetime.now().isoformat(timespec='seconds')}\n".encode())
        yield True
    finally:
        os.close(fd)


# Valid /api/ui-label rename keys (#173): every NAV key the frontend actually
# renders a button for — mirrors app.js's TABS + AUTOMATION_TABS arrays + the
# two standalone admin/dev tabs, verbatim. Deliberately NOT derived from
# AUTOMATIONS_REG's Automation.key set: the "Nevyzdvihnuté zásielky" tab's nav
# key is the legacy "posta" while its Automation.key is "posta_uncollected" —
# those are two different strings for the one automation, and only the NAV key
# is ever looked up by UI_LABELS on the frontend (_navButton/PAGE_TITLES key on
# the nav key). Keep this set in sync with app.js's TABS/AUTOMATION_TABS keys
# whenever a nav tab is added/renamed-in-code.
#
# "shoptet_upload" (#299) is DELIBERATELY absent here even though it is a full
# Automation — same reason the three vystavy_* chains above are absent: it has
# NO nav tab of its own yet. test_nav_keys_match_appjs (test_webreview_ui_labels.py)
# cross-checks this exact set against app.js's TABS/AUTOMATION_TABS/SYSTEM_TABS
# arrays and fails on ANY key present on only one side — adding it here without
# app.js's matching #tab-shoptet_upload card, NAV_ICONS entry and PAGE_TITLE (the
# plan's own Task 7) would 400 every rename attempt at the SAME key from the UI
# and break that drift guard immediately. Add it here in the SAME commit that adds
# the app.js card, never before.
NAV_KEYS = {
    "toorder", "nedostupne", "vystavy", "search", "notes", "review",     # TABS
    "posta", "orders_reminder", "shoptet_sync", "parovania_eshop",
    "grube_externalcode", "split_links",
    "dodavatelsky_sklad", "riziko_vypadku", "restock_skladom", "stock_skladom",
    "image_health",                                                      # AUTOMATION_TABS
    "users", "dev",
}


@app.route("/api/automations")
def api_automations():
    """Status of every registered automation (sidebar + tab header), plus its
    plain-language description (#173). Session-gated by the default-deny
    before_request like every other endpoint."""
    out = []
    for a in RUNNER.status():
        a["description"] = AUTOMATION_DESCRIPTIONS.get(a["key"], "")
        out.append(a)
    # `scheduler` — see `_scheduler_state`: without it a blocked / switched-off / dead
    # instance is indistinguishable from a healthy one in the UI.
    return jsonify({"automations": out, "scheduler": _scheduler_state()})


@app.route("/api/ui-labels")
def api_ui_labels():
    """Admin-set custom display names for nav tabs + automations (#173). GET is
    open to every logged-in user (a renamed tab must show its new name for
    everyone, not just the admin who renamed it) — only the POST below that
    changes a label is admin-gated."""
    return jsonify({"labels": _load_ui_labels()})


@app.route("/api/ui-label", methods=["POST"])
def api_ui_label():
    """Set or clear a custom display name for one nav tab / automation. Admin-
    only (like /api/users). Empty label clears the override (reverts to the
    built-in default name)."""
    me = _admin_or_none()
    if not me:
        return _forbidden()
    body = request.get_json(silent=True) or {}
    key = str(body.get("key") or "").strip()
    if key not in NAV_KEYS:
        return jsonify({"ok": False, "error": "neznáma položka"}), 400
    label = str(body.get("label") or "").strip()
    if len(label) > UI_LABEL_MAX:
        return jsonify({"ok": False,
                        "error": f"názov môže mať najviac {UI_LABEL_MAX} znakov"}), 400
    with _lock:
        d = _load_ui_labels()
        if label:
            d[key] = label
        else:
            d.pop(key, None)
        _save_ui_labels(d)
    log.info("ui-label: %s set %s -> %r", me["email"], key, label)
    return jsonify({"ok": True, "label": label})


def _export_status_names() -> list:
    """The DISTINCT `statusName` values the cached orders export actually carries.

    Without it a status name that matches NOTHING is invisible: the manager types it, the
    card echoes it back exactly as typed, and the tab, „Nedostupné" and the reminders just
    go empty (PR #295 review, B5). With it the panel can say „this one matches 0 orders",
    which is the only way a typo, a rename or a normalisation mismatch ever surfaces.

    Read-only, bounded, and never able to break the card it decorates: it decorates the one
    screen the manager reaches WHEN THINGS ARE ALREADY BROKEN, so an unreadable export is
    an empty list, not a 500."""
    try:
        text = _orders_csv_cached().decode("cp1250", errors="replace")
        rd = csv.DictReader(io.StringIO(text), delimiter=";")
        if "statusName" not in (rd.fieldnames or []):
            return []
        got = set()
        for r in rd:
            got.add(norm_status(r.get("statusName"))[:ORDERS_UNKNOWN_STATUS_MAXLEN]
                    or ORDERS_BLANK_STATUS_LABEL)
            if len(got) > ORDER_STATUS_MAX:
                break
        return sorted(got)[:ORDER_STATUS_MAX]
    except Exception as e:  # noqa: BLE001 — decoration only; never break the panel
        log.warning("stavy z exportu sa nepodarilo zistiť (%r)", e)
        return []


@app.route("/api/order-statuses")
def api_order_statuses():
    """#209 — the three order-status sets the app is ACTUALLY using, plus the built-in
    defaults it falls back to. GET is open to every logged-in user (the card shows the sets
    next to the statuses a run did not recognise, and that is not admin-only reading); only
    the POST below is admin-gated, like /api/ui-label."""
    st, reason = _order_statuses_state()
    return jsonify({
        "statuses": {k: sorted(v) for k, v in st.items()},
        "defaults": {k: sorted(v) for k, v in ORDER_STATUS_DEFAULTS.items()},
        "max_per_set": ORDER_STATUS_MAX,
        "max_len": ORDERS_UNKNOWN_STATUS_MAXLEN,
        # PR #295 review — the sets alone cannot say „these are the DEFAULTS standing in
        # for a file we could not read". Without it the panel renders the built-in list as
        # though the manager had typed it, on the one card where he would go to fix it.
        "reason": reason,
        # …and what the EXPORT really carries, so a name matching nothing can be flagged
        "export_statuses": _export_status_names(),
    })


@app.route("/api/order-statuses/impact", methods=["POST"])
def api_order_statuses_impact():
    """#297 — how many customers a CANDIDATE `to_order` set would newly reach with a
    reminder mail.

    It runs on a candidate the manager has NOT saved, so it writes no configuration, touches
    none of his stores and sends nothing. It is NOT literally side-effect-free, and saying so
    would be the kind of comment that outlives its truth: `_orders_csv_cached` refreshes the
    shared orders cache when that copy has aged out, exactly as `/api/orders` does on any page
    load. That is our own copy of Shoptet's data on the ordinary read path, not a write to
    anything the manager made.

    `to_order` drives three things at once — the „Na objednanie" tab, „Nedostupné" AND
    `run_orders_reminder`. That is deliberate (#209: one notion of „open", not four, so a
    rename cannot silently kill the mailing automation), but it leaves a sharp edge: adding a
    status makes EVERY order in it older than `MIN_DAYS` instantly mail-eligible, and the
    dedup store only stops the SECOND mail, never the first wave. Measured on the live export
    (28.7.2026): adding „Kompletná" reaches 2 more orders, „Osob. odber" 3 — and „Vybavená"
    387 orders, 250 of them with a note and an address = 237 distinct customers, all at once,
    under a card that answers „✅ Uložené". (370 is the address count WITHOUT the note filter
    — the over-count this endpoint's three numbers exist to avoid; PR #298 review, B-F2.)

    Three numbers rather than one, because they answer different questions and a preview that
    exaggerates is one the manager stops reading:

    * `orders`   — how many orders the app would start watching;
    * `mailable` — the honest UPPER BOUND on the wave: of those, the ones that carry an
      internal note (a note-less order only ever surfaces as the red „nikto sa jej nedotkol"
      alert — see orders_reminder's clean state machine), have an address to mail, and are
      not already resolved in the dedup store. It cannot be exact: whether a noted order is
      mailed is the AI classifier's verdict, which is not knowable without paying for it.
    * `customers` — distinct addresses among those, which is what „how many people hear from
      us" actually means.

    An export we cannot read answers `unknown`, never a reassuring 0 — a zero would wave the
    change through on no evidence at all, which is the opposite of the point."""
    if not _admin_or_none():
        return _forbidden()
    body = request.get_json(silent=True)
    body = body if isinstance(body, dict) else {}
    candidate = frozenset(_clean_status_list(body.get("to_order")) or ())
    # Measure against what is EFFECTIVE, not against what the loader renders (PR #298 review,
    # A3). On an unusable configuration `_order_statuses()` answers with the DEFAULTS — but
    # `run_orders_reminder` is fail-closed on that same reason and sends nothing at all, so the
    # set that is actually reaching customers right now is EMPTY and every status in the
    # candidate is newly reachable. Subtracting the defaults reported „nothing new" for the
    # single largest wave this app can release, which is the class of blindness the ticket is
    # about: measured on a corrupt store, preview 0 vs 37 orders mail-eligible the moment the
    # file was repaired. `unknown` would be the wrong answer here too — the number IS knowable,
    # and hiding it exactly when it is biggest is the same failure wearing a different label.
    current_sets, status_reason = _order_statuses_state()
    current = frozenset() if status_reason else current_sets["to_order"]
    added = sorted(candidate - current)
    out = {"ok": True, "added": added, "orders": 0, "mailable": 0, "customers": 0,
           "config_broken": bool(status_reason)}
    if not added:
        return jsonify(out)
    try:
        csv_bytes = _orders_csv_cached()
    except Exception as e:  # noqa: BLE001 — a preview must never be the reason a save fails
        log.warning("náhľad dosahu stavov: objednávky sa nedajú prečítať (%r)", e)
        return jsonify({**out, "unknown": True})
    try:
        # ONLY the added statuses: `select_orders` filters per status, so the orders it
        # returns for them ARE the difference against the current set — no need to run it
        # twice and subtract, which would also double the cost of reading the export. (It is
        # the ~1.1 MB ORDERS export here, not the ~55 MB catalog one — PR #298 review, B-F5.)
        newly = orders_reminder.select_orders(csv_bytes, statuses=added)
        with _lock:
            done = _load_orders_reminder().get("orders") or {}
    except Exception as e:  # noqa: BLE001 — same reason; an unreadable dedup store included
        log.warning("náhľad dosahu stavov: nedá sa spočítať (%r)", e)
        return jsonify({**out, "unknown": True})
    resolved = {c for c, v in done.items() if isinstance(v, dict)
                and v.get("status") in REMINDER_TERMINAL_STATUSES}
    mailable = [o for o in newly if o["has_note"] and o["email"]
                and o["code"] not in resolved]
    return jsonify({**out, "orders": len(newly), "mailable": len(mailable),
                    "customers": len({o["email"] for o in mailable})})


@app.route("/api/order-statuses", methods=["POST"])
def api_order_statuses_save():
    """Save all three sets at once. Admin-only.

    REFUSES — rather than silently correcting — the two configurations that would break the
    prune, because a correction the manager did not ask for is a configuration he does not
    know he is running:

    * an EMPTY `to_order` or `terminal`: the first blanks „Na objednanie", „Nedostupné" and
      the customer reminders; the second disarms the prune (nothing is ever finished);
    * a status claimed by BOTH: it would mean „still being handled" and „over" at once, and
      the prune would delete the marks of live orders — the exact harm #212/#292 exist to
      prevent.

    A set that is merely omitted from the payload keeps its stored value, so a screen that
    only edits one box cannot wipe the other two.

    And whatever survives all of that is put through the LOADER'S OWN resolution before it
    is written (PR #295 review, B3). Validating the payload as posted is not enough: the
    loader re-reads the file and substitutes defaults for anything it finds unusable, and
    those defaults can clash with the sets that WERE written — a clash discards the
    configuration whole. The card then says „✅ Uložené. Platí to hneď pre celú appku."
    while the rename reverts, the mails go to nobody and the prune is disarmed under a
    banner naming a „contradictory list" this very panel renders as empty. Accepted-then-
    discarded is the one answer this endpoint must never give."""
    me = _admin_or_none()
    if not me:
        return _forbidden()
    # `get_json(silent=True) or {}` neutralises null / {} / [], but a JSON string or number
    # sails straight through and blows up on `.get` — a malformed request is a 400, never
    # a 500.
    body = request.get_json(silent=True)
    body = body if isinstance(body, dict) else {}
    stored = _read_json_store(ORDER_STATUSES, {})
    out = {}
    for key in ORDER_STATUS_DEFAULTS:
        # REFUSE what we cannot store faithfully rather than silently trimming it: a status
        # quietly cut to 80 characters never matches the export again, and a list quietly
        # cut to 50 loses entries while the card answers „✅ Uložené".
        if key in body:
            raw = body[key]
            if not isinstance(raw, list):
                return jsonify({"ok": False, "error": (
                    "zoznam „%s“ musí byť zoznam stavov" % ORDER_STATUS_LABELS[key])}), 400
            if len(raw) > ORDER_STATUS_MAX:
                return jsonify({"ok": False, "error": (
                    "zoznam „%s“ má %d položiek, povolených je najviac %d"
                    % (ORDER_STATUS_LABELS[key], len(raw), ORDER_STATUS_MAX))}), 400
            too_long = [v for v in raw if isinstance(v, str)
                        and len(v.strip()) > ORDERS_UNKNOWN_STATUS_MAXLEN]
            if too_long:
                return jsonify({"ok": False, "error": (
                    "názov stavu môže mať najviac %d znakov (v zozname „%s“ je dlhší)"
                    % (ORDERS_UNKNOWN_STATUS_MAXLEN, ORDER_STATUS_LABELS[key]))}), 400
            # A status name is free text that ends up in `log.info(...)` and in the prune's
            # „nothing is open" message; an interior newline forges a log line, a NUL or an
            # ANSI escape corrupts a terminal reading it. Refused at the door rather than
            # silently dropped, for the same reason the two cuts above are (PR #295, B7).
            if [v for v in raw if isinstance(v, str) and _STATUS_CTRL.search(v)]:
                return jsonify({"ok": False, "error": (
                    "názov stavu nesmie obsahovať riadiace znaky (nový riadok a podobne) "
                    "— v zozname „%s“ taký je" % ORDER_STATUS_LABELS[key])}), 400
        raw = body.get(key, stored.get(key))
        vals = _clean_status_list(raw)
        # `[]` now comes back as `[]`, so „unusable" and „deliberately emptied" are
        # distinguishable here too (PR #295, B4): the two load-bearing sets refuse both,
        # `known_open` accepts the empty one and the store keeps it.
        if key in body and key in ORDER_STATUS_REQUIRED and not vals:
            return jsonify({"ok": False, "error": (
                "zoznam „%s“ nesmie byť prázdny — bez neho by appka nevedela, ktoré "
                "objednávky sú rozpracované a ktoré ukončené"
                % ORDER_STATUS_LABELS[key])}), 400
        out[key] = vals if vals is not None else list(ORDER_STATUS_DEFAULTS[key])
    clash = _status_overlap({k: set(v) for k, v in out.items()})
    if clash:
        return jsonify({"ok": False, "error": (
            "stav %s je uvedený naraz vo viacerých zoznamoch — to sa navzájom vylučuje "
            "a pri mazaní starých značiek by to zmazalo prácu pri živých objednávkach"
            % ", ".join(clash))}), 400
    # #296 — `cancelled` refines `terminal`, so a name in one and not the other is the drift
    # this set exists to prevent („moved it, forgot to delete it from the other box" — the
    # likeliest mis-edit of copy-pasteable lists).
    #
    # It is checked on the RESULTING configuration whenever the request touches either side of
    # the invariant (PR #298 review, A6). Keying it on `"cancelled" in body` alone left the
    # other half of the same mis-edit wide open: a call narrowing `terminal` produced exactly
    # the drift — „Stornovaná" stays cancelled while dropping out of terminal, `terminal −
    # cancelled` subtracts nothing, and cancelled orders return to the set the escalation
    # chases.
    #
    # The upgrade exemption survives, and is now stated in terms of what it was always about:
    # a `cancelled` NOBODY EVER CONFIGURED — no key in the payload and none in the stored file,
    # so the value is this version's built-in default. Refusing a `terminal` edit over a set
    # the manager has never seen would red-banner an install for a state it did not create
    # (which the loader, deliberately, does not do either). Once the set exists — and the panel
    # writes all four boxes on every save — both edits are checked.
    cancelled_configured = "cancelled" in body or "cancelled" in stored
    touches_invariant = "cancelled" in body or "terminal" in body
    stray = (_cancelled_outside_terminal({k: set(v) for k, v in out.items()})
             if touches_invariant and cancelled_configured else [])
    if stray:
        return jsonify({"ok": False, "error": (
            "stav %s je v zozname „%s“, ale nie je medzi ukončenými — zrušená objednávka je "
            "vždy aj ukončená, takže doplň ten istý názov aj do zoznamu „%s“"
            % (", ".join(stray), ORDER_STATUS_LABELS["cancelled"],
               ORDER_STATUS_LABELS["terminal"]))}), 400
    with _lock:
        d = _read_json_store(ORDER_STATUSES, {})
        d.update(out)
        # THE candidate file, resolved by the rule the loader will apply to it. Anything
        # that would not survive is refused here, where the manager can still act on it.
        _sets, why = _resolve_status_sets(d)
        if why:
            return jsonify({"ok": False, "error": (
                "takto uložené nastavenie by appka nevedela použiť (%s) — oprav zoznamy a "
                "skús to znova" % why)}), 400
        _atomic_write_json(ORDER_STATUSES, d, protect=True)
    log.info("order-statuses: %s set to_order=%s terminal=%s known_open=%s cancelled=%s",
             me["email"], out["to_order"], out["terminal"], out["known_open"],
             out["cancelled"])
    return jsonify({"ok": True, "statuses": {k: sorted(v) for k, v in out.items()},
                    "export_statuses": _export_status_names()})


@app.route("/api/automations/<key>/toggle", methods=["POST"])
def api_automation_toggle(key):
    body = request.get_json(silent=True) or {}
    enabled = bool(body.get("enabled"))
    try:
        RUNNER.set_enabled(key, enabled)
    except KeyError:
        return jsonify({"ok": False, "error": "neznáma automatizácia"}), 404
    log.info("automations: %s -> %s (user %s)", key,
             "enabled" if enabled else "disabled", session.get("user"))
    return jsonify({"ok": True, "enabled": enabled})


@app.route("/api/automations/<key>/run", methods=["POST"])
def api_automation_run(key):
    # NOTE: „⚡ Spustiť teraz" runs even on a WEBREVIEW_NO_SCHEDULER instance — the flag
    # removes the SCHEDULER (nothing fires by itself, which is what an orphaned instance
    # does), not the explicit action of a logged-in human who opened this instance's UI.
    try:
        started = RUNNER.run_now(key)
    except KeyError:
        return jsonify({"ok": False, "error": "neznáma automatizácia"}), 404
    log.info("automations: manual run of %s by %s (started=%s)",
             key, session.get("user"), started)
    return jsonify({"ok": True, "started": started})


@app.route("/api/posta-uncollected")
def api_posta_uncollected():
    """Display data for the 'Nevyzdvihnuté zásielky' tab — the last run's full
    result (uncollected + invalid-format + per-shipment errors)."""
    with _lock:
        st, corrupt = _load_posta_state_display()
    return jsonify({
        "last_check": st.get("last_check", ""),
        "uncollected": st.get("uncollected") or [],
        "invalid": st.get("invalid") or [],
        "errors": st.get("errors") or [],
        "stats": st.get("stats") or {},
        # an unreadable store must not look like a quiet day on the tab (#225 / PR #228 review)
        "store_corrupt": corrupt,
    })


@app.route("/api/posta-uncollected/preview", methods=["GET", "POST"])
def api_posta_uncollected_preview():
    """#217 — show the manager EXACTLY what the customer would receive, BEFORE anything goes out.

    Strictly READ-ONLY: no claim, no escalation bump, no SMTP, not a single write. It renders
    posta_uncollected.build_email from the shipment's CURRENT display row, so the count, post
    office and retention date are the real ones the automation would use.

    The number shown is the NEXT escalation mail (already sent + 1). Once the cadence is
    exhausted there is no next mail, so it previews the LAST one that went out and says so
    (`max_reached`) — inventing a 5th mail the automation will never send would be a lie."""
    body = request.get_json(silent=True) or {}
    pkg = str(body.get("package") or request.values.get("package") or "").strip()
    if not pkg:
        return jsonify({"ok": False, "error": "chýba číslo zásielky"}), 400
    with _lock:
        st = _load_posta_state()
    row = next((r for r in st.get("uncollected") or []
                if isinstance(r, dict) and r.get("packageNumber") == pkg), None)
    if row is None:
        return jsonify({"ok": False, "error": "zásielka sa v aktuálnom zozname nenašla"}), 404
    try:
        already = int(row.get("count") or 0)
    except (TypeError, ValueError):     # a partial write must not 500 a read-only preview
        already = 0
    max_reached = already >= posta_uncollected.MAX_EMAILS
    count = min(already + 1, posta_uncollected.MAX_EMAILS)
    subject, html = posta_uncollected.build_email(
        count, row.get("name", ""), pkg, row.get("office_name", ""),
        row.get("office_addr", ""), row.get("retained_till", ""))
    return jsonify({"ok": True, "subject": subject, "html": html,
                    "recipient": row.get("email", ""), "name": row.get("name", ""),
                    "packageNumber": pkg, "orderCode": row.get("orderCode", ""),
                    "count": count, "already_sent": already, "max_reached": max_reached})


def _find_current_row(st: dict, code: str) -> dict | None:
    """Look up `code`'s row in the CURRENT red/orange/skipped snapshot (#153 manual override) —
    the display data already carries every field the override needs (billFullName, email,
    itemName, shopRemark), so it never has to re-read the CSV export."""
    for section in ("red", "orange", "skipped"):
        for r in st.get(section) or []:
            if r.get("code") == code:
                return r
    return None


def _pop_row(st: dict, section: str, code: str) -> None:
    st[section] = [r for r in st.get(section) or [] if r.get("code") != code]


@app.route("/api/orders-reminder/override", methods=["POST"])
def api_orders_reminder_override():
    """Manual per-row override (#153): the manager corrects the automation directly from the tab
    — either the note is empty (no AI classification ever ran, a RED row) or the AI verdict was
    wrong (a SKIPPED row it marked 'already contacted'). action='contact' records the order as
    manually-contacted — the same terminal dedup as the AI 'already contacted' path, no e-mail,
    never shown again. action='send' sends the ONE reminder e-mail right now — allowed on a red OR
    skipped row (overriding a wrong 'already contacted' verdict), but NEVER on an already-emailed
    one (the same dedup as the automated run — no double-send).

    The SMTP call happens OUTSIDE the store lock — mirroring run_orders_reminder, which also
    classifies/e-mails unlocked and only holds `_lock` for the file read/write. `_lock` is the
    app's single GLOBAL lock (guards every store), so holding it for the duration of a network
    call would stall every other admin action on the site for the SMTP timeout.

    Because the lock is NOT held across SMTP, the pre-check alone cannot stop a double-click:
    two requests would both pass it and both e-mail the customer. So 'send' CLAIMS the order
    under the first lock — a transient `status='sending'` record persisted BEFORE the SMTP call
    — and a second request that sees a live claim gets 409. A failed send releases the claim
    (the previous state is restored) so the manager can simply try again, and a claim older
    than SENDING_CLAIM_TTL_S counts as abandoned, so a crash mid-send can never lock the order
    forever."""
    body = request.get_json(silent=True) or {}
    code = str(body.get("code") or "").strip()
    action = str(body.get("action") or "").strip()
    if not code or action not in ("contact", "send"):
        return jsonify({"ok": False, "error": "neplatná požiadavka"}), 400

    now_iso = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    claim_token = secrets.token_hex(8)
    prev_entry = None
    with _lock:
        st = _load_orders_reminder()
        row = _find_current_row(st, code)
        if row is None:
            return jsonify({"ok": False, "error": "objednávka sa v aktuálnom zozname nenašla"}), 404
        prev_entry = (st.get("orders") or {}).get(code)
        if not isinstance(prev_entry, dict):
            prev_entry = None       # garbage from a partial write — treat as 'not resolved'
        prev_status = (prev_entry or {}).get("status")
        sending_now = _reminder_claim_active(prev_entry)
        if action == "contact" and (_reminder_is_terminal(prev_entry) or sending_now):
            return jsonify({"ok": False, "error": "objednávka je už vybavená",
                            "status": prev_status}), 409
        if action == "send":
            if prev_status == "emailed":
                return jsonify({"ok": False, "error": "pripomienka už bola odoslaná"}), 409
            if sending_now:
                return jsonify({"ok": False, "error": "pripomienka sa práve odosiela",
                                "status": "sending"}), 409
            if not (row.get("email") or ""):
                return jsonify({"ok": False, "error": "objednávka nemá e-mail"}), 400
            if _mail_bcc() is None:
                # „BCC vždy" is BINDING for a customer mail, so _send_mail_html would refuse and
                # the manager would get a generic 502 „odoslanie zlyhalo" — indistinguishable
                # from a transient glitch, so they keep clicking forever. Pre-flight it BEFORE
                # the claim: no wasted claim, and an answer that says what to actually fix.
                log.error("orders_reminder: manual send obj. %s odmietnutý — MAIL_BCC nie je "
                          "nastavené (data/.mail_env)", code)
                return jsonify({"ok": False, "error": (
                    "Chýba MAIL_BCC v data/.mail_env — mail sa neodošle, doplň konfiguráciu.")}), 503
            # CLAIM the send before releasing the lock — a concurrent double-click now 409s
            st.setdefault("orders", {})[code] = {
                "status": "sending", "claimed_at": now_iso, "claim": claim_token,
                "email": row.get("email", ""), "manual": True}
            _save_orders_reminder(st)

    base = {"name": row.get("billFullName", ""), "email": row.get("email", ""),
            "itemName": row.get("itemName", ""), "note": row.get("shopRemark", ""),
            "date": now_iso, "manual": True}
    # The row this override RESOLVES must not carry the run's „nedokončené" note: `pending`
    # means „a run gave up on this order", which is exactly what the manager is undoing here.
    # _relocate and the incremental fast path strip it for the same reason; re-appending the row
    # verbatim would leave the warning (and its „z toho N nedokončených" count) sitting on a
    # finished row until the next run rebuilds the lists — up to 24 h (PR #224 review).
    # …and it gains `manual` (#227): the record already carried it, but the DISPLAY row did not,
    # so the tab kept listing hand-resolved orders under „AI usúdilo, že zákazník je už
    # kontaktovaný" — a verdict the classifier never made (a red row has no note to classify,
    # and a pending one was skipped precisely because the AI was not called).
    row_done = _mark_manual({k: v for k, v in row.items() if k != "pending"}, base)

    def _release_claim() -> None:
        """Undo OUR claim (only ours — never a concurrent winner's terminal record)."""
        with _lock:
            st2 = _load_orders_reminder()
            cur = (st2.get("orders") or {}).get(code)
            cur = cur if isinstance(cur, dict) else {}
            if cur.get("claim") != claim_token:
                return
            if prev_entry is None:
                st2.get("orders", {}).pop(code, None)
            else:
                st2["orders"][code] = prev_entry
            _save_orders_reminder(st2)

    if action == "contact":
        with _lock:
            st = _load_orders_reminder()
            done = st.setdefault("orders", {})
            cur = done.get(code)
            # the same test as the first gate — an EXPIRED claim (orphaned by a restart between
            # claim and send) must NOT permanently block resolving the order
            if _reminder_is_terminal(cur) or _reminder_claim_active(cur):
                return jsonify({"ok": False, "error": "objednávka je už vybavená",
                                "status": (cur or {}).get("status")}), 409
            done[code] = {**base, "status": "skipped_contacted"}
            _pop_row(st, "red", code)
            _pop_row(st, "skipped", code)
            st.setdefault("skipped", []).append({**row_done, "sent_date": now_iso})
            _save_orders_reminder(st)
        log.info("orders_reminder: manual override %s -> kontaktované (user %s)",
                 code, session.get("user"))
        return jsonify({"ok": True, "status": "skipped_contacted"})

    # action == "send" — allowed from red or skipped (override), never from already-emailed.
    # No lock held here: the SMTP round-trip (up to ~20s, see _send_mail_html) must never block
    # every other manager's request on the shared global lock. The claim taken above is what
    # keeps a concurrent double-click out, and it is released on every failure path below.
    email = row.get("email") or ""
    subject, html = orders_reminder.build_reminder_email(row.get("billFullName", ""), code)
    # require_bcc: this is a real customer mail — no owner copy configured, no send (#VYL 4)
    if not _send_mail_html(email, subject, html, require_bcc=True):
        _release_claim()
        return jsonify({"ok": False, "error": "odoslanie e-mailu zlyhalo"}), 502

    try:
        with _lock:
            st = _load_orders_reminder()
            done = st.setdefault("orders", {})
            if _reminder_is_terminal(done.get(code)) and \
                    done[code].get("status") == "emailed":
                # a concurrent request already recorded this send while we were talking to SMTP —
                # the e-mail landed (this one too, unlikely double-click race), state is correct either way.
                return jsonify({"ok": True, "status": "emailed"})
            done[code] = {**base, "status": "emailed"}
            _pop_row(st, "red", code)
            _pop_row(st, "skipped", code)
            _pop_row(st, "orange", code)
            st.setdefault("orange", []).append({**row_done, "sent_date": now_iso})
            _save_orders_reminder(st)
    except Exception as e:  # noqa: BLE001 — full disk / permissions
        # The mail ALREADY reached the customer. Reporting a plain failure would invite the
        # manager to click again (= a second mail), so say plainly what happened and log the
        # code for manual follow-up.
        #
        # Leaving the transient claim in place is NOT enough (PR #223 review): `sending` expires
        # after SENDING_CLAIM_TTL_S and is not terminal, so ten minutes later the daily run sees
        # an unprocessed order and mails the customer AGAIN — the mitigation would depend on a
        # human reacting within those ten minutes. Write a NON-EXPIRING terminal marker instead,
        # in its own minimal transaction (no display-list work, which is what most likely failed
        # above). If even that write fails nothing is worse off than before.
        try:
            with _lock:
                st = _load_orders_reminder()
                st.setdefault("orders", {})[code] = {**base, "status": "emailed",
                                                     "persist_failed": True}
                _save_orders_reminder(st)
        except Exception as e2:  # noqa: BLE001 — then the TTL is all that is left
            log.error("orders_reminder: ani núdzový zápis stavu obj. %s neprešiel (%r) — po "
                      "vypršaní nároku (%s s) hrozí duplicitný mail v ďalšom behu",
                      code, e2, SENDING_CLAIM_TTL_S)
        log.error("orders_reminder: mail pre obj. %s ODIŠIEL na %s, ale zápis stavu ZLYHAL "
                  "(%r) — NEposielaj znova, skontroluj ručne", code, email, e)
        return jsonify({"ok": False, "error": (
            f"E-mail zákazníkovi ODIŠIEL, ale stav objednávky {code} sa nepodarilo uložiť. "
            "NEklikaj znova (poslal by si druhý mail) — nahlás to na kontrolu.")}), 500
    log.info("orders_reminder: manual send %s -> %s (user %s)", code, email, session.get("user"))
    return jsonify({"ok": True, "status": "emailed"})


@app.route("/api/orders-reminder/preview", methods=["GET", "POST"])
def api_orders_reminder_preview():
    """#217 — the reminder e-mail this order would receive, rendered for review only.

    Strictly READ-ONLY, exactly like the Pošta preview above: no claim, no dedup record, no SMTP.
    It is deliberately a SEPARATE endpoint from the override 'send' action, so that looking can
    never turn into sending by accident."""
    body = request.get_json(silent=True) or {}
    code = str(body.get("code") or request.values.get("code") or "").strip()
    if not code:
        return jsonify({"ok": False, "error": "chýba kód objednávky"}), 400
    with _lock:
        st = _load_orders_reminder()
    row = _find_current_row(st, code)
    if row is None:
        return jsonify({"ok": False, "error": "objednávka sa v aktuálnom zozname nenašla"}), 404
    subject, html = orders_reminder.build_reminder_email(row.get("billFullName", ""), code)
    return jsonify({"ok": True, "code": code, "subject": subject, "html": html,
                    "recipient": row.get("email", ""), "name": row.get("billFullName", "")})


@app.route("/api/orders-reminder")
def api_orders_reminder():
    """Display data for the „Pripomienky objednávok" tab (#105) — the last run's red list (no-note
    >4d orders), orange list (reminder e-mail sent) + summary stats."""
    with _lock:
        st, corrupt = _load_orders_reminder_display()
    return jsonify({
        "last_check": st.get("last_check", ""),
        # an unreadable store must not look like a quiet day on the tab (#225 / PR #228 review)
        "store_corrupt": corrupt,
        "red": st.get("red") or [],
        "orange": st.get("orange") or [],
        "skipped": st.get("skipped") or [],
        # orders whose customer has no e-mail on file — the reminder can never be sent, so the
        # manager has to fill the address in (they never reach the AI classifier, #BUG 4)
        "no_email": st.get("no_email") or [],
        "stats": st.get("stats") or {},
    })


@app.route("/api/supplier-stock")
def api_supplier_stock():
    """Display data for the „Dodávateľský sklad" tab — the last scraper run's rows
    (availability / price / source / last-checked / errors) + summary stats."""
    with _lock:
        st = _load_supplier_stock()
    return jsonify({
        "last_check": st.get("last_check", ""),
        "rows": st.get("rows") or [],
        "stats": st.get("stats") or {},
    })


@app.route("/api/riziko-vypadku")
def api_riziko_vypadku():
    """Display data for the „Riziko výpadku" tab — the last join's risk rows +
    whether the '#106 Dodávateľský sklad' scraper has ever produced data at all
    (has_supplier_data=False -> the tab shows 'spusti Dodávateľský sklad first',
    never a misleading empty 'no risk' list)."""
    with _lock:
        st = _load_riziko()
    return jsonify({
        "last_check": st.get("last_check", ""),
        "has_supplier_data": bool(st.get("has_supplier_data")),
        "supplier_last_check": st.get("supplier_last_check", ""),
        "risks": st.get("risks") or [],
    })


@app.route("/api/riziko-vypadku/csv")
def api_riziko_vypadku_csv():
    """Optional CSV download of the last run's risk rows (per the digest — ready
    to eyeball or hand to someone deciding which to flip to 'Vypredané'). Never
    imported back automatically by this app, but formula-injection-guarded like
    every other CSV sink here (_csv_safe)."""
    with _lock:
        st = _load_riziko()
    header = ["kod", "parovaci_kod", "nazov", "dodavatel", "nasa_cena", "nas_sklad",
              "dostupnost_u_dodavatela", "link", "kontrolovane"]
    rows = [[_csv_safe(r.get(k, "")) for k in riziko_vypadku.RISK_FIELDS]
            for r in (st.get("risks") or [])]
    return _csv_response(header, rows, "riziko_vypadku.csv")


@app.route("/api/restock-skladom")
def api_restock_skladom():
    """Display data for the „Vypredané → Skladom" tab — the last restock run's
    candidate products (kód, názov, naša cena vs cena dodávateľa, linky), the import
    outcome (spracované / naskladnené / zlyhania), and whether the '#106 Dodávateľský
    sklad' scraper has produced data at all (has_supplier_data=False → the tab shows
    'najprv spusti Dodávateľský sklad', never a misleading empty list)."""
    with _lock:
        st = _load_restock()
    return jsonify({
        "last_check": st.get("last_check", ""),
        "has_supplier_data": bool(st.get("has_supplier_data")),
        "supplier_last_check": st.get("supplier_last_check", ""),
        "status": st.get("status", ""),
        "candidates": st.get("candidates") or [],
        "processed": st.get("processed"),
        "updated": st.get("updated"),
        "failed": st.get("failed"),
        "error_detail": st.get("error_detail", ""),
    })


@app.route("/api/stock-skladom")
def api_stock_skladom():
    """Display data for the „Máme skladom → Skladom" tab (#98) — the last run's
    candidate products (kód, názov, naša cena, náš sklad, čo teraz zobrazujú), the
    import outcome (spracované / naskladnené / zlyhania) and the run status."""
    with _lock:
        st = _load_stock_skladom()
    return jsonify({
        "last_check": st.get("last_check", ""),
        "status": st.get("status", ""),
        "candidates": st.get("candidates") or [],
        "processed": st.get("processed"),
        "updated": st.get("updated"),
        "failed": st.get("failed"),
        "error_detail": st.get("error_detail", ""),
    })


if __name__ == "__main__":
    _start_scheduler()
    app.run(host="0.0.0.0", port=int(os.environ.get("WEBREVIEW_PORT", "8801")),
            threaded=True)
