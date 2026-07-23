"""Smoke/route tests for the deployed Flask review UI (webreview/app.py).

The app tolerates missing data files at import (loads 0 products), so these run
in CI without the gitignored data/ tree.
"""
import io
import json
import os
import sys

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
        "20261045;2026-04-24 19:14:05;Vybavuje sa;Polokošeľa HART;1;61247/L;Veľkosť: L;BETALOV\r\n"
        "20261099;2026-05-01 10:00:00;Vybavená;Iné;1;99999/M;Veľkosť: M;ORBIS\r\n"
        "20261045;2026-04-24 19:14:05;Vybavuje sa;Kuriér;1;SHIPPING11;;\r\n"
    )
    products = [{"key": "BETALOV|231", "supplier": "BETALOV", "name": "Polokošeľa HART",
                 "variant_codes": ["61247/L"], "pairCode": "231"}]
    decisions = {"BETALOV|231": {"status": "good", "url": "https://www.huntingshop.eu/x"}}
    rows = webapp.build_to_order_rows(orders, products, decisions, {"61247/L": "231"})
    assert len(rows) == 1                      # Vybavená + SHIPPING dropped
    r = rows[0]
    assert r["itemCode"] == "61247/L" and r["qty"] == "1" and r["supplier"] == "BETALOV"
    assert r["size"] == "Veľkosť: L"
    assert r["key"] == "20261045|61247/L"
    assert r["orderDate"] == "2026-04-24"      # date column, time dropped
    assert r["supplierUrl"] == "https://www.huntingshop.eu/x"


def test_build_to_order_rows_missing_date_is_empty():
    orders = ("code;statusName;itemName;itemAmount;itemCode;itemVariantName;itemSupplier\r\n"
              "20261045;Vybavuje sa;X;1;61247/L;L;BETALOV\r\n")
    rows = webapp.build_to_order_rows(orders, [], {}, {})
    assert rows[0]["orderDate"] == ""          # no date column → graceful empty


def test_ordered_endpoint_persists(monkeypatch, tmp_path):
    monkeypatch.setattr(webapp, "ORDERED", str(tmp_path / "ordered.json"))
    c = _client()
    r = c.post("/api/ordered", json={"key": "20261045|61247/L", "ordered": True})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert c.get("/api/ordered").get_json()["ordered"]["20261045|61247/L"] is True
    c.post("/api/ordered", json={"key": "20261045|61247/L", "ordered": False})
    assert "20261045|61247/L" not in c.get("/api/ordered").get_json()["ordered"]


def test_orders_route_joins_and_merges_ordered(monkeypatch, tmp_path):
    orders = ("code;statusName;itemName;itemAmount;itemCode;itemVariantName;itemSupplier\r\n"
              "20261045;Vybavuje sa;Polokošeľa;1;61247/L;Veľkosť: L;BETALOV\r\n")
    monkeypatch.setattr(webapp, "_orders_csv_cached", lambda: orders.encode("cp1250"))
    monkeypatch.setattr(webapp, "PRODUCTS",
        [{"key": "BETALOV|231", "supplier": "BETALOV", "name": "Polokošeľa",
          "variant_codes": ["61247/L"], "pairCode": "231"}])
    monkeypatch.setattr(webapp, "CODE2PAIR", {"61247/L": "231"})
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
    r = c.post("/api/instock", json={"key": "20261045|61247/L", "instock": True})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert c.get("/api/instock").get_json()["instock"]["20261045|61247/L"] is True
    c.post("/api/instock", json={"key": "20261045|61247/L", "instock": False})
    assert "20261045|61247/L" not in c.get("/api/instock").get_json()["instock"]


def test_unavailable_endpoint_persists(monkeypatch, tmp_path):
    monkeypatch.setattr(webapp, "UNAVAIL", str(tmp_path / "unavail.json"))
    c = _client()
    r = c.post("/api/unavailable", json={"key": "20261045|61247/L", "unavailable": True})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert c.get("/api/unavailable").get_json()["unavailable"]["20261045|61247/L"] is True
    c.post("/api/unavailable", json={"key": "20261045|61247/L", "unavailable": False})
    assert "20261045|61247/L" not in c.get("/api/unavailable").get_json()["unavailable"]


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


def test_orders_route_merges_instock_and_unavailable(monkeypatch, tmp_path):
    orders = ("code;statusName;itemName;itemAmount;itemCode;itemVariantName;itemSupplier\r\n"
              "20261045;Vybavuje sa;Polokošeľa;1;61247/L;Veľkosť: L;BETALOV\r\n")
    monkeypatch.setattr(webapp, "_orders_csv_cached", lambda: orders.encode("cp1250"))
    monkeypatch.setattr(webapp, "PRODUCTS",
        [{"key": "BETALOV|231", "supplier": "BETALOV", "name": "Polokošeľa",
          "variant_codes": ["61247/L"], "pairCode": "231"}])
    monkeypatch.setattr(webapp, "CODE2PAIR", {"61247/L": "231"})
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
        json.dumps({"20261045|61247/L": True}), encoding="utf-8")
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
               json={"orderCode": "20261045", "comment": "zavolať zákazníkovi"})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert c.get("/api/order-comment").get_json()["comments"]["20261045"] == "zavolať zákazníkovi"
    assert webapp._load_order_comments()["20261045"] == "zavolať zákazníkovi"
    # empty comment clears the entry
    c.post("/api/order-comment", json={"orderCode": "20261045", "comment": ""})
    assert "20261045" not in c.get("/api/order-comment").get_json()["comments"]


def test_order_comment_requires_ordercode(monkeypatch, tmp_path):
    monkeypatch.setattr(webapp, "ORDER_COMMENTS", str(tmp_path / "oc.json"))
    r = _client().post("/api/order-comment", json={"comment": "x"})
    assert r.status_code == 400
    assert webapp._load_order_comments() == {}


def test_order_comment_rejects_too_long(monkeypatch, tmp_path):
    monkeypatch.setattr(webapp, "ORDER_COMMENTS", str(tmp_path / "oc.json"))
    big = "x" * (webapp.ORDER_COMMENT_MAX + 1)
    r = _client().post("/api/order-comment", json={"orderCode": "20261045", "comment": big})
    assert r.status_code == 400
    assert webapp._load_order_comments() == {}
    # exactly at the cap is accepted
    ok = "y" * webapp.ORDER_COMMENT_MAX
    assert _client().post("/api/order-comment",
                          json={"orderCode": "20261045", "comment": ok}).status_code == 200


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
        "20261045;Vybavuje sa;chýba nám 1 kus;Polokošeľa;1;61247/L;Veľkosť: L;BETALOV\r\n")
    rows = webapp.build_to_order_rows(orders, [], {}, {})
    assert rows[0]["shopRemark"] == "chýba nám 1 kus"


def test_orders_route_merges_comment_and_shopremark(monkeypatch, tmp_path):
    orders = ("code;statusName;shopRemark;itemName;itemAmount;itemCode;itemVariantName;itemSupplier\r\n"
              "20261045;Vybavuje sa;interná poznámka;Polokošeľa;1;61247/L;Veľkosť: L;BETALOV\r\n")
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
        json.dumps({"20261045": "objednané u dodávateľa"}), encoding="utf-8")
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
              "20261050;Vybavuje sa;Vesta;1;99999/X;Veľkosť: X;ORBIS\r\n")
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


def test_orders_exposes_assigned_supplier(monkeypatch, tmp_path):
    orders = ("code;statusName;itemName;itemAmount;itemCode;itemVariantName;itemSupplier\r\n"
              "20261060;Vybavuje sa;Bez dod;1;88/Z;Veľkosť: Z;\r\n")   # NO itemSupplier
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
              "20261045;Vybavuje sa;Bunda Grand Nord;1;60645/L;Veľkosť: L;GRUBE\r\n")
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


def test_pairings_uploads_link_csv_and_marks_uploaded(monkeypatch, tmp_path):
    dec = {"k1": {"status": "good", "url": "https://supplier/x"}}
    tok = _arm_pairings(monkeypatch, tmp_path, dec)
    seen = {}

    def fake_run(csv_path, dry_run=False, timeout=300):
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            rd = list(_csv.reader(f, delimiter=";"))
        seen["header"] = rd[0]
        seen["rows"] = rd[1:]
        return 0, "VÝSLEDOK: spracované=2 upravené=2 zlyhania=0", ""

    monkeypatch.setattr(webapp, "run_import", fake_run)
    r = _client().post("/api/n8n/upload-pairings", headers={"Authorization": f"Bearer {tok}"})
    j = r.get_json()
    assert r.status_code == 200 and j["ok"] and j["count"] == 1
    # the import file carries internalNote (the reorder link) — NOT stripped
    assert seen["header"] == ["code", "pairCode", "internalNote"]
    assert ["A/1", "100", "https://supplier/x"] in seen["rows"]
    assert j["products"][0]["supplier_url"] == "https://supplier/x"
    # uploaded state recorded → a second call uploads nothing
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run again")))
    r2 = _client().post("/api/n8n/upload-pairings", headers={"Authorization": f"Bearer {tok}"})
    assert r2.get_json()["count"] == 0


def test_pairings_response_carries_summary_counts(monkeypatch, tmp_path):
    # The n8n notifier needs totals to post ONE summary Discord message instead of
    # one-per-product: new (count), total uploaded, remaining, total products, review link.
    dec = {"k1": {"status": "good", "url": "https://supplier/x"}}
    tok = _arm_pairings(monkeypatch, tmp_path, dec)
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: (0, "VÝSLEDOK: spracované=2 upravené=2 zlyhania=0", ""))
    j = _client().post("/api/n8n/upload-pairings",
                       headers={"Authorization": f"Bearer {tok}"}).get_json()
    assert j["count"] == 1                       # newly uploaded this run
    assert j["total_products"] == 1              # PRODUCTS in the review set
    assert j["total_uploaded"] == 1              # uploaded total now includes this run
    assert j["remaining"] == 0                   # nothing left without a pairing
    assert j["review_url"].startswith("https://")


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


def test_pairings_partial_batch_only_marks_keys_with_rows_uploaded(monkeypatch, tmp_path):
    # #49: a batch with ONE coded (uploadable) key and ONE code-less (blocked) key
    # must mark ONLY the coded key as uploaded — the code-less key must stay "new"
    # so a later run retries it, instead of being silently lost forever.
    cred = tmp_path / ".shoptet_admin"
    cred.write_text("N8N_IMPORT_TOKEN=secret-tok\n", encoding="utf-8")
    monkeypatch.setattr(webapp, "CRED_PATH", str(cred))
    monkeypatch.setattr(webapp, "OUT", str(tmp_path))
    monkeypatch.setattr(webapp, "PAIRINGS_STATE", str(tmp_path / "uploaded.json"))
    monkeypatch.setattr(webapp, "ORDER_PAIRINGS", str(tmp_path / "order_pairings.json"))
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
                        lambda *a, **k: (0, "VÝSLEDOK: spracované=1 upravené=1 zlyhania=0", ""))
    j = _client().post("/api/n8n/upload-pairings",
                       headers={"Authorization": "Bearer secret-tok"}).get_json()
    assert j["ok"] is True
    assert j["count"] == 1                # only k1 genuinely got a row uploaded
    assert j["blocked"] == 1               # k2 surfaced as blocked, not silently dropped
    uploaded = json.loads((tmp_path / "uploaded.json").read_text())
    assert uploaded == {"k1": "https://supplier/x1"}   # k2 must NOT be recorded

    # k2 must still be retried on the next run — it was never marked uploaded
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("k1 must not re-import")))
    j2 = _client().post("/api/n8n/upload-pairings",
                        headers={"Authorization": "Bearer secret-tok"}).get_json()
    assert j2["count"] == 0 and j2["blocked"] == 1     # k2 retried, still blocked (no codes)


def test_pairings_rejects_wrong_token(monkeypatch, tmp_path):
    _arm_pairings(monkeypatch, tmp_path, {})
    r = _client().post("/api/n8n/upload-pairings", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_pairings_failed_import_does_not_mark_uploaded(monkeypatch, tmp_path):
    # A FAILED import (rc != 0) must NOT record the pairing as uploaded — else it's
    # silently lost and never retried.
    dec = {"k1": {"status": "good", "url": "https://supplier/z"}}
    tok = _arm_pairings(monkeypatch, tmp_path, dec)
    monkeypatch.setattr(webapp, "run_import",
                        lambda p, dry_run=False, timeout=300: (2, "POZOR: zlyhania", "boom"))
    r = _client().post("/api/n8n/upload-pairings", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 502 and r.get_json()["ok"] is False
    # not marked → a later (succeeding) call still sees it as new
    calls = {"n": 0}

    def ok_run(p, dry_run=False, timeout=300):
        calls["n"] += 1
        return 0, "VÝSLEDOK: spracované=2 upravené=2 zlyhania=0", ""

    monkeypatch.setattr(webapp, "run_import", ok_run)
    r2 = _client().post("/api/n8n/upload-pairings", headers={"Authorization": f"Bearer {tok}"})
    assert r2.get_json()["count"] == 1 and calls["n"] == 1


def test_pairings_hard_error_surfaces_error_detail_and_does_not_mark_uploaded(monkeypatch, tmp_path):
    # #23: a hard Shoptet error (aborted import, e.g. duplicate 'code') must be
    # surfaced as error_detail AND must not mark the pairing uploaded — ask #3.
    dec = {"k1": {"status": "good", "url": "https://supplier/z"}}
    tok = _arm_pairings(monkeypatch, tmp_path, dec)
    err = "Chyba | Číslo riadku: 7 - Data in column code are not unique"
    monkeypatch.setattr(webapp, "run_import", lambda p, dry_run=False, timeout=300: (2, err, "boom"))
    r = _client().post("/api/n8n/upload-pairings", headers={"Authorization": f"Bearer {tok}"})
    j = r.get_json()
    assert r.status_code == 502 and j["ok"] is False
    assert j["processed"] is None
    assert j["error_detail"] == err
    assert (tmp_path / "uploaded.json").exists() is False   # nothing was ever marked uploaded


def test_pairings_whitespace_url_does_not_re_upload_forever(monkeypatch, tmp_path):
    # A decision URL with surrounding whitespace must be normalized so it's marked
    # uploaded and not re-selected every night.
    dec = {"k1": {"status": "good", "url": "https://supplier/w  "}}
    tok = _arm_pairings(monkeypatch, tmp_path, dec)
    monkeypatch.setattr(webapp, "run_import",
                        lambda p, dry_run=False, timeout=300: (0, "spracované=2", ""))
    assert _client().post("/api/n8n/upload-pairings",
                          headers={"Authorization": f"Bearer {tok}"}).get_json()["count"] == 1
    # second run: must be 0 (marked despite the trailing spaces)
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("re-uploaded!")))
    assert _client().post("/api/n8n/upload-pairings",
                          headers={"Authorization": f"Bearer {tok}"}).get_json()["count"] == 0


def test_pairings_dry_run_does_not_mark_uploaded(monkeypatch, tmp_path):
    dec = {"k1": {"status": "manual", "url": "https://supplier/y"}}
    tok = _arm_pairings(monkeypatch, tmp_path, dec)
    monkeypatch.setattr(webapp, "run_import", lambda p, dry_run=False, timeout=300: (0, "spracované=2", ""))
    r = _client().post("/api/n8n/upload-pairings?dry_run=1", headers={"Authorization": f"Bearer {tok}"})
    assert r.get_json()["dry_run"] is True
    # dry-run must NOT persist → still 1 new on the next (real) call
    monkeypatch.setattr(webapp, "run_import", lambda p, dry_run=False, timeout=300: (0, "spracované=2", ""))
    r2 = _client().post("/api/n8n/upload-pairings", headers={"Authorization": f"Bearer {tok}"})
    assert r2.get_json()["count"] == 1


# --- #38: nightly push ALSO covers order_pairings.json (inline 'Na objednanie' --- #
# --- pairings, outside the review set) — same import run, own uploaded state.  --- #
def test_order_pairings_uploaded_and_marked_under_order_namespace(monkeypatch, tmp_path):
    tok = _arm_pairings(monkeypatch, tmp_path, {},
                        order_pairings={"B/1": "https://supplier/inline"})
    seen = {}

    def fake_run(csv_path, dry_run=False, timeout=300):
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            rd = list(_csv.reader(f, delimiter=";"))
        seen["header"] = rd[0]
        seen["rows"] = rd[1:]
        return 0, "VÝSLEDOK: spracované=1 upravené=1 zlyhania=0", ""

    monkeypatch.setattr(webapp, "run_import", fake_run)
    r = _client().post("/api/n8n/upload-pairings", headers={"Authorization": f"Bearer {tok}"})
    j = r.get_json()
    assert r.status_code == 200 and j["ok"]
    assert j["count"] == 0                          # no decisions this run
    assert j["order_count"] == 1 and j["order_blocked"] == 0
    assert seen["header"] == ["code", "pairCode", "internalNote"]
    assert ["B/1", "", "https://supplier/inline"] in seen["rows"]
    uploaded = json.loads((tmp_path / "uploaded.json").read_text())
    assert uploaded["order:B/1"] == "https://supplier/inline"

    # unchanged on the next run → nothing pushed again
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run again")))
    r2 = _client().post("/api/n8n/upload-pairings", headers={"Authorization": f"Bearer {tok}"})
    j2 = r2.get_json()
    assert j2["count"] == 0 and j2["order_count"] == 0


def test_order_pairings_code_covered_by_decision_is_excluded_and_blocked(monkeypatch, tmp_path):
    # a code already covered by a reviewed decision this run must NOT be duplicated
    # in the same import CSV (Shoptet aborts the whole import on a duplicate code) —
    # the reviewed decision wins, the order_pairing stays "blocked" (not uploaded).
    dec = {"k1": {"status": "good", "url": "https://supplier/x"}}
    tok = _arm_pairings(monkeypatch, tmp_path, dec,
                        order_pairings={"A/1": "https://supplier/inline"})
    calls = []

    def fake_run(csv_path, dry_run=False, timeout=300):
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            rd = list(_csv.reader(f, delimiter=";"))
        calls.append(rd[1:])
        return 0, "VÝSLEDOK: spracované=2 upravené=2 zlyhania=0", ""

    monkeypatch.setattr(webapp, "run_import", fake_run)
    r = _client().post("/api/n8n/upload-pairings", headers={"Authorization": f"Bearer {tok}"})
    j = r.get_json()
    assert j["ok"] and j["count"] == 1               # k1's A/1+A/2 uploaded via the decision
    assert j["order_count"] == 0 and j["order_blocked"] == 1
    rows = calls[0]
    assert [row for row in rows if row[0] == "A/1"] == [["A/1", "100", "https://supplier/x"]]
    uploaded = json.loads((tmp_path / "uploaded.json").read_text())
    assert "order:A/1" not in uploaded


def test_order_pairings_dry_run_does_not_mark_uploaded(monkeypatch, tmp_path):
    tok = _arm_pairings(monkeypatch, tmp_path, {},
                        order_pairings={"B/1": "https://supplier/z"})
    monkeypatch.setattr(webapp, "run_import",
                        lambda p, dry_run=False, timeout=300: (0, "spracované=1", ""))
    r = _client().post("/api/n8n/upload-pairings?dry_run=1", headers={"Authorization": f"Bearer {tok}"})
    assert r.get_json()["dry_run"] is True
    # dry-run must NOT persist → no state file written at all
    assert not (tmp_path / "uploaded.json").exists()
    # dry-run must NOT persist → still 1 new order pairing on the next (real) call
    monkeypatch.setattr(webapp, "run_import",
                        lambda p, dry_run=False, timeout=300: (0, "spracované=1", ""))
    r2 = _client().post("/api/n8n/upload-pairings", headers={"Authorization": f"Bearer {tok}"})
    assert r2.get_json()["order_count"] == 1


def test_order_pairings_failed_import_does_not_mark_uploaded(monkeypatch, tmp_path):
    tok = _arm_pairings(monkeypatch, tmp_path, {},
                        order_pairings={"B/1": "https://supplier/z"})
    monkeypatch.setattr(webapp, "run_import",
                        lambda p, dry_run=False, timeout=300: (2, "POZOR: zlyhania", "boom"))
    r = _client().post("/api/n8n/upload-pairings", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 502 and r.get_json()["ok"] is False
    assert not (tmp_path / "uploaded.json").exists()
    # not marked → a later (succeeding) call still sees it as new
    monkeypatch.setattr(webapp, "run_import",
                        lambda p, dry_run=False, timeout=300: (0, "VÝSLEDOK: spracované=1 upravené=1 zlyhania=0", ""))
    r2 = _client().post("/api/n8n/upload-pairings", headers={"Authorization": f"Bearer {tok}"})
    assert r2.get_json()["order_count"] == 1


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
    monkeypatch.setattr(webapp, "run_import", lambda *a, **k: (2, err, "boom"))
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
    monkeypatch.setattr(webapp, "CODE2PAIR", {"88/Z": "777"})
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


def test_suppliers_uploads_csv_and_marks_uploaded(monkeypatch, tmp_path):
    tok = _arm_suppliers(monkeypatch, tmp_path, {"88/Z": "BETALOV"})
    seen = {}

    def fake_run(csv_path, dry_run=False, timeout=300):
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            rd = list(_csv.reader(f, delimiter=";"))
        seen["header"] = rd[0]
        seen["rows"] = rd[1:]
        return 0, "VÝSLEDOK: spracované=1 upravené=1 zlyhania=0", ""

    monkeypatch.setattr(webapp, "run_import", fake_run)
    j = _client().post("/api/n8n/upload-suppliers",
                       headers={"Authorization": f"Bearer {tok}"}).get_json()
    assert j["ok"] and j["count"] == 1
    # the import file carries ONLY code;pairCode;supplier (no internalNote/state → safe)
    assert seen["header"] == ["code", "pairCode", "supplier"]
    assert ["88/Z", "777", "BETALOV"] in seen["rows"]
    assert j["products"][0] == {"code": "88/Z", "supplier": "BETALOV"}
    assert j["total_assigned"] == 1 and j["total_uploaded"] == 1 and j["remaining"] == 0
    # uploaded state recorded → a second call sends nothing
    monkeypatch.setattr(webapp, "run_import",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run again")))
    r2 = _client().post("/api/n8n/upload-suppliers", headers={"Authorization": f"Bearer {tok}"})
    assert r2.get_json()["count"] == 0


def test_suppliers_dry_run_does_not_mark_uploaded(monkeypatch, tmp_path):
    tok = _arm_suppliers(monkeypatch, tmp_path, {"88/Z": "WETLAND"})
    monkeypatch.setattr(webapp, "run_import", lambda p, dry_run=False, timeout=300: (0, "spracované=1", ""))
    r = _client().post("/api/n8n/upload-suppliers?dry_run=1", headers={"Authorization": f"Bearer {tok}"})
    assert r.get_json()["dry_run"] is True
    # dry-run must NOT persist → still 1 new on the next (real) call
    monkeypatch.setattr(webapp, "run_import", lambda p, dry_run=False, timeout=300: (0, "spracované=1", ""))
    r2 = _client().post("/api/n8n/upload-suppliers", headers={"Authorization": f"Bearer {tok}"})
    assert r2.get_json()["count"] == 1


def test_suppliers_failed_import_does_not_mark_uploaded(monkeypatch, tmp_path):
    # A FAILED import (rc != 0) must NOT record the assignment as uploaded.
    tok = _arm_suppliers(monkeypatch, tmp_path, {"88/Z": "ODIMON"})
    monkeypatch.setattr(webapp, "run_import",
                        lambda p, dry_run=False, timeout=300: (1, "chyba", "boom"))
    r = _client().post("/api/n8n/upload-suppliers", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 502 and r.get_json()["ok"] is False
    # not marked → a later successful call still sends it
    ok_run = lambda p, dry_run=False, timeout=300: (0, "spracované=1", "")  # noqa: E731
    monkeypatch.setattr(webapp, "run_import", ok_run)
    r2 = _client().post("/api/n8n/upload-suppliers", headers={"Authorization": f"Bearer {tok}"})
    assert r2.get_json()["count"] == 1


def test_suppliers_hard_error_surfaces_error_detail(monkeypatch, tmp_path):
    # #23: same hard-error surfacing for the supplier write-back endpoint.
    tok = _arm_suppliers(monkeypatch, tmp_path, {"88/Z": "BETALOV"})
    err = "Chyba | Číslo riadku: 3 - Data in column code are not unique"
    monkeypatch.setattr(webapp, "run_import",
                        lambda p, dry_run=False, timeout=300: (2, err, "boom"))
    r = _client().post("/api/n8n/upload-suppliers", headers={"Authorization": f"Bearer {tok}"})
    j = r.get_json()
    assert r.status_code == 502 and j["ok"] is False
    assert j["processed"] is None
    assert j["error_detail"] == err
