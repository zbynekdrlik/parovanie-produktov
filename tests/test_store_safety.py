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
import ast
import json
import os
import sys
import types

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


def _import_time_out_uses():
    """Every place app.py reads `OUT` in an expression that is EVALUATED AT IMPORT
    (i.e. not inside a function body). Those are the frozen ones — the shape that
    caused the wipe. Inside a function `OUT` is read per call, which is the point."""
    with open(os.path.join(ROOT, "webreview", "app.py"), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    frozen = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Name) and node.id == "OUT"
                and isinstance(node.ctx, ast.Load)):
            continue
        cur, lazy = node, False
        while cur in parents:
            cur = parents[cur]
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                lazy = True
                break
        if not lazy:
            frozen.append(node.lineno)
    return frozen


def test_no_store_path_is_frozen_at_import():
    """Drift guard, structural. The string-shaped version it replaces only saw
    module-level `str` globals, so a `pathlib.Path(OUT) / "x.json"`, an f-string, or a
    path frozen into a dict / class attribute / default argument was invisible to it —
    and every one of those re-introduces the #261 freeze. Reading the AST catches the
    CAUSE (OUT evaluated once, at import) instead of one of its shapes."""
    frozen = _import_time_out_uses()
    assert frozen == [], (
        f"app.py:{frozen} builds a path from OUT at import time — use `_store(name)`, "
        "which resolves against the CURRENT OUT on every use")


def test_no_module_level_value_hides_a_path_under_out():
    """The runtime half of the same guard: a path frozen INSIDE a container, a class
    attribute or a function default is still a frozen path, and none of those are a
    module-level `str`."""
    out = os.fspath(webapp.OUT)

    def _under_out(v):
        return isinstance(v, str) and os.path.isabs(v) and v.startswith(out + os.sep)

    # Runtime registries KEYED by a resolved path, by design — they hold whatever OUT
    # was when the app last touched a store, which is the opposite of frozen.
    runtime_caches = {"_store_reads", "_quarantined"}

    frozen = []
    for name, value in list(vars(webapp).items()):
        if name.startswith("__") or name in runtime_caches:
            continue
        # `type(...) is` on purpose: app.py holds Flask/werkzeug proxies whose attributes
        # explode outside a request context, so nothing here may probe an unknown object.
        if type(value) in (list, tuple, set, frozenset):
            seen = list(value)
        elif type(value) is dict:
            seen = list(value.keys()) + list(value.values())
        elif type(value) is type:
            seen = list(vars(value).values())
        elif type(value) is types.FunctionType:
            seen = (list(value.__defaults__ or ())
                    + list((value.__kwdefaults__ or {}).values()))
        else:
            seen = [value]
        frozen += [name for v in seen if _under_out(v)]
    assert sorted(set(frozen)) == [], f"frozen paths under OUT: {sorted(set(frozen))}"


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


def test_an_unreadable_store_is_never_degraded_to_empty(monkeypatch, tmp_path):
    """A missing file is a first run and a truncated one is repairable — but an I/O
    error on a store that IS there (permissions, EIO) is not evidence of „no work".
    Degrading it to `{}` and letting the next click persist a one-entry file is the
    silent loss this whole PR exists to prevent; it must stay a loud failure."""
    monkeypatch.setattr(webapp, "OUT", str(tmp_path))
    (tmp_path / "decisions.json").mkdir()          # open() → IsADirectoryError
    with pytest.raises(OSError):
        webapp._load_decisions()


def test_a_read_that_did_not_yield_the_stored_content_cannot_legitimise_a_wipe(
        monkeypatch, tmp_path):
    """Wrong-type store: the loader hands back the default, so the caller never saw
    the entries on disk — an empty write after that is still a wipe."""
    monkeypatch.setattr(webapp, "OUT", str(tmp_path))
    (tmp_path / "decisions.json").write_text('["a", "b", "c"]', encoding="utf-8")
    assert webapp._load_decisions() == {}
    with pytest.raises(webapp.StoreWipeRefused):
        webapp._save_decisions({})


def test_a_shrink_this_process_never_read_is_refused(monkeypatch, tmp_path):
    """The incident's fixture was not empty — `_arm_pairings` stubs `_load_decisions`
    to a small map. Refusing only EMPTY writes would have let a 3-entry fixture land
    on 2831 entries just as happily."""
    monkeypatch.setattr(webapp, "OUT", str(tmp_path))
    webapp._save_decisions(_two_decisions())
    with pytest.raises(webapp.StoreWipeRefused):
        webapp._save_decisions({"S|9": {"status": "manual", "url": "https://x.test/z"}})
    assert webapp._load_decisions() == _two_decisions()


def test_a_shrink_the_manager_actually_made_is_allowed(monkeypatch, tmp_path):
    monkeypatch.setattr(webapp, "OUT", str(tmp_path))
    webapp._save_decisions(_two_decisions())
    d = webapp._load_decisions()
    d.pop("S|2")
    webapp._save_decisions(d)
    assert list(webapp._load_decisions()) == ["S|1"]


def test_growing_a_store_never_needs_a_read(monkeypatch, tmp_path):
    """Only shrinking is suspicious — an incremental upload state that only ever
    grows must not start failing because it was computed before the read."""
    monkeypatch.setattr(webapp, "OUT", str(tmp_path))
    webapp._save_decisions(_two_decisions())
    webapp._store_reads.pop(os.fspath(webapp.DECISIONS), None)
    bigger = dict(_two_decisions(), **{"S|3": {"status": "good", "url": "https://x.test/c"}})
    webapp._save_decisions(bigger)
    assert len(webapp._load_decisions()) == 3


def test_the_dedup_stores_are_protected_too(monkeypatch, tmp_path):
    """orders_reminder.json / posta_uncollected.json are the ones whose loss means a
    SECOND mail to every customer — they were the only manager-critical stores left
    without the write guard."""
    monkeypatch.setattr(webapp, "OUT", str(tmp_path))
    (tmp_path / "orders_reminder.json").write_text(
        json.dumps({"orders": {"1": {"status": "emailed"}}, "red": []}), encoding="utf-8")
    with pytest.raises(webapp.StoreWipeRefused):
        webapp._save_orders_reminder({})
    st = webapp._load_orders_reminder()
    st["orders"]["2"] = {"status": "emailed"}
    webapp._save_orders_reminder(st)          # the normal path still works
    assert len(webapp._load_orders_reminder()["orders"]) == 2


# --------------------------------------------------------------------------- #
# Layer 3, hardened (PR #265 review) — the receipt is PROVENANCE-bound
#
# The first cut recorded „how many entries THIS PROCESS last read from that store"
# in a process-GLOBAL map. That is a stale-read detector, not a provenance check:
# `_load_decisions()` runs at import and on every /api/products + /api/orders, so
# the recorded count always equalled the disk count and the guard was permanently
# disarmed — the incident's own write would have been ALLOWED. The receipt is now
# the READ ITSELF (the object the loader handed back), so a caller that never
# loaded the store cannot produce one.
# --------------------------------------------------------------------------- #
def test_a_normal_read_elsewhere_never_legitimises_a_wipe_from_a_path_that_read_nothing(
        monkeypatch, tmp_path):
    """THE incident's shape, reproduced: the live app reads decisions on every page
    load, and then something that never loaded the store writes over it."""
    monkeypatch.setattr(webapp, "OUT", str(tmp_path))
    webapp._save_decisions(_two_decisions())
    webapp._load_decisions()                     # a normal read, as /api/products does
    with pytest.raises(webapp.StoreWipeRefused):
        webapp._save_decisions({})               # …from a path that never loaded it
    assert webapp._load_decisions() == _two_decisions()


def test_a_read_of_a_DIFFERENT_store_cannot_legitimise_a_wipe(monkeypatch, tmp_path):
    """The receipt is per-store: loading ordered_items must say nothing about what
    may be written over decisions."""
    monkeypatch.setattr(webapp, "OUT", str(tmp_path))
    webapp._save_decisions(_two_decisions())
    webapp._save_ordered({"O1|A": True, "O1|B": True})
    webapp._load_ordered()
    with pytest.raises(webapp.StoreWipeRefused):
        webapp._save_decisions({})


def test_two_undos_in_a_row_are_both_allowed(monkeypatch, tmp_path):
    """After a successful write the process HAS the store it just wrote — the receipt
    must follow it, or the manager's second undo in a row 503s (the guard's own
    bookkeeping going stale)."""
    monkeypatch.setattr(webapp, "OUT", str(tmp_path))
    webapp._save_decisions(dict(_two_decisions(),
                                **{"S|3": {"status": "good", "url": "https://x.test/c"}}))
    d = webapp._load_decisions()
    d.pop("S|1")
    webapp._save_decisions(d)
    d.pop("S|2")
    webapp._save_decisions(d)                    # used to raise: the receipt was stale
    assert list(webapp._load_decisions()) == ["S|3"]


def test_a_rebuilt_map_may_shrink_when_it_names_the_read_it_was_built_from(
        monkeypatch, tmp_path):
    """Not every read-modify-write mutates in place — the startup prune builds a NEW
    dict from the one it read. Such a caller passes `prev=` (the map it loaded)."""
    monkeypatch.setattr(webapp, "OUT", str(tmp_path))
    webapp._save_decisions(_two_decisions())
    d0 = webapp._load_decisions()
    d1 = {k: v for k, v in d0.items() if k == "S|1"}
    webapp._save_decisions(d1, prev=d0)
    assert list(webapp._load_decisions()) == ["S|1"]


def test_a_rebuilt_map_that_names_nothing_is_still_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(webapp, "OUT", str(tmp_path))
    webapp._save_decisions(_two_decisions())
    webapp._load_decisions()
    with pytest.raises(webapp.StoreWipeRefused):
        webapp._save_decisions({"S|1": {"status": "manual", "url": "https://x.test/a"}})


# --------------------------------------------------------------------------- #
# Layer 3, hardened — „unreadable" must never be read as „empty"
# --------------------------------------------------------------------------- #
def test_a_truncated_store_is_never_treated_as_zero_entries(monkeypatch, tmp_path):
    """A half-written decisions.json parses as NOTHING, so the entry count used to come
    back 0 and the guard skipped itself entirely — one manager click then replaced ~1400
    recoverable entries with one. There is no fsync-free way to rule this out, so it is
    the MOST likely real corruption, not the least."""
    monkeypatch.setattr(webapp, "OUT", str(tmp_path))
    webapp._save_decisions(_two_decisions())
    p = tmp_path / "decisions.json"
    raw = p.read_text(encoding="utf-8")
    p.write_text(raw[:len(raw) // 2], encoding="utf-8")     # cut mid-write
    assert webapp._load_decisions() == {}                   # display still degrades…
    with pytest.raises(webapp.StoreWipeRefused):            # …but a write is refused
        webapp._save_decisions({"S|9": {"status": "manual", "url": "https://x.test/z"}})
    assert list(tmp_path.glob("decisions.json.corrupt-*")), \
        "the unreadable bytes must be preserved before anything refuses"
    assert p.read_text(encoding="utf-8") == raw[:len(raw) // 2]   # original untouched


def test_a_truncated_store_is_refused_even_when_the_write_would_GROW_it(
        monkeypatch, tmp_path):
    """Growth normally needs no read — but over a file we cannot parse we do not know
    what we are growing FROM, so there is nothing to compare and everything to lose."""
    monkeypatch.setattr(webapp, "OUT", str(tmp_path))
    (tmp_path / "ordered_items.json").write_text('{"a": true, "b"', encoding="utf-8")
    with pytest.raises(webapp.StoreWipeRefused):
        webapp._save_ordered({"a": True, "b": True, "c": True})


def test_an_empty_file_is_not_corruption(monkeypatch, tmp_path):
    """A zero-byte store is a fresh/never-written one — nothing to lose, so a write
    must go straight through (a `touch`ed file must not brick the tab)."""
    monkeypatch.setattr(webapp, "OUT", str(tmp_path))
    (tmp_path / "decisions.json").write_text("", encoding="utf-8")
    webapp._save_decisions({"S|1": {"status": "manual", "url": "https://x.test/a"}})
    assert list(webapp._load_decisions()) == ["S|1"]


# --------------------------------------------------------------------------- #
# Layer 3, hardened — the two dedup stores keep their map NESTED
# --------------------------------------------------------------------------- #
def _reminder_state(n: int) -> dict:
    return {"orders": {str(i): {"status": "emailed"} for i in range(n)},
            "red": [], "orange": [], "skipped": [], "no_email": [],
            "stats": {}, "last_check": "", "fingerprints": {}}


def test_the_nested_dedup_map_cannot_be_wiped_by_a_path_that_never_read_it(
        monkeypatch, tmp_path):
    """`orders_reminder.json` is `{"orders": {...}, "red": [...], …}` — the record of
    who was already mailed is the NESTED map, and every real writer keeps the same
    top-level keys. Counting only the outer dict made `protect` inert for exactly the
    two stores whose loss re-mails every customer."""
    monkeypatch.setattr(webapp, "OUT", str(tmp_path))
    (tmp_path / "orders_reminder.json").write_text(
        json.dumps(_reminder_state(50)), encoding="utf-8")
    with pytest.raises(webapp.StoreWipeRefused):
        webapp._save_orders_reminder(_reminder_state(0))
    assert len(webapp._load_orders_reminder()["orders"]) == 50


def test_the_nested_escalation_map_is_guarded_the_same_way(monkeypatch, tmp_path):
    monkeypatch.setattr(webapp, "OUT", str(tmp_path))
    state = {"escalation": {str(i): "1|2026-01-01" for i in range(20)},
             "terminal": {}, "uncollected": [], "invalid": [], "errors": [],
             "stats": {}, "last_check": ""}
    (tmp_path / "posta_uncollected.json").write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(webapp.StoreWipeRefused):
        webapp._save_posta_state(dict(state, escalation={}))
    assert len(webapp._load_posta_state()["escalation"]) == 20


def test_pruning_the_dedup_map_after_a_real_read_still_works(monkeypatch, tmp_path):
    """#220 retention really does shrink `orders` — a blanket „never shrink the nested
    map" rule would break the run that bounds it. The receipt is what tells them apart."""
    monkeypatch.setattr(webapp, "OUT", str(tmp_path))
    (tmp_path / "orders_reminder.json").write_text(
        json.dumps(_reminder_state(50)), encoding="utf-8")
    st = webapp._load_orders_reminder()
    st["orders"] = {k: v for k, v in list(st["orders"].items())[:10]}
    webapp._save_orders_reminder(st)
    assert len(webapp._load_orders_reminder()["orders"]) == 10


# --------------------------------------------------------------------------- #
# Belt and braces — a test process may never write into the manager's data dir
# --------------------------------------------------------------------------- #
def test_a_write_into_the_live_data_dir_is_refused_while_pytest_runs(tmp_path):
    """The conftest pin is the real defence; this is the net under it — a helper (or a
    subprocess that inherits the env) that resolves OUT to the live dir must still be
    unable to write there."""
    target = os.path.join(LIVE_OUT, "pytest-must-never-write.json")
    with pytest.raises(webapp.StoreWipeRefused):
        webapp._atomic_write_json(target, {"x": 1})
    assert not os.path.exists(target)


def test_the_quarantine_memo_is_keyed_by_the_resolved_path(monkeypatch, tmp_path):
    """`_quarantined` used to be keyed by the store object, whose hash follows the
    CURRENT OUT — every repointed data dir left an entry nobody can ever look up again."""
    monkeypatch.setattr(webapp, "OUT", str(tmp_path))
    (tmp_path / "orders_reminder.json").write_text("{ truncated", encoding="utf-8")
    webapp._quarantine_corrupt_store(webapp.ORDERS_REMINDER_STATE)
    assert webapp._quarantined, "nothing was memoised"
    assert all(isinstance(k, str) for k in webapp._quarantined), \
        f"memo keys must be plain paths: {list(webapp._quarantined)}"


def test_a_refused_write_answers_503_with_something_to_fix(monkeypatch, tmp_path):
    """A bare 500 reads like a transient glitch and invites another click; the manager
    must be told what happened (same shape as the corrupt-dedup-store handler)."""
    for exc in (webapp.StoreWipeRefused("x"), webapp.StoreLockTimeout("y")):
        handler = webapp.app.error_handler_spec[None][None][type(exc)]
        with webapp.app.test_request_context():
            body, status = handler(exc)
        assert status == 503 and body.get_json()["ok"] is False
        assert body.get_json()["error"]


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
