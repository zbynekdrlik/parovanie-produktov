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
    # #299 review N3: the cycle now also asks `_export_age_s()` whether to skip
    # the PRE-import download. Default to "unknown age" (never fresh) so every
    # test below keeps its pre-N3 behaviour (PRE-import download always runs)
    # regardless of the REAL data/products.csv on whatever machine runs this
    # suite — a test that wants the freshness branch overrides this itself.
    monkeypatch.setattr(webapp, "_export_age_s", lambda: None)
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


# ── #299 review N3 — shoptet_sync and shoptet_upload share the same 60-minute ─ #
# ── schedule, so a tick that ran shoptet_sync moments ago must not re-fetch ─── #
# ── the 57 MB catalogue a second time via the PRE-import download. ─────────── #

def test_a_fresh_export_skips_the_PRE_import_download_but_not_the_POST_import_one(
        cycle, monkeypatch):
    monkeypatch.setattr(webapp, "_export_age_s", lambda: 5 * 60)  # 5 min old
    webapp.queue_shoptet_fields("parovania_eshop", "code;pairCode;internalNote",
                                [["A", "P", "https://x"]])
    res = webapp.run_shoptet_upload()
    assert cycle["run_sync"].count("shoptet_sync") == 1, (
        "only the POST-import download must have run")
    assert res["resynced"] == 1
    assert res["skipped_second_sync"] is False


def test_a_stale_export_still_runs_the_PRE_import_download(cycle, monkeypatch):
    monkeypatch.setattr(webapp, "_export_age_s", lambda: 20 * 60)  # 20 min old
    res = webapp.run_shoptet_upload()
    assert cycle["run_sync"].count("shoptet_sync") == 1, (
        "the PRE-import download must have run")
    assert res["resynced"] == 1
    assert res["skipped_second_sync"] is True  # empty table -> nothing sent


def test_an_unknown_export_age_is_never_treated_as_fresh(cycle, monkeypatch):
    """No export on disk yet (or a test double that cannot stat it) must NEVER
    be read as "fresh" — the fail-safe direction is always to download."""
    monkeypatch.setattr(webapp, "_export_age_s", lambda: None)
    res = webapp.run_shoptet_upload()
    assert cycle["run_sync"].count("shoptet_sync") == 1
    assert res["resynced"] == 1


def test_the_freshness_boundary_itself_still_counts_as_stale(cycle, monkeypatch):
    # exactly SHOPTET_UPLOAD_SKIP_PRESYNC_FRESHER_THAN_S old is NOT "younger
    # than" the limit -> the download must still run.
    monkeypatch.setattr(webapp, "_export_age_s",
                        lambda: webapp.SHOPTET_UPLOAD_SKIP_PRESYNC_FRESHER_THAN_S)
    res = webapp.run_shoptet_upload()
    assert cycle["run_sync"].count("shoptet_sync") == 1
    assert res["resynced"] == 1


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
    """#299 review I2 — a producer not yet in QUEUE_MIGRATED still writes straight to
    the live eshop on its own daily schedule (Tasks 8-10 migrate the five producers
    one by one, in the SAME commit that switches each one over — Task 8 moved
    grube_externalcode/split_links; parovania_eshop is still pending). The cycle
    must NEVER start an unmigrated producer just because a manager enabled it in
    the meantime — that would turn a 1x/day automation into 24x/day writes to
    forestshop.sk. Kills the mutation that deletes the `QUEUE_MIGRATED` membership
    check."""
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


# ── #299 Task 8 — the first two producers switch to the queue: GRUBE ─────────── #
# ── externalCode write-back and per-size split links. Both automations start ── #
# ── DISABLED (#93 contract), so this is zero live-write risk. ───────────────── #

def test_grube_producer_queues_instead_of_importing(pend, monkeypatch):
    """The brief's own fixture used `{"pairCode": ..., "externalCode": ...}` for the
    grube_codes.json entry shape — but `new_externalcode_keys`/`externalcode_rows`
    read `info["itemId"]` (confirmed against the real grube_codes.json producer,
    `.claude/skills/grube` + `test_webreview_grube_externalcode.py`'s own
    `_seed_grube` helper: `{itemId, size, deUrl, productId}`). The brief's literal
    fixture has no `itemId` key at all, so `new_externalcode_keys` would filter code
    "A" out entirely (empty itemId) and the assertion `queued == 1` would never even
    exercise the guard it exists to pin — corrected here to the real shape."""
    monkeypatch.setattr(webapp, "_import_rows_chunked",
                        lambda *a, **k: pytest.fail("producer must not import"))
    monkeypatch.setattr(webapp, "_load_grube_codes",
                        lambda: {"A": {"itemId": "12345"}})
    monkeypatch.setattr(webapp, "_load_uploaded_externalcodes", lambda: {})
    res, status = webapp._do_upload_externalcodes(False)
    assert status == 200
    assert res["queued"] == 1
    assert res["count"] == res["queued"]           # #299 review m1 — never allowed to drift
    d = webapp._load_pending()
    assert d["A"]["fields"]["externalCode"]["value"] == "12345"
    assert d["A"]["fields"]["externalCode"]["source"] == "grube_externalcode"
    # #299 review I2 — field["credit"]["value"] was never asserted anywhere in the
    # whole suite; that gap is exactly how C1 (split_links crediting the wrong,
    # normalized value) shipped unnoticed. For grube_externalcode the credit value
    # is the same raw itemId in both places (no normalization happens), so this
    # pins the healthy case; the GRUBE-normalizing case lives in
    # test_split_links_grube_credit_value_is_RAW_not_normalized below.
    assert d["A"]["fields"]["externalCode"]["credit"]["value"] == "12345"


def test_grube_producer_never_credits_itself(pend, monkeypatch):
    """#299 Task 8 decision #2 (carried over from earlier tasks, not in this task's
    brief): the producer must not write its own uploaded_externalcodes.json anymore
    — that credit belongs to the drain's `_credit_producer`, called only AFTER
    Shoptet's import actually confirms the row (the #257 class of bug: an
    "uploaded" mark written on our own say-so). Kills a regression that re-adds
    `_record_uploaded`/`_save_uploaded_externalcodes` to the producer itself."""
    monkeypatch.setattr(webapp, "_load_grube_codes",
                        lambda: {"A": {"itemId": "12345"}})
    monkeypatch.setattr(webapp, "_load_uploaded_externalcodes", lambda: {})
    monkeypatch.setattr(webapp, "_save_uploaded_externalcodes",
                        lambda d: pytest.fail("producer must not credit itself"))
    res, status = webapp._do_upload_externalcodes(False)
    assert status == 200
    assert res["queued"] == 1


def test_split_links_producer_queues_instead_of_importing(pend, monkeypatch):
    monkeypatch.setattr(webapp, "_import_rows_chunked",
                        lambda *a, **k: pytest.fail("producer must not import"))
    monkeypatch.setattr(webapp, "PRODUCTS",
                        [{"key": "TRIGONA|395", "variant_codes": ["60645/S"]}])
    monkeypatch.setattr(webapp, "CODE2PAIR", {"60645/S": "395"})
    monkeypatch.setattr(webapp, "_load_variant_links",
                        lambda: {"60645/S": "https://trigona.sk/s"})
    monkeypatch.setattr(webapp, "_load_uploaded_variant_links", lambda: {})
    monkeypatch.setattr(webapp, "_load_decisions",
                        lambda: {"TRIGONA|395": {"status": "split", "url": ""}})
    res, status = webapp._do_upload_variant_links(False)
    assert status == 200
    assert res["queued"] == 1
    assert res["count"] == res["queued"]           # #299 review m1 — never allowed to drift
    d = webapp._load_pending()
    assert d["60645/S"]["fields"]["internalNote"]["value"] == "https://trigona.sk/s"
    assert d["60645/S"]["fields"]["internalNote"]["source"] == "split_links"
    # #299 review I2 — TRIGONA doesn't normalize, so raw == queued value here; the
    # GRUBE case (where they DIFFER) is the actual C1 regression test below.
    assert d["60645/S"]["fields"]["internalNote"]["credit"]["value"] == "https://trigona.sk/s"


def test_split_links_grube_credit_value_is_RAW_not_normalized(pend, monkeypatch):
    """#299 review C1 (Critical) — the split-link credit value must be the RAW
    variant_links.json URL that `import_builder.new_variant_link_keys` compares
    against `uploaded_variant_links.json`, never the normalized `.de` URL
    `link_rows` builds for the eshop's `internalNote` cell. The old code credited
    `r[2]` (the normalized cell value) — for GRUBE that is a DIFFERENT string than
    the raw stored link, so the incremental check (`uploaded.get(c) != u`, always
    comparing against RAW) could never see a match: the same link would queue
    again on every single hourly run, forever, and `total_uploaded` would stay 0
    even after the drain "confirmed" it. Regression: revert `credit_value` back to
    `r[0]: r[2]` and this fails (credit.value becomes the normalized .de URL)."""
    monkeypatch.setattr(webapp, "PRODUCTS", [
        {"key": "GRUBE|700", "supplier": "GRUBE", "variant_codes": ["70000/S"]}])
    monkeypatch.setattr(webapp, "CODE2PAIR", {"70000/S": "700"})
    raw = "https://www.grube.sk/p/grand-nord/154773/?q=a#itemId=1"
    monkeypatch.setattr(webapp, "_load_variant_links", lambda: {"70000/S": raw})
    monkeypatch.setattr(webapp, "_load_uploaded_variant_links", lambda: {})
    monkeypatch.setattr(webapp, "_load_decisions",
                        lambda: {"GRUBE|700": {"status": "split", "url": ""}})

    res, status = webapp._do_upload_variant_links(False)

    assert status == 200
    assert res["queued"] == 1
    d = webapp._load_pending()
    field = d["70000/S"]["fields"]["internalNote"]
    # the CELL sent to Shoptet is normalized (proves link_rows/to_grube_de still ran)
    assert field["value"] == "https://www.grube.de/p/x/154773/"
    # the CREDIT must be the RAW value — what new_variant_link_keys will compare
    # against uploaded_variant_links.json on the next run
    assert field["credit"]["value"] == raw


def test_split_links_producer_never_credits_itself(pend, monkeypatch):
    """Mirrors `test_grube_producer_never_credits_itself` for the second Task 8
    producer — same #257-class reasoning."""
    monkeypatch.setattr(webapp, "PRODUCTS",
                        [{"key": "TRIGONA|395", "variant_codes": ["60645/S"]}])
    monkeypatch.setattr(webapp, "CODE2PAIR", {"60645/S": "395"})
    monkeypatch.setattr(webapp, "_load_variant_links",
                        lambda: {"60645/S": "https://trigona.sk/s"})
    monkeypatch.setattr(webapp, "_load_uploaded_variant_links", lambda: {})
    monkeypatch.setattr(webapp, "_load_decisions",
                        lambda: {"TRIGONA|395": {"status": "split", "url": ""}})
    monkeypatch.setattr(webapp, "_save_uploaded_variant_links",
                        lambda d: pytest.fail("producer must not credit itself"))
    res, status = webapp._do_upload_variant_links(False)
    assert status == 200
    assert res["queued"] == 1


def test_all_four_migrated_producers_are_open_in_the_queue_migration_gate(cycle, monkeypatch):
    """#299 review m4 — the old version of this test asserted membership in the
    QUEUE_MIGRATED tuple directly (`assert "x" in webapp.QUEUE_MIGRATED`), which is
    an assert about a CONSTANT, not about BEHAVIOUR: a mutation that emptied
    QUEUE_MIGRATED entirely was the only thing it could ever catch, and the gate's
    real behaviour (a producer missing from the gate never runs even when enabled)
    was already independently pinned by
    `test_a_producer_not_yet_migrated_to_the_queue_never_runs_even_when_enabled`.
    Rewritten to exercise `run_shoptet_upload` itself with all four migrated
    producers (Task 8's grube_externalcode/split_links + Task 9's
    restock_skladom/stock_skladom) ENABLED against the REAL (unmonkeypatched)
    QUEUE_MIGRATED — so a regression that drops ANY of the four keys out of the
    gate leaves that producer's key missing from `cycle["run_sync"]`/
    `res["producers"]`, which this test can actually observe. Kills a regression
    that removes any of the four from QUEUE_MIGRATED (verified by mutation, not by
    inspection — see the report)."""
    monkeypatch.setattr(webapp.RUNNER, "status", lambda: [
        {"key": "grube_externalcode", "enabled": True},
        {"key": "split_links", "enabled": True},
        {"key": "restock_skladom", "enabled": True},
        {"key": "stock_skladom", "enabled": True},
    ])
    res = webapp.run_shoptet_upload()
    assert cycle["run_sync"].count("grube_externalcode") == 1
    assert cycle["run_sync"].count("split_links") == 1
    assert cycle["run_sync"].count("restock_skladom") == 1
    assert cycle["run_sync"].count("stock_skladom") == 1
    assert res["producers"] == {"grube_externalcode": True, "split_links": True,
                                 "restock_skladom": True, "stock_skladom": True}


def test_a_confirmed_credit_for_grube_externalcode_actually_writes_EXTERNALCODES_STATE(
        cycle, tmp_path, monkeypatch):
    """Mirrors `test_a_confirmed_credit_for_parovania_eshop_actually_writes_
    PAIRINGS_STATE` for the `_credit_producer` mapping this task adds. Kills the
    mutation that drops "grube_externalcode" from that mapping."""
    state = tmp_path / "uploaded_externalcodes.json"
    monkeypatch.setattr(webapp, "EXTERNALCODES_STATE", str(state))
    webapp.queue_shoptet_fields(
        "grube_externalcode", "code;pairCode;externalCode",
        [["A", "P", "12345"]], credit_group={"A": "A"}, credit_value={"A": "12345"})

    res = webapp.run_shoptet_upload()

    assert res["confirmed"] == 1
    d = json.loads(state.read_text(encoding="utf-8"))
    assert d["A"] == "12345"


def test_a_confirmed_credit_for_split_links_actually_writes_VARIANT_LINKS_STATE(
        cycle, tmp_path, monkeypatch):
    """Kills the mutation that drops "split_links" from the `_credit_producer`
    mapping this task adds."""
    state = tmp_path / "uploaded_variant_links.json"
    monkeypatch.setattr(webapp, "VARIANT_LINKS_STATE", str(state))
    webapp.queue_shoptet_fields(
        "split_links", "code;pairCode;internalNote",
        [["60645/S", "395", "https://trigona.sk/s"]],
        credit_group={"60645/S": "60645/S"},
        credit_value={"60645/S": "https://trigona.sk/s"})

    res = webapp.run_shoptet_upload()

    assert res["confirmed"] == 1
    d = json.loads(state.read_text(encoding="utf-8"))
    assert d["60645/S"] == "https://trigona.sk/s"


def test_a_GRUBE_split_link_is_credited_and_never_requeued_again_end_to_end(
        cycle, tmp_path, monkeypatch):
    """#299 review C1/I2 — the full producer→drain→credit loop for the GRUBE
    normalizing case, proving the fix closes the actual live-eshop-facing bug (not
    just the isolated credit-value assert above): queue the GRUBE split link via
    the REAL producer (`_do_upload_variant_links`, normalized cell + raw credit),
    run the hourly cycle so `_credit_producer` writes `uploaded_variant_links.json`
    from that same credit value, then run the producer AGAIN and confirm the link
    is now genuinely skipped (queued == 0) instead of being queued forever (the
    review's own probe measured `SECOND RUN count: 1` against the buggy code —
    this reproduces that exact scenario end to end)."""
    monkeypatch.setattr(webapp, "VARIANT_LINKS_STATE", str(tmp_path / "uploaded_variant_links.json"))
    monkeypatch.setattr(webapp, "PRODUCTS", [
        {"key": "GRUBE|700", "supplier": "GRUBE", "variant_codes": ["70000/S"]}])
    monkeypatch.setattr(webapp, "CODE2PAIR", {"70000/S": "700"})
    raw = "https://www.grube.sk/p/grand-nord/154773/?q=a#itemId=1"
    monkeypatch.setattr(webapp, "_load_variant_links", lambda: {"70000/S": raw})
    # NOT monkeypatched: `_load_uploaded_variant_links` stays the REAL function
    # (`_read_json_store(VARIANT_LINKS_STATE, {})`) — it must re-read the state
    # `_credit_producer` writes below, or the second call below proves nothing.
    monkeypatch.setattr(webapp, "_load_decisions",
                        lambda: {"GRUBE|700": {"status": "split", "url": ""}})

    res1, _ = webapp._do_upload_variant_links(False)
    assert res1["queued"] == 1

    cycle_res = webapp.run_shoptet_upload()
    assert cycle_res["confirmed"] == 1

    res2, _ = webapp._do_upload_variant_links(False)
    assert res2["queued"] == 0, (
        "the same GRUBE split link must NOT be a candidate again once the drain "
        "confirmed it — a non-zero queued here is the C1 infinite-requeue bug")


# ── #299 review m3 — dry_run must PREVIEW what would be queued, not just say ── #
# ── "0" unconditionally. The old direct-import producers' dry_run reached a ── #
# ── real Shoptet dry-run validation; these two no longer import at all, so ── #
# ── the honest equivalent is: report how many field values WOULD be queued, ── #
# ── while genuinely queueing nothing. ────────────────────────────────────── #

def test_dry_run_previews_the_grube_queue_count_without_queuing_anything(pend, monkeypatch):
    monkeypatch.setattr(webapp, "_load_grube_codes",
                        lambda: {"A": {"itemId": "12345"}, "B": {"itemId": "999"}})
    monkeypatch.setattr(webapp, "_load_uploaded_externalcodes", lambda: {})

    res, status = webapp._do_upload_externalcodes(True)

    assert status == 200
    assert res["dry_run"] is True
    assert res["queued"] == 0                 # nothing was ACTUALLY queued
    assert res["would_queue"] == 2             # but the preview says what WOULD be
    assert webapp._load_pending() == {}        # and the pending table proves it


def test_dry_run_previews_the_split_links_queue_count_without_queuing_anything(pend, monkeypatch):
    monkeypatch.setattr(webapp, "PRODUCTS",
                        [{"key": "TRIGONA|395", "variant_codes": ["60645/S"]}])
    monkeypatch.setattr(webapp, "CODE2PAIR", {"60645/S": "395"})
    monkeypatch.setattr(webapp, "_load_variant_links",
                        lambda: {"60645/S": "https://trigona.sk/s"})
    monkeypatch.setattr(webapp, "_load_uploaded_variant_links", lambda: {})
    monkeypatch.setattr(webapp, "_load_decisions",
                        lambda: {"TRIGONA|395": {"status": "split", "url": ""}})

    res, status = webapp._do_upload_variant_links(True)

    assert status == 200
    assert res["dry_run"] is True
    assert res["queued"] == 0
    assert res["would_queue"] == 1
    assert webapp._load_pending() == {}


# ── #299 Task 9 — restock_skladom / stock_skladom switch to the queue ────────── #
# ── Neither producer imports directly any more, and neither credits itself — a ── #
# ── candidate is entirely state-driven (Vypredané+visible in the LIVE export), ── #
# ── so once Shoptet confirms the flip the product's own state stops it being a ── #
# ── candidate again; no separate "already uploaded" bookkeeping exists or is ──── #
# ── needed for either producer (unlike grube_externalcode/split_links). ──────── #

def test_restock_queues_availability_and_stock_without_a_credit(pend, monkeypatch):
    monkeypatch.setattr(webapp, "_import_rows_chunked",
                        lambda *a, **k: pytest.fail("producer must not import"))
    monkeypatch.setattr(webapp, "_restock_candidate_rows",
                        lambda: [["A", "P", "Skladom", "Skladom", "visible", "5"]])
    res = webapp.run_restock_skladom()
    assert res["queued"] == 4
    f = webapp._load_pending()["A"]["fields"]
    assert f["availabilityInStock"]["value"] == "Skladom"
    assert f["stock"]["value"] == "5"
    assert "credit" not in f["stock"]
