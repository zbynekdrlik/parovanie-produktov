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
