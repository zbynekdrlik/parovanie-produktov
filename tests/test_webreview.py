"""Smoke/route tests for the deployed Flask review UI (webreview/app.py).

The app tolerates missing data files at import (loads 0 products), so these run
in CI without the gitignored data/ tree.
"""
import io
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webreview"))
import app as webapp  # noqa: E402

from tests.conftest import authed_client as _client  # noqa: E402 — logged-in session (#91)


def test_version_route_returns_vsemver():
    r = _client().get("/api/version")
    assert r.status_code == 200
    body = r.data.decode()
    assert body.startswith("v") and body[1:].split(".")[0].isdigit()


def test_products_route_shape():
    r = _client().get("/api/products")
    assert r.status_code == 200
    j = r.get_json()
    assert "products" in j and "decisions" in j


def test_import_route_returns_zip():
    r = _client().get("/api/import")
    assert r.status_code == 200
    assert r.headers["Content-Type"] == "application/zip"
    assert r.data[:2] == b"PK"  # zip magic


def test_export_route_shape():
    r = _client().get("/api/export")
    assert r.status_code == 200
    assert "decisions" in r.get_json()


# --- Na objednanie (to-order tab) ------------------------------------------- #
def test_build_to_order_rows_filters_and_joins():
    orders = (
        "code;date;statusName;itemName;itemAmount;itemCode;itemVariantName;itemSupplier\r\n"
        "99001045;2026-04-24 19:14:05;Vybavuje sa;Polokošeľa HART;1;TESTKOD/L;Veľkosť: L;BETALOV\r\n"
        "99001099;2026-05-01 10:00:00;Vybavená;Iné;1;99999/M;Veľkosť: M;ORBIS\r\n"
        "99001045;2026-04-24 19:14:05;Vybavuje sa;Kuriér;1;SHIPPING11;;\r\n"
    )
    products = [{"key": "BETALOV|231", "supplier": "BETALOV", "name": "Polokošeľa HART",
                 "variant_codes": ["TESTKOD/L"], "pairCode": "231"}]
    decisions = {"BETALOV|231": {"status": "good", "url": "https://www.huntingshop.eu/x"}}
    rows = webapp.build_to_order_rows(orders, products, decisions, {"TESTKOD/L": "231"})
    assert len(rows) == 1                      # Vybavená + SHIPPING dropped
    r = rows[0]
    assert r["itemCode"] == "TESTKOD/L" and r["qty"] == "1" and r["supplier"] == "BETALOV"
    assert r["size"] == "Veľkosť: L"
    assert r["key"] == "99001045|TESTKOD/L"
    assert r["orderDate"] == "2026-04-24"      # date column, time dropped
    assert r["supplierUrl"] == "https://www.huntingshop.eu/x"


def test_build_to_order_rows_missing_date_is_empty():
    orders = ("code;statusName;itemName;itemAmount;itemCode;itemVariantName;itemSupplier\r\n"
              "99001045;Vybavuje sa;X;1;TESTKOD/L;L;BETALOV\r\n")
    rows = webapp.build_to_order_rows(orders, [], {}, {})
    assert rows[0]["orderDate"] == ""          # no date column → graceful empty


def test_ordered_endpoint_persists(monkeypatch, tmp_path):
    monkeypatch.setattr(webapp, "ORDERED", str(tmp_path / "ordered.json"))
    c = _client()
    r = c.post("/api/ordered", json={"key": "99001045|TESTKOD/L", "ordered": True})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert c.get("/api/ordered").get_json()["ordered"]["99001045|TESTKOD/L"] is True
    c.post("/api/ordered", json={"key": "99001045|TESTKOD/L", "ordered": False})
    assert "99001045|TESTKOD/L" not in c.get("/api/ordered").get_json()["ordered"]


# --- VYLEPŠENIE 1: mark a whole supplier group ordered in one atomic write ------ #
def test_ordered_bulk_endpoint_persists_and_validates(monkeypatch, tmp_path):
    monkeypatch.setattr(webapp, "ORDERED", str(tmp_path / "o.json"))
    c = _client()
    # set a whole group ordered; blank/whitespace keys are dropped
    r = c.post("/api/ordered/bulk", json={"keys": ["A|1", "B|2", "", "  "], "ordered": True})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert r.get_json()["count"] == 2
    assert c.get("/api/ordered").get_json()["ordered"] == {"A|1": True, "B|2": True}
    # un-order the group
    c.post("/api/ordered/bulk", json={"keys": ["A|1", "B|2"], "ordered": False})
    assert c.get("/api/ordered").get_json()["ordered"] == {}
    # missing / non-list keys → 400; all-blank keys → 400
    assert c.post("/api/ordered/bulk", json={"ordered": True}).status_code == 400
    assert c.post("/api/ordered/bulk", json={"keys": "nope", "ordered": True}).status_code == 400
    assert c.post("/api/ordered/bulk", json={"keys": ["", "  "], "ordered": True}).status_code == 400


# --- BUG 2: corrupt store must not 500 the tab; blank key must not write "None" --- #
def test_ordered_waiting_reject_missing_key(monkeypatch, tmp_path):
    # a POST with no key must 400 — never write a "None"/"" key into the store
    # (mirrors instock/unavailable). Guards the /api/ordered + /api/waiting sinks.
    monkeypatch.setattr(webapp, "ORDERED", str(tmp_path / "o.json"))
    monkeypatch.setattr(webapp, "WAITING", str(tmp_path / "w.json"))
    c = _client()
    assert c.post("/api/ordered", json={"ordered": True}).status_code == 400
    assert c.post("/api/waiting", json={"waiting": True}).status_code == 400
    assert c.post("/api/ordered", json={"key": "  ", "ordered": True}).status_code == 400
    assert c.get("/api/ordered").get_json()["ordered"] == {}
    assert c.get("/api/waiting").get_json()["waiting"] == {}


def test_toorder_loaders_tolerate_corrupt_store(monkeypatch, tmp_path):
    # a hand-corrupted / wrong-type flag store must degrade to {} (like _load_instock),
    # never raise — one bad file must not 500 the whole /api/orders tab.
    for name, loader in (("ORDERED", "_load_ordered"), ("WAITING", "_load_waiting"),
                         ("ORDER_PAIRINGS", "_load_order_pairings"),
                         ("VARIANT_LINKS", "_load_variant_links")):
        bad = tmp_path / (name + ".json")
        bad.write_text("{ this is not json", encoding="utf-8")
        monkeypatch.setattr(webapp, name, str(bad))
        assert getattr(webapp, loader)() == {}, name
        bad.write_text("[]", encoding="utf-8")   # wrong top-level type
        assert getattr(webapp, loader)() == {}, name


def test_orders_route_tolerates_corrupt_flag_store(monkeypatch, tmp_path):
    orders = ("code;statusName;itemName;itemAmount;itemCode;itemVariantName;itemSupplier\r\n"
              "99001045;Vybavuje sa;Polokošeľa;1;TESTKOD/L;Veľkosť: L;BETALOV\r\n")
    monkeypatch.setattr(webapp, "_orders_csv_cached", lambda: orders.encode("cp1250"))
    monkeypatch.setattr(webapp, "PRODUCTS",
        [{"key": "BETALOV|231", "supplier": "BETALOV", "name": "Polokošeľa",
          "variant_codes": ["TESTKOD/L"], "pairCode": "231"}])
    monkeypatch.setattr(webapp, "CODE2PAIR", {"TESTKOD/L": "231"})
    ordf = tmp_path / "o.json"
    ordf.write_text("{ broken", encoding="utf-8")
    waitf = tmp_path / "w.json"
    waitf.write_text("[]", encoding="utf-8")  # wrong type
    # decisions.json + supplier_assignments.json are the MOST exposed stores on this tab
    # (decisions written on every review click; supplier_assign written by the app AND by
    # n8n) — a corrupt/partial one must degrade to {} too, never 500 the whole /api/orders
    # tab. This test drives the REAL corrupt-file path of _load_decisions +
    # _load_supplier_assign (no monkeypatched loader) — RED before their guard, GREEN after.
    decf = tmp_path / "decisions.json"
    decf.write_text("{ broken", encoding="utf-8")
    saf = tmp_path / "supplier_assignments.json"
    saf.write_text("[]", encoding="utf-8")  # wrong type
    monkeypatch.setattr(webapp, "ORDERED", str(ordf))
    monkeypatch.setattr(webapp, "WAITING", str(waitf))
    monkeypatch.setattr(webapp, "INSTOCK", str(tmp_path / "is.json"))
    monkeypatch.setattr(webapp, "UNAVAIL", str(tmp_path / "un.json"))
    monkeypatch.setattr(webapp, "ORDER_PAIRINGS", str(tmp_path / "op.json"))
    monkeypatch.setattr(webapp, "DECISIONS", str(decf))
    monkeypatch.setattr(webapp, "SUPPLIER_ASSIGN", str(saf))
    r = _client().get("/api/orders")
    assert r.status_code == 200          # corrupt store degrades, tab still renders
    j = r.get_json()
    assert j["orders"][0]["ordered"] is False
    assert j["orders"][0]["waiting"] is False
    assert j["orders"][0]["assignedSupplier"] == ""   # corrupt supplier_assign → {} → no assign


def test_orders_route_joins_and_merges_ordered(monkeypatch, tmp_path):
    orders = ("code;statusName;itemName;itemAmount;itemCode;itemVariantName;itemSupplier\r\n"
              "99001045;Vybavuje sa;Polokošeľa;1;TESTKOD/L;Veľkosť: L;BETALOV\r\n")
    monkeypatch.setattr(webapp, "_orders_csv_cached", lambda: orders.encode("cp1250"))
    monkeypatch.setattr(webapp, "PRODUCTS",
        [{"key": "BETALOV|231", "supplier": "BETALOV", "name": "Polokošeľa",
          "variant_codes": ["TESTKOD/L"], "pairCode": "231"}])
    monkeypatch.setattr(webapp, "CODE2PAIR", {"TESTKOD/L": "231"})
    monkeypatch.setattr(webapp, "ORDERED", str(tmp_path / "o.json"))
    monkeypatch.setattr(webapp, "WAITING", str(tmp_path / "w.json"))
    monkeypatch.setattr(webapp, "ORDER_PAIRINGS", str(tmp_path / "op.json"))
    monkeypatch.setattr(webapp, "SUPPLIER_ASSIGN", str(tmp_path / "sa.json"))
    monkeypatch.setattr(webapp, "_load_decisions",
        lambda: {"BETALOV|231": {"status": "good", "url": "https://www.huntingshop.eu/x"}})
    j = _client().get("/api/orders").get_json()
    assert len(j["orders"]) == 1
    assert j["orders"][0]["supplierUrl"] == "https://www.huntingshop.eu/x"
    assert j["orders"][0]["ordered"] is False
    assert j["orders"][0]["waiting"] is False
    assert j["orders"][0]["assignedSupplier"] == ""


# --- Na objednanie: 'skladom' / 'nedostupné' per-line flags (#84) --------------- #
def test_instock_endpoint_persists(monkeypatch, tmp_path):
    monkeypatch.setattr(webapp, "INSTOCK", str(tmp_path / "instock.json"))
    c = _client()
    r = c.post("/api/instock", json={"key": "99001045|TESTKOD/L", "instock": True})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert c.get("/api/instock").get_json()["instock"]["99001045|TESTKOD/L"] is True
    c.post("/api/instock", json={"key": "99001045|TESTKOD/L", "instock": False})
    assert "99001045|TESTKOD/L" not in c.get("/api/instock").get_json()["instock"]


def test_unavailable_endpoint_persists(monkeypatch, tmp_path):
    monkeypatch.setattr(webapp, "UNAVAIL", str(tmp_path / "unavail.json"))
    c = _client()
    r = c.post("/api/unavailable", json={"key": "99001045|TESTKOD/L", "unavailable": True})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert c.get("/api/unavailable").get_json()["unavailable"]["99001045|TESTKOD/L"] is True
    c.post("/api/unavailable", json={"key": "99001045|TESTKOD/L", "unavailable": False})
    assert "99001045|TESTKOD/L" not in c.get("/api/unavailable").get_json()["unavailable"]


def test_instock_unavailable_reject_missing_key(monkeypatch, tmp_path):
    monkeypatch.setattr(webapp, "INSTOCK", str(tmp_path / "instock.json"))
    monkeypatch.setattr(webapp, "UNAVAIL", str(tmp_path / "unavail.json"))
    c = _client()
    # a POST with no key must 400, never write a "None"/"" key into the store
    assert c.post("/api/instock", json={"instock": True}).status_code == 400
    assert c.post("/api/unavailable", json={"unavailable": True}).status_code == 400
    assert c.get("/api/instock").get_json()["instock"] == {}
    assert c.get("/api/unavailable").get_json()["unavailable"] == {}


def test_instock_unavailable_tolerate_corrupt_store(monkeypatch, tmp_path):
    # a hand-corrupted flag store must not 500 the loader (mirrors _load_notes)
    isf = tmp_path / "instock.json"
    isf.write_text("{ this is not json", encoding="utf-8")
    unf = tmp_path / "unavail.json"
    unf.write_text("[]", encoding="utf-8")  # wrong type
    monkeypatch.setattr(webapp, "INSTOCK", str(isf))
    monkeypatch.setattr(webapp, "UNAVAIL", str(unf))
    assert webapp._load_instock() == {}
    assert webapp._load_unavailable() == {}


# --- #211: the STATUS flags are mutually exclusive; the SERVER enforces it ------ #
#
# Two axes, decided from the manager's own live data (27 rows carried a combination and
# EVERY one of them involved „objednané"; „čaká sa"/„skladom"/„nedostupné" never once
# overlapped each other):
#   axis A  „objednané"  = we placed the order — independent, coexists with anything
#   axis B  „čaká sa" ⊕ „skladom" ⊕ „nedostupné" = the line's status — mutually exclusive
# Setting an axis-B flag clears the other two IN THE SAME `with _lock:` write, so no
# reader can ever observe a row holding two contradictory statuses.
def _flag_paths(monkeypatch, tmp_path):
    for name, fn in (("ORDERED", "o.json"), ("WAITING", "w.json"),
                     ("INSTOCK", "is.json"), ("UNAVAIL", "un.json")):
        monkeypatch.setattr(webapp, name, str(tmp_path / fn))


_STATUS = [("/api/waiting", "waiting"), ("/api/instock", "instock"),
           ("/api/unavailable", "unavailable")]


@pytest.mark.parametrize("path, field", _STATUS)
def test_setting_a_status_flag_clears_the_other_two(monkeypatch, tmp_path, path, field):
    _flag_paths(monkeypatch, tmp_path)
    c = _client()
    key = "90000001|TESTKOD-A"
    for p, f in _STATUS:            # start from every other status already set
        if p != path:
            c.post(p, json={"key": key, f: True})
    r = c.post(path, json={"key": key, field: True})
    assert r.status_code == 200
    for p, f in _STATUS:
        got = c.get(p).get_json()[f]
        assert (key in got) is (p == path), (p, got)


@pytest.mark.parametrize("path, field", _STATUS)
def test_a_status_flag_never_clears_objednane(monkeypatch, tmp_path, path, field):
    """„objednané" is the OTHER axis: objednané + čaká sa na dodávateľa (the row button's
    own tooltip) and objednané + už prišlo are both real states the manager uses. Clearing
    it here would delete markings he made on purpose."""
    _flag_paths(monkeypatch, tmp_path)
    c = _client()
    key = "90000001|TESTKOD-A"
    c.post("/api/ordered", json={"key": key, "ordered": True})
    c.post(path, json={"key": key, field: True})
    assert c.get("/api/ordered").get_json()["ordered"][key] is True
    # …and the reverse: marking it ordered afterwards leaves the status flag alone
    c.post("/api/ordered", json={"key": key, "ordered": True})
    assert key in c.get(path).get_json()[field]


@pytest.mark.parametrize("path, field", _STATUS)
def test_turning_a_status_flag_OFF_clears_nothing_else(monkeypatch, tmp_path, path, field):
    """Only switching a flag ON is a statement about the line's status. Switching one OFF
    just makes the line unhandled again — it must not reach into the other stores."""
    _flag_paths(monkeypatch, tmp_path)
    c = _client()
    key, other = "90000001|TESTKOD-A", "90000001|TESTKOD-B"
    c.post("/api/ordered", json={"key": key, "ordered": True})
    # A DIFFERENT row, carrying the LEGAL maximum: axis A + exactly one axis-B flag.
    # (Seeding all three on it would be seeding a state the server now refuses to hold.)
    c.post("/api/ordered", json={"key": other, "ordered": True})
    c.post(path, json={"key": other, field: True})
    c.post(path, json={"key": key, field: False})
    assert c.get("/api/ordered").get_json()["ordered"][key] is True
    assert c.get("/api/ordered").get_json()["ordered"][other] is True
    assert other in c.get(path).get_json()[field]
    # …and the OTHER two stores of the row being switched off stay as they were: empty,
    # because nothing ever put a flag there. Turning one off reaches into no store but its own.
    for p, f in _STATUS:
        if p != path:
            assert c.get(p).get_json()[f] == {}, p


_ALL_FLAGS = {"ordered", "waiting", "instock", "unavailable"}


@pytest.mark.parametrize("path, field", _STATUS)
def test_the_write_answers_with_the_resulting_flags(monkeypatch, tmp_path, path, field):
    """The server is the authority on the row's state, so it says what the state now IS —
    the client only reflects it."""
    _flag_paths(monkeypatch, tmp_path)
    c = _client()
    key = "90000001|TESTKOD-A"
    c.post("/api/waiting", json={"key": key, "waiting": True})
    flags = c.post(path, json={"key": key, field: True}).get_json()["flags"]
    assert flags[field] is True
    assert set(flags) == _ALL_FLAGS
    for p, f in _STATUS:
        assert flags[f] is (f == field), (f, flags)


def test_the_objednane_write_answers_with_the_flags_it_did_not_touch(monkeypatch, tmp_path):
    """„objednané" answers in the SAME shape (one thing for the client to mirror) but it is
    axis A: the „čaká sa" standing on that row is still there afterwards. Folding this case
    into the axis-B test above asserted the opposite — that marking a line ordered wipes its
    status — which is exactly the four-way exclusivity #211 rejected."""
    _flag_paths(monkeypatch, tmp_path)
    c = _client()
    key = "90000001|TESTKOD-A"
    c.post("/api/waiting", json={"key": key, "waiting": True})
    flags = c.post("/api/ordered", json={"key": key, "ordered": True}).get_json()["flags"]
    assert set(flags) == _ALL_FLAGS
    assert flags["ordered"] is True
    assert flags["waiting"] is True                      # untouched — the other axis
    assert flags["instock"] is False and flags["unavailable"] is False
    assert c.get("/api/waiting").get_json()["waiting"] == {key: True}


def test_a_status_flag_only_touches_the_row_it_names(monkeypatch, tmp_path):
    """The clear is per KEY. A sibling line of the same product (or any other row) that
    happens to be „čaká sa" must be untouched — the flags are per ORDER LINE."""
    _flag_paths(monkeypatch, tmp_path)
    c = _client()
    c.post("/api/waiting", json={"key": "A|1", "waiting": True})
    c.post("/api/waiting", json={"key": "B|1", "waiting": True})
    c.post("/api/instock", json={"key": "A|1", "instock": True})
    assert c.get("/api/waiting").get_json()["waiting"] == {"B|1": True}


def test_ordered_bulk_leaves_every_status_flag_alone(monkeypatch, tmp_path):
    """Marking a whole supplier group „objednané" is axis A — it says nothing about
    whether those lines are waiting / in stock / unavailable."""
    _flag_paths(monkeypatch, tmp_path)
    c = _client()
    c.post("/api/waiting", json={"key": "A|1", "waiting": True})
    c.post("/api/instock", json={"key": "B|2", "instock": True})
    c.post("/api/ordered/bulk", json={"keys": ["A|1", "B|2"], "ordered": True})
    assert c.get("/api/waiting").get_json()["waiting"] == {"A|1": True}
    assert c.get("/api/instock").get_json()["instock"] == {"B|2": True}


def test_clearing_conflicts_never_writes_a_store_it_did_not_change(monkeypatch, tmp_path):
    """These stores are `protect=True` — the manager's irreplaceable work. A write that
    changes nothing must not rewrite the file at all (and a store that does not exist yet
    must not be created just because a conflicting flag was checked for)."""
    _flag_paths(monkeypatch, tmp_path)
    c = _client()
    c.post("/api/instock", json={"key": "A|1", "instock": True})
    assert not (tmp_path / "w.json").exists(), "an untouched store was written"
    assert not (tmp_path / "un.json").exists(), "an untouched store was written"
    c.post("/api/waiting", json={"key": "A|1", "waiting": True})
    before = (tmp_path / "un.json").exists()
    c.post("/api/waiting", json={"key": "A|1", "waiting": True})   # idempotent re-write
    assert (tmp_path / "un.json").exists() is before


_real_save_waiting = webapp._save_waiting


def test_a_failed_clear_never_erases_the_flag_the_manager_just_set(monkeypatch, tmp_path):
    """`_write_status_flag` touches up to TWO files and `os.replace` is only atomic per
    file, so the ORDER decides what a mid-way failure leaves behind. Saving the clicked
    store FIRST means a failing clear leaves a SUPERSET (both flags), which the next axis-B
    write heals by itself. The other order — clear first — would leave the row with NO
    status at all: the manager's „skladom" is gone AND the „čaká sa" it replaced is gone,
    which is exactly the irreplaceable-work loss `protect=True` exists to prevent."""
    _flag_paths(monkeypatch, tmp_path)
    c = _client()
    key = "90000001|TESTKOD-A"
    c.post("/api/waiting", json={"key": key, "waiting": True})

    def boom(_d):
        raise OSError("disk full")

    monkeypatch.setattr(webapp, "_save_waiting", boom)      # the CLEAR half fails
    assert c.post("/api/instock", json={"key": key, "instock": True}).status_code == 500

    monkeypatch.setattr(webapp, "_save_waiting", _real_save_waiting)
    assert c.get("/api/instock").get_json()["instock"] == {key: True}, \
        "the flag the manager just clicked was not persisted"
    assert key in c.get("/api/waiting").get_json()["waiting"], \
        "the flag it replaced was erased by a write that did not finish"


def test_orders_route_merges_instock_and_unavailable(monkeypatch, tmp_path):
    orders = ("code;statusName;itemName;itemAmount;itemCode;itemVariantName;itemSupplier\r\n"
              "99001045;Vybavuje sa;Polokošeľa;1;TESTKOD/L;Veľkosť: L;BETALOV\r\n")
    monkeypatch.setattr(webapp, "_orders_csv_cached", lambda: orders.encode("cp1250"))
    monkeypatch.setattr(webapp, "PRODUCTS",
        [{"key": "BETALOV|231", "supplier": "BETALOV", "name": "Polokošeľa",
          "variant_codes": ["TESTKOD/L"], "pairCode": "231"}])
    monkeypatch.setattr(webapp, "CODE2PAIR", {"TESTKOD/L": "231"})
    monkeypatch.setattr(webapp, "ORDERED", str(tmp_path / "o.json"))
    monkeypatch.setattr(webapp, "WAITING", str(tmp_path / "w.json"))
    monkeypatch.setattr(webapp, "INSTOCK", str(tmp_path / "is.json"))
    monkeypatch.setattr(webapp, "UNAVAIL", str(tmp_path / "un.json"))
    monkeypatch.setattr(webapp, "ORDER_PAIRINGS", str(tmp_path / "op.json"))
    monkeypatch.setattr(webapp, "SUPPLIER_ASSIGN", str(tmp_path / "sa.json"))
    monkeypatch.setattr(webapp, "_load_decisions", lambda: {})
    j = _client().get("/api/orders").get_json()
    assert j["orders"][0]["instock"] is False
    assert j["orders"][0]["unavailable"] is False
    (tmp_path / "is.json").write_text(
        json.dumps({"99001045|TESTKOD/L": True}), encoding="utf-8")
    j2 = _client().get("/api/orders").get_json()
    assert j2["orders"][0]["instock"] is True
    assert j2["orders"][0]["unavailable"] is False   # independent toggles


def test_order_pair_endpoint_persists(monkeypatch, tmp_path):
    monkeypatch.setattr(webapp, "ORDER_PAIRINGS", str(tmp_path / "op.json"))
    c = _client()
    r = c.post("/api/order-pair", json={"code": "60028/XL", "url": "https://www.huntingshop.eu/v"})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert webapp._load_order_pairings()["60028/XL"] == "https://www.huntingshop.eu/v"
    # clearing (empty url) removes the pairing
    c.post("/api/order-pair", json={"code": "60028/XL", "url": ""})
    assert "60028/XL" not in webapp._load_order_pairings()


def test_order_pair_rejects_non_http_url(monkeypatch, tmp_path):
    # server guard must match the client (^https?://) — block javascript:/data: AND
    # malformed 'httpfoo'/'http' that the lax startswith("http") used to let through.
    monkeypatch.setattr(webapp, "ORDER_PAIRINGS", str(tmp_path / "op.json"))
    for bad in ("javascript:alert(1)", "data:text/html,x", "httpfoo://x", "http", "ftp://x"):
        r = _client().post("/api/order-pair", json={"code": "X", "url": bad})
        assert r.status_code == 400, f"should reject {bad!r}"
    assert webapp._load_order_pairings() == {}


def test_order_pair_rejects_formula_code(monkeypatch, tmp_path):
    # a code beginning with a spreadsheet formula trigger is a CSV-injection attempt
    monkeypatch.setattr(webapp, "ORDER_PAIRINGS", str(tmp_path / "op.json"))
    for bad in ('=HYPERLINK("http://evil","x")', "+1", "-cmd", "@SUM"):
        r = _client().post("/api/order-pair", json={"code": bad, "url": "https://supplier/x"})
        assert r.status_code == 400, f"should reject code {bad!r}"
    assert webapp._load_order_pairings() == {}
    # a real code with an interior dash / space is still accepted
    assert _client().post("/api/order-pair",
                          json={"code": "61449 JELEN", "url": "https://supplier/x"}).status_code == 200


def test_order_pair_requires_code(monkeypatch, tmp_path):
    monkeypatch.setattr(webapp, "ORDER_PAIRINGS", str(tmp_path / "op.json"))
    r = _client().post("/api/order-pair", json={"code": "", "url": "https://x"})
    assert r.status_code == 400


# --- #101: per-order comment (+ shopRemark surfaced) -------------------------- #
def test_order_comment_endpoint_persists(monkeypatch, tmp_path):
    monkeypatch.setattr(webapp, "ORDER_COMMENTS", str(tmp_path / "oc.json"))
    c = _client()
    r = c.post("/api/order-comment",
               json={"orderCode": "99001045", "comment": "zavolať zákazníkovi"})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert c.get("/api/order-comment").get_json()["comments"]["99001045"] == "zavolať zákazníkovi"
    assert webapp._load_order_comments()["99001045"] == "zavolať zákazníkovi"
    # empty comment clears the entry
    c.post("/api/order-comment", json={"orderCode": "99001045", "comment": ""})
    assert "99001045" not in c.get("/api/order-comment").get_json()["comments"]


def test_order_comment_requires_ordercode(monkeypatch, tmp_path):
    monkeypatch.setattr(webapp, "ORDER_COMMENTS", str(tmp_path / "oc.json"))
    r = _client().post("/api/order-comment", json={"comment": "x"})
    assert r.status_code == 400
    assert webapp._load_order_comments() == {}


def test_order_comment_rejects_too_long(monkeypatch, tmp_path):
    monkeypatch.setattr(webapp, "ORDER_COMMENTS", str(tmp_path / "oc.json"))
    big = "x" * (webapp.ORDER_COMMENT_MAX + 1)
    r = _client().post("/api/order-comment", json={"orderCode": "99001045", "comment": big})
    assert r.status_code == 400
    assert webapp._load_order_comments() == {}
    # exactly at the cap is accepted
    ok = "y" * webapp.ORDER_COMMENT_MAX
    assert _client().post("/api/order-comment",
                          json={"orderCode": "99001045", "comment": ok}).status_code == 200


def test_order_comment_tolerate_corrupt_store(monkeypatch, tmp_path):
    f = tmp_path / "oc.json"
    f.write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(webapp, "ORDER_COMMENTS", str(f))
    assert webapp._load_order_comments() == {}
    f.write_text("[]", encoding="utf-8")   # wrong type
    assert webapp._load_order_comments() == {}


def test_build_to_order_rows_captures_shopremark():
    orders = (
        "code;statusName;shopRemark;itemName;itemAmount;itemCode;itemVariantName;itemSupplier\r\n"
        "99001045;Vybavuje sa;chýba nám 1 kus;Polokošeľa;1;TESTKOD/L;Veľkosť: L;BETALOV\r\n")
    rows = webapp.build_to_order_rows(orders, [], {}, {})
    assert rows[0]["shopRemark"] == "chýba nám 1 kus"


def test_orders_route_merges_comment_and_shopremark(monkeypatch, tmp_path):
    orders = ("code;statusName;shopRemark;itemName;itemAmount;itemCode;itemVariantName;itemSupplier\r\n"
              "99001045;Vybavuje sa;interná poznámka;Polokošeľa;1;TESTKOD/L;Veľkosť: L;BETALOV\r\n")
    monkeypatch.setattr(webapp, "_orders_csv_cached", lambda: orders.encode("cp1250"))
    monkeypatch.setattr(webapp, "PRODUCTS", [])
    monkeypatch.setattr(webapp, "CODE2PAIR", {})
    monkeypatch.setattr(webapp, "ORDERED", str(tmp_path / "o.json"))
    monkeypatch.setattr(webapp, "WAITING", str(tmp_path / "w.json"))
    monkeypatch.setattr(webapp, "INSTOCK", str(tmp_path / "is.json"))
    monkeypatch.setattr(webapp, "UNAVAIL", str(tmp_path / "un.json"))
    monkeypatch.setattr(webapp, "ORDER_PAIRINGS", str(tmp_path / "op.json"))
    monkeypatch.setattr(webapp, "SUPPLIER_ASSIGN", str(tmp_path / "sa.json"))
    monkeypatch.setattr(webapp, "ORDER_COMMENTS", str(tmp_path / "oc.json"))
    monkeypatch.setattr(webapp, "_load_decisions", lambda: {})
    j = _client().get("/api/orders").get_json()
    row = j["orders"][0]
    assert row["shopRemark"] == "interná poznámka"
    assert row["comment"] == ""                       # none set yet
    (tmp_path / "oc.json").write_text(
        json.dumps({"99001045": "objednané u dodávateľa"}), encoding="utf-8")
    row2 = _client().get("/api/orders").get_json()["orders"][0]
    assert row2["comment"] == "objednané u dodávateľa"   # per-ORDER comment merged in


# --- #174: split a product into per-size links -------------------------------- #
def test_variant_link_endpoint_persists(monkeypatch, tmp_path):
    monkeypatch.setattr(webapp, "VARIANT_LINKS", str(tmp_path / "vl.json"))
    c = _client()
    r = c.post("/api/variant-link", json={"code": "62059/S", "url": "https://trigona.sk/vel-s"})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert webapp._load_variant_links()["62059/S"] == "https://trigona.sk/vel-s"
    # a second size gets its OWN link (per variant code, independent)
    c.post("/api/variant-link", json={"code": "62059/M", "url": "https://trigona.sk/vel-m"})
    assert webapp._load_variant_links() == {
        "62059/S": "https://trigona.sk/vel-s", "62059/M": "https://trigona.sk/vel-m"}
    # clearing (empty url) removes ONLY that variant's link
    c.post("/api/variant-link", json={"code": "62059/S", "url": ""})
    assert "62059/S" not in webapp._load_variant_links()
    assert "62059/M" in webapp._load_variant_links()


def test_variant_link_rejects_non_http_url(monkeypatch, tmp_path):
    monkeypatch.setattr(webapp, "VARIANT_LINKS", str(tmp_path / "vl.json"))
    for bad in ("javascript:alert(1)", "data:text/html,x", "httpfoo://x", "http", "ftp://x"):
        r = _client().post("/api/variant-link", json={"code": "X", "url": bad})
        assert r.status_code == 400, f"should reject {bad!r}"
    assert webapp._load_variant_links() == {}


def test_variant_link_rejects_formula_code(monkeypatch, tmp_path):
    monkeypatch.setattr(webapp, "VARIANT_LINKS", str(tmp_path / "vl.json"))
    for bad in ('=HYPERLINK("http://evil","x")', "+1", "-cmd", "@SUM"):
        r = _client().post("/api/variant-link", json={"code": bad, "url": "https://s/x"})
        assert r.status_code == 400, f"should reject code {bad!r}"
    assert webapp._load_variant_links() == {}


def test_variant_link_requires_code(monkeypatch, tmp_path):
    monkeypatch.setattr(webapp, "VARIANT_LINKS", str(tmp_path / "vl.json"))
    assert _client().post("/api/variant-link", json={"code": "", "url": "https://x"}).status_code == 400


def test_variants_endpoint_returns_sizes_and_links(monkeypatch, tmp_path):
    monkeypatch.setattr(webapp, "PRODUCTS",
                        [{"key": "TRIGONA|156", "variant_codes": ["62059/S", "62059/M"]}])
    monkeypatch.setattr(webapp, "CODE2VARIANT", {"62059/S": "S", "62059/M": "M"})
    monkeypatch.setattr(webapp, "VARIANT_LINKS", str(tmp_path / "vl.json"))
    webapp._save_variant_links({"62059/S": "https://trigona.sk/vel-s"})
    r = _client().get("/api/variants?key=TRIGONA|156")
    assert r.status_code == 200
    j = r.get_json()
    assert j["variants"] == [
        {"code": "62059/S", "size": "S", "link": "https://trigona.sk/vel-s"},
        {"code": "62059/M", "size": "M", "link": ""},
    ]


def test_variants_endpoint_unknown_key_404(monkeypatch):
    monkeypatch.setattr(webapp, "PRODUCTS", [{"key": "A|1", "variant_codes": ["x"]}])
    assert _client().get("/api/variants?key=NOPE").status_code == 404


def test_products_route_includes_variant_links(monkeypatch, tmp_path):
    monkeypatch.setattr(webapp, "VARIANT_LINKS", str(tmp_path / "vl.json"))
    webapp._save_variant_links({"62059/S": "https://trigona.sk/vel-s"})
    j = _client().get("/api/products").get_json()
    assert j.get("variant_links") == {"62059/S": "https://trigona.sk/vel-s"}


def test_import_zip_writes_per_variant_split_link(monkeypatch, tmp_path):
    # end-to-end: a `split` decision + per-variant links → the import zip's
    # import_links.csv carries a DIFFERENT internalNote per variant code.
    import zipfile
    monkeypatch.setattr(webapp, "PRODUCTS",
                        [{"key": "TRIGONA|156", "supplier": "TRIGONA",
                          "variant_codes": ["62059/S", "62059/M"]}])
    monkeypatch.setattr(webapp, "CODE2PAIR", {"62059/S": "156", "62059/M": "156"})
    monkeypatch.setattr(webapp, "_load_decisions",
                        lambda: {"TRIGONA|156": {"status": "split", "url": ""}})
    monkeypatch.setattr(webapp, "VARIANT_LINKS", str(tmp_path / "vl.json"))
    monkeypatch.setattr(webapp, "ORDER_PAIRINGS", str(tmp_path / "op.json"))
    monkeypatch.setattr(webapp, "SUPPLIER_ASSIGN", str(tmp_path / "sa.json"))
    monkeypatch.setattr(webapp, "GRUBE_CODES", str(tmp_path / "gc.json"))
    webapp._save_variant_links({"62059/S": "https://trigona.sk/vel-s",
                                "62059/M": "https://trigona.sk/vel-m"})
    data = _client().get("/api/import").data
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        links = z.read("import_links.csv").decode("utf-8-sig")
    assert "62059/S;156;https://trigona.sk/vel-s" in links
    assert "62059/M;156;https://trigona.sk/vel-m" in links


# --- Poznámky tab (#83) --------------------------------------------------------- #
def test_notes_add_persists_and_newest_first(monkeypatch, tmp_path):
    monkeypatch.setattr(webapp, "NOTES", str(tmp_path / "notes.json"))
    c = _client()
    r1 = c.post("/api/notes", json={"text": "prvá poznámka"})
    assert r1.status_code == 200
    n1 = r1.get_json()["note"]
    assert n1["text"] == "prvá poznámka" and n1["done"] is False and n1["id"]
    r2 = c.post("/api/notes", json={"text": "druhá poznámka"})
    n2 = r2.get_json()["note"]
    notes = c.get("/api/notes").get_json()["notes"]
    assert [n["id"] for n in notes] == [n2["id"], n1["id"]]   # newest-first


def test_notes_rejects_empty_and_too_long(monkeypatch, tmp_path):
    monkeypatch.setattr(webapp, "NOTES", str(tmp_path / "notes.json"))
    c = _client()
    assert c.post("/api/notes", json={"text": ""}).status_code == 400
    assert c.post("/api/notes", json={"text": "   "}).status_code == 400
    assert c.post("/api/notes", json={"text": "x" * 5001}).status_code == 400
    assert c.get("/api/notes").get_json()["notes"] == []


def test_notes_done_toggle_and_delete(monkeypatch, tmp_path):
    monkeypatch.setattr(webapp, "NOTES", str(tmp_path / "notes.json"))
    c = _client()
    nid = c.post("/api/notes", json={"text": "objednať sprej"}).get_json()["note"]["id"]
    r = c.post("/api/note", json={"id": nid, "done": True})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert c.get("/api/notes").get_json()["notes"][0]["done"] is True
    c.post("/api/note", json={"id": nid, "done": False})
    assert c.get("/api/notes").get_json()["notes"][0]["done"] is False
    r = c.post("/api/note", json={"id": nid, "delete": True})
    assert r.status_code == 200
    assert c.get("/api/notes").get_json()["notes"] == []


def test_note_unknown_id_404(monkeypatch, tmp_path):
    monkeypatch.setattr(webapp, "NOTES", str(tmp_path / "notes.json"))
    r = _client().post("/api/note", json={"id": "doesnotexist", "done": True})
    assert r.status_code == 404


def test_import_zip_formula_escapes_codes(monkeypatch, tmp_path):
    # defense-in-depth: even a formula-leading code already sitting in the store
    # (bypassing the endpoint guard) is neutralized with a leading ' in the export.
    monkeypatch.setattr(webapp, "PRODUCTS", [])
    monkeypatch.setattr(webapp, "CODE2PAIR", {})
    monkeypatch.setattr(webapp, "_load_decisions", lambda: {})
    monkeypatch.setattr(webapp, "ORDER_PAIRINGS", str(tmp_path / "op.json"))
    webapp._save_order_pairings({'=HYPERLINK("http://evil","x")': "https://supplier/x"})
    r = _client().get("/api/import")
    import zipfile
    raw = zipfile.ZipFile(io.BytesIO(r.data)).read("import_links.csv").decode("utf-8-sig")
    rows = list(_csv.reader(io.StringIO(raw), delimiter=";"))
    # CSV-parsed back: the code cell is neutralized with a leading ' (text, not formula)
    assert rows[1][0].startswith("'=HYPERLINK")


def test_orders_exposes_inline_pair_url(monkeypatch, tmp_path):
    # an ordered item OUTSIDE the review dataset (no product, no decision) — the
    # inline pairing must still attach to it and surface as pairUrl.
    orders = ("code;statusName;itemName;itemAmount;itemCode;itemVariantName;itemSupplier\r\n"
              "99001050;Vybavuje sa;Vesta;1;99999/X;Veľkosť: X;ORBIS\r\n")
    monkeypatch.setattr(webapp, "_orders_csv_cached", lambda: orders.encode("cp1250"))
    monkeypatch.setattr(webapp, "PRODUCTS", [])
    monkeypatch.setattr(webapp, "CODE2PAIR", {})
    monkeypatch.setattr(webapp, "ORDERED", str(tmp_path / "o.json"))
    monkeypatch.setattr(webapp, "ORDER_PAIRINGS", str(tmp_path / "op.json"))
    monkeypatch.setattr(webapp, "SUPPLIER_ASSIGN", str(tmp_path / "sa.json"))
    monkeypatch.setattr(webapp, "_load_decisions", lambda: {})
    j = _client().get("/api/orders").get_json()
    assert j["orders"][0]["supplierUrl"] == "" and j["orders"][0]["pairUrl"] == ""
    _client().post("/api/order-pair", json={"code": "99999/X", "url": "https://supplier/z"})
    j2 = _client().get("/api/orders").get_json()
    assert j2["orders"][0]["pairUrl"] == "https://supplier/z"
    assert j2["orders"][0]["supplierUrl"] == ""   # decision link stays separate from inline


def test_import_zip_includes_inline_pairings(monkeypatch, tmp_path):
    monkeypatch.setattr(webapp, "PRODUCTS", [])
    monkeypatch.setattr(webapp, "CODE2PAIR", {"60028/XL": "555"})
    monkeypatch.setattr(webapp, "_load_decisions", lambda: {})
    monkeypatch.setattr(webapp, "ORDER_PAIRINGS", str(tmp_path / "op.json"))
    webapp._save_order_pairings({"60028/XL": "https://supplier/inline"})
    r = _client().get("/api/import")
    assert r.status_code == 200
    import zipfile
    z = zipfile.ZipFile(io.BytesIO(r.data))
    links = z.read("import_links.csv").decode("utf-8-sig")
    assert "60028/XL;555;https://supplier/inline" in links


# --- supplier assignment (order line without a supplier) -------------------- #
def test_order_supplier_endpoint_persists(monkeypatch, tmp_path):
    monkeypatch.setattr(webapp, "SUPPLIER_ASSIGN", str(tmp_path / "sa.json"))
    c = _client()
    r = c.post("/api/order-supplier", json={"code": "88/Z", "supplier": "BETALOV"})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert webapp._load_supplier_assign()["88/Z"] == "BETALOV"
    # empty supplier clears the assignment
    c.post("/api/order-supplier", json={"code": "88/Z", "supplier": ""})
    assert "88/Z" not in webapp._load_supplier_assign()


def test_order_supplier_requires_code(monkeypatch, tmp_path):
    monkeypatch.setattr(webapp, "SUPPLIER_ASSIGN", str(tmp_path / "sa.json"))
    r = _client().post("/api/order-supplier", json={"code": "", "supplier": "BETALOV"})
    assert r.status_code == 400


def test_order_supplier_rejects_formula_code_and_supplier(monkeypatch, tmp_path):
    monkeypatch.setattr(webapp, "SUPPLIER_ASSIGN", str(tmp_path / "sa.json"))
    c = _client()
    # formula-leading code rejected
    assert c.post("/api/order-supplier",
                  json={"code": "=cmd", "supplier": "BETALOV"}).status_code == 400
    # formula-leading supplier name rejected (CSV-injection into the supplier column)
    assert c.post("/api/order-supplier",
                  json={"code": "88/Z", "supplier": "=HYPERLINK(1)"}).status_code == 400
    assert webapp._load_supplier_assign() == {}
    # a real supplier name with a leading alnum is accepted
    assert c.post("/api/order-supplier",
                  json={"code": "88/Z", "supplier": "JŠ SERVIS"}).status_code == 200


def test_order_supplier_collapses_inner_whitespace(monkeypatch, tmp_path):
    """#203 — 'Citrade  s.r.o.' and 'Citrade s.r.o.' are the same supplier; a stray
    double space would fragment the grouping AND write two spellings into the eshop
    `supplier` column. Whitespace is normalised on write.

    Case is deliberately NOT folded here: this value goes VERBATIM into
    import_suppliers.csv → the Shoptet `supplier` field, so lower-casing it would
    rewrite the supplier's real name in the eshop. Case-insensitivity is a DISPLAY
    concern and lives in the tab's grouping (supKey in app.js)."""
    monkeypatch.setattr(webapp, "SUPPLIER_ASSIGN", str(tmp_path / "sa.json"))
    c = _client()
    assert c.post("/api/order-supplier",
                  json={"code": "88/Z", "supplier": " Citrade   s.r.o. "}).status_code == 200
    assert webapp._load_supplier_assign()["88/Z"] == "Citrade s.r.o."
    # a tab/newline inside the name collapses too (it would break the CSV cell)
    c.post("/api/order-supplier", json={"code": "77/X", "supplier": "JŠ\tSERVIS"})
    assert webapp._load_supplier_assign()["77/X"] == "JŠ SERVIS"
    # capitalisation is preserved exactly as the manager typed it
    c.post("/api/order-supplier", json={"code": "66/L", "supplier": "CITRADE"})
    assert webapp._load_supplier_assign()["66/L"] == "CITRADE"


def test_orders_exposes_assigned_supplier(monkeypatch, tmp_path):
    orders = ("code;statusName;itemName;itemAmount;itemCode;itemVariantName;itemSupplier\r\n"
              "99001060;Vybavuje sa;Bez dod;1;88/Z;Veľkosť: Z;\r\n")   # NO itemSupplier
    monkeypatch.setattr(webapp, "_orders_csv_cached", lambda: orders.encode("cp1250"))
    monkeypatch.setattr(webapp, "PRODUCTS", [])
    monkeypatch.setattr(webapp, "CODE2PAIR", {})
    monkeypatch.setattr(webapp, "ORDERED", str(tmp_path / "o.json"))
    monkeypatch.setattr(webapp, "ORDER_PAIRINGS", str(tmp_path / "op.json"))
    monkeypatch.setattr(webapp, "SUPPLIER_ASSIGN", str(tmp_path / "sa.json"))
    monkeypatch.setattr(webapp, "_load_decisions", lambda: {})
    j = _client().get("/api/orders").get_json()
    assert j["orders"][0]["supplier"] == "" and j["orders"][0]["assignedSupplier"] == ""
    _client().post("/api/order-supplier", json={"code": "88/Z", "supplier": "ORBIS"})
    j2 = _client().get("/api/orders").get_json()
    assert j2["orders"][0]["assignedSupplier"] == "ORBIS"
    assert j2["orders"][0]["supplier"] == ""   # original order supplier stays empty/separate


def test_import_zip_includes_supplier_file(monkeypatch, tmp_path):
    monkeypatch.setattr(webapp, "PRODUCTS", [])
    monkeypatch.setattr(webapp, "CODE2PAIR", {"88/Z": "777"})
    monkeypatch.setattr(webapp, "_load_decisions", lambda: {})
    monkeypatch.setattr(webapp, "ORDER_PAIRINGS", str(tmp_path / "op.json"))
    monkeypatch.setattr(webapp, "SUPPLIER_ASSIGN", str(tmp_path / "sa.json"))
    webapp._save_supplier_assign({"88/Z": "BETALOV"})
    r = _client().get("/api/import")
    assert r.status_code == 200
    import zipfile
    sup = zipfile.ZipFile(io.BytesIO(r.data)).read("import_suppliers.csv").decode("utf-8-sig")
    assert sup.splitlines()[0] == "code;pairCode;supplier"
    assert "88/Z;777;BETALOV" in sup


# --- GRUBE per-size code on Na objednanie ----------------------------------- #
def test_orders_attach_grube_code(monkeypatch, tmp_path):
    # _attach_grube joins the durable grube_codes store onto an order row by its
    # forestshop variant code (itemCode) → grubeItemId (copyable code) + grubeDeUrl.
    monkeypatch.setattr(webapp, "GRUBE_CODES", str(tmp_path / "gc.json"))
    (tmp_path / "gc.json").write_text(
        '{"60645/L": {"itemId": "1547734519", "size": "L",'
        ' "deUrl": "https://www.grube.de/p/x/154773/", "productId": "154773"}}',
        encoding="utf-8")
    r = webapp._attach_grube({"itemCode": "60645/L"})
    assert r["grubeItemId"] == "1547734519"
    assert r["grubeDeUrl"] == "https://www.grube.de/p/x/154773/"
    # a code with no grube entry → empty fields (non-grube / link-only line)
    r2 = webapp._attach_grube({"itemCode": "99999/X"})
    assert r2["grubeItemId"] == "" and r2["grubeDeUrl"] == ""


def test_orders_attach_grube_rejects_non_https_deurl(monkeypatch, tmp_path):
    # the deUrl reaches an <a href> on the client → only https:// passes the server
    # guard; javascript:/data:/http:// are dropped (never reach the DOM).
    monkeypatch.setattr(webapp, "GRUBE_CODES", str(tmp_path / "gc.json"))
    (tmp_path / "gc.json").write_text(
        '{"X/1": {"itemId": "123", "deUrl": "javascript:alert(1)"},'
        ' "X/2": {"itemId": "456", "deUrl": "http://insecure/x"}}', encoding="utf-8")
    r1 = webapp._attach_grube({"itemCode": "X/1"})
    assert r1["grubeItemId"] == "123" and r1["grubeDeUrl"] == ""   # code kept, url dropped
    r2 = webapp._attach_grube({"itemCode": "X/2"})
    assert r2["grubeDeUrl"] == ""                                  # plain http rejected too


def test_orders_route_attaches_grube_fields(monkeypatch, tmp_path):
    # full /api/orders wiring: a GRUBE order line carries grubeItemId + grubeDeUrl.
    orders = ("code;statusName;itemName;itemAmount;itemCode;itemVariantName;itemSupplier\r\n"
              "99001045;Vybavuje sa;Bunda Grand Nord;1;60645/L;Veľkosť: L;GRUBE\r\n")
    monkeypatch.setattr(webapp, "_orders_csv_cached", lambda: orders.encode("cp1250"))
    monkeypatch.setattr(webapp, "PRODUCTS", [])
    monkeypatch.setattr(webapp, "CODE2PAIR", {})
    monkeypatch.setattr(webapp, "ORDERED", str(tmp_path / "o.json"))
    monkeypatch.setattr(webapp, "WAITING", str(tmp_path / "w.json"))
    monkeypatch.setattr(webapp, "ORDER_PAIRINGS", str(tmp_path / "op.json"))
    monkeypatch.setattr(webapp, "SUPPLIER_ASSIGN", str(tmp_path / "sa.json"))
    monkeypatch.setattr(webapp, "GRUBE_CODES", str(tmp_path / "gc.json"))
    (tmp_path / "gc.json").write_text(
        '{"60645/L": {"itemId": "1547734519",'
        ' "deUrl": "https://www.grube.de/p/x/154773/"}}', encoding="utf-8")
    monkeypatch.setattr(webapp, "_load_decisions", lambda: {})
    j = _client().get("/api/orders").get_json()
    assert j["orders"][0]["grubeItemId"] == "1547734519"
    assert j["orders"][0]["grubeDeUrl"] == "https://www.grube.de/p/x/154773/"


def test_supplier_meta_parses_price_and_availability():
    html = '<meta property="product:price:amount" content="12.50"> Skladom dnes'
    price, avail = webapp._supplier_meta(html)
    assert price == "12,50"
    assert avail == "Skladom"


# --- n8n Shoptet import endpoint -------------------------------------------- #
import csv as _csv  # noqa: E402

_FEED = ("code;pairCode;name;purchasePrice;productVisibility;availabilityInStock;"
         "availabilityOutOfStock;stock\r\n"
         "15233/M;1564;Vesta;999;visible;Skladom;Skladom;5\r\n").encode("utf-8")


def _arm_token(monkeypatch, tmp_path, token="secret-tok"):
    cred = tmp_path / ".shoptet_admin"
    cred.write_text(f"N8N_IMPORT_TOKEN={token}\n", encoding="utf-8")
    monkeypatch.setattr(webapp, "CRED_PATH", str(cred))
    monkeypatch.setattr(webapp, "OUT", str(tmp_path))
    return token


def test_import_rejects_without_token(monkeypatch, tmp_path):
    _arm_token(monkeypatch, tmp_path)
    r = _client().post("/api/n8n/shoptet-import", data=_FEED)
    assert r.status_code == 401


def test_import_rejects_wrong_token(monkeypatch, tmp_path):
    _arm_token(monkeypatch, tmp_path)
    r = _client().post("/api/n8n/shoptet-import", data=_FEED,
                       headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_import_sanitizes_then_runs(monkeypatch, tmp_path):
    tok = _arm_token(monkeypatch, tmp_path)
    seen = {}

    def fake_run(csv_path, dry_run=False, timeout=300):
        seen["path"] = csv_path
        seen["dry_run"] = dry_run
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            seen["fields"] = _csv.DictReader(f, delimiter=";").fieldnames
        return 0, "VÝSLEDOK: spracované=1 upravené=1 zlyhania=0", ""

    monkeypatch.setattr(webapp, "run_import", fake_run)
    r = _client().post("/api/n8n/shoptet-import", data=_FEED,
                       headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    j = r.get_json()
    assert j["ok"] and j["rows"] == 1 and j["processed"] == 1
    # the file handed to the importer carries ONLY the safe restock columns
    assert seen["fields"] == webapp.import_builder.RESTOCK_COLS
    assert "purchasePrice" not in seen["fields"] and "name" not in seen["fields"]


def test_import_zero_rows_skips_runner(monkeypatch, tmp_path):
    tok = _arm_token(monkeypatch, tmp_path)
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not run")))
    empty = ("code;pairCode;productVisibility;availabilityInStock;"
             "availabilityOutOfStock;stock\r\n").encode("utf-8")
    r = _client().post("/api/n8n/shoptet-import", data=empty,
                       headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200 and r.get_json()["rows"] == 0


def test_import_busy_returns_409(monkeypatch, tmp_path):
    tok = _arm_token(monkeypatch, tmp_path)
    assert webapp._import_lock.acquire(blocking=False)
    try:
        r = _client().post("/api/n8n/shoptet-import", data=_FEED,
                           headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 409
    finally:
        webapp._import_lock.release()


def _arm_pairings(monkeypatch, tmp_path, decisions, token="secret-tok", order_pairings=None):
    cred = tmp_path / ".shoptet_admin"
    cred.write_text(f"N8N_IMPORT_TOKEN={token}\n", encoding="utf-8")
    monkeypatch.setattr(webapp, "CRED_PATH", str(cred))
    monkeypatch.setattr(webapp, "OUT", str(tmp_path))
    monkeypatch.setattr(webapp, "PAIRINGS_STATE", str(tmp_path / "uploaded.json"))
    # #299 Task 10: _do_upload_pairings now QUEUES into the shared pending_shoptet
    # table instead of importing directly — isolate it like every other store here,
    # never the live one.
    monkeypatch.setattr(webapp, "PENDING_SHOPTET", str(tmp_path / "pending_shoptet.json"))
    # #38: isolate the manager's live order_pairings.json — never read the real one
    # (this box also runs the deployed app; an unmocked path would leak real data
    # into the test and make the "0 new pairings" tests flaky/failing).
    monkeypatch.setattr(webapp, "ORDER_PAIRINGS", str(tmp_path / "order_pairings.json"))
    monkeypatch.setattr(webapp, "PRODUCTS",
                        [{"key": "k1", "name": "Vesta XY", "our_url": "https://forestshop/x",
                          "variant_codes": ["A/1", "A/2"]}])
    monkeypatch.setattr(webapp, "CODE2PAIR", {"A/1": "100", "A/2": "100"})
    monkeypatch.setattr(webapp, "_load_decisions", lambda: decisions)
    if order_pairings is not None:
        webapp._save_order_pairings(order_pairings)
    return token


def test_pairings_rejects_without_token(monkeypatch, tmp_path):
    _arm_pairings(monkeypatch, tmp_path, {})
    assert _client().post("/api/n8n/upload-pairings").status_code == 401


def test_pairings_zero_new_returns_count_0(monkeypatch, tmp_path):
    tok = _arm_pairings(monkeypatch, tmp_path, {})
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
    r = _client().post("/api/n8n/upload-pairings", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200 and r.get_json()["count"] == 0


def test_pairings_queues_link_fields_for_the_hourly_drain(monkeypatch, tmp_path):
    """#299 Task 10 — `_do_upload_pairings` no longer imports directly: it QUEUES
    into the shared pending_shoptet table for the next hourly "Sync do Shoptetu"
    drain (`test_webreview_shoptet_upload.py` covers the drain + credit path).
    `count` (keys credited immediately) stays 0 here — nothing is confirmed by an
    eshop export in this fixture, so nothing is credited yet; `queued` reports
    the field actually queued. Idempotency now belongs to the QUEUE+DRAIN, not
    this producer: without a credited uploaded_pairings.json entry (only the
    drain writes one, once Shoptet confirms), the SAME key is a "new" candidate
    on every call — harmless, since re-queueing just overwrites the same pending
    field with the same value."""
    dec = {"k1": {"status": "good", "url": "https://supplier/x"}}
    tok = _arm_pairings(monkeypatch, tmp_path, dec)
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: pytest.fail("must not import — must queue"))
    r = _client().post("/api/n8n/upload-pairings", headers={"Authorization": f"Bearer {tok}"})
    j = r.get_json()
    # _arm_pairings' fixture product carries TWO variant codes (A/1, A/2) → 2 fields
    assert r.status_code == 200 and j["ok"] and j["queued"] == 2 and j["count"] == 0
    assert j["products"][0]["supplier_url"] == "https://supplier/x"
    pending = json.loads((tmp_path / "pending_shoptet.json").read_text(encoding="utf-8"))
    assert pending["A/1"]["fields"]["internalNote"]["value"] == "https://supplier/x"
    assert pending["A/1"]["fields"]["internalNote"]["source"] == "parovania_eshop"

    # re-queued again on a second call — never marked uploaded (nothing confirmed it
    # yet), so it stays a "new" candidate; the field value is unchanged, not doubled
    r2 = _client().post("/api/n8n/upload-pairings", headers={"Authorization": f"Bearer {tok}"})
    assert r2.get_json()["queued"] == 2
    pending2 = json.loads((tmp_path / "pending_shoptet.json").read_text(encoding="utf-8"))
    assert pending2["A/1"]["fields"]["internalNote"]["value"] == "https://supplier/x"


def test_pairings_response_carries_summary_counts(monkeypatch, tmp_path):
    # The n8n notifier needs totals to post ONE summary Discord message instead of
    # one-per-product: queued this run, total uploaded, remaining, total products,
    # review link. #299 Task 10 — `total_uploaded`/`remaining` now only move once
    # the hourly drain (or an immediate export-confirmed credit) actually records
    # the key uploaded, not the moment it is merely queued.
    dec = {"k1": {"status": "good", "url": "https://supplier/x"}}
    tok = _arm_pairings(monkeypatch, tmp_path, dec)
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: pytest.fail("must not import — must queue"))
    j = _client().post("/api/n8n/upload-pairings",
                       headers={"Authorization": f"Bearer {tok}"}).get_json()
    assert j["queued"] == 2                       # newly queued this run (2 variant codes)
    assert j["count"] == 0                        # not credited yet — nothing confirmed it
    assert j["total_products"] == 1               # PRODUCTS in the review set
    assert j["total_uploaded"] == 0                # still waiting on the drain
    assert j["remaining"] == 1
    assert j["review_url"].startswith("https://")

    # once the drain confirms it (simulated directly — the drain itself is
    # test_webreview_shoptet_upload.py's job), the totals move
    webapp._credit_producer("parovania_eshop", {"k1": "https://supplier/x"})
    j2 = _client().post("/api/n8n/upload-pairings",
                        headers={"Authorization": f"Bearer {tok}"}).get_json()
    assert j2["queued"] == 0 and j2["count"] == 0   # already credited → no longer "new"
    assert j2["total_uploaded"] == 1 and j2["remaining"] == 0


def test_pairings_zero_new_still_reports_totals(monkeypatch, tmp_path):
    # no new pairings → no Discord per-product spam, but the summary still carries totals
    tok = _arm_pairings(monkeypatch, tmp_path, {})
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
    j = _client().post("/api/n8n/upload-pairings",
                       headers={"Authorization": f"Bearer {tok}"}).get_json()
    assert j["count"] == 0
    assert j["total_products"] == 1 and j["total_uploaded"] == 0 and j["remaining"] == 1


def test_pairings_summary_excludes_stale_uploaded_keys(monkeypatch, tmp_path):
    # a key uploaded for a product that has since left the review set must NOT count —
    # otherwise the ratio can read "Spolu 2 / 1" and remaining looks wrong.
    tok = _arm_pairings(monkeypatch, tmp_path, {})              # no new decisions
    (tmp_path / "uploaded.json").write_text('{"GONE|1": "https://x"}', encoding="utf-8")
    j = _client().post("/api/n8n/upload-pairings",
                       headers={"Authorization": f"Bearer {tok}"}).get_json()
    assert j["count"] == 0
    assert j["total_products"] == 1 and j["total_uploaded"] == 0 and j["remaining"] == 1


def test_pairings_loader_coerces_non_dict_state(monkeypatch, tmp_path):
    # a stray JSON array repeating a valid key would, unfiltered, make total_uploaded
    # exceed total_products (the invariant this PR guards). The loader must coerce to {}.
    tok = _arm_pairings(monkeypatch, tmp_path, {})              # no new decisions
    (tmp_path / "uploaded.json").write_text('["k1", "k1"]', encoding="utf-8")
    j = _client().post("/api/n8n/upload-pairings",
                       headers={"Authorization": f"Bearer {tok}"}).get_json()
    assert j["total_products"] == 1 and j["total_uploaded"] == 0 and j["remaining"] == 1


def test_pairings_blocked_when_codes_missing(monkeypatch, tmp_path):
    # a paired product with no variant codes yields 0 import rows — the response flags
    # `blocked` so the notifier warns instead of staying silent.
    cred = tmp_path / ".shoptet_admin"
    cred.write_text("N8N_IMPORT_TOKEN=secret-tok\n", encoding="utf-8")
    monkeypatch.setattr(webapp, "CRED_PATH", str(cred))
    monkeypatch.setattr(webapp, "OUT", str(tmp_path))
    monkeypatch.setattr(webapp, "PAIRINGS_STATE", str(tmp_path / "uploaded.json"))
    monkeypatch.setattr(webapp, "ORDER_PAIRINGS", str(tmp_path / "order_pairings.json"))
    monkeypatch.setattr(webapp, "PRODUCTS",
                        [{"key": "k1", "name": "X", "our_url": "u", "variant_codes": []}])
    monkeypatch.setattr(webapp, "CODE2PAIR", {})               # no codes → 0 import rows
    monkeypatch.setattr(webapp, "_load_decisions",
                        lambda: {"k1": {"status": "good", "url": "https://supplier/x"}})
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
    j = _client().post("/api/n8n/upload-pairings",
                       headers={"Authorization": "Bearer secret-tok"}).get_json()
    assert j["count"] == 0 and j["blocked"] == 1 and j["total_products"] == 1


def test_pairings_partial_batch_only_queues_the_coded_key(monkeypatch, tmp_path):
    # #49: a batch with ONE coded (uploadable) key and ONE code-less (blocked) key
    # must QUEUE only the coded key — the code-less key must stay "new" so a later
    # run retries it, instead of being silently lost forever. #299 Task 10: nothing
    # is credited here at all (no catalog export → nothing confirmed), so k1 is
    # QUEUED, not marked uploaded — the drain credits it later.
    cred = tmp_path / ".shoptet_admin"
    cred.write_text("N8N_IMPORT_TOKEN=secret-tok\n", encoding="utf-8")
    monkeypatch.setattr(webapp, "CRED_PATH", str(cred))
    monkeypatch.setattr(webapp, "OUT", str(tmp_path))
    monkeypatch.setattr(webapp, "PAIRINGS_STATE", str(tmp_path / "uploaded.json"))
    monkeypatch.setattr(webapp, "ORDER_PAIRINGS", str(tmp_path / "order_pairings.json"))
    monkeypatch.setattr(webapp, "PENDING_SHOPTET", str(tmp_path / "pending_shoptet.json"))
    monkeypatch.setattr(webapp, "PRODUCTS", [
        {"key": "k1", "name": "X1", "our_url": "u1", "variant_codes": ["A/1"]},
        {"key": "k2", "name": "X2", "our_url": "u2", "variant_codes": []},
    ])
    monkeypatch.setattr(webapp, "CODE2PAIR", {"A/1": "100"})
    monkeypatch.setattr(webapp, "_load_decisions", lambda: {
        "k1": {"status": "good", "url": "https://supplier/x1"},
        "k2": {"status": "good", "url": "https://supplier/x2"},
    })
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: pytest.fail("must not import — must queue"))
    j = _client().post("/api/n8n/upload-pairings",
                       headers={"Authorization": "Bearer secret-tok"}).get_json()
    assert j["ok"] is True
    assert j["queued"] == 1                # only k1 genuinely got a row queued
    assert j["count"] == 0                 # not credited yet — nothing confirmed it
    assert j["blocked"] == 1               # k2 surfaced as blocked, not silently dropped
    assert not (tmp_path / "uploaded.json").exists()   # k2 must NOT be recorded, k1 not YET
    pending = json.loads((tmp_path / "pending_shoptet.json").read_text())
    assert pending["A/1"]["fields"]["internalNote"]["value"] == "https://supplier/x1"

    # k2 stays blocked on the next run too; k1 (never confirmed) is re-queued
    j2 = _client().post("/api/n8n/upload-pairings",
                        headers={"Authorization": "Bearer secret-tok"}).get_json()
    assert j2["queued"] == 1 and j2["blocked"] == 1


def test_pairings_rejects_wrong_token(monkeypatch, tmp_path):
    _arm_pairings(monkeypatch, tmp_path, {})
    r = _client().post("/api/n8n/upload-pairings", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


    # #299 Task 10 — `test_pairings_failed_import_does_not_mark_uploaded` deleted.
    # `_do_upload_pairings` no longer imports directly (Shoptet import failures can
    # only happen inside the hourly drain's OWN `_import_rows_chunked` call now),
    # so "a FAILED import must not mark uploaded" is no longer this function's
    # protection to carry. It lives on generically for EVERY producer in
    # `test_webreview_shoptet_upload.py::test_a_pairing_key_whose_second_code_failed_is_NOT_credited`
    # (a pairing-specific partial-failure scenario, added by this task) and in the
    # drain's own chunk/lock tests (`test_the_import_is_skipped_when_another_import_is_already_running`
    # et al., from Tasks 6/7).


def _hard_error_stdout(err):
    """scripts/shoptet_import.py's REAL stdout for a hard Shoptet abort: its own
    result marker (the app parses only the slice from there on — the raw stdout opens
    with the PREVIOUS Log entry's counts, #196/#257) and then the Shoptet error line."""
    return ("\nVÝSLEDOK: spracované=None upravené=None zlyhania=None\n"
            f"CHYBA LOGU: {err}\n")


# #299 Task 10 — `test_pairings_hard_error_surfaces_error_detail_and_does_not_mark_uploaded`
# deleted. `_do_upload_pairings` no longer runs an import at all, so it can no
# longer surface a hard Shoptet import error (`error_detail`/`processed` from a
# parsed Shoptet log) — that surface now belongs entirely to the hourly drain
# (`run_shoptet_upload`, whose `_import_rows_chunked` call is exercised by
# `test_webreview_shoptet_upload.py`'s `cycle` fixture) and to
# `_chunk_error_msg`'s own unit coverage in `test_import_builder.py` /
# `test_webreview.py`'s generic n8n-import endpoint tests, neither of which this
# task's migration touches.


def test_pairings_whitespace_url_does_not_re_upload_forever(monkeypatch, tmp_path):
    # A decision URL with surrounding whitespace must be normalized so it is
    # QUEUED with the stripped value and, once credited (the drain's job — the
    # #257 lesson), never re-selected again. #299 Task 10: the credit itself is
    # simulated directly (`_credit_producer`, exactly what the drain calls once
    # Shoptet confirms) — the drain's OWN confirm→credit path is
    # `test_webreview_shoptet_upload.py`'s job.
    dec = {"k1": {"status": "good", "url": "https://supplier/w  "}}
    tok = _arm_pairings(monkeypatch, tmp_path, dec)
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: pytest.fail("must not import — must queue"))
    j = _client().post("/api/n8n/upload-pairings",
                       headers={"Authorization": f"Bearer {tok}"}).get_json()
    assert j["queued"] == 2
    pending = json.loads((tmp_path / "pending_shoptet.json").read_text())
    # the QUEUED value is normalized (stripped) — never the raw whitespace-padded one
    assert pending["A/1"]["fields"]["internalNote"]["value"] == "https://supplier/w"
    webapp._credit_producer("parovania_eshop", {"k1": "https://supplier/w"})
    # second run: must queue nothing (marked despite the trailing spaces)
    j2 = _client().post("/api/n8n/upload-pairings",
                        headers={"Authorization": f"Bearer {tok}"}).get_json()
    assert j2["queued"] == 0


def test_pairings_dry_run_does_not_mark_uploaded(monkeypatch, tmp_path):
    # #299 Task 10 — dry_run no longer reaches a real Shoptet dry-run import (there
    # is no import left to dry-run); the honest equivalent is `would_queue`, a
    # preview of the field count, while genuinely queueing nothing.
    dec = {"k1": {"status": "manual", "url": "https://supplier/y"}}
    tok = _arm_pairings(monkeypatch, tmp_path, dec)
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: pytest.fail("a dry run must never import"))
    r = _client().post("/api/n8n/upload-pairings?dry_run=1", headers={"Authorization": f"Bearer {tok}"})
    j = r.get_json()
    assert j["dry_run"] is True
    assert j["queued"] == 0 and j["would_queue"] == 2
    assert not (tmp_path / "pending_shoptet.json").exists()
    # dry-run must NOT persist → still 2 fields to queue on the next (real) call
    r2 = _client().post("/api/n8n/upload-pairings", headers={"Authorization": f"Bearer {tok}"})
    assert r2.get_json()["queued"] == 2


# --- #38: nightly push ALSO covers order_pairings.json (inline 'Na objednanie' --- #
# --- pairings, outside the review set) — same import run, own uploaded state.  --- #
def test_order_pairings_queued_under_order_namespace(monkeypatch, tmp_path):
    # #299 Task 10 — an inline order pairing now QUEUES exactly like a reviewed
    # decision, credit_group `order:<code>`, and is credited by the drain, never
    # by this producer.
    #
    # #299 Task 11 finding 3 — `order_count` used to be codes confirmed RIGHT
    # AWAY from the export match (`len(uploaded_order_codes)`), so it read 0
    # here even though "B/1" genuinely queued this run — the "📦 Inline páry"
    # card counter was always zero. It now reports what THIS run actually
    # queued for the inline-order bucket.
    tok = _arm_pairings(monkeypatch, tmp_path, {},
                        order_pairings={"B/1": "https://supplier/inline"})
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: pytest.fail("must not import — must queue"))
    r = _client().post("/api/n8n/upload-pairings", headers={"Authorization": f"Bearer {tok}"})
    j = r.get_json()
    assert r.status_code == 200 and j["ok"]
    assert j["count"] == 0                          # no decisions this run
    assert j["order_count"] == 1 and j["order_blocked"] == 0    # "B/1" queued this run
    pending = json.loads((tmp_path / "pending_shoptet.json").read_text())
    assert pending["B/1"]["fields"]["internalNote"]["value"] == "https://supplier/inline"
    assert pending["B/1"]["fields"]["internalNote"]["credit"]["group"] == "order:B/1"
    assert not (tmp_path / "uploaded.json").exists()

    # once credited (the drain's job — simulated directly here), the code is no
    # longer "new" at all — it queues (and reports) nothing further
    webapp._credit_producer("parovania_eshop", {"order:B/1": "https://supplier/inline"})
    r2 = _client().post("/api/n8n/upload-pairings", headers={"Authorization": f"Bearer {tok}"})
    j2 = r2.get_json()
    assert j2["count"] == 0 and j2["order_count"] == 0


def test_order_pairings_code_covered_by_decision_is_excluded_and_blocked(monkeypatch, tmp_path):
    # a code already covered by a reviewed decision this run must NOT be duplicated
    # in the same import CSV (Shoptet aborts the whole import on a duplicate code) —
    # the reviewed decision wins, the order_pairing stays "blocked" (not queued).
    dec = {"k1": {"status": "good", "url": "https://supplier/x"}}
    tok = _arm_pairings(monkeypatch, tmp_path, dec,
                        order_pairings={"A/1": "https://supplier/inline"})
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: pytest.fail("must not import — must queue"))
    r = _client().post("/api/n8n/upload-pairings", headers={"Authorization": f"Bearer {tok}"})
    j = r.get_json()
    assert j["ok"] and j["queued"] == 2              # k1's A/1+A/2 queued via the decision
    assert j["order_count"] == 0 and j["order_blocked"] == 1
    pending = json.loads((tmp_path / "pending_shoptet.json").read_text())
    assert pending["A/1"]["fields"]["internalNote"]["value"] == "https://supplier/x"
    # nothing confirmed this run → nothing credited at all yet, either way
    assert not (tmp_path / "uploaded.json").exists()


def test_a_decision_already_uploaded_still_outranks_a_stale_inline_pairing(monkeypatch, tmp_path):
    """The exclusion must hold on EVERY later night, not just the one that ships the
    decision.

    `exclude_codes` used to be built from THIS run's NEW decision keys only. Once a
    decision is recorded uploaded it stops being "new", the exclusion set goes empty,
    and the stale `order_pairings[code]` — 8 codes on live data carry both values, at
    least one of them different — is emitted and written to the live `internalNote`.
    The correction therefore survived exactly ONE night and the eshop then held the
    WRONG supplier page permanently (the code is recorded and never retried), while
    the tab, /api/orders and /api/import all kept showing the corrected one. That URL
    feeds automatic reordering, so a wrong link orders the wrong product.
    """
    dec = {"k1": {"status": "manual", "url": "https://CORRECT.test/x"}}
    tok = _arm_pairings(monkeypatch, tmp_path, dec,
                        order_pairings={"A/1": "https://STALE-INLINE.test/x"})
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: pytest.fail("must not import — must queue"))
    # night 1 — the correction is QUEUED, the stale inline pairing is blocked
    j1 = _client().post("/api/n8n/upload-pairings",
                        headers={"Authorization": f"Bearer {tok}"}).get_json()
    assert j1["queued"] == 2 and j1["order_count"] == 0 and j1["order_blocked"] == 1
    pending1 = json.loads((tmp_path / "pending_shoptet.json").read_text())
    assert pending1["A/1"]["fields"]["internalNote"]["value"] == "https://CORRECT.test/x"
    assert pending1["A/2"]["fields"]["internalNote"]["value"] == "https://CORRECT.test/x"

    # the drain confirms + credits k1 overnight (simulated directly — its own
    # confirm→credit path is test_webreview_shoptet_upload.py's job)
    webapp._credit_producer("parovania_eshop", {"k1": "https://CORRECT.test/x"})

    # night 2 — the decision is no longer "new", but it still OWNS A/1: the stale
    # inline pairing must stay blocked, never emitted to the live eshop
    j2 = _client().post("/api/n8n/upload-pairings",
                        headers={"Authorization": f"Bearer {tok}"}).get_json()
    assert j2["queued"] == 0, f"night 2 re-queued the eshop: {j2}"
    assert j2["order_count"] == 0 and j2["order_blocked"] == 1
    uploaded = json.loads((tmp_path / "uploaded.json").read_text())
    assert "order:A/1" not in uploaded          # never recorded → never silently final
    assert uploaded["k1"] == "https://CORRECT.test/x"


def test_a_split_decision_blocks_a_stale_inline_pairing_too(monkeypatch, tmp_path):
    """A `split` product's codes are owned per size (#174) and shipped by the
    `split_links` automation — an inline pairing for the same code must not race it
    into the same internalNote cell. The exclusion therefore has to see variant_links,
    exactly like the manual zip does."""
    dec = {"k1": {"status": "split", "url": ""}}
    tok = _arm_pairings(monkeypatch, tmp_path, dec,
                        order_pairings={"A/1": "https://STALE-INLINE.test/x"})
    monkeypatch.setattr(webapp, "VARIANT_LINKS", str(tmp_path / "variant_links.json"))
    webapp._save_variant_links({"A/1": "https://per-size.test/l"})
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
    j = _client().post("/api/n8n/upload-pairings",
                       headers={"Authorization": f"Bearer {tok}"}).get_json()
    assert j["order_count"] == 0 and j["order_blocked"] == 1
    assert not (tmp_path / "uploaded.json").exists()


def test_order_pairings_dry_run_does_not_mark_uploaded(monkeypatch, tmp_path):
    tok = _arm_pairings(monkeypatch, tmp_path, {},
                        order_pairings={"B/1": "https://supplier/z"})
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: pytest.fail("a dry run must never import"))
    r = _client().post("/api/n8n/upload-pairings?dry_run=1", headers={"Authorization": f"Bearer {tok}"})
    j = r.get_json()
    assert j["dry_run"] is True
    assert j["would_queue"] == 1
    # dry-run must NOT persist → no state file written at all
    assert not (tmp_path / "uploaded.json").exists()
    assert not (tmp_path / "pending_shoptet.json").exists()
    # dry-run must NOT persist → still 1 new order pairing to queue on the next (real) call
    r2 = _client().post("/api/n8n/upload-pairings", headers={"Authorization": f"Bearer {tok}"})
    # #299 Task 11 finding 3 — this scenario is order-pairing-only (no reviewed
    # decisions), so the DECISION bucket ("queued") is 0 and the inline-order
    # bucket ("order_count") is the 1 that actually queued.
    j2 = r2.get_json()
    assert j2["queued"] == 0 and j2["order_count"] == 1


# #299 Task 10 — `test_order_pairings_failed_import_does_not_mark_uploaded` deleted.
# `_do_upload_pairings` no longer imports directly, so it has no failed-import
# branch left to guard for the order-pairings half either — that protection now
# lives in `test_webreview_shoptet_upload.py::test_a_pairing_key_whose_second_code_failed_is_NOT_credited`
# (generic across decision keys AND order codes, since both flow through the same
# credit_group mechanism) and the drain's own chunk/lock tests.


def test_import_dry_run_passthrough(monkeypatch, tmp_path):
    tok = _arm_token(monkeypatch, tmp_path)
    seen = {}
    monkeypatch.setattr(webapp, "run_import",
                        lambda p, dry_run=False, timeout=300:
                        (seen.update(dry_run=dry_run), (0, "spracované=1", ""))[1])
    r = _client().post("/api/n8n/shoptet-import?dry_run=1", data=_FEED,
                       headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200 and seen["dry_run"] is True


def test_import_fail_closed_without_creds(monkeypatch, tmp_path):
    # No creds file → token None → even a Bearer call is rejected (never open).
    monkeypatch.setattr(webapp, "CRED_PATH", str(tmp_path / "missing"))
    r = _client().post("/api/n8n/shoptet-import", data=_FEED,
                       headers={"Authorization": "Bearer anything"})
    assert r.status_code == 401


def test_import_non_ascii_auth_header_is_401_not_500(monkeypatch, tmp_path):
    _arm_token(monkeypatch, tmp_path)
    r = _client().post("/api/n8n/shoptet-import", data=_FEED,
                       headers={"Authorization": "Bearer ÿþ"})
    assert r.status_code == 401


def test_import_releases_lock_after_run(monkeypatch, tmp_path):
    tok = _arm_token(monkeypatch, tmp_path)
    monkeypatch.setattr(webapp, "run_import", lambda *a, **k: (0, "spracované=1", ""))
    _client().post("/api/n8n/shoptet-import", data=_FEED,
                   headers={"Authorization": f"Bearer {tok}"})
    # lock must be free again (a leaked lock would wedge every future import)
    assert webapp._import_lock.acquire(blocking=False)
    webapp._import_lock.release()


def test_import_multipart_file_path(monkeypatch, tmp_path):
    tok = _arm_token(monkeypatch, tmp_path)
    monkeypatch.setattr(webapp, "run_import", lambda *a, **k: (0, "spracované=1", ""))
    r = _client().post(
        "/api/n8n/shoptet-import",
        data={"file": (io.BytesIO(_FEED), "restock.csv")},
        content_type="multipart/form-data",
        headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200 and r.get_json()["rows"] == 1


def test_import_hard_error_surfaces_error_detail(monkeypatch, tmp_path):
    # #23: a hard Shoptet error (import aborted, no Spracované summary) must be
    # surfaced to the caller/notifier as an explicit error_detail — not silently
    # swallowed into a bare "processed: null".
    tok = _arm_token(monkeypatch, tmp_path)
    err = "Chyba | Číslo riadku: 42 - Data in column code are not unique"
    out = _hard_error_stdout(err)
    monkeypatch.setattr(webapp, "run_import", lambda *a, **k: (2, out, "boom"))
    r = _client().post("/api/n8n/shoptet-import", data=_FEED,
                       headers={"Authorization": f"Bearer {tok}"})
    j = r.get_json()
    assert r.status_code == 502 and j["ok"] is False
    assert j["processed"] is None
    assert j["error_detail"] == err


# ── #158: the restock feed has the SAME 120s browser-redirect-timeout risk #156
#    fixed for the pairings/suppliers pushes — must route through the SAME chunked
#    import helper (_import_rows_chunked). Hermetic: run_import stubbed. ──────────
def _large_feed(n):
    header = ("code;pairCode;name;purchasePrice;productVisibility;availabilityInStock;"
              "availabilityOutOfStock;stock\r\n")
    rows = "".join(f"{i}/M;P{i};Vesta {i};9;visible;Skladom;Skladom;5\r\n" for i in range(n))
    return (header + rows).encode("utf-8")


def _recording_import(fail_on_call=None):
    """run_import stub recording each chunk CSV's rows; optionally FAIL the Nth call
    (1-based) to simulate a mid-batch chunk failure (mirrors
    test_webreview_parovania_eshop.py's #156 pattern)."""
    calls = []

    def fake_run(csv_path, dry_run=False, timeout=300):
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            rd = list(_csv.reader(f, delimiter=";"))
        rows = rd[1:]
        calls.append({"header": rd[0], "rows": rows, "dry_run": dry_run})
        if fail_on_call is not None and len(calls) == fail_on_call:
            return 2, "POZOR: Shoptet hlási zlyhania", "boom"
        return 0, f"VÝSLEDOK: spracované={len(rows)} upravené={len(rows)} zlyhania=0", ""
    return fake_run, calls


def test_n8n_import_large_batch_split_into_chunks(monkeypatch, tmp_path):
    # 650 rows -> must be imported in >=2 chunks, each <= IMPORT_CHUNK_ROWS.
    # RED before the fix: a single 650-row import call.
    tok = _arm_token(monkeypatch, tmp_path)
    n = 650
    fake_run, calls = _recording_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)
    r = _client().post("/api/n8n/shoptet-import", data=_large_feed(n),
                       headers={"Authorization": f"Bearer {tok}"})
    j = r.get_json()
    assert r.status_code == 200 and j["ok"] and j["rows"] == n and j["processed"] == n
    assert len(calls) >= 2                                   # split, not one giant import
    assert max(len(c["rows"]) for c in calls) <= webapp.IMPORT_CHUNK_ROWS
    imported = [row[0] for c in calls for row in c["rows"]]
    assert sorted(imported) == sorted(f"{i}/M" for i in range(n))
    assert all(c["dry_run"] is False for c in calls)


def test_n8n_import_mid_batch_chunk_failure_returns_502_with_progress(monkeypatch, tmp_path):
    # a chunk failing mid-batch must -> 502 with a clear, tab-surfaced error
    # message, STOP after the failing chunk, and release the import lock.
    tok = _arm_token(monkeypatch, tmp_path)
    n = 650
    fake_run, calls = _recording_import(fail_on_call=2)      # 1st chunk ok, 2nd fails
    monkeypatch.setattr(webapp, "run_import", fake_run)
    r = _client().post("/api/n8n/shoptet-import", data=_large_feed(n),
                       headers={"Authorization": f"Bearer {tok}"})
    j = r.get_json()
    assert r.status_code == 502 and j["ok"] is False
    assert len(calls) == 2                                   # batch STOPS after the failing chunk
    assert "časti 2/" in j["error"]
    assert "z 650 riadkov" in j["error"]
    # the import lock was released despite the failure (else the next call 409s)
    assert webapp._import_lock.acquire(blocking=False)
    webapp._import_lock.release()


def test_n8n_import_small_batch_still_single_import(monkeypatch, tmp_path):
    # a small batch must NOT be needlessly chunked — one import call, as before.
    tok = _arm_token(monkeypatch, tmp_path)
    fake_run, calls = _recording_import()
    monkeypatch.setattr(webapp, "run_import", fake_run)
    r = _client().post("/api/n8n/shoptet-import", data=_FEED,
                       headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert len(calls) == 1 and len(calls[0]["rows"]) == 1


# --- n8n nightly supplier write-back (assigned names → eshop `supplier`) ----- #
def _arm_suppliers(monkeypatch, tmp_path, assigns, token="secret-tok"):
    cred = tmp_path / ".shoptet_admin"
    cred.write_text(f"N8N_IMPORT_TOKEN={token}\n", encoding="utf-8")
    monkeypatch.setattr(webapp, "CRED_PATH", str(cred))
    monkeypatch.setattr(webapp, "OUT", str(tmp_path))
    monkeypatch.setattr(webapp, "SUPPLIERS_STATE", str(tmp_path / "uploaded_suppliers.json"))
    monkeypatch.setattr(webapp, "SUPPLIER_ASSIGN", str(tmp_path / "sa.json"))
    # #299 Task 10: _do_upload_suppliers now QUEUES into the shared pending_shoptet
    # table instead of importing directly.
    monkeypatch.setattr(webapp, "PENDING_SHOPTET", str(tmp_path / "pending_shoptet.json"))
    monkeypatch.setattr(webapp, "CODE2PAIR", {"88/Z": "777"})
    # BUG 1 fail-closed: the write-back refuses to run without a catalog export. Provide a
    # minimal non-empty export where 88/Z has NO own supplier (empty column) → not excluded
    # → the assignment is written, exactly as in production. (Without this stub the CI box,
    # which has no data/products.csv, would read an empty export and block the upload.)
    monkeypatch.setattr(
        webapp, "_iter_export_lines",
        lambda: iter(["code;pairCode;supplier\r\n", "88/Z;777;\r\n"]))
    # …and the same gate ALSO refuses an implausibly small export (PR #276 review): the
    # production floor is 1000 codes, this stub has one, so the floor is lowered for the
    # fixture and pinned at its real value by
    # test_an_implausibly_small_export_blocks_the_supplier_write_back.
    monkeypatch.setattr(webapp, "EXPORT_MIN_CODES", 1)
    webapp._save_supplier_assign(assigns)
    return token


def test_suppliers_rejects_without_token(monkeypatch, tmp_path):
    _arm_suppliers(monkeypatch, tmp_path, {})
    assert _client().post("/api/n8n/upload-suppliers").status_code == 401


def test_suppliers_zero_new_returns_count_0(monkeypatch, tmp_path):
    tok = _arm_suppliers(monkeypatch, tmp_path, {})
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
    r = _client().post("/api/n8n/upload-suppliers", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200 and r.get_json()["count"] == 0


def test_suppliers_queues_assignments_for_the_hourly_drain(monkeypatch, tmp_path):
    """#299 Task 10 — `_do_upload_suppliers` no longer imports directly: it QUEUES
    into the shared pending_shoptet table, source "parovania_eshop_suppliers"
    (distinct from the pairings push's "parovania_eshop" — see `_credit_producer`).
    Unlike pairings, suppliers has NO export-confirmed fast path (every candidate
    always goes through the queue, matching the grube_externalcode/split_links
    precedent from Task 8), so `count` is simply an alias for `queued` — but
    `total_uploaded`/`remaining` (built from the PERSISTED uploaded_suppliers.json)
    stay untouched until the drain actually credits it."""
    tok = _arm_suppliers(monkeypatch, tmp_path, {"88/Z": "BETALOV"})
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: pytest.fail("must not import — must queue"))
    j = _client().post("/api/n8n/upload-suppliers",
                       headers={"Authorization": f"Bearer {tok}"}).get_json()
    assert j["ok"] and j["queued"] == 1 and j["count"] == 1
    assert j["products"][0] == {"code": "88/Z", "supplier": "BETALOV"}
    assert j["total_assigned"] == 1 and j["total_uploaded"] == 0 and j["remaining"] == 1
    pending = json.loads((tmp_path / "pending_shoptet.json").read_text())
    # the queued field carries ONLY the supplier cell (no internalNote/state → safe)
    assert set(pending["88/Z"]["fields"]) == {"supplier"}
    assert pending["88/Z"]["fields"]["supplier"]["value"] == "BETALOV"

    # once the drain confirms + credits it (simulated directly), nothing queues again
    webapp._credit_producer("parovania_eshop_suppliers", {"88/Z": "BETALOV"})
    r2 = _client().post("/api/n8n/upload-suppliers", headers={"Authorization": f"Bearer {tok}"})
    j2 = r2.get_json()
    assert j2["queued"] == 0
    assert j2["total_uploaded"] == 1 and j2["remaining"] == 0


def test_suppliers_dry_run_does_not_mark_uploaded(monkeypatch, tmp_path):
    tok = _arm_suppliers(monkeypatch, tmp_path, {"88/Z": "WETLAND"})
    monkeypatch.setattr(webapp, "run_import", lambda p, dry_run=False, timeout=300: (0, "spracované=1", ""))
    r = _client().post("/api/n8n/upload-suppliers?dry_run=1", headers={"Authorization": f"Bearer {tok}"})
    assert r.get_json()["dry_run"] is True
    # dry-run must NOT persist → still 1 new on the next (real) call
    monkeypatch.setattr(webapp, "run_import", lambda p, dry_run=False, timeout=300: (0, "spracované=1", ""))
    r2 = _client().post("/api/n8n/upload-suppliers", headers={"Authorization": f"Bearer {tok}"})
    assert r2.get_json()["count"] == 1


# #299 Task 10 — `test_suppliers_failed_import_does_not_mark_uploaded` and
# `test_suppliers_hard_error_surfaces_error_detail` deleted. `_do_upload_suppliers`
# no longer imports directly, so it has no failed/hard-error import branch left to
# guard — that surface now belongs entirely to the hourly drain
# (`run_shoptet_upload`) and its own coverage in `test_webreview_shoptet_upload.py`,
# plus `test_a_pairing_key_whose_second_code_failed_is_NOT_credited` for the
# generic partial-credit-withheld shape (same credit_group mechanism suppliers
# use, per-code instead of per-decision-key).
