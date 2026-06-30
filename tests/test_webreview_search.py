"""Catalog search + promote-on-pair endpoints (webreview/app.py):
GET /api/search and POST /api/search-pair.

Mirrors tests/test_webreview.py: the app module is imported once (sys.path) and
the per-test fixture monkeypatches the module globals (CATALOG / PRODUCTS /
CODE2PAIR / DATA / DECISIONS / SRC / _CODE2URL) so each test runs against a
small in-memory catalog and a tmp store — never the real 56 MB export or the
59 MB marketing XML.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webreview"))
import app as webapp  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    catalog = webapp.build_catalog_index([
        {"code": "60611/L", "pairCode": "425", "name": "Bunda Tradition",
         "supplier": "GRUBE", "defaultImage": "i.jpg"},
        {"code": "4931/S", "pairCode": "512", "name": "Mikina GARDE",
         "supplier": "WETLAND", "defaultImage": ""},
    ], review_keys=set())
    monkeypatch.setattr(webapp, "CATALOG", catalog)
    monkeypatch.setattr(webapp, "PRODUCTS", [])
    monkeypatch.setattr(webapp, "CODE2PAIR", {"60611/L": "425", "4931/S": "512"})
    monkeypatch.setattr(webapp, "DECISIONS", str(tmp_path / "decisions.json"))
    monkeypatch.setattr(webapp, "DATA", str(tmp_path / "review_data.json"))
    # No live export → `current` snapshot resolves to {} (fast, deterministic).
    monkeypatch.setattr(webapp, "SRC", str(tmp_path / "no_export.csv"))
    # Pre-seed the marketing-XML cache empty so our_url resolution never parses the
    # real 59 MB data/out/marketing.xml during the test.
    monkeypatch.setattr(webapp, "_CODE2URL", {})
    with open(webapp.DATA, "w", encoding="utf-8") as f:
        json.dump([], f)
    return webapp.app.test_client()


def test_search_endpoint_filters(client):
    r = client.get("/api/search?q=tradition").get_json()
    assert [x["pairCode"] for x in r["results"]] == ["425"]
    # query shorter than 2 normalized chars → no results
    assert client.get("/api/search?q=").get_json()["results"] == []


def test_search_result_shape_and_in_review_after_promote(client):
    res = client.get("/api/search?q=tradition").get_json()["results"][0]
    assert set(res) >= {"pairCode", "name", "supplier", "codes", "image",
                        "in_review", "our_url", "idx"}
    assert res["codes"] == ["60611/L"] and res["image"] == "i.jpg"
    assert res["in_review"] is False and res["idx"] is None and res["our_url"] is None
    # after a pair, the catalog entry flips to in_review and exposes the new idx
    client.post("/api/search-pair", json={"pairCode": "425", "url": "https://www.grube.de/x/"})
    res2 = client.get("/api/search?q=tradition").get_json()["results"][0]
    assert res2["in_review"] is True and res2["idx"] == 0


def test_search_pair_promotes_and_writes_decision(client):
    r = client.post("/api/search-pair",
                    json={"pairCode": "425", "url": "https://www.grube.de/p/x/154773/"})
    assert r.status_code == 200 and r.get_json()["promoted"] is True
    assert r.get_json()["key"] == "425"
    # review_data.json now holds the promoted product
    rd = json.load(open(webapp.DATA))
    e = [p for p in rd if p["key"] == "425"][0]
    assert e["supplier"] == "GRUBE" and e["variant_codes"] == ["60611/L"]
    assert e["ai_status"] == "unmatched" and e["our_images"] == ["i.jpg"]
    # decision written as a manual pairing
    dec = json.load(open(webapp.DECISIONS))
    assert dec["425"]["status"] == "manual"
    assert dec["425"]["url"].startswith("https://www.grube.de/")
    # in-memory PRODUCTS appended (no app restart needed to see it)
    assert any(p["key"] == "425" for p in webapp.PRODUCTS)


def test_search_pair_idempotent_second_call_decision_only(client):
    r1 = client.post("/api/search-pair", json={"pairCode": "425", "url": "https://www.grube.de/a/"})
    assert r1.get_json()["promoted"] is True
    r2 = client.post("/api/search-pair", json={"pairCode": "425", "url": "https://www.grube.de/b/"})
    assert r2.status_code == 200 and r2.get_json()["promoted"] is False
    # decision updated to the latest url, product not duplicated
    dec = json.load(open(webapp.DECISIONS))
    assert dec["425"]["url"] == "https://www.grube.de/b/"
    assert sum(1 for p in webapp.PRODUCTS if p["key"] == "425") == 1


def test_search_pair_rejects_bad_url(client):
    r = client.post("/api/search-pair", json={"pairCode": "425", "url": "javascript:x"})
    assert r.status_code == 400
    # nothing promoted, no decision written
    assert webapp.PRODUCTS == []
    assert (not os.path.exists(webapp.DECISIONS)
            or "425" not in json.load(open(webapp.DECISIONS)))


def test_search_pair_rejects_empty_url(client):
    assert client.post("/api/search-pair", json={"pairCode": "425", "url": ""}).status_code == 400


def test_search_pair_unknown_paircode_404(client):
    assert client.post("/api/search-pair",
                       json={"pairCode": "999", "url": "https://x.sk/"}).status_code == 404
