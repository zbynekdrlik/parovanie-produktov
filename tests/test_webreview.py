"""Smoke/route tests for the deployed Flask review UI (webreview/app.py).

The app tolerates missing data files at import (loads 0 products), so these run
in CI without the gitignored data/ tree.
"""
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webreview"))
import app as webapp  # noqa: E402


def _client():
    return webapp.app.test_client()


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
    monkeypatch.setattr(webapp, "_load_decisions",
        lambda: {"BETALOV|231": {"status": "good", "url": "https://www.huntingshop.eu/x"}})
    j = _client().get("/api/orders").get_json()
    assert len(j["orders"]) == 1
    assert j["orders"][0]["supplierUrl"] == "https://www.huntingshop.eu/x"
    assert j["orders"][0]["ordered"] is False


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


def test_supplier_meta_parses_price_and_availability():
    html = '<meta property="product:price:amount" content="12.50"> Skladom dnes'
    price, avail = webapp._supplier_meta(html)
    assert price == "12,50"
    assert avail == "Skladom"


# --- n8n Shoptet import endpoint -------------------------------------------- #
import csv as _csv  # noqa: E402

_FEED = ("code;pairCode;name;purchasePrice;productVisibility;availabilityInStock;stock\r\n"
         "15233/M;1564;Vesta;999;visible;Skladom;5\r\n").encode("utf-8")


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

    def fake_run(csv_path, dry_run=False):
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
    empty = b"code;pairCode;stock\r\n"
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


def _arm_pairings(monkeypatch, tmp_path, decisions, token="secret-tok"):
    cred = tmp_path / ".shoptet_admin"
    cred.write_text(f"N8N_IMPORT_TOKEN={token}\n", encoding="utf-8")
    monkeypatch.setattr(webapp, "CRED_PATH", str(cred))
    monkeypatch.setattr(webapp, "OUT", str(tmp_path))
    monkeypatch.setattr(webapp, "PAIRINGS_STATE", str(tmp_path / "uploaded.json"))
    monkeypatch.setattr(webapp, "PRODUCTS",
                        [{"key": "k1", "name": "Vesta XY", "our_url": "https://forestshop/x",
                          "variant_codes": ["A/1", "A/2"]}])
    monkeypatch.setattr(webapp, "CODE2PAIR", {"A/1": "100", "A/2": "100"})
    monkeypatch.setattr(webapp, "_load_decisions", lambda: decisions)
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


def test_import_dry_run_passthrough(monkeypatch, tmp_path):
    tok = _arm_token(monkeypatch, tmp_path)
    seen = {}
    monkeypatch.setattr(webapp, "run_import",
                        lambda p, dry_run=False: (seen.update(dry_run=dry_run), (0, "spracované=1", ""))[1])
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
