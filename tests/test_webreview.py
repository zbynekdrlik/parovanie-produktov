"""Smoke/route tests for the deployed Flask review UI (webreview/app.py).

The app tolerates missing data files at import (loads 0 products), so these run
in CI without the gitignored data/ tree.
"""
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


def test_import_dry_run_passthrough(monkeypatch, tmp_path):
    tok = _arm_token(monkeypatch, tmp_path)
    seen = {}
    monkeypatch.setattr(webapp, "run_import",
                        lambda p, dry_run=False: (seen.update(dry_run=dry_run), (0, "spracované=1", ""))[1])
    r = _client().post("/api/n8n/shoptet-import?dry_run=1", data=_FEED,
                       headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200 and seen["dry_run"] is True
