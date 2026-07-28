"""In-app „Párovania → eshop" automation (#109) — the nightly push of the
workers' NEW pairings (reorder links → internalNote) + newly assigned suppliers
(→ supplier field) to the Shoptet eshop, migrated from the n8n workflow
`YuDugCCOnwejRfva` onto the generic automation runner (#93).

Hermetic: run_import (the careful Shoptet import subprocess) is monkeypatched —
NO real eshop write ever happens in a test. Every store path is redirected to
tmp. Mirrors test_webreview_shoptet_sync.py's isolation pattern; the automation
reuses the SAME upload cores (_do_upload_pairings/_do_upload_suppliers) as the
two n8n endpoints, so the existing endpoint tests in test_webreview.py stay
green (one place for the logic, NEkopíruj logiku).
"""
import csv as _csv
import inspect
import io
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webreview"))
import app as webapp  # noqa: E402

from tests.conftest import authed_client  # noqa: E402

# The PRODUCTION export-plausibility floor, captured at import time — the `iso` fixture
# lowers the module global to 1 so the tiny fixture exports pass, so a test that wants to
# pin the real threshold must restore THIS value (and cannot be disarmed by the fixture).
PROD_EXPORT_MIN_CODES = webapp.EXPORT_MIN_CODES


def _product(variant_codes=("1/M",)):
    return {"key": "BETALOV|P1", "idx": 0, "supplier": "BETALOV", "name": "Bunda Test",
            "pairCode": "P1", "variant_codes": list(variant_codes),
            "our_url": "https://forestshop/x", "ai_status": "matched",
            "ai_chosen_url": "", "ai_reason": "", "candidates": [], "current": {}}


# The production reader splits on \n, \r and \r\n and on NOTHING else. `str.splitlines`
# also splits on \x0b \x0c \x1c \x1d \x1e \x85 \u2028 \u2029, so a fixture using it would
# parse under different rules than production (verified: unquoted "a\x0cb" is ONE record
# in production, two through splitlines).
_LINE_SPLIT = re.compile(r"(?<=\n)|(?<=\r)(?!\n)")


def _export_lines(text):
    """monkeypatch value for `webapp._iter_export_lines` — the nightly push STREAMS
    the catalog export line by line (#272), so THIS is the seam a test feeds a fake
    export through (patching the whole-text `_read_export_for_links` no longer
    reaches the push: it is defined in terms of this one). Returns a fresh iterator
    on every call, exactly like the real generator."""
    lines = [ln for ln in _LINE_SPLIT.split(text) if ln]
    return lambda: iter(lines)


def _stub_catalog_export(monkeypatch, codes, notes=None):
    """Give the fixture export the codes THIS test pushes (no own supplier, no note).
    Production's export lists all ~14 000 catalogue codes, so a code the export does not
    list is — since #270 — a code the eshop genuinely does not have: its row is HELD and
    reported. That is never what a chunking / partial-failure test means, so those tests
    declare their catalogue here instead of relying on the fixture's three codes."""
    notes = notes or {}
    monkeypatch.setattr(webapp, "_iter_export_lines", _export_lines(
        "code;pairCode;internalNote;supplier\r\n"
        + "".join(f"{c};P;{notes.get(c, '')};\r\n" for c in codes)))


@pytest.fixture
def iso(tmp_path, monkeypatch):
    """Isolate every store the automation reads/writes + the import subprocess."""
    monkeypatch.setattr(webapp.RUNNER, "state_path", str(tmp_path / "automations.json"))
    monkeypatch.setattr(webapp, "OUT", str(tmp_path))
    monkeypatch.setattr(webapp, "DECISIONS", str(tmp_path / "decisions.json"))
    monkeypatch.setattr(webapp, "PAIRINGS_STATE", str(tmp_path / "uploaded_pairings.json"))
    # #38: the manager's inline 'Na objednanie' pairings — own tmp path (never the
    # real live order_pairings.json this box also serves).
    monkeypatch.setattr(webapp, "ORDER_PAIRINGS", str(tmp_path / "order_pairings.json"))
    monkeypatch.setattr(webapp, "SUPPLIER_ASSIGN", str(tmp_path / "supplier_assignments.json"))
    monkeypatch.setattr(webapp, "SUPPLIERS_STATE", str(tmp_path / "uploaded_suppliers.json"))
    # #299 Task 10: both cores now QUEUE into the shared pending_shoptet table
    # instead of importing directly — isolate it like every other store here.
    monkeypatch.setattr(webapp, "PENDING_SHOPTET", str(tmp_path / "pending_shoptet.json"))
    products = [_product()]
    monkeypatch.setattr(webapp, "PRODUCTS", products)
    monkeypatch.setattr(webapp, "CODE2PAIR", {"1/M": "P1", "9/Z": "777"})
    # BUG 1 fail-closed: the supplier write-back refuses to run without a USABLE catalog
    # export (missing/empty OR fewer than EXPORT_MIN_CODES codes → blocked). Default to a
    # small but usable export that LISTS every code these tests push (none of them carries
    # its own supplier and none carries our note → nothing is excluded, nothing is credited
    # or held, the normal push path proceeds); tests that assert the exclusion /
    # blocked-on-unusable / missing-code behaviour override the export seam themselves.
    # Without this the CI box (no data/products.csv) reads an empty export and blocks.
    monkeypatch.setattr(webapp, "_iter_export_lines", _export_lines(
        "code;pairCode;internalNote;supplier\r\n"
        "1/M;P1;;\r\n9/Z;777;;\r\n7/Y;P1;;\r\n"))
    # #270: production refuses to trust an export carrying fewer than EXPORT_MIN_CODES
    # (1000) codes — a fake export here has a handful, so the floor is lowered for the
    # fixtures and pinned SEPARATELY at its real value by
    # test_an_implausibly_small_export_is_not_trusted (verdicts) and
    # test_an_implausibly_small_export_blocks_the_supplier_write_back (the write gate).
    monkeypatch.setattr(webapp, "EXPORT_MIN_CODES", 1)
    # #215 fail-closed precondition (PR #295 review): the „the eshop overtook this
    # assignment" removal now needs POSITIVE evidence that the write-back record was
    # really read off disk — a missing or corrupt `uploaded_suppliers.json` degrades to
    # `{}`, which reads as „nothing was ever written back" and would condemn every
    # assignment at once. An on-disk empty record is the realistic state after the first
    # run, and it is what the removal tests here mean by „never uploaded".
    webapp._save_uploaded_suppliers({})
    return {"tmp": tmp_path, "products": products}


def _seed_pairing():
    webapp._save_decisions({"BETALOV|P1": {"status": "good", "url": "https://supplier/x"}})


def _seed_supplier():
    webapp._save_supplier_assign({"9/Z": "BETALOV"})


def _seed_order_pairing():
    webapp._save_order_pairings({"7/Y": "https://supplier/inline"})


def _ok_import():
    """A run_import stub that records every CSV it was handed (header + rows) and
    reports a clean success. Handles both the links CSV and the suppliers CSV."""
    calls = []

    def fake_run(csv_path, dry_run=False, timeout=300):
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            rd = list(_csv.reader(f, delimiter=";"))
        calls.append({"header": rd[0], "rows": rd[1:], "dry_run": dry_run})
        # report back exactly as many rows as the CSV carried — the real script always
        # does (Shoptet's 'Spracované: N' == the rows we submitted), and an rc-0 result
        # whose count disagrees is now correctly refused as a chunk we may not credit
        n = len(rd) - 1
        return 0, f"VÝSLEDOK: spracované={n} upravené={n} zlyhania=0", ""
    return fake_run, calls


# ── registration + status ──────────────────────────────────────────────────────
def test_parovania_eshop_registered_disabled_daily_2100(iso):
    c = authed_client()
    j = c.get("/api/automations").get_json()
    (a,) = [x for x in j["automations"] if x["key"] == "parovania_eshop"]
    assert a["name"] == "Párovania → eshop"
    # SAFETY: this automation WRITES to the live eshop → deploy starts stopped (#93 contract)
    assert a["enabled"] is False
    assert a["schedule"] == "denne o 21:00"
    assert a["running"] is False


# ── successful nightly push ─────────────────────────────────────────────────────
def test_run_pushes_pairings_and_suppliers_and_records_counts(iso, monkeypatch):
    """#299 Task 10 — `run_parovania_eshop` no longer imports directly through
    either core: both now QUEUE their candidate rows into the shared
    pending_shoptet table for the next hourly "Sync do Shoptetu" drain
    (`test_webreview_shoptet_upload.py` covers the drain + credit path end to
    end). `count`/`total_uploaded` stay 0 here — the `iso` fixture's catalog
    export lists both codes but with a DIFFERENT internalNote, so neither row is
    export-confirmed; `queued` is what actually landed in the pending table."""
    _seed_pairing()
    _seed_supplier()
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: pytest.fail("must not import — must queue"))

    result = webapp.run_parovania_eshop()

    assert result["status"] == "ok"
    assert result["pairings"]["queued"] == 1
    assert result["pairings"]["count"] == 0
    assert result["pairings"]["total_uploaded"] == 0
    assert result["pairings"]["total_products"] == 1
    assert result["suppliers"]["queued"] == 1
    assert result["suppliers"]["count"] == 1     # suppliers has no confirm step — count==queued
    assert result["suppliers"]["total_uploaded"] == 0
    assert result["suppliers"]["total_assigned"] == 1
    assert result["review_url"].startswith("https://")

    # BOTH cores queued — one links field, one supplier field, distinct sources
    pending = webapp._load_pending()
    assert pending["1/M"]["fields"]["internalNote"]["value"] == "https://supplier/x"
    assert pending["1/M"]["fields"]["internalNote"]["source"] == "parovania_eshop"
    assert pending["9/Z"]["fields"]["supplier"]["value"] == "BETALOV"
    assert pending["9/Z"]["fields"]["supplier"]["source"] == "parovania_eshop_suppliers"

    # neither core writes its own incremental state — that credit belongs to the
    # hourly drain (`_credit_producer`), only once Shoptet actually confirms it
    assert not (iso["tmp"] / "uploaded_pairings.json").exists()
    assert json.loads((iso["tmp"] / "uploaded_suppliers.json").read_text()) == {}

    # simulate the drain confirming + crediting both (its own confirm→credit path
    # is test_webreview_shoptet_upload.py's job)
    webapp._credit_producer("parovania_eshop", {"BETALOV|P1": "https://supplier/x"})
    webapp._credit_producer("parovania_eshop_suppliers", {"9/Z": "BETALOV"})
    assert json.loads((iso["tmp"] / "uploaded_pairings.json").read_text())["BETALOV|P1"] \
        == "https://supplier/x"
    assert json.loads((iso["tmp"] / "uploaded_suppliers.json").read_text())["9/Z"] == "BETALOV"


# ── #38: the nightly push ALSO covers inline order_pairings (via the SAME shared
#    _do_upload_pairings core, no new HTTP round-trip / no duplicated logic) ────
def test_run_also_pushes_inline_order_pairings(iso, monkeypatch):
    """#299 Task 10 — an inline order pairing now QUEUES exactly like a reviewed
    decision, credit_group `order:<code>`; credited only by the drain."""
    _seed_pairing()
    _seed_order_pairing()
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: pytest.fail("must not import — must queue"))

    result = webapp.run_parovania_eshop()

    assert result["status"] == "ok"
    assert result["pairings"]["order_count"] == 0     # not credited yet
    assert result["pairings"]["order_blocked"] == 0
    pending = webapp._load_pending()
    assert pending["7/Y"]["fields"]["internalNote"]["value"] == "https://supplier/inline"
    assert pending["7/Y"]["fields"]["internalNote"]["credit"]["group"] == "order:7/Y"
    assert not (iso["tmp"] / "uploaded_pairings.json").exists()

    # the drain confirms + credits both overnight (simulated directly)
    webapp._credit_producer("parovania_eshop", {
        "BETALOV|P1": "https://supplier/x", "order:7/Y": "https://supplier/inline"})
    assert json.loads((iso["tmp"] / "uploaded_pairings.json").read_text())["order:7/Y"] \
        == "https://supplier/inline"

    # idempotent: a second run queues neither the decision nor the order pairing again
    result2 = webapp.run_parovania_eshop()
    assert result2["pairings"]["queued"] == 0 and result2["pairings"]["order_count"] == 0


# ── BUG 1: the nightly supplier write-back must NOT overwrite a REAL eshop
#    supplier with a (possibly stale) manual assignment. A per-product assignment
#    is meant to FILL IN a supplier for an order line that arrived WITHOUT one —
#    a code whose product ALREADY carries its own `supplier` in the current export
#    is excluded, so the automation never clobbers live catalog data. ──────────────
def test_do_upload_suppliers_skips_codes_with_own_supplier_in_export(iso, monkeypatch):
    # two assignments: 9/Z (no own supplier in the export → should be queued) and
    # 5/A (product ALREADY has its own supplier in the export → must be excluded).
    webapp._save_supplier_assign({"9/Z": "BETALOV", "5/A": "STALE_ASSIGN"})
    monkeypatch.setattr(webapp, "CODE2PAIR", {"9/Z": "777", "5/A": "555"})
    export = ("code;pairCode;supplier\r\n"
              "9/Z;777;\r\n"
              "5/A;555;REAL_SUPPLIER\r\n")
    monkeypatch.setattr(webapp, "_iter_export_lines", _export_lines(export))
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: pytest.fail("must not import — must queue"))

    result, status = webapp._do_upload_suppliers(dry=False)
    assert status == 200
    pending = webapp._load_pending()
    written = set(pending)
    assert written == {"9/Z"}          # 5/A excluded (own supplier already in export)
    assert "5/A" not in written
    # #299 Task 10 — the producer never writes uploaded_suppliers.json itself
    assert json.loads((iso["tmp"] / "uploaded_suppliers.json").read_text()) == {}
    # once the drain credits it, it IS the code we queued (never the excluded one)
    webapp._credit_producer("parovania_eshop_suppliers", {"9/Z": "BETALOV"})
    up = json.loads((iso["tmp"] / "uploaded_suppliers.json").read_text())
    assert up == {"9/Z": "BETALOV"}


# ── #215: an assignment BLOCKED by BUG 1 must not linger for ever ─────────────────
#    After the BUG 1 exclusion the assignment is never written, so it is never recorded
#    as uploaded either — it stays „new" on every single nightly run (a warning every
#    night) and, worse, it would FIRE later if the manager ever deliberately cleared that
#    supplier in the eshop: the app cannot tell „never had one" from „just deleted it".
#    A per-product assignment for a product that has its own supplier is by definition
#    out of date, so it is removed — on POSITIVE evidence only, and never on a dry run.
def test_an_assignment_the_eshop_has_OVERTAKEN_is_removed_from_the_store(iso, monkeypatch):
    webapp._save_supplier_assign({"9/Z": "BETALOV", "5/A": "STALE_ASSIGN"})
    monkeypatch.setattr(webapp, "CODE2PAIR", {"9/Z": "777", "5/A": "555"})
    monkeypatch.setattr(webapp, "_iter_export_lines", _export_lines(
        "code;pairCode;supplier\r\n9/Z;777;\r\n5/A;555;REAL_SUPPLIER\r\n"))
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: pytest.fail("must not import — must queue"))

    result, status = webapp._do_upload_suppliers(dry=False)

    assert status == 200
    # the overtaken one is gone from the manager's store — this half is UNCHANGED
    # by #299 Task 10 (the removal never went through import at all)
    assert webapp._load_supplier_assign() == {"9/Z": "BETALOV"}
    assert result["obsolete_removed"] == ["5/A"], result
    # 9/Z is QUEUED but not yet credited (nothing confirms it until the drain runs)
    assert result["remaining"] == 1, result
    webapp._credit_producer("parovania_eshop_suppliers", {"9/Z": "BETALOV"})
    # …and it stops being counted as work still waiting to go up, once credited
    result2, _s2 = webapp._do_upload_suppliers(dry=False)
    assert result2["remaining"] == 0, result2


def test_an_assignment_whose_name_the_manager_CHANGED_is_never_removed(iso, monkeypatch):
    """The trap in „the eshop already has its own supplier": after WE wrote a supplier back,
    the export carries it — so a name the manager then EDITS looks exactly like an
    assignment the eshop overtook. Removing it would delete his correction overnight and
    leave the OLD name live in the shop, with the run logging a reassuring „0 new codes".

    The two cases separate cleanly on the upload record: #215 is about an assignment that
    was NEVER written back (blocked from the first run and for ever after). One we have
    already uploaded is not blocked at all."""
    webapp._save_supplier_assign({"5/A": "NOVY_DODAVATEL"})
    webapp._save_uploaded_suppliers({"5/A": "STARY_DODAVATEL"})   # we wrote this earlier
    monkeypatch.setattr(webapp, "CODE2PAIR", {"5/A": "555"})
    monkeypatch.setattr(webapp, "_iter_export_lines", _export_lines(
        "code;pairCode;supplier\r\n5/A;555;STARY_DODAVATEL\r\n"))
    fake_run, _calls = _ok_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)

    result, _status = webapp._do_upload_suppliers(dry=False)

    assert webapp._load_supplier_assign() == {"5/A": "NOVY_DODAVATEL"}
    assert result["obsolete_removed"] == [], result


def test_a_failure_to_clean_up_does_not_take_the_whole_NIGHTLY_RUN_down(iso, monkeypatch):
    """store-prune §3 — the deletion is housekeeping and must be wrapped like one. A
    concurrent click invalidates the read receipt and `_save_supplier_assign` raises
    `StoreWipeRefused`; unwrapped, that aborts the supplier write-back before a single
    import row is built, and the manager's assignments stop going up entirely."""
    webapp._save_supplier_assign({"9/Z": "BETALOV", "5/A": "STALE_ASSIGN"})
    monkeypatch.setattr(webapp, "CODE2PAIR", {"9/Z": "777", "5/A": "555"})
    monkeypatch.setattr(webapp, "_iter_export_lines", _export_lines(
        "code;pairCode;supplier\r\n9/Z;777;\r\n5/A;555;REAL_SUPPLIER\r\n"))
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: pytest.fail("must not import — must queue"))

    def boom(_d):
        raise webapp.StoreWipeRefused("concurrent write")
    monkeypatch.setattr(webapp, "_save_supplier_assign", boom)

    result, status = webapp._do_upload_suppliers(dry=False)

    assert status == 200, result
    assert result["obsolete_removed"] == [], result   # nothing was removed, and it says so
    assert set(webapp._load_pending()) == {"9/Z"}     # the run itself carried on


def test_the_result_reports_what_was_ACTUALLY_dropped(iso, monkeypatch):
    """Not what we intended to drop: if a code vanished from the store meanwhile, naming it
    as removed is a false report about the manager's data."""
    webapp._save_supplier_assign({"5/A": "STALE_ASSIGN"})
    monkeypatch.setattr(webapp, "CODE2PAIR", {"5/A": "555"})
    monkeypatch.setattr(webapp, "_iter_export_lines", _export_lines(
        "code;pairCode;supplier\r\n5/A;555;REAL_SUPPLIER\r\n"))
    # the first read (which builds `new_codes`) sees the assignment; the re-read inside the
    # lock finds it already gone — the shape a concurrent delete produces
    calls = {"n": 0}
    real_load = webapp._load_supplier_assign

    def racing_load():
        calls["n"] += 1
        return real_load() if calls["n"] == 1 else {}
    monkeypatch.setattr(webapp, "_load_supplier_assign", racing_load)

    result, _status = webapp._do_upload_suppliers(dry=False)

    assert calls["n"] >= 2, calls          # the re-read under the lock really happened
    assert result["obsolete_removed"] == [], result


def test_a_DRY_run_removes_NOTHING(iso, monkeypatch):
    """A dry run exists to show what WOULD happen. A store it quietly edits is not a
    dry run."""
    webapp._save_supplier_assign({"5/A": "STALE_ASSIGN"})
    monkeypatch.setattr(webapp, "CODE2PAIR", {"5/A": "555"})
    monkeypatch.setattr(webapp, "_iter_export_lines", _export_lines(
        "code;pairCode;supplier\r\n5/A;555;REAL_SUPPLIER\r\n"))

    result, _status = webapp._do_upload_suppliers(dry=True)

    assert webapp._load_supplier_assign() == {"5/A": "STALE_ASSIGN"}
    assert result["obsolete_removed"] == [], result


def test_a_code_the_CATALOGUE_DOES_NOT_CARRY_is_never_removed(iso, monkeypatch):
    """#275 holds a code the eshop does not have — a DIFFERENT reason with a different
    fate. That hold is self-healing: the code may appear in the catalogue tomorrow and the
    assignment must still be there when it does. Absence is not evidence (store-prune §1)."""
    webapp._save_supplier_assign({"absent/XL": "BETALOV"})
    monkeypatch.setattr(webapp, "CODE2PAIR", {"absent/XL": "999"})
    monkeypatch.setattr(webapp, "_iter_export_lines", _export_lines(
        "code;pairCode;supplier\r\n5/A;555;REAL_SUPPLIER\r\n"))
    fake_run, _calls = _ok_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)

    result, _status = webapp._do_upload_suppliers(dry=False)

    assert webapp._load_supplier_assign() == {"absent/XL": "BETALOV"}
    assert result["obsolete_removed"] == [], result
    assert result["missing_count"] == 1, result


@pytest.mark.parametrize("why,setup", [
    ("empty", lambda mp: mp.setattr(webapp, "_iter_export_lines", _export_lines(""))),
    ("small", lambda mp: mp.setattr(webapp, "EXPORT_MIN_CODES", 1000)),
    ("stale", lambda mp: mp.setattr(webapp, "_export_age_s",
                                    lambda: webapp.EXPORT_MAX_AGE_S + 60)),
])
def test_an_export_we_cannot_BELIEVE_removes_nothing(iso, monkeypatch, why, setup):
    """The removal is a WRITE condition, so it may only stand on bytes we believe — the
    same three gates that already block the upload itself. An unbelievable export cannot
    tell which codes carry their own supplier, so it cannot condemn anything."""
    webapp._save_supplier_assign({"5/A": "STALE_ASSIGN"})
    monkeypatch.setattr(webapp, "CODE2PAIR", {"5/A": "555"})
    monkeypatch.setattr(webapp, "_iter_export_lines", _export_lines(
        "code;pairCode;supplier\r\n5/A;555;REAL_SUPPLIER\r\n"))
    setup(monkeypatch)

    result, _status = webapp._do_upload_suppliers(dry=False)

    assert webapp._load_supplier_assign() == {"5/A": "STALE_ASSIGN"}, why
    assert result["obsolete_removed"] == [], (why, result)


def test_an_assignment_ALREADY_uploaded_is_left_alone(iso, monkeypatch):
    """Only assignments still waiting to go up are candidates. One that was written back
    long ago is not „blocked" — it is done, and its record is what keeps the upload
    incremental."""
    webapp._save_supplier_assign({"5/A": "BETALOV"})
    webapp._save_uploaded_suppliers({"5/A": "BETALOV"})
    monkeypatch.setattr(webapp, "CODE2PAIR", {"5/A": "555"})
    monkeypatch.setattr(webapp, "_iter_export_lines", _export_lines(
        "code;pairCode;supplier\r\n5/A;555;BETALOV\r\n"))

    result, _status = webapp._do_upload_suppliers(dry=False)

    assert webapp._load_supplier_assign() == {"5/A": "BETALOV"}
    assert result["obsolete_removed"] == [], result


# ── PR #295 review — the removal rests on `c not in uploaded`, so a store that was
#    never READ condemns EVERYTHING ──────────────────────────────────────────────────
#    `_read_json_store` degrades a missing OR corrupt store to `{}`, and `{}` reads as
#    „we have never written a single one of these back" — i.e. every assignment the
#    export shows with its own supplier becomes obsolete at once. It is not theoretical:
#    on the live box `data/out/uploaded_suppliers.json` does not exist at all, so the
#    whole store is one export appearance away from being wiped. store-prune §1: absence
#    is never evidence — the removal needs POSITIVE proof that the record was read.
@pytest.mark.parametrize("why,prepare", [
    ("missing", lambda p: p.unlink(missing_ok=True)),
    ("corrupt", lambda p: p.write_text('{"5/A": "BET', encoding="utf-8")),
    ("wrong-type", lambda p: p.write_text('["5/A"]', encoding="utf-8")),
])
def test_an_upload_record_we_could_not_READ_condemns_nothing(iso, monkeypatch, why,
                                                             prepare):
    """Fail-CLOSED: without the record we cannot tell „never written back" (#215's case)
    from „written back, and the manager has just edited the name" — and the second one is
    the manager's correction, which this would throw away overnight."""
    webapp._save_supplier_assign({"5/A": "STALE_ASSIGN"})
    prepare(iso["tmp"] / "uploaded_suppliers.json")
    monkeypatch.setattr(webapp, "CODE2PAIR", {"5/A": "555"})
    monkeypatch.setattr(webapp, "_iter_export_lines", _export_lines(
        "code;pairCode;supplier\r\n5/A;555;REAL_SUPPLIER\r\n"))
    fake_run, _calls = _ok_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)

    result, _status = webapp._do_upload_suppliers(dry=False)

    assert webapp._load_supplier_assign() == {"5/A": "STALE_ASSIGN"}, why
    assert result["obsolete_removed"] == [], (why, result)
    # …and it is SURFACED, not silent: the caller sees what was held back and why
    assert result["obsolete_held"] == ["5/A"], (why, result)


def test_an_UNREADABLE_upload_record_stops_the_run_instead_of_deleting(iso, monkeypatch):
    """`_read_json_store` propagates an I/O error on a store that IS there, on purpose:
    „unreadable" is not evidence that no work was done. The run therefore fails loudly —
    and, crucially, without having removed anything."""
    webapp._save_supplier_assign({"5/A": "STALE_ASSIGN"})
    p = iso["tmp"] / "uploaded_suppliers.json"
    p.write_text("{}", encoding="utf-8")
    p.chmod(0o000)
    monkeypatch.setattr(webapp, "CODE2PAIR", {"5/A": "555"})
    monkeypatch.setattr(webapp, "_iter_export_lines", _export_lines(
        "code;pairCode;supplier\r\n5/A;555;REAL_SUPPLIER\r\n"))
    try:
        with pytest.raises(OSError):
            webapp._do_upload_suppliers(dry=False)
    finally:
        p.chmod(0o600)

    assert webapp._load_supplier_assign() == {"5/A": "STALE_ASSIGN"}


def test_a_record_that_is_GENUINELY_EMPTY_still_condemns(iso, monkeypatch):
    """The other side of the same coin — an on-disk `{}` is real evidence („nothing has
    ever been written back"), not a degraded read, so #215 still does its job."""
    webapp._save_supplier_assign({"5/A": "STALE_ASSIGN"})
    (iso["tmp"] / "uploaded_suppliers.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(webapp, "CODE2PAIR", {"5/A": "555"})
    monkeypatch.setattr(webapp, "_iter_export_lines", _export_lines(
        "code;pairCode;supplier\r\n5/A;555;REAL_SUPPLIER\r\n"))
    fake_run, _calls = _ok_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)

    result, _status = webapp._do_upload_suppliers(dry=False)

    assert webapp._load_supplier_assign() == {}
    assert result["obsolete_removed"] == ["5/A"], result
    assert result["obsolete_held"] == [], result


def test_obsolete_removed_is_on_the_result_of_EVERY_path(iso, monkeypatch):
    """A caller must never have to tell „nothing was removed" from „this build does not
    report it" — every early-return path must carry the field.

    #299 Task 10 — the SECOND path this test originally pinned ("another import
    already running", reached via `_import_lock` AFTER the removal) no longer
    exists: `_do_upload_suppliers` does not import at all any more, so there is
    no `_import_lock` acquisition left to race. That protection is gone WITH the
    code path it guarded, not silently dropped — replaced here with the DRY-run
    early-return branch, the other path this migration adds that returns before
    reaching the queue."""
    webapp._save_supplier_assign({"9/Z": "BETALOV"})
    webapp._save_uploaded_suppliers({"9/Z": "BETALOV"})           # nothing new to send
    monkeypatch.setattr(webapp, "CODE2PAIR", {"9/Z": "777", "7/Y": "P1"})
    r1, _s = webapp._do_upload_suppliers(dry=False)
    assert r1["obsolete_removed"] == [], r1
    assert r1["obsolete_held"] == [], r1

    # now there IS something new (a code the fixture export really lists) — the
    # DRY-run path must ALSO carry the field
    webapp._save_supplier_assign({"9/Z": "BETALOV", "7/Y": "BETALOV"})
    r2, s2 = webapp._do_upload_suppliers(dry=True)
    assert s2 == 200, r2
    assert r2["obsolete_removed"] == [], r2
    assert r2["obsolete_held"] == [], r2


def test_the_store_is_not_REWRITTEN_when_there_is_nothing_to_remove(iso, monkeypatch):
    """store-prune §3 — a no-op write over a `protect=True` store burns its read receipt
    and rewrites a file nobody asked to change."""
    webapp._save_supplier_assign({"9/Z": "BETALOV"})
    monkeypatch.setattr(webapp, "CODE2PAIR", {"9/Z": "777"})
    monkeypatch.setattr(webapp, "_iter_export_lines", _export_lines(
        "code;pairCode;supplier\r\n9/Z;777;\r\n"))
    fake_run, _calls = _ok_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)
    p = iso["tmp"] / "supplier_assignments.json"
    before, mtime = p.read_bytes(), p.stat().st_mtime_ns

    webapp._do_upload_suppliers(dry=False)

    assert p.read_bytes() == before
    assert p.stat().st_mtime_ns == mtime


# ── BUG 1 safety: FAIL-CLOSED when the export is missing/empty/unreadable. The
#    exclusion guard (_export_supplier_index) needs the catalog export to know which
#    codes ALREADY carry their own eshop supplier. With NO export it cannot tell — it
#    must NOT fall open and write, or a stale per-product assignment could clobber a
#    real supplier in the live eshop (exactly the bug this PR fixes). The whole supplier
#    upload is skipped: 0 written, everything blocked, the idempotency store untouched. ──
def test_do_upload_suppliers_blocks_when_export_empty(iso, monkeypatch):
    webapp._save_supplier_assign({"9/Z": "BETALOV", "5/A": "ORBIS"})
    monkeypatch.setattr(webapp, "CODE2PAIR", {"9/Z": "777", "5/A": "555"})
    monkeypatch.setattr(webapp, "_iter_export_lines", _export_lines(""))  # missing/unreadable
    fake_run, calls = _ok_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)

    result, status = webapp._do_upload_suppliers(dry=False)
    assert status == 200
    assert result["count"] == 0           # nothing written to the live eshop
    assert result["blocked"] == 2         # both assignments held back
    assert calls == []                    # the careful import never ran
    # nothing recorded as uploaded → the idempotency store is left exactly as it was
    # (the fixture seeds an on-disk empty record — see `iso`)
    assert json.loads((iso["tmp"] / "uploaded_suppliers.json").read_text()) == {}


# ── BUG 1 (case c): a code that IS in supplier_assignments but has no "own supplier"
#    in the current export is NOT excluded by the clobber guard. Whether it is then
#    SENT depends on the #270 catalogue verdict, which lives in its own tests below —
#    this one pins only that the BUG 1 exclusion helper leaves it alone. ─────────────
def test_the_clobber_guard_does_not_flag_a_code_absent_from_the_export(iso, monkeypatch):
    webapp._save_supplier_assign({"9/Z": "BETALOV"})
    monkeypatch.setattr(webapp, "CODE2PAIR", {"9/Z": "777"})
    # a NON-empty export listing OTHER products (5/A carries its own supplier) but NOT 9/Z
    export = ("code;pairCode;supplier\r\n"
              "5/A;555;REAL_SUPPLIER\r\n")
    monkeypatch.setattr(webapp, "_iter_export_lines", _export_lines(export))

    own, codes, present = webapp._export_supplier_index()

    assert "9/Z" not in own            # nothing to clobber — the eshop has no supplier there
    assert codes == {"5/A"} and present is True


def test_run_is_idempotent_second_run_pushes_nothing(iso, monkeypatch):
    """#299 Task 10 — once the drain has CREDITED both (simulated directly — its
    own confirm→credit path is test_webreview_shoptet_upload.py's job), a second
    run queues nothing further: idempotency now depends on the credit record, not
    on this producer's own memory of what it just sent."""
    _seed_pairing()
    _seed_supplier()
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: pytest.fail("must not import — must queue"))
    result1 = webapp.run_parovania_eshop()
    assert result1["pairings"]["queued"] == 1 and result1["suppliers"]["queued"] == 1

    webapp._credit_producer("parovania_eshop", {"BETALOV|P1": "https://supplier/x"})
    webapp._credit_producer("parovania_eshop_suppliers", {"9/Z": "BETALOV"})

    result = webapp.run_parovania_eshop()
    assert result["status"] == "ok"
    assert result["pairings"]["queued"] == 0 and result["suppliers"]["queued"] == 0
    assert result["pairings"]["count"] == 0 and result["suppliers"]["count"] == 0


def test_run_zero_new_reports_ok_without_importing(iso, monkeypatch):
    # no decisions, no assignments → clean no-op run (like the n8n `return []`)
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
    result = webapp.run_parovania_eshop()
    assert result["status"] == "ok"
    assert result["pairings"]["count"] == 0 and result["suppliers"]["count"] == 0


# ── graceful degradation ────────────────────────────────────────────────────────
# #299 Task 10 — `test_import_failure_surfaces_failed_status_and_does_not_mark_uploaded`
# deleted. `run_parovania_eshop` no longer imports through either core, so an
# import failure (rc != 0) can no longer happen at THIS level at all — both cores
# always return `ok: True` now (Task 8's I1 precedent: "producers can no longer
# return ok:false"). A real Shoptet import failure can only happen inside the
# hourly drain's OWN `_import_rows_chunked` call, whose "not-yet-credited on
# failure" protection is `test_webreview_shoptet_upload.py`'s job
# (`test_a_pairing_key_whose_second_code_failed_is_NOT_credited`).


def test_blocked_when_variant_codes_missing(iso, monkeypatch):
    # a paired product with NO variant codes yields 0 import rows → blocked (surfaced,
    # not silent — the n8n Sprava node did the same with a ⚠️ warning)
    monkeypatch.setattr(webapp, "PRODUCTS", [_product(variant_codes=[])])
    webapp._save_decisions({"BETALOV|P1": {"status": "good", "url": "https://supplier/x"}})
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
    result = webapp.run_parovania_eshop()
    assert result["status"] == "blocked"
    assert result["pairings"]["blocked"] == 1


# ── #156: a large batch is split into chunked imports (no single import overruns
#    the 120s browser redirect timeout — the nightly 415-product / 1195-row push
#    failed on Timeout 120000ms). Hermetic: run_import (the Playwright subprocess)
#    is mocked; the assertion is on how the rows are SPLIT across import calls. ──
def _recording_import(fail_on_call=None):
    """run_import stub recording each chunk CSV's rows; optionally FAIL the Nth call
    (1-based) to simulate a mid-batch chunk failure."""
    calls = []

    def fake_run(csv_path, dry_run=False, timeout=300):
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            rd = list(_csv.reader(f, delimiter=";"))
        rows = rd[1:]
        calls.append({"header": rd[0], "rows": rows})
        if fail_on_call is not None and len(calls) == fail_on_call:
            return 2, "POZOR: Shoptet hlási zlyhania", "boom"
        return 0, f"VÝSLEDOK: spracované={len(rows)} upravené={len(rows)} zlyhania=0", ""
    return fake_run, calls


# #299 Task 10 — chunking (#156: no single import overruns the 120s browser
# redirect timeout) is no longer this producer's concern at all: neither core
# imports directly any more, so there is no batch here to split into chunks —
# that job (and its mid-batch-failure/lock-release protection) belongs entirely
# to the hourly drain's OWN `_import_rows_chunked` call, already covered by
# `test_webreview_shoptet_upload.py`'s `cycle` fixture (chunking itself is
# generic across every producer, not pairings/suppliers-specific). What THIS
# producer still owns and must be proven NOT to lose: building and queueing
# EVERY candidate row of a large batch, not just chunking it correctly — the
# two tests below replace `test_large_pairing_batch_split_into_chunks` /
# `test_large_supplier_batch_split_into_chunks`, testing that instead.
# `test_mid_batch_chunk_failure_records_partial_and_releases_lock` and
# `test_small_batch_still_single_import` are deleted outright — the first is now
# purely a drain-level scenario (mirrored, pairings-specific, by
# `test_webreview_shoptet_upload.py::test_a_pairing_key_whose_second_code_failed_is_NOT_credited`);
# the second's premise (batching is chunked-vs-single at THIS level) no longer
# exists — there is no import left here to be "single" or "chunked".

def test_a_large_pairing_batch_queues_every_code(iso, monkeypatch):
    n = 650
    codes = [f"{i}/M" for i in range(n)]
    monkeypatch.setattr(webapp, "PRODUCTS", [_product(variant_codes=codes)])
    monkeypatch.setattr(webapp, "CODE2PAIR", {c: "P1" for c in codes})
    _stub_catalog_export(monkeypatch, codes)
    _seed_pairing()
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: pytest.fail("must not import — must queue"))

    result = webapp.run_parovania_eshop()

    assert result["status"] == "ok"
    assert result["pairings"]["ok"] is True and result["pairings"]["queued"] == n
    pending = webapp._load_pending()
    assert sorted(pending) == sorted(codes)          # every code queued once, none lost
    # the whole group credits together once the drain confirms all of it
    webapp._credit_producer("parovania_eshop", {"BETALOV|P1": "https://supplier/x"})
    assert json.loads((iso["tmp"] / "uploaded_pairings.json").read_text()) \
        == {"BETALOV|P1": "https://supplier/x"}


def test_a_large_supplier_batch_queues_every_code(iso, monkeypatch):
    # the supplier write-back path handles a large batch too (#156 named pairings +
    # suppliers) — same "every code queued, none lost" contract, no cross-code
    # grouping (each code credits on its own).
    n = 400
    assigns = {f"{i}/S": f"SUP{i}" for i in range(n)}
    monkeypatch.setattr(webapp, "CODE2PAIR", {f"{i}/S": "P" for i in range(n)})
    webapp._save_supplier_assign(assigns)
    _stub_catalog_export(monkeypatch, list(assigns))
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: pytest.fail("must not import — must queue"))

    result = webapp.run_parovania_eshop()

    assert result["suppliers"]["ok"] is True and result["suppliers"]["queued"] == n
    pending = webapp._load_pending()
    assert sorted(pending) == sorted(assigns)


def test_run_via_runner_records_error_when_queueing_raises(iso, monkeypatch):
    """#299 Task 10 — the ONE way `_do_upload_pairings` can now fail is
    `queue_shoptet_fields` refusing to write on top of an unreadable pending
    table (`StoreWipeRefused`) or another genuine exception — that must still
    propagate to the runner, which records last_status='error' and keeps the app
    alive (same contract a raising `run_import` used to prove, before this
    migration removed the import call entirely)."""
    _seed_pairing()
    with open(webapp.PENDING_SHOPTET, "w", encoding="utf-8") as f:
        f.write("{ this is not json")

    assert webapp.RUNNER._execute("parovania_eshop") is True    # runner survives
    (st,) = [x for x in webapp.RUNNER.status() if x["key"] == "parovania_eshop"]
    assert st["last_status"] == "error"
    assert st["running"] is False


# ── disabled automation never runs on a scheduler tick ──────────────────────────
def test_disabled_automation_is_not_ticked(iso, monkeypatch):
    _seed_pairing()
    _seed_supplier()
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("disabled must not run")))
    webapp.RUNNER.tick_once()                    # default state = disabled
    (st,) = [x for x in webapp.RUNNER.status() if x["key"] == "parovania_eshop"]
    assert st["enabled"] is False
    assert st["last_run"] == ""                  # never ran


# ── http run endpoint + runner integration ──────────────────────────────────────
def test_run_now_via_http_endpoint_and_runner(iso, monkeypatch):
    _seed_pairing()
    _seed_supplier()
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: pytest.fail("must not import — must queue"))
    c = authed_client()
    r = c.post("/api/automations/parovania_eshop/run")
    assert r.status_code == 200 and r.get_json()["started"] is True
    webapp.RUNNER._threads["parovania_eshop"].join(timeout=15)
    (st,) = [x for x in webapp.RUNNER.status() if x["key"] == "parovania_eshop"]
    assert st["last_status"] == "ok"
    assert st["last_result"]["status"] == "ok"
    assert st["last_result"]["pairings"]["queued"] == 1
    assert st["enabled"] is False                # run-now must not enable the schedule


# ── never modifies the manager's decision stores (reads only) ───────────────────
def test_run_reads_but_never_writes_manager_decision_stores(iso, monkeypatch):
    webapp._save_decisions({"BETALOV|P1": {"status": "good", "url": "https://supplier/x"}})
    webapp._save_supplier_assign({"9/Z": "BETALOV"})
    dec_before = (iso["tmp"] / "decisions.json").read_text()
    sa_before = (iso["tmp"] / "supplier_assignments.json").read_text()
    fake_run, _calls = _ok_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)

    webapp.run_parovania_eshop()

    # the manager's live stores are untouched (the automation only READS them)
    assert (iso["tmp"] / "decisions.json").read_text() == dec_before
    assert (iso["tmp"] / "supplier_assignments.json").read_text() == sa_before


# ── #257 cause 2 (historical): a partially-failed chunk is not a batch that
# imported nothing. #299 Task 10 DELETES the import-log-parsing half of this
# section — `test_partial_stdout_is_read_from_the_scripts_own_result_line`,
# `test_partial_message_promises_export_confirmation_only_where_it_happens`,
# `test_partial_chunk_keeps_importing_the_rest_of_the_batch`,
# `test_unreadable_result_is_not_reported_as_zero_imported_rows`,
# `test_hard_shoptet_error_reaches_the_automation_card`,
# `test_partial_chunk_then_hard_failure_reports_what_really_landed` and
# `test_timed_out_chunk_is_reported_as_uncertain_not_as_zero_imported` — because
# `_do_upload_pairings`/`_do_upload_suppliers` no longer call `_import_rows_chunked`
# or parse a single script's stdout at all: chunking, partial-chunk continuation,
# the baseline-Log-entry trap, unreadable/timed-out results and hard Shoptet
# errors are now EXCLUSIVELY the hourly drain's concern
# (`run_shoptet_upload`'s own `_import_rows_chunked` call), covered generically
# for every producer by `test_webreview_shoptet_upload.py`'s `cycle` fixture —
# and, for the credit-withholding shape specifically, by
# `test_a_pairing_key_whose_second_code_failed_is_NOT_credited` (added by this
# task). `_chunk_error_msg`'s own "z exportu" / "odmietol N z M riadkov" wording
# no longer has a per-producer caller to distinguish (the drain reports ONE
# combined result for the whole pending table, not a separate message per
# producer) — that distinction dissolved WITH the code path it described.
#
# What SURVIVES unchanged and is kept below: `_export_row_verdicts`'s CONFIRMED
# verdict is still computed and still used by `_do_upload_pairings` exactly as
# before this migration (the task's explicit instruction) — the two rewrites
# below prove the SAME verdict logic still holds, just observed through the
# pending table instead of an intercepted import call.

def _export(pairs):
    """A minimal catalog export (the eshop's own truth) carrying code→internalNote."""
    head = "code;pairCode;internalNote;supplier\r\n"
    return head + "".join(f"{c};P1;{note};\r\n" for c, note in pairs.items())


def test_export_confirmation_ignores_a_code_the_export_lists_twice(iso, monkeypatch):
    # the catalog holds duplicate products sharing variant codes (see link_rows) — if
    # two export rows disagree about a code's internalNote, neither proves anything,
    # so the code must stay unconfirmed and be QUEUED.
    _seed_pairing()
    monkeypatch.setattr(webapp, "_iter_export_lines",
                        _export_lines("code;pairCode;internalNote;supplier\r\n"
                                      "1/M;P1;https://supplier/OTHER;\r\n"
                                      "1/M;P1;https://supplier/x;\r\n"))
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: pytest.fail("must not import — must queue"))

    result = webapp.run_parovania_eshop()

    assert result["pairings"]["confirmed_in_export"] == 0
    pending = webapp._load_pending()
    assert pending["1/M"]["fields"]["internalNote"]["value"] == "https://supplier/x"


def test_rows_already_correct_in_the_eshop_are_credited_from_the_export(iso, monkeypatch):
    """THE unfreeze (#257): the import log reports aggregate counts only, so a
    partially-failed chunk cannot say WHICH rows landed. The eshop's own export can —
    a code whose internalNote already equals the URL we would send is proven to be on
    the eshop, so it is recorded uploaded and never re-sent."""
    _seed_pairing()                                   # BETALOV|P1 → https://supplier/x
    monkeypatch.setattr(webapp, "_iter_export_lines",
                        _export_lines(_export({"1/M": "https://supplier/x"})))
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: pytest.fail("nothing left to import"))

    result = webapp.run_parovania_eshop()

    assert result["pairings"]["ok"] is True
    assert result["pairings"]["count"] == 1
    assert result["pairings"]["confirmed_in_export"] == 1
    assert json.loads((iso["tmp"] / "uploaded_pairings.json").read_text()) \
        == {"BETALOV|P1": "https://supplier/x"}


def test_export_confirmation_needs_an_exact_url_match(iso, monkeypatch):
    # a stale / different note on the eshop proves nothing — the row is still QUEUED
    _seed_pairing()
    monkeypatch.setattr(webapp, "_iter_export_lines",
                        _export_lines(_export({"1/M": "https://supplier/OLD"})))
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: pytest.fail("must not import — must queue"))

    result = webapp.run_parovania_eshop()

    assert result["pairings"]["confirmed_in_export"] == 0
    pending = webapp._load_pending()
    assert pending["1/M"]["fields"]["internalNote"]["value"] == "https://supplier/x"


# ── PR #271 review — the four ways this push could still lie about what landed ──
def test_run_import_pins_the_child_stdout_encoding(monkeypatch):
    """IMPORTANT 2 — `encoding='utf-8'` on Popen only decodes what the CHILD produced;
    the child encodes with ITS OWN locale. On a box whose locale is not UTF-8 the
    result marker comes back mojibake'd ('V?SLEDOK:'), the slice is empty, processed is
    None and EVERY chunk is booked failed/unreadable — the whole nightly push dies on
    one character. Pin the child's stdout encoding explicitly."""
    seen = {}

    class FakePopen:
        returncode = 0

        def __init__(self, cmd, **kw):
            seen["cmd"], seen["kw"] = cmd, kw

        def communicate(self, timeout=None):
            return "VÝSLEDOK: spracované=1 upravené=1 zlyhania=0", ""

    monkeypatch.setattr(webapp.subprocess, "Popen", FakePopen)
    rc, out, _err = webapp.run_import("/tmp/does-not-matter.csv")

    assert rc == 0 and "VÝSLEDOK" in out
    assert seen["kw"]["env"]["PYTHONIOENCODING"] == "utf-8"
    assert seen["kw"]["env"]["PYTHONPATH"].endswith(os.path.join("", "src"))


def _write_export(path, pairs, age_s=0):
    """A real on-disk catalog export (cp1250, as „Sync zo Shoptetu" writes it), aged
    `age_s` seconds — the freshness of THIS file is what may credit rows as uploaded."""
    path.write_bytes(_export(pairs).encode("cp1250"))
    if age_s:
        old = time.time() - age_s
        os.utime(path, (old, old))
    return str(path)


def test_export_confirmation_refuses_a_stale_export(tmp_path, monkeypatch):
    """IMPORTANT 3 — confirmed rows are NOT sent at all and ARE recorded uploaded, on
    the strength of data/products.csv, which a SEPARATE hourly automation refreshes. If
    that sync is off/broken, a code cleared or changed in the eshop after the last sync
    is silently never re-written. The sibling supplier write-back already fails closed
    on an unusable export; this path trusted it at any age."""
    p = tmp_path / "products.csv"
    _write_export(p, {"1/M": "https://supplier/x"}, age_s=webapp.EXPORT_MAX_AGE_S + 60)
    monkeypatch.setattr(webapp, "SRC", str(p))

    assert (webapp._export_row_verdicts([["1/M", "P1", "https://supplier/x"]])["confirmed"]
            == set())


def test_export_confirmation_still_credits_a_fresh_export(tmp_path, monkeypatch):
    # the guard must not block the normal case — an export the hourly sync just wrote
    p = tmp_path / "products.csv"
    _write_export(p, {"1/M": "https://supplier/x"})
    monkeypatch.setattr(webapp, "SRC", str(p))
    monkeypatch.setattr(webapp, "EXPORT_MIN_CODES", 1)   # tiny fixture export

    assert (webapp._export_row_verdicts([["1/M", "P1", "https://supplier/x"]])["confirmed"]
            == {"1/M"})


# #299 Task 10 — `test_timed_out_chunk_is_reported_as_uncertain_not_as_zero_imported`
# deleted, same reasoning as the section above: `_do_upload_pairings` no longer
# calls `run_import`/`_import_rows_chunked`, so a chunk timing out can only
# happen inside the hourly drain's own import call now
# (`test_webreview_shoptet_upload.py`).


def test_partially_accepted_row_count_never_goes_negative():
    """MINOR — 'Zlyhanie variantov: N' is an aggregate Shoptet count; if it ever counts
    VARIANTS rather than rows it can exceed the rows we sent, and the message told the
    manager a negative number of rows was accepted."""
    res = {"chunks_ok": 0, "chunks_partial": 1, "chunks_total": 3, "rows_ok": 0,
           "rows_partial": 300, "partial_failed": 400, "unreadable": False,
           "error_detail": None}
    msg = webapp._chunk_error_msg(res, 900)
    assert "-100" not in msg and "+0 čiastočne prijatých" in msg


# --------------------------------------------------------------------------- #
# #272 — the nightly push read the WHOLE 57 MB catalog export into memory (and
# copied it again through io.StringIO, ~115 MB transient) on EVERY run, dry runs
# included, just to learn which codes the eshop already carries. The index is
# built by STREAMING the file line by line; only the {code: internalNote} pairs
# survive the pass.
# --------------------------------------------------------------------------- #
def _big_export(path, rows=6000, pad=2000):
    with open(path, "wb") as f:
        f.write(b"code;pairCode;internalNote;supplier;description\r\n")
        filler = "x" * pad
        for i in range(rows):
            f.write(f"{i}/M;P{i};https://s/{i};;{filler}\r\n".encode("cp1250"))
    return path.stat().st_size


def test_the_export_index_streams_instead_of_materialising_the_whole_file(tmp_path, monkeypatch):
    import tracemalloc
    p = tmp_path / "products.csv"
    size = _big_export(p)
    monkeypatch.setattr(webapp, "SRC", str(p))

    tracemalloc.start()
    try:
        notes, codes = webapp._export_note_index()
        _cur, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert len(codes) == 6000 and notes["0/M"] == "https://s/0"
    # the old path allocated >= 2x the file size (bytes -> str -> io.StringIO copy)
    assert peak < size / 2, (
        f"export index peaked at {peak / 1e6:.1f} MB for a {size / 1e6:.1f} MB export "
        "— it is still materialising the file instead of streaming it")


def test_the_streamed_index_parses_exactly_like_a_whole_text_parse(tmp_path, monkeypatch):
    """Streaming must not change WHAT is parsed: cp1250 decoding, quoted fields with
    an embedded newline, empty notes, and the duplicate-code rule (two rows that
    disagree about a code prove nothing, so the code is dropped from the notes but
    is still PRESENT in the catalog)."""
    text = ("code;pairCode;internalNote;supplier\r\n"
            "1/M;P1;https://s/x;\r\n"
            "2/M;P2;\"riadok\r\ns novým riadkom\";\r\n"
            "3/M;P3;;\r\n"
            "4/M;P4;https://s/A;\r\n"
            "4/M;P4;https://s/B;\r\n"
            "5/M;P5;\"samotný \r návrat vozíka\";\r\n")
    p = tmp_path / "products.csv"
    p.write_bytes(text.encode("cp1250"))
    monkeypatch.setattr(webapp, "SRC", str(p))

    notes, codes = webapp._export_note_index()

    # the reference: what a whole-text parse (the pre-#272 path) makes of the same bytes
    ref = {}
    for r in _csv.DictReader(io.StringIO(text), delimiter=";"):
        c = (r.get("code") or "").strip()
        if c:
            ref[c] = (r.get("internalNote") or "").strip()
    assert set(codes) == set(ref)
    assert {c: v for c, v in notes.items()} == {c: v for c, v in ref.items() if c != "4/M"}

    assert codes == {"1/M", "2/M", "3/M", "4/M", "5/M"}
    assert notes["1/M"] == "https://s/x"
    assert "novým riadkom" in notes["2/M"]
    assert notes["3/M"] == ""
    assert "4/M" not in notes                      # conflicting rows prove nothing
    # a LONE \r inside a quoted field: line iteration with newline='' breaks there,
    # csv reassembles it — the value must come back byte-for-byte, as a whole-text
    # parse produced it
    assert notes["5/M"] == "samotný \r návrat vozíka"
    # the whole-text reader (still used by the scraping automations) is unchanged
    assert webapp._read_export_for_links() == text


def test_a_missing_export_yields_an_empty_index_and_never_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "SRC", str(tmp_path / "nope.csv"))
    assert webapp._export_note_index() == ({}, set())
    assert webapp._read_export_for_links() == ""


# --------------------------------------------------------------------------- #
# #270 — a code the eshop's CATALOGUE does not carry at all can never be
# imported: Shoptet rejects that row on every single run („Zlyhanie variantov:
# 2", the same two rows every night since 24. 7.), and the manager saw only a red
# „Shoptet odmietol 2 riadkov" with no way to learn WHICH code or WHY. Such rows
# are now held back and LISTED with the code + the URL we tried to write.
# --------------------------------------------------------------------------- #
def test_a_code_the_catalogue_does_not_have_is_held_back_and_listed(iso, monkeypatch):
    _seed_pairing()                                    # BETALOV|P1 → 1/M
    # a fresh, perfectly normal export of OTHER products — 1/M is not in the eshop
    monkeypatch.setattr(webapp, "_iter_export_lines",
                        _export_lines(_export({"9/Z": "", "8/Z": ""})))
    fake_run, calls = _recording_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)

    p, status = webapp._do_upload_pairings(dry=False)

    assert status == 200
    assert calls == []                                 # the doomed row is NOT sent
    assert p["missing_count"] == 1
    assert p["missing_in_eshop"] == [{"code": "1/M", "value": "https://supplier/x"}]
    # NOT recorded uploaded — the moment the code appears in the catalogue it is sent
    assert (not (iso["tmp"] / "uploaded_pairings.json").exists()
            or json.loads((iso["tmp"] / "uploaded_pairings.json").read_text()) == {})


def test_a_code_that_reappears_in_the_catalogue_is_sent_on_the_next_run(iso, monkeypatch):
    """Holding a row back is never permanent: it is not credited, so once the
    manager fixes the code in the eshop the very next run QUEUES it."""
    _seed_pairing()
    monkeypatch.setattr(webapp, "_iter_export_lines",
                        _export_lines(_export({"9/Z": ""})))
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: pytest.fail("must not import — must queue"))
    r1, _s1 = webapp._do_upload_pairings(dry=False)
    assert r1["queued"] == 0

    monkeypatch.setattr(webapp, "_iter_export_lines",
                        _export_lines(_export({"9/Z": "", "1/M": ""})))
    p, _st = webapp._do_upload_pairings(dry=False)

    assert p["missing_count"] == 0
    assert p["queued"] == 1
    pending = webapp._load_pending()
    assert pending["1/M"]["fields"]["internalNote"]["value"] == "https://supplier/x"
    # not credited yet (the note in the export is "", never matches the URL) — the
    # drain credits it once Shoptet's own import confirms it
    assert not (iso["tmp"] / "uploaded_pairings.json").exists()


def test_an_empty_export_never_holds_a_row_back(iso, monkeypatch):
    """FAIL-SAFE: no export = we know NOTHING about the catalogue, so nothing may be
    called missing. Everything is QUEUED, exactly as before (an idempotent re-write)."""
    _seed_pairing()
    monkeypatch.setattr(webapp, "_iter_export_lines", _export_lines(""))
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: pytest.fail("must not import — must queue"))

    p, _st = webapp._do_upload_pairings(dry=False)

    assert p["missing_count"] == 0
    assert p["queued"] == 1
    pending = webapp._load_pending()
    assert pending["1/M"]["fields"]["internalNote"]["value"] == "https://supplier/x"


def test_a_stale_export_never_holds_a_row_back(tmp_path, monkeypatch):
    """Same guard as the export CREDIT (#271): an export older than EXPORT_MAX_AGE_S
    describes a catalogue that may have changed since, so it may neither confirm a
    row nor declare one missing."""
    p = tmp_path / "products.csv"
    _write_export(p, {"9/Z": ""}, age_s=webapp.EXPORT_MAX_AGE_S + 60)
    monkeypatch.setattr(webapp, "SRC", str(p))
    monkeypatch.setattr(webapp, "EXPORT_MIN_CODES", 1)   # tiny fixture export

    v = webapp._export_row_verdicts([["1/M", "P1", "https://supplier/x"]])
    assert v["absent"] == set() and v["confirmed"] == set()


def test_a_fresh_export_reports_both_verdicts_in_one_pass(tmp_path, monkeypatch):
    p = tmp_path / "products.csv"
    _write_export(p, {"1/M": "https://supplier/x", "2/M": ""})
    monkeypatch.setattr(webapp, "SRC", str(p))
    monkeypatch.setattr(webapp, "EXPORT_MIN_CODES", 1)   # tiny fixture export
    reads, real = [], webapp._iter_export_lines
    monkeypatch.setattr(webapp, "_iter_export_lines",
                        lambda: (reads.append(1), real())[1])

    v = webapp._export_row_verdicts([["1/M", "P1", "https://supplier/x"],
                                     ["2/M", "P1", "https://supplier/y"],
                                     ["3/M", "P1", "https://supplier/z"]])

    assert len(reads) == 1                    # ONE pass over the ~57 MB export, not two
    assert v["confirmed"] == {"1/M"}          # already exactly as we would write it
    assert v["absent"] == {"3/M"}             # the eshop has no such code at all
    # 2/M exists but carries a different note → still sent (unchanged behaviour)


def test_the_verdicts_cannot_be_handed_notes_that_skip_the_freshness_check():
    """#271's guard, kept through the #270 rename: the credit (and now the
    missing-code verdict) may only ever be computed from an export whose AGE was
    checked, so there must be no parameter through which pre-read bytes can enter."""
    params = inspect.signature(webapp._export_row_verdicts).parameters
    assert "notes" not in params and "text" not in params and "codes" not in params
    # and the index UNDER it is where pre-read bytes would actually be injected — it
    # must take nothing at all, so the read can only happen after the age check
    assert inspect.signature(webapp._export_note_index).parameters == {}
    assert inspect.signature(webapp._export_supplier_index).parameters == {}


def test_a_missing_code_turns_the_run_orange_and_reaches_the_tab(iso, monkeypatch):
    """The manager's only window into the nightly push is the automation card, so
    the codes must reach `last_result` — and the run must not look plain green."""
    _seed_pairing()
    monkeypatch.setattr(webapp, "_iter_export_lines",
                        _export_lines(_export({"9/Z": ""})))
    fake_run, _calls = _recording_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)

    result = webapp.run_parovania_eshop()

    assert result["status"] == "blocked"
    assert result["pairings"]["missing_count"] == 1
    assert result["pairings"]["missing_in_eshop"][0]["code"] == "1/M"


def test_supplier_codes_absent_from_the_catalogue_are_HELD_not_written(iso, monkeypatch):
    """#275 — the supplier write-back now holds a code the eshop's catalogue does not
    carry, exactly as the pairings half has since #270.

    Such a row can NEVER import: Shoptet rejects it on every single run, so
    uploaded_suppliers.json never records it, it stays „new" for ever and the whole
    nightly run goes red every night (live on prod: 145/3XL). Holding is NOT the drop
    PR #213 forbade — the assignment stays in supplier_assignments.json, is never
    credited, and goes up the moment the code appears in the catalogue."""
    webapp._save_supplier_assign({"9/Z": "BETALOV", "1/M": "ORBIS"})
    monkeypatch.setattr(webapp, "CODE2PAIR", {"9/Z": "777", "1/M": "P1"})
    # a trusted export that carries 1/M but NOT 9/Z
    monkeypatch.setattr(webapp, "_iter_export_lines",
                        _export_lines("code;pairCode;supplier\r\n1/M;P1;\r\n5/A;555;REAL\r\n"))
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: pytest.fail("must not import — must queue"))

    s, status = webapp._do_upload_suppliers(dry=False)

    assert status == 200
    pending = webapp._load_pending()
    assert set(pending) == {"1/M"}          # 9/Z withheld, 1/M still queued
    # …and it is NEVER recorded uploaded by the producer, so it is retried the
    # moment the code exists — the drain credits it once Shoptet confirms it
    assert json.loads((iso["tmp"] / "uploaded_suppliers.json").read_text()) == {}
    webapp._credit_producer("parovania_eshop_suppliers", {"1/M": "ORBIS"})
    assert json.loads((iso["tmp"] / "uploaded_suppliers.json").read_text()) == {"1/M": "ORBIS"}
    # still surfaced by name, with the value we wanted to write (the tab renders this)
    assert s["missing_in_eshop"] == [{"code": "9/Z", "value": "BETALOV"}]
    assert s["missing_count"] == 1


def test_a_held_supplier_code_goes_up_once_the_catalogue_carries_it(iso, monkeypatch):
    """The hold is bounded and self-healing — the property that makes it safe."""
    webapp._save_supplier_assign({"9/Z": "BETALOV"})
    monkeypatch.setattr(webapp, "CODE2PAIR", {"9/Z": "777"})
    monkeypatch.setattr(webapp, "_iter_export_lines",
                        _export_lines("code;pairCode;supplier\r\n5/A;555;REAL\r\n"))
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: pytest.fail("must not import — must queue"))
    assert webapp._do_upload_suppliers(dry=False)[0]["count"] == 0
    assert webapp._load_pending() == {}

    # the manager fixes the code in the eshop → it appears in the next export
    monkeypatch.setattr(webapp, "_iter_export_lines", _export_lines(
        "code;pairCode;supplier\r\n5/A;555;REAL\r\n9/Z;777;\r\n"))

    s, _st = webapp._do_upload_suppliers(dry=False)

    assert s["count"] == 1 and s["missing_count"] == 0
    assert set(webapp._load_pending()) == {"9/Z"}


# RETIRED (PR #280 review) — `test_an_untrusted_export_holds_nothing_on_the_supplier_side`
# stood here and asserted that a STALE export makes the write-back SEND the very code the
# catalogue does not carry (`{r[0] for r in calls[0]["rows"]} == {"9/Z"}  # sent, not held`).
#
# Its premise was that suppressing the hold is the safe fallback, because the hold is a
# WRITE condition and may only stand on bytes we believe. Half of that is right — the hold
# does need trusted bytes. The conclusion does not follow: with no trustworthy export the
# answer is to write NOTHING, not to write EVERYTHING. Suppressing only the hold left the
# dangerous half (the eshop write) running on bytes we had just declared untrustworthy,
# so it fail-OPENED — and #275's whole fix evaporated during any window in which the
# hourly sync had been down for 6 h before the 21:00 push. Worse, the BUG 1 clobber guard
# then ran on the same stale `own_supplier` bytes, so an assignment could overwrite a
# supplier a colleague had set in Shoptet meanwhile.
#
# It is replaced (not weakened) by
# `test_a_stale_export_blocks_the_whole_supplier_write_back` below: a stale export now
# blocks the upload and HOLDS every assignment, which is safe and self-healing — the same
# shape the size gate beside it has always had. Retired in its own commit, per the rule
# that a test codifying a defect is replaced with a stated justification, never quietly
# edited into agreement.


def test_a_stale_export_blocks_the_whole_supplier_write_back(iso, monkeypatch):
    """PR #280 review, MUST FIX 1. Freshness must gate the WRITE, not merely the hold.

    Measured on `dev` before the fix, with a catalogue carrying only 5/A:

        fresh export (1 h)     ROWS SENT: [['5/A', …]]                  missing=1
        stale export (6h + 1s) ROWS SENT: [['5/A', …], ['9/Z', …]]      missing=0
        stale export (3 days)  ROWS SENT: [['5/A', …], ['9/Z', …]]      missing=0

    9/Z is exactly the catalogue-absent code #275 exists to hold, so the fix evaporated
    in any window where the hourly sync had been down 6 h before the 21:00 push — and the
    BUG 1 clobber guard then ran on the same stale `own_supplier` bytes, letting an
    assignment overwrite a supplier a colleague had set in Shoptet meanwhile.

    A stale export is therefore treated exactly like an implausibly small one: the WHOLE
    upload is blocked and every assignment HELD."""
    webapp._save_supplier_assign({"5/A": "STALE_ASSIGNMENT", "9/Z": "FOREST"})
    monkeypatch.setattr(webapp, "CODE2PAIR", {"5/A": "5", "9/Z": "777"})
    # plausible and complete-looking, but OLD: the catalogue carries 5/A, not 9/Z
    monkeypatch.setattr(webapp, "_iter_export_lines",
                        _export_lines("code;pairCode;supplier\r\n5/A;5;\r\n"))
    monkeypatch.setattr(webapp, "_export_age_s", lambda: webapp.EXPORT_MAX_AGE_S + 1)
    fake_run, calls = _ok_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)

    result, status = webapp._do_upload_suppliers(dry=False)

    assert status == 200
    assert calls == []                       # NOTHING reached the live eshop
    assert result["count"] == 0
    assert result["blocked"] == 2            # both HELD — held is not dropped
    # the assignments survive untouched, so the next good export sends them
    assert json.loads((iso["tmp"] / "uploaded_suppliers.json").read_text()) == {}
    assert webapp._load_supplier_assign() == {"5/A": "STALE_ASSIGNMENT", "9/Z": "FOREST"}


def test_a_held_stale_run_goes_up_in_full_once_the_export_is_fresh_again(iso, monkeypatch):
    """The stale block is bounded and self-healing — the property that makes holding safe.
    Mirrors the size gate's own second half (test_an_implausibly_small_export_...)."""
    webapp._save_supplier_assign({"5/A": "BETALOV"})
    monkeypatch.setattr(webapp, "CODE2PAIR", {"5/A": "5"})
    monkeypatch.setattr(webapp, "_iter_export_lines",
                        _export_lines("code;pairCode;supplier\r\n5/A;5;\r\n"))
    monkeypatch.setattr(webapp, "_export_age_s", lambda: webapp.EXPORT_MAX_AGE_S + 1)
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: pytest.fail("must not import — must queue"))
    assert webapp._do_upload_suppliers(dry=False)[0]["count"] == 0
    assert webapp._load_pending() == {}

    # the hourly sync recovers → the export on disk is fresh again
    monkeypatch.setattr(webapp, "_export_age_s", lambda: 60.0)

    s, _st = webapp._do_upload_suppliers(dry=False)

    # the success path carries no block at all (neither the count nor the reason)
    assert s["count"] == 1 and not s.get("blocked") and "gate_blocked" not in s
    assert set(webapp._load_pending()) == {"5/A"}


def test_an_unknown_export_age_does_not_block_the_supplier_write_back(iso, monkeypatch):
    """`_export_age_s()` returns None when the file cannot be stat'd. Unknown age must
    never BLOCK (the documented contract): with no file at all the export index yields
    nothing, so the size gate above already refuses — this branch must not add a second,
    stricter refusal that a patched reader (every test seam) would trip over."""
    webapp._save_supplier_assign({"5/A": "BETALOV"})
    monkeypatch.setattr(webapp, "CODE2PAIR", {"5/A": "5"})
    monkeypatch.setattr(webapp, "_iter_export_lines",
                        _export_lines("code;pairCode;supplier\r\n5/A;5;\r\n"))
    monkeypatch.setattr(webapp, "_export_age_s", lambda: None)
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: pytest.fail("must not import — must queue"))

    s, _st = webapp._do_upload_suppliers(dry=False)

    assert s["count"] == 1
    assert set(webapp._load_pending()) == {"5/A"}


def test_a_run_whose_only_fault_is_a_missing_code_is_orange_not_red(iso, monkeypatch):
    """The point of the ticket, end to end. Until now the held-back code was still sent,
    Shoptet refused it, s_ok went False and run_parovania_eshop reported „failed" — a red
    row every single night for a condition the manager cannot read out of a red count."""
    _seed_pairing()
    webapp._save_supplier_assign({"9/Z": "BETALOV"})
    monkeypatch.setattr(webapp, "CODE2PAIR", {"1/M": "P1", "9/Z": "777"})
    monkeypatch.setattr(webapp, "_iter_export_lines",
                        _export_lines("code;pairCode;internalNote;supplier\r\n1/M;P1;;\r\n"))

    def refuses_unknown_codes(csv_path, dry_run=False, timeout=300):
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            rows = list(_csv.reader(f, delimiter=";"))[1:]
        if any(r[0] == "9/Z" for r in rows):   # what the real Shoptet does with 145/3XL
            return (2, "POZOR: Shoptet hlási zlyhania\n"
                    "VÝSLEDOK: spracované=1 upravené=0 zlyhania=1", "")
        n = len(rows)
        return 0, f"VÝSLEDOK: spracované={n} upravené={n} zlyhania=0", ""
    monkeypatch.setattr(webapp, "run_import", refuses_unknown_codes)

    result = webapp.run_parovania_eshop()

    assert result["suppliers"]["ok"] is True
    assert result["status"] == "blocked"                   # orange, not red
    assert result["suppliers"]["missing_count"] == 1


def test_an_implausibly_small_export_is_not_trusted(tmp_path, monkeypatch):
    """The catalogue is ~14 000 variant codes. A fresh, non-empty export carrying a
    handful is a BROKEN feed (truncated download, a filter left on), not a small shop —
    trusting it would accuse codes the eshop really has and withhold their rows. Pinned
    at the PRODUCTION value of EXPORT_MIN_CODES, so lowering it in a fixture cannot
    disarm this."""
    p = tmp_path / "products.csv"
    _write_export(p, {f"{i}/M": "" for i in range(webapp.EXPORT_MIN_CODES - 1)})
    monkeypatch.setattr(webapp, "SRC", str(p))

    v = webapp._export_row_verdicts([["nope/M", "P1", "https://supplier/x"]])
    assert v["absent"] == set() and v["confirmed"] == set()

    # one more code and the very same export IS trusted
    _write_export(p, {f"{i}/M": "" for i in range(webapp.EXPORT_MIN_CODES)})
    v = webapp._export_row_verdicts([["nope/M", "P1", "https://supplier/x"]])
    assert v["absent"] == {"nope/M"}


def test_the_supplier_report_is_gated_on_the_same_export_trust(iso, monkeypatch):
    """The supplier half only REPORTS missing codes, but that report turns the whole
    nightly row orange — so it needs the same gates as the pairings verdict. A stale
    export must not accuse a code the eshop has had for days."""
    webapp._save_supplier_assign({"9/Z": "BETALOV"})
    monkeypatch.setattr(webapp, "CODE2PAIR", {"9/Z": "777"})
    monkeypatch.setattr(webapp, "_iter_export_lines",
                        _export_lines("code;pairCode;supplier\r\n5/A;555;REAL\r\n"))
    fake_run, _calls = _ok_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)
    monkeypatch.setattr(webapp, "_export_age_s",
                        lambda: webapp.EXPORT_MAX_AGE_S + 60)

    s, _st = webapp._do_upload_suppliers(dry=False)

    assert s["missing_count"] == 0 and s["missing_in_eshop"] == []


def test_an_implausibly_small_export_blocks_the_supplier_write_back(iso, monkeypatch):
    """PR #276 review, IMPORTANT 1. The DANGEROUS half of the export gate must not be
    weaker than the cosmetic one: the write-back's fail-closed check used to ask only
    „had the file any bytes at all", while the (merely reporting) missing-code verdict
    next to it already demanded EXPORT_MIN_CODES. A broken feed yielding a handful of
    rows therefore passed — `own_supplier` came back nearly empty, nothing was excluded,
    and a stale assignment could overwrite a REAL eshop supplier: exactly the clobber
    PR #213's gate exists to prevent. An implausibly small export must BLOCK the upload
    (nothing sent to the live eshop) and HOLD the assignments (a hold is safe and
    self-healing, a drop is loss). Pinned at the PRODUCTION threshold."""
    monkeypatch.setattr(webapp, "EXPORT_MIN_CODES", PROD_EXPORT_MIN_CODES)
    webapp._save_supplier_assign({"9/Z": "STALE_ASSIGNMENT"})
    monkeypatch.setattr(webapp, "CODE2PAIR", {"9/Z": "777"})
    # fresh, non-empty, and carrying THREE codes out of a ~14 000-code catalogue
    monkeypatch.setattr(webapp, "_iter_export_lines", _export_lines(
        "code;pairCode;supplier\r\n1/A;1;OWN\r\n2/A;2;OWN\r\n3/A;3;OWN\r\n"))
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: pytest.fail("must not import — must queue"))

    result, status = webapp._do_upload_suppliers(dry=False)

    assert status == 200
    assert webapp._load_pending() == {}      # NOTHING reached the live eshop
    assert result["count"] == 0
    assert result["blocked"] == 1            # held, not dropped
    assert result["products"] == [{"code": "9/Z", "supplier": "STALE_ASSIGNMENT"}]
    assert json.loads((iso["tmp"] / "uploaded_suppliers.json").read_text()) == {}

    # …and the very same assignment IS written once the export is plausible again —
    # the gate blocks a broken feed, it does not freeze the write-back.
    plausible = ("code;pairCode;supplier\r\n"
                 + "".join(f"{i}/A;{i};\r\n" for i in range(PROD_EXPORT_MIN_CODES))
                 + "9/Z;777;\r\n")
    monkeypatch.setattr(webapp, "_iter_export_lines", _export_lines(plausible))

    result2, status2 = webapp._do_upload_suppliers(dry=False)

    assert status2 == 200
    assert set(webapp._load_pending()) == {"9/Z"}
    assert result2["count"] == 1


def test_the_supplier_write_gate_uses_the_same_ratio_floor_as_the_verdicts(iso, monkeypatch):
    """#277 — ONE threshold, TWO gates, now that the threshold is a ratio of the
    catalogue watermark rather than a flat 1000. A 1 200-code export clears the
    absolute floor and would have been fully trusted; against a 14 066-code catalogue
    it is a broken feed, and the DANGEROUS half (the live `supplier` write) must never
    be the permissive one — the PR #276 review's lesson restated against the stronger
    floor. Pinned at the PRODUCTION absolute floor so the fixture cannot disarm it."""
    monkeypatch.setattr(webapp, "EXPORT_MIN_CODES", PROD_EXPORT_MIN_CODES)
    today = datetime.now(timezone.utc).date().isoformat()
    (iso["tmp"] / "export_watermark.json").write_text(
        json.dumps({"days": {today: 14066}}), encoding="utf-8")
    webapp._save_supplier_assign({"9/Z": "STALE_ASSIGNMENT"})
    monkeypatch.setattr(webapp, "CODE2PAIR", {"9/Z": "777"})
    monkeypatch.setattr(webapp, "_iter_export_lines", _export_lines(
        "code;pairCode;supplier\r\n"
        + "".join(f"{i}/A;{i};\r\n" for i in range(PROD_EXPORT_MIN_CODES + 200))))
    fake_run, calls = _ok_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)

    result, status = webapp._do_upload_suppliers(dry=False)

    assert status == 200
    assert calls == []                       # NOTHING reached the live eshop
    assert result["count"] == 0 and result["blocked"] == 1      # held, not dropped
    assert json.loads((iso["tmp"] / "uploaded_suppliers.json").read_text()) == {}


def test_the_blocked_run_reports_WHY_the_gate_blocked(iso, monkeypatch):
    """PR #280 review, item 3. During ANY gate block the card read
    „🏷️ Dodávatelia: … N zablokovaných (chýbajú kódy)" — which names the wrong cause:
    nothing is missing, the export is simply not believable. `_do_upload_suppliers`
    already carried a `message`, but `run_parovania_eshop` never propagated it and the
    concrete numbers stayed in a log.warning. #277 widens the blocking band from <1000
    to <7033 codes, so the manager WILL meet this state."""
    webapp._save_supplier_assign({"5/A": "BETALOV"})
    monkeypatch.setattr(webapp, "CODE2PAIR", {"5/A": "5"})
    monkeypatch.setattr(webapp, "_iter_export_lines",
                        _export_lines("code;pairCode;supplier\r\n5/A;5;\r\n"))
    monkeypatch.setattr(webapp, "_export_age_s", lambda: webapp.EXPORT_MAX_AGE_S + 1)
    fake_run, calls = _ok_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)

    result = webapp.run_parovania_eshop()

    g = result["suppliers"]["gate_blocked"]
    assert g["reason"] == "stale"
    assert g["age_h"] == 6.0 and g["max_age_h"] == 6.0
    assert result["suppliers"]["blocked"] == 1
    assert result["status"] == "blocked"          # orange, not falsely green
    assert calls == []


def test_a_too_small_export_reports_the_size_reason_with_the_numbers(iso, monkeypatch):
    webapp._save_supplier_assign({"5/A": "BETALOV"})
    monkeypatch.setattr(webapp, "CODE2PAIR", {"5/A": "5"})
    monkeypatch.setattr(webapp, "EXPORT_MIN_CODES", 7033)
    monkeypatch.setattr(webapp, "_iter_export_lines",
                        _export_lines("code;pairCode;supplier\r\n5/A;5;\r\n"))
    fake_run, calls = _ok_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)

    result = webapp.run_parovania_eshop()

    g = result["suppliers"]["gate_blocked"]
    assert g["reason"] == "small"
    assert g["codes"] == 1 and g["min_codes"] == 7033
    assert calls == []


def test_a_healthy_run_carries_no_gate_reason(iso, monkeypatch):
    """The absence matters: the card must not render a blocked line on a good night."""
    webapp._save_supplier_assign({"1/M": "BETALOV"})
    fake_run, _calls = _ok_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)

    result = webapp.run_parovania_eshop()

    assert result["suppliers"]["gate_blocked"] is None
    assert result["suppliers"]["count"] == 1
