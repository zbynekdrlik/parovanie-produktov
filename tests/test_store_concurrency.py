"""#264 — no lost updates between processes, and a backup for every store that
proves who has already been mailed.

The atomic tmp+replace every store uses protects against a HALF-WRITTEN file. It does
nothing about two processes doing read-modify-write at the same time: both read the
same map, both write theirs, the second silently erases the first one's entry. That
was not theoretical — a second app instance ran for four days over the same data/out
(#262). And the stores that record which customer already got a reminder had no
backup history at all, so nobody could even check afterwards.
"""
import os
import subprocess
import sys
import textwrap

import fcntl
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webreview"))
import app as webapp  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUP_SCRIPT = os.path.join(ROOT, "scripts", "backup_data.sh")

# Stores whose loss means a customer gets a second e-mail, or the manager loses work
# that exists nowhere else. Every one of them must be in the backup rotation.
MUST_BE_BACKED_UP = [
    "decisions.json", "review_data.json", "ordered_items.json", "uploaded_pairings.json",
    "orders_reminder.json", "posta_uncollected.json", "automations.json",
    "order_pairings.json", "supplier_assignments.json", "variant_links.json",
    "waiting_items.json", "instock_items.json", "unavailable_items.json",
    "order_comments.json", "nedostupne.json", "vystavy.json", "users.json",
]


def test_the_backup_script_covers_every_irreplaceable_store():
    with open(BACKUP_SCRIPT, encoding="utf-8") as f:
        script = f.read()
    missing = [f for f in MUST_BE_BACKED_UP if f not in script]
    assert missing == [], f"no backup history for: {missing}"


def test_the_store_lock_is_exclusive_across_processes(tmp_path, monkeypatch):
    """Holding the app's store lock must be visible to OTHER processes — an
    in-process threading.Lock alone leaves two instances free to clobber each other."""
    monkeypatch.setattr(webapp, "OUT", str(tmp_path))
    with webapp._lock:
        lockfile = os.fspath(webapp._lock.path)
        assert os.path.exists(lockfile), "the store lock leaves no cross-process trace"
        fd = os.open(lockfile, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            with pytest.raises(OSError):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)


def test_the_store_lock_is_released_again(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "OUT", str(tmp_path))
    with webapp._lock:
        pass
    fd = os.open(os.fspath(webapp._lock.path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)   # must not raise
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def test_the_store_lock_is_reentrant_for_one_thread(tmp_path, monkeypatch):
    """Every `_save_*` takes the lock itself now, and almost all of them are called
    from inside a `with _lock:` read-modify-write — a non-reentrant lock would
    deadlock the whole app on the first click."""
    monkeypatch.setattr(webapp, "OUT", str(tmp_path))
    with webapp._lock:
        webapp._save_ordered({"O1|A": True})
        # bounded, so a non-reentrant lock FAILS the test instead of hanging it
        assert webapp._lock.acquire(timeout=5), \
            "the store lock is not reentrant — a save inside `with _lock:` would deadlock"
        try:
            assert webapp._load_ordered() == {"O1|A": True}
        finally:
            webapp._lock.release()


_WORKER = textwrap.dedent("""
    import os, sys, time
    sys.path.insert(0, os.path.join(sys.argv[1], "webreview"))
    import app as webapp
    tag, rounds = sys.argv[2], int(sys.argv[3])
    for i in range(rounds):
        with webapp._lock:
            d = webapp._load_ordered()
            d["%s|%d" % (tag, i)] = True
            time.sleep(0.003)          # widen the read-modify-write window
            webapp._save_ordered(d)
""")


def test_two_processes_writing_at_once_never_lose_an_entry(tmp_path):
    """The proof #264 asks for: without a cross-process lock the two runs overwrite
    each other's entries and the store ends up with far fewer than 2×rounds keys."""
    rounds = 30
    worker = tmp_path / "worker.py"
    worker.write_text(_WORKER, encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    env = dict(os.environ,
               WEBREVIEW_OUT=str(out),
               WEBREVIEW_PRODUCTS=str(tmp_path / "nonexistent.csv"),
               WEBREVIEW_NO_SCHEDULER="1",
               PYTHONPATH=os.path.join(ROOT, "src"))
    procs = [subprocess.Popen([sys.executable, str(worker), ROOT, tag, str(rounds)],
                              env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
             for tag in ("A", "B")]
    for pr in procs:
        _, err = pr.communicate(timeout=120)
        assert pr.returncode == 0, err.decode()[-2000:]

    import json
    stored = json.loads((out / "ordered_items.json").read_text(encoding="utf-8"))
    expected = {f"{tag}|{i}" for tag in ("A", "B") for i in range(rounds)}
    assert set(stored) == expected, f"lost {len(expected - set(stored))} of {len(expected)} writes"
