import json
import logging

import pytest
from webreview import app as webapp


@pytest.fixture
def pend(tmp_path, monkeypatch):
    p = tmp_path / "pending_shoptet.json"
    monkeypatch.setattr(webapp, "PENDING_SHOPTET", str(p))
    return p


@pytest.fixture
def claim(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "CYCLE_CLAIM", str(tmp_path / ".cycle.lock"))


def test_queue_shoptet_fields_persists_and_counts(pend):
    n = webapp.queue_shoptet_fields(
        "parovania_eshop", "code;pairCode;internalNote",
        [["A", "P", "https://x"]])
    assert n == 1
    d = json.loads(pend.read_text(encoding="utf-8"))
    assert d["A"]["fields"]["internalNote"]["value"] == "https://x"
    assert d["A"]["fields"]["internalNote"]["queued_at"]


def test_queueing_twice_keeps_both_sources(pend):
    webapp.queue_shoptet_fields("parovania_eshop", "code;pairCode;internalNote",
                                [["A", "P", "https://x"]])
    webapp.queue_shoptet_fields("restock_skladom",
                                "code;pairCode;availabilityInStock",
                                [["A", "P", "Skladom"]])
    d = webapp._load_pending()
    assert set(d["A"]["fields"]) == {"internalNote", "availabilityInStock"}


def test_an_unreadable_table_refuses_the_write_rather_than_wiping_it(pend):
    """A table we cannot parse is NOT an empty table — writing on top of it would
    silently drop everything already queued. The refusal comes from
    `_atomic_write_json(protect=True)` inside `_save_pending`
    (`webapp.StoreWipeRefused`), not from a hand-rolled check in
    `queue_shoptet_fields` — so this pins the exact exception type, that the file on
    disk is left byte-for-byte untouched, and that the unreadable bytes are preserved
    in a quarantine copy before the write is refused."""
    pend.write_text("{ this is not json", encoding="utf-8")
    before = pend.read_bytes()
    with pytest.raises(webapp.StoreWipeRefused):
        webapp.queue_shoptet_fields("s", "code;pairCode;internalNote",
                                    [["A", "P", "u"]])
    assert pend.read_bytes() == before                      # original untouched
    assert list(pend.parent.glob("pending_shoptet.json.corrupt-*")), \
        "the unreadable bytes must be preserved before the write is refused"


def test_queueing_a_new_code_does_not_wipe_what_is_already_waiting(pend):
    """The whole point of the table: independent producers queue at any time, so
    queueing a DIFFERENT code must never disturb what an EARLIER call already put on
    disk and is waiting for the next hourly upload (the risk #299 exists to end)."""
    webapp.queue_shoptet_fields("parovania_eshop", "code;pairCode;internalNote",
                                [["B", "P", "https://existing"]])
    existing_b = webapp._load_pending()["B"]

    webapp.queue_shoptet_fields("restock_skladom",
                                "code;pairCode;availabilityInStock",
                                [["A", "P", "Skladom"]])

    d = webapp._load_pending()
    assert d["B"] == existing_b
    assert d["A"]["fields"]["availabilityInStock"]["value"] == "Skladom"


def test_the_whole_read_modify_write_runs_under_the_store_lock(pend, monkeypatch):
    """Pins the CAUSE, not the symptom. `_lock` is depth-counted, so a spy on
    `_save_pending` observes depth 0 the moment the write is moved outside its
    `with _lock:` block — the exact shape a future refactor could introduce."""
    depths = []
    real = webapp._save_pending

    def spy(d):
        depths.append(webapp._lock._depth)
        return real(d)
    monkeypatch.setattr(webapp, "_save_pending", spy)

    n = webapp.queue_shoptet_fields(
        "parovania_eshop", "code;pairCode;internalNote",
        [["A", "P", "https://x"]])

    assert n == 1
    assert depths == [1], (
        "_save_pending ran while the store lock was NOT held", depths)


def test_the_claim_is_exclusive_and_reports_busy_while_held(claim):
    with webapp._shoptet_cycle_claim() as got:
        assert got is True
        assert webapp._cycle_busy() is True
        with webapp._shoptet_cycle_claim() as second:
            assert second is False
    assert webapp._cycle_busy() is False


def test_the_claim_is_released_even_when_the_body_raises(claim):
    with pytest.raises(ValueError):
        with webapp._shoptet_cycle_claim():
            raise ValueError("boom")
    assert webapp._cycle_busy() is False


# --------------------------------------------------------------------------- #
# Task 6 (#299) — the hourly cycle itself: run_shoptet_upload.
# --------------------------------------------------------------------------- #
@pytest.fixture
def cycle(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "PENDING_SHOPTET", str(tmp_path / "pending.json"))
    monkeypatch.setattr(webapp, "CYCLE_CLAIM", str(tmp_path / ".cycle.lock"))
    calls = {"import": [], "run_sync": []}
    monkeypatch.setattr(webapp, "_import_rows_chunked",
                        lambda rows, header, dry, prefix, csv_safe=False, timeout=900: (
                            calls["import"].append((header, [list(r) for r in rows], csv_safe))
                            or {"ok": True, "partial": False,
                                "success_codes": {r[0] for r in rows},
                                "partial_codes": set(), "partial_failed": 0,
                                "chunks_total": 1, "chunks_ok": 1,
                                "processed": len(rows), "updated": len(rows),
                                "failed": 0, "rc": 0, "error_detail": None,
                                "stdout_tail": "", "err": "", "unreadable": False}))
    monkeypatch.setattr(webapp, "_export_row_verdicts",
                        lambda rows, note_col=2: {"confirmed": set(), "absent": set()})
    # #299 review I3: the cycle now runs everything through the SYNCHRONOUS
    # RUNNER.run_sync (never the fire-and-forget run_now) — see automation_runner.py.
    monkeypatch.setattr(webapp.RUNNER, "run_sync",
                        lambda key: calls["run_sync"].append(key) or True)
    monkeypatch.setattr(webapp.RUNNER, "status", lambda: [])
    return calls


def test_an_empty_table_uploads_nothing_and_skips_the_second_download(cycle):
    res = webapp.run_shoptet_upload()
    assert res["ok"] is True
    assert res["sent"] == 0
    assert cycle["import"] == []
    assert res["skipped_second_sync"] is True
    assert cycle["run_sync"].count("shoptet_sync") == 1
    assert res["resynced"] == 1


def test_a_queued_change_goes_up_in_ONE_import_and_leaves_the_table(cycle):
    webapp.queue_shoptet_fields("parovania_eshop", "code;pairCode;internalNote",
                                [["A", "P", "https://x"]])
    webapp.queue_shoptet_fields("restock_skladom",
                                "code;pairCode;availabilityInStock",
                                [["B", "P2", "Skladom"]])
    res = webapp.run_shoptet_upload()
    assert len(cycle["import"]) == 1, "the whole table must ride in ONE import"
    header, rows, csv_safe = cycle["import"][0]
    assert header == "code;pairCode;availabilityInStock;internalNote"
    assert sorted(r[0] for r in rows) == ["A", "B"]
    assert csv_safe is True, "M3 — the combined import must keep the formula-injection guard"
    assert res["sent"] == 2 and res["confirmed"] == 2
    assert webapp._load_pending() == {}
    assert res["skipped_second_sync"] is False
    assert cycle["run_sync"].count("shoptet_sync") == 2
    assert res["resynced"] == 2


def test_a_code_missing_from_the_catalogue_is_blocked_and_kept(cycle, monkeypatch):
    monkeypatch.setattr(webapp, "_export_row_verdicts",
                        lambda rows, note_col=2: {"confirmed": set(), "absent": {"A"}})
    webapp.queue_shoptet_fields("parovania_eshop", "code;pairCode;internalNote",
                                [["A", "P", "https://x"]])
    res = webapp.run_shoptet_upload()
    assert res["blocked"] == 1
    assert cycle["import"] == []
    assert webapp._load_pending()["A"]["blocked"]["reason"] == "not-in-catalog"


def test_the_cycle_refuses_to_run_twice_at_once(cycle):
    with webapp._shoptet_cycle_claim():
        res = webapp.run_shoptet_upload()
    assert res["ok"] is False
    assert res["error"] == "cyklus už beží"          # #299 review M2 — Slovak, like the rest
    assert cycle["run_sync"] == []


def test_resynced_reflects_run_sync_s_ACTUAL_return_value_not_a_hard_coded_guess(cycle, monkeypatch):
    """#299 review I3 — `run_now`'s fire-and-forget True made `resynced` a lie the
    moment two hourly automations with the same interval overlapped (shoptet_sync
    already running for its own schedule). run_sync can genuinely return False
    (another run of the SAME automation already in flight) — resynced must reflect
    that, both for the first download and (when it happens) the second."""
    monkeypatch.setattr(webapp.RUNNER, "run_sync", lambda key: False)
    res = webapp.run_shoptet_upload()
    assert res["resynced"] == 0
    assert res["skipped_second_sync"] is True   # nothing was sent -> no 2nd sync attempted


# ── review carry-overs (Task 4 M2 / Task 5 minor / brief note on note_col) ──── #

def test_a_corrupt_pending_table_refuses_the_drain_instead_of_pretending_it_is_empty(cycle):
    """#299 review (Task 4 minor, deferred here as M2): the drain must carry the
    SAME strictness as queueing. A table we cannot parse is NOT an empty table —
    settling against {} would credit nothing while completing as if the hour were
    clean. The unconditional `_save_pending(settled)` at the end of the cycle
    re-validates the REAL file on disk (not what an earlier degraded read handed
    back) and must refuse loudly — proving `run_shoptet_upload` never swallows
    that refusal into a quiet no-op."""
    with open(webapp.PENDING_SHOPTET, "w", encoding="utf-8") as f:
        f.write("{ this is not json")
    with pytest.raises(webapp.StoreWipeRefused):
        webapp.run_shoptet_upload()


def test_a_claim_file_that_cannot_be_opened_skips_the_run_instead_of_crashing(
        cycle, tmp_path, monkeypatch):
    """#299 review (Task 5 minor, deferred to Task 6): os.open on the claim file
    was unguarded — a PermissionError/OSError (full disk, wrong perms) crashed the
    whole hourly run instead of the clean "skip this hour, try again next" the
    busy-claim path already gives. A claim path whose parent directory does not
    exist is a real, reproducible OSError (FileNotFoundError) from os.open — no
    permission trickery needed."""
    monkeypatch.setattr(webapp, "CYCLE_CLAIM",
                        str(tmp_path / "no-such-dir" / ".cycle.lock"))

    with webapp._shoptet_cycle_claim() as got:
        assert got is False        # never raises just to enter the context

    res = webapp.run_shoptet_upload()          # must not raise either
    assert res["ok"] is False
    assert cycle["run_sync"] == []              # nothing was even attempted


def test_the_import_is_skipped_when_another_import_is_already_running(cycle, monkeypatch):
    """`_import_rows_chunked`'s own docstring: the caller MUST hold `_import_lock`
    across the call — every other of its 7 call sites in this module does. Not
    holding it here would let this cycle's import race a manual "Spustiť teraz"
    of a producer still on its OLD direct-import path (parovania_eshop etc. are
    migrated to the queue in a later task). Simulate that race by making the lock
    already held: the cycle must skip ITS import (never call
    `_import_rows_chunked`), leave the row queued for the next hour, and report
    a non-ok, non-crashing result."""
    webapp.queue_shoptet_fields("parovania_eshop", "code;pairCode;internalNote",
                                [["A", "P", "https://x"]])
    assert webapp._import_lock.acquire(blocking=False) is True
    try:
        res = webapp.run_shoptet_upload()
    finally:
        webapp._import_lock.release()

    assert res["ok"] is False
    assert res["confirmed"] == 0
    assert res["sent"] == 1
    assert cycle["import"] == []                     # _import_rows_chunked never ran
    assert "A" in webapp._load_pending()              # row stays queued for next hour


def test_note_col_none_asks_only_whether_the_code_is_in_the_catalogue(tmp_path, monkeypatch):
    """The combined-import verdict pass has no single fixed note column (every row
    in the drain can carry a different set of queued fields), so it must never
    index `r[note_col]` with `note_col=None` — it only withholds codes the eshop
    does not carry at all."""
    export = tmp_path / "products.csv"
    export.write_bytes(("code;pairCode;internalNote;supplier\r\n"
                        "A;P;whatever;\r\n").encode("cp1250"))
    monkeypatch.setattr(webapp, "SRC", str(export))
    monkeypatch.setattr(webapp, "EXPORT_MIN_CODES", 1)

    v = webapp._export_row_verdicts(
        [["A", "P", "Skladom"], ["B", "P2", "Skladom"]], note_col=None)

    assert v == {"confirmed": set(), "absent": {"B"}}


# ── #299 review I1 — the producer half of the cycle had ZERO tests: the ────── #
# ── `cycle` fixture stubs RUNNER.status to [], so CYCLE_PRODUCERS never ran, ── #
# ── and no queued row ever carried a credit_group, so _credit_producer was ─── #
# ── never even called. Three mutations survived a green suite because of it. ─ #

def test_only_an_ENABLED_and_QUEUE_MIGRATED_producer_runs_and_producers_reflects_it(
        cycle, monkeypatch):
    """Kills the mutation that deletes `if key not in enabled: continue` — without
    it the cycle would start DISABLED automations that write to the live eshop
    every single hour, a direct hole in the #93 contract."""
    monkeypatch.setattr(webapp, "QUEUE_MIGRATED",
                        ("parovania_eshop", "grube_externalcode"))
    monkeypatch.setattr(webapp.RUNNER, "status", lambda: [
        {"key": "parovania_eshop", "enabled": True},
        {"key": "grube_externalcode", "enabled": False},
    ])
    res = webapp.run_shoptet_upload()
    assert cycle["run_sync"].count("parovania_eshop") == 1
    assert "grube_externalcode" not in cycle["run_sync"]
    assert res["producers"] == {"parovania_eshop": True}


def test_a_producer_not_yet_migrated_to_the_queue_never_runs_even_when_enabled(
        cycle, monkeypatch):
    """#299 review I2 — every one of CYCLE_PRODUCERS still writes straight to the
    live eshop today (QUEUE_MIGRATED stays empty until Tasks 8-10 migrate them one
    by one, in the SAME commit that switches each one over). The cycle must NEVER
    start one just because a manager enabled it in the meantime — that would turn
    a 1x/day automation into 24x/day writes to forestshop.sk. Kills the mutation
    that deletes the `QUEUE_MIGRATED` membership check."""
    monkeypatch.setattr(webapp.RUNNER, "status",
                        lambda: [{"key": "parovania_eshop", "enabled": True}])
    res = webapp.run_shoptet_upload()
    assert "parovania_eshop" not in cycle["run_sync"]
    assert res["producers"] == {}


def test_a_confirmed_credit_for_parovania_eshop_actually_writes_PAIRINGS_STATE(
        cycle, tmp_path, monkeypatch):
    """Kills the mutation that empties `_credit_producer`'s body — without it a
    confirmed group would never be recorded and the producer would re-upload the
    same link forever."""
    state = tmp_path / "uploaded_pairings.json"
    monkeypatch.setattr(webapp, "PAIRINGS_STATE", str(state))
    webapp.queue_shoptet_fields(
        "parovania_eshop", "code;pairCode;internalNote",
        [["A", "P", "https://dodavatel.sk/x"]],
        credit_group={"A": "FOREST|60648"},
        credit_value={"A": "https://dodavatel.sk/x"})

    res = webapp.run_shoptet_upload()

    assert res["confirmed"] == 1
    d = json.loads(state.read_text(encoding="utf-8"))
    assert d["FOREST|60648"] == "https://dodavatel.sk/x"


def test_a_credit_for_an_unknown_store_is_dropped_with_a_log_not_a_crash(
        cycle, caplog):
    """The credit map only knows `parovania_eshop` today — a group credited by
    any of the other four producers must be discarded with a log line, not crash
    the cycle (a silent loss for 4 of 5 future producers otherwise)."""
    webapp.queue_shoptet_fields(
        "restock_skladom", "code;pairCode;availabilityInStock",
        [["A", "P", "Skladom"]],
        credit_group={"A": "G"}, credit_value={"A": "Skladom"})

    with caplog.at_level(logging.WARNING, logger="webreview"):
        res = webapp.run_shoptet_upload()

    assert res["ok"] is True
    assert res["confirmed"] == 1
    assert any("neznámy kredit store" in r.message for r in caplog.records)
