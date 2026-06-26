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
