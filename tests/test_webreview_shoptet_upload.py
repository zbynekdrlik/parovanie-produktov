import json
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
    calls = {"import": [], "run_now": []}
    monkeypatch.setattr(webapp, "_import_rows_chunked",
                        lambda rows, header, dry, prefix, timeout=900: (
                            calls["import"].append((header, [list(r) for r in rows]))
                            or {"ok": True, "partial": False,
                                "success_codes": {r[0] for r in rows},
                                "partial_codes": set(), "partial_failed": 0,
                                "chunks_total": 1, "chunks_ok": 1,
                                "processed": len(rows), "updated": len(rows),
                                "failed": 0, "rc": 0, "error_detail": None,
                                "stdout_tail": "", "err": "", "unreadable": False}))
    monkeypatch.setattr(webapp, "_export_row_verdicts",
                        lambda rows, note_col=2: {"confirmed": set(), "absent": set()})
    monkeypatch.setattr(webapp.RUNNER, "run_now",
                        lambda key: calls["run_now"].append(key) or True)
    monkeypatch.setattr(webapp.RUNNER, "status", lambda: [])
    return calls


def test_an_empty_table_uploads_nothing_and_skips_the_second_download(cycle):
    res = webapp.run_shoptet_upload()
    assert res["ok"] is True
    assert res["sent"] == 0
    assert cycle["import"] == []
    assert res["skipped_second_sync"] is True
    assert cycle["run_now"].count("shoptet_sync") == 1


def test_a_queued_change_goes_up_in_ONE_import_and_leaves_the_table(cycle):
    webapp.queue_shoptet_fields("parovania_eshop", "code;pairCode;internalNote",
                                [["A", "P", "https://x"]])
    webapp.queue_shoptet_fields("restock_skladom",
                                "code;pairCode;availabilityInStock",
                                [["B", "P2", "Skladom"]])
    res = webapp.run_shoptet_upload()
    assert len(cycle["import"]) == 1, "the whole table must ride in ONE import"
    header, rows = cycle["import"][0]
    assert header == "code;pairCode;availabilityInStock;internalNote"
    assert sorted(r[0] for r in rows) == ["A", "B"]
    assert res["sent"] == 2 and res["confirmed"] == 2
    assert webapp._load_pending() == {}
    assert res["skipped_second_sync"] is False
    assert cycle["run_now"].count("shoptet_sync") == 2


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
    assert res["error"] == "cycle-busy"
    assert cycle["run_now"] == []


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
    assert cycle["run_now"] == []               # nothing was even attempted


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
