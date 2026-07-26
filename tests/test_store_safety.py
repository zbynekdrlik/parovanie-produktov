"""#261 — the manager's live data/out must be unreachable from a test run.

On 2026-07-26 a pytest run wiped all 2831 review decisions: `DECISIONS` (and every
other store path) was computed from `OUT` at IMPORT time, so a test helper that
repointed `webapp.OUT` at a tmp dir left `DECISIONS` aimed at the live store — and
the first `_save_decisions` of a fixture wrote straight over the manager's work.

Three layers are pinned here, each on its own:
  1. every store path is derived from the CURRENT `OUT` (patching OUT redirects all);
  2. the whole backend suite runs against a tmp `WEBREVIEW_OUT` (conftest), so even a
     helper that forgets to patch anything cannot reach data/out;
  3. a write that would drop a populated store to empty is refused, loudly.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webreview"))
import app as webapp  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIVE_OUT = os.path.abspath(os.path.join(ROOT, "data", "out"))

# Every module-level path that lives inside OUT. A new store MUST be added here —
# and `test_no_store_path_is_frozen_at_import` catches it even if someone forgets.
STORE_ATTRS = [
    "DATA", "DECISIONS", "IMGCACHE", "USERS", "RESET_TOKENS", "ORDERED",
    "ORDER_PAIRINGS", "VARIANT_LINKS", "WAITING", "INSTOCK", "UNAVAIL",
    "ORDER_COMMENTS", "NEDOSTUPNE", "UI_LABELS", "NOTES", "SUPPLIER_ASSIGN",
    "GRUBE_CODES", "ORDERS_CACHE", "CUSTOMERS_CACHE", "VYSTAVY", "PAIRINGS_STATE",
    "SUPPLIERS_STATE", "EXTERNALCODES_STATE", "VARIANT_LINKS_STATE",
    "AUTOMATIONS_STATE", "POSTA_STATE", "ORDERS_REMINDER_STATE",
    "SUPPLIER_STOCK_STATE", "RIZIKO_STATE", "RESTOCK_STATE", "STOCK_SKLADOM_STATE",
    "IMAGE_HEALTH_STATE",
]


# --------------------------------------------------------------------------- #
# Layer 1 — paths follow the CURRENT OUT, never the import-time one
# --------------------------------------------------------------------------- #
def test_patching_out_redirects_every_store_path(monkeypatch, tmp_path):
    monkeypatch.setattr(webapp, "OUT", str(tmp_path))
    frozen = [n for n in STORE_ATTRS
              if os.path.dirname(os.fspath(getattr(webapp, n))) != str(tmp_path)]
    assert frozen == [], f"still frozen at import time: {frozen}"


def test_no_store_path_is_frozen_at_import():
    """Drift guard: a NEW `X = os.path.join(OUT, ...)` constant would be a plain
    absolute str under OUT — exactly the shape that caused the wipe."""
    out = os.fspath(webapp.OUT)
    frozen = sorted(n for n, v in vars(webapp).items()
                    if isinstance(v, str) and not n.startswith("__")
                    and os.path.isabs(v) and v.startswith(out + os.sep))
    assert frozen == [], f"store paths frozen as plain strings: {frozen}"


# --------------------------------------------------------------------------- #
# Layer 2 — the suite itself can never reach data/out
# --------------------------------------------------------------------------- #
def test_the_backend_suite_runs_against_a_throwaway_out():
    assert os.path.abspath(os.fspath(webapp.OUT)) != LIVE_OUT


def test_no_store_of_this_run_points_into_the_live_data_dir():
    """The proof the issue asks for: a write aimed at data/out must be impossible."""
    live = [n for n in STORE_ATTRS
            if os.path.abspath(os.fspath(getattr(webapp, n))).startswith(LIVE_OUT + os.sep)]
    assert live == [], f"these stores still point at the manager's live data: {live}"


def test_the_shoptet_export_is_isolated_too():
    """`run_shoptet_sync` overwrites SRC in place — a test must not rewrite the
    real data/products.csv either."""
    assert os.path.abspath(os.fspath(webapp.SRC)) != os.path.abspath(
        os.path.join(ROOT, "data", "products.csv"))


def test_the_exact_helper_that_wiped_the_store_now_lands_in_the_tmp_dir(monkeypatch, tmp_path):
    """Reproduces `_arm_pairings`: patch OUT only, then save. Before the fix this
    wrote decisions.json into data/out."""
    monkeypatch.setattr(webapp, "OUT", str(tmp_path))
    webapp._save_decisions({"S|1": {"status": "manual", "url": "https://x.test/a"}})
    assert json.loads((tmp_path / "decisions.json").read_text(encoding="utf-8"))
    assert webapp._load_decisions() == {"S|1": {"status": "manual", "url": "https://x.test/a"}}


# --------------------------------------------------------------------------- #
# Layer 3 — an empty map never overwrites a populated store
# --------------------------------------------------------------------------- #
def _two_decisions():
    return {"S|1": {"status": "manual", "url": "https://x.test/a"},
            "S|2": {"status": "good", "url": "https://x.test/b"}}


def test_an_empty_map_never_overwrites_a_populated_decisions_store(monkeypatch, tmp_path):
    monkeypatch.setattr(webapp, "OUT", str(tmp_path))
    webapp._save_decisions(_two_decisions())
    with pytest.raises(webapp.StoreWipeRefused):
        webapp._save_decisions({})
    assert webapp._load_decisions() == _two_decisions()   # untouched


def test_undoing_the_very_last_decision_is_still_allowed(monkeypatch, tmp_path):
    """The manager CAN legitimately undo his only remaining decision (1 → 0). The
    real flow reads the store, drops the key and saves — that read is what tells the
    guard the empty map really is the manager's work and not a fixture."""
    monkeypatch.setattr(webapp, "OUT", str(tmp_path))
    webapp._save_decisions({"S|1": {"status": "manual", "url": "https://x.test/a"}})
    d = webapp._load_decisions()
    d.pop("S|1")
    webapp._save_decisions(d)
    assert webapp._load_decisions() == {}


def test_clearing_a_whole_group_in_one_write_is_still_allowed(monkeypatch, tmp_path):
    """`/api/ordered/bulk` un-marks a whole supplier group at once, so a populated
    store legitimately empties in ONE write — a plain „empty over non-empty" rule
    would break the manager's bulk button (it did, in the first cut of this fix)."""
    monkeypatch.setattr(webapp, "OUT", str(tmp_path))
    webapp._save_ordered({"O1|A": True, "O1|B": True, "O1|C": True})
    d = webapp._load_ordered()
    for k in list(d):
        d.pop(k)
    webapp._save_ordered(d)
    assert webapp._load_ordered() == {}


def test_a_wipe_is_refused_even_when_the_store_holds_a_single_entry(monkeypatch, tmp_path):
    """No count threshold: what makes an empty write legitimate is having READ the
    store, not how big it is."""
    monkeypatch.setattr(webapp, "OUT", str(tmp_path))
    webapp._save_decisions({"S|1": {"status": "manual", "url": "https://x.test/a"}})
    with pytest.raises(webapp.StoreWipeRefused):
        webapp._save_decisions({})


def test_a_wipe_is_refused_when_someone_else_wrote_after_our_read(monkeypatch, tmp_path):
    """Read 2, another process appends a third, then we save empty: our empty map is
    not what the manager just did to THAT store — it would silently lose the third
    entry (the #264 lost-update shape)."""
    monkeypatch.setattr(webapp, "OUT", str(tmp_path))
    webapp._save_decisions(_two_decisions())
    webapp._load_decisions()
    grown = dict(_two_decisions(), **{"S|3": {"status": "good", "url": "https://x.test/c"}})
    (tmp_path / "decisions.json").write_text(json.dumps(grown), encoding="utf-8")
    with pytest.raises(webapp.StoreWipeRefused):
        webapp._save_decisions({})
    assert len(webapp._load_decisions()) == 3


@pytest.mark.parametrize("save_fn,load_fn", [
    ("_save_decisions", "_load_decisions"),
    ("_save_ordered", "_load_ordered"),
    ("_save_order_pairings", "_load_order_pairings"),
    ("_save_variant_links", "_load_variant_links"),
    ("_save_waiting", "_load_waiting"),
    ("_save_instock", "_load_instock"),
    ("_save_unavailable", "_load_unavailable"),
    ("_save_order_comments", "_load_order_comments"),
    ("_save_supplier_assign", "_load_supplier_assign"),
    ("_save_nedostupne", "_load_nedostupne"),
])
def test_every_manager_work_store_refuses_a_wipe(monkeypatch, tmp_path, save_fn, load_fn):
    monkeypatch.setattr(webapp, "OUT", str(tmp_path))
    populated = {"a": {"x": 1}, "b": {"x": 2}}
    getattr(webapp, save_fn)(populated)
    with pytest.raises(webapp.StoreWipeRefused):
        getattr(webapp, save_fn)({})
    assert getattr(webapp, load_fn)() == populated


def test_a_refused_wipe_leaves_no_half_written_temp_file(monkeypatch, tmp_path):
    monkeypatch.setattr(webapp, "OUT", str(tmp_path))
    webapp._save_decisions(_two_decisions())
    with pytest.raises(webapp.StoreWipeRefused):
        webapp._save_decisions({})
    assert list(tmp_path.glob("*.tmp")) == []


def test_the_startup_prune_never_wipes_when_the_product_list_failed_to_load(
        monkeypatch, tmp_path):
    """review_data.json missing → PRODUCTS == [] → every decision looks orphaned.
    Pruning then would delete the manager's whole history at the next restart."""
    monkeypatch.setattr(webapp, "OUT", str(tmp_path))
    webapp._save_decisions(_two_decisions())
    webapp._prune_orphan_decisions([])
    assert webapp._load_decisions() == _two_decisions()


def test_the_startup_prune_still_drops_a_genuine_orphan(monkeypatch, tmp_path):
    monkeypatch.setattr(webapp, "OUT", str(tmp_path))
    webapp._save_decisions(_two_decisions())
    webapp._prune_orphan_decisions([{"key": "S|1"}])
    assert list(webapp._load_decisions()) == ["S|1"]
