"""Catalog search + promote-on-pair endpoints (webreview/app.py):
GET /api/search and POST /api/search-pair.

Mirrors tests/test_webreview.py: the app module is imported once (sys.path) and
the per-test fixture monkeypatches the module globals (CATALOG / PRODUCTS /
CODE2PAIR / DATA / DECISIONS / SRC / _CODE2URL) so each test runs against a
small in-memory catalog and a tmp store — never the real 56 MB export or the
59 MB marketing XML.
"""
import importlib
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webreview"))
import app as webapp  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    catalog = webapp.build_catalog_index([
        # 425 carries the commerce columns (price/stock/state flow to /api/search);
        # 512 stays minimal → the endpoint must serve safe defaults for it.
        {"code": "60611/L", "pairCode": "425", "name": "Bunda Tradition",
         "supplier": "GRUBE", "defaultImage": "i.jpg", "productVisibility": "visible",
         "availabilityInStock": "Skladom", "availabilityOutOfStock": "Vypredané",
         "price": "119", "stock": "4"},
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


def test_search_result_carries_price_stock_state(client):
    """Manager complaint: search rows show 'almost no data'. /api/search must expose
    the entry's OUR price, summed stock and 3-state classification."""
    r = client.get("/api/search?q=tradition").get_json()["results"][0]
    assert r["price"] == "119"
    assert r["stock"] == 4
    assert r["state"] == 1
    # a product whose export rows lack the commerce columns → safe defaults
    r2 = client.get("/api/search?q=garde").get_json()["results"][0]
    assert r2["price"] == "" and r2["stock"] == 0 and r2["state"] == 1


def test_search_paired_url_null_without_review_or_decision(client):
    r = client.get("/api/search?q=tradition").get_json()["results"][0]
    assert r["paired_url"] is None


def test_search_paired_url_appears_after_search_pair(client):
    client.post("/api/search-pair",
                json={"pairCode": "425", "url": "https://www.grube.de/p/x/154773/"})
    r = client.get("/api/search?q=tradition").get_json()["results"][0]
    assert r["paired_url"] == "https://www.grube.de/p/x/154773/"


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


# --------------------------------------------------------------------------- #
# C1 regression: a review entry is keyed "SUPPLIER|pairCode" for MOST suppliers
# (1586/2555 live entries have '|' in key); only BETALOV + most WETLAND use a bare
# pairCode. The catalog index is grouped by the BARE pairCode, so the search feature
# must match an in-review product by pairCode (NOT key) and write decisions under the
# entry's REAL key — else a SUPPLIER|pairCode product shows the wrong badge, the manual
# panel opens instead of the candidates panel, a DUPLICATE bare-key entry is written, and
# the manager's corrected URL lands under a key link_rows never reads (silently dropped).
# --------------------------------------------------------------------------- #
def _build_catalog_with_review(rows, products):
    """Build CATALOG exactly as webreview/app.py does at load (line ~88): review_keys are
    the products' BARE pairCodes, NOT their composite 'SUPPLIER|pairCode' keys."""
    return webapp.build_catalog_index(
        rows, review_keys={p.get("pairCode") for p in products})


@pytest.fixture
def supplier_key_client(tmp_path, monkeypatch):
    """An ALREADY-in-review product keyed 'GRUBE|425' (the dominant scheme) whose bare
    pairCode is '425'; the catalog is indexed by the bare pairCode '425'."""
    products = [{
        "idx": 7, "key": "GRUBE|425", "pairCode": "425", "supplier": "GRUBE",
        "name": "Bunda Tradition", "variant_codes": ["60611/L"], "our_images": [],
        "our_url": "https://www.forestshop.sk/bunda-tradition/",
        "ai_status": "manual", "ai_chosen_url": "https://www.grube.de/old/",
        "ai_reason": "", "candidates": [], "current": {},
    }]
    catalog = _build_catalog_with_review([
        {"code": "60611/L", "pairCode": "425", "name": "Bunda Tradition",
         "supplier": "GRUBE", "defaultImage": "i.jpg"},
    ], products)
    monkeypatch.setattr(webapp, "CATALOG", catalog)
    monkeypatch.setattr(webapp, "PRODUCTS", products)
    monkeypatch.setattr(webapp, "CODE2PAIR", {"60611/L": "425"})
    monkeypatch.setattr(webapp, "DECISIONS", str(tmp_path / "decisions.json"))
    monkeypatch.setattr(webapp, "DATA", str(tmp_path / "review_data.json"))
    monkeypatch.setattr(webapp, "SRC", str(tmp_path / "no_export.csv"))
    monkeypatch.setattr(webapp, "_CODE2URL", {})
    with open(webapp.DATA, "w", encoding="utf-8") as f:
        json.dump(products, f)
    return webapp.app.test_client()


def test_search_supplier_keyed_product_is_in_review_with_our_url(supplier_key_client):
    """C1: a 'SUPPLIER|pairCode'-keyed product must show in_review=True AND expose its
    our_url/idx. _search_result matched the in-review product by `key == pairCode`, false
    for every 'GRUBE|425'-style entry → the deep-link our_url/idx was dropped (None)."""
    res = supplier_key_client.get("/api/search?q=tradition").get_json()["results"]
    assert len(res) == 1
    r = res[0]
    assert r["pairCode"] == "425"
    assert r["in_review"] is True                                       # contract (line 88)
    assert r["our_url"] == "https://www.forestshop.sk/bunda-tradition/"  # was None (C1)
    assert r["idx"] == 7                                                # was None (C1)


def test_search_pair_existing_supplier_key_no_dup_decision_under_real_key(supplier_key_client):
    """C1 core: re-pairing a 'SUPPLIER|pairCode' product from search must NOT append a
    duplicate bare-key entry, and MUST write the decision under the REAL key 'GRUBE|425'
    (where link_rows reads it) — not under the bare '425' (which is silently dropped)."""
    r = supplier_key_client.post(
        "/api/search-pair", json={"pairCode": "425", "url": "https://www.grube.de/new/"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["promoted"] is False        # existing → not promoted (was True → a dup!)
    assert body["key"] == "GRUBE|425"        # decision target = real key (was "425")
    # no duplicate appended: still exactly ONE product for pairCode 425
    assert sum(1 for p in webapp.PRODUCTS if p.get("pairCode") == "425") == 1
    dec = json.load(open(webapp.DECISIONS))
    assert dec["GRUBE|425"]["status"] == "manual"
    assert dec["GRUBE|425"]["url"] == "https://www.grube.de/new/"   # reaches link_rows
    assert "425" not in dec                  # NOT written under the bare pairCode


def test_search_paired_url_reads_decision_under_real_key_and_grube_normalizes(supplier_key_client):
    """An in-review product's CURRENT decision URL is exposed as `paired_url` — read
    under the entry's REAL key ('GRUBE|425'), and for a GRUBE product the DISPLAY is
    normalized to grube.de (mirrors /api/products; storage untouched)."""
    sk = "https://www.grube.sk/p/slug/619850/"
    with open(webapp.DECISIONS, "w", encoding="utf-8") as f:
        json.dump({"GRUBE|425": {"status": "manual", "url": sk}}, f)
    r = supplier_key_client.get("/api/search?q=tradition").get_json()["results"][0]
    assert r["paired_url"] == "https://www.grube.de/p/x/619850/"
    # storage untouched — decisions.json still holds the manager's .sk URL
    assert json.load(open(webapp.DECISIONS))["GRUBE|425"]["url"] == sk


def test_search_paired_url_null_for_non_link_decision(supplier_key_client):
    """A state-only decision (unavailable/discontinued) carries no supplier URL."""
    with open(webapp.DECISIONS, "w", encoding="utf-8") as f:
        json.dump({"GRUBE|425": {"status": "unavailable", "url": ""}}, f)
    r = supplier_key_client.get("/api/search?q=tradition").get_json()["results"][0]
    assert r["paired_url"] is None


def test_promote_current_reflects_hidden_visibility(tmp_path, monkeypatch):
    """Regression: _current_for_paircode must scan the export's `productVisibility`
    column (there is NO `visibility` column — build_review_data.py / resync_export.py
    both read productVisibility). Reading the wrong name left vis="" so state_of never
    applied the hidden/blocked rule and the promoted snapshot wrongly showed state 1
    (sellable) for a visibility-hidden product — the snapshot-drift bug CLAUDE.md warns
    about. This FAILS on the buggy `visibility` read and PASSES after the fix."""
    # A real cp1250 export row (full column set the promote-time scan reads) for a
    # visibility-hidden product. ais/aos empty so the ONLY thing pushing it off-sale
    # is productVisibility=hidden — isolating the column-name bug.
    csv_path = tmp_path / "products.csv"
    header = ("code;pairCode;name;supplier;productVisibility;availabilityInStock;"
              "availabilityOutOfStock;price;standardPrice;stock;defaultImage")
    row = "77001/L;900;Bunda Hidden;GRUBE;hidden;;;12.50;19.90;3;i.jpg"
    csv_path.write_text(header + "\r\n" + row + "\r\n", encoding="cp1250")

    catalog = webapp.build_catalog_index(
        [{"code": "77001/L", "pairCode": "900", "name": "Bunda Hidden",
          "supplier": "GRUBE", "defaultImage": "i.jpg"}], review_keys=set())
    monkeypatch.setattr(webapp, "CATALOG", catalog)
    monkeypatch.setattr(webapp, "PRODUCTS", [])
    monkeypatch.setattr(webapp, "DECISIONS", str(tmp_path / "decisions.json"))
    monkeypatch.setattr(webapp, "DATA", str(tmp_path / "review_data.json"))
    monkeypatch.setattr(webapp, "SRC", str(csv_path))   # real export the scan reads
    monkeypatch.setattr(webapp, "_CODE2URL", {})
    with open(webapp.DATA, "w", encoding="utf-8") as f:
        json.dump([], f)
    client = webapp.app.test_client()

    r = client.post("/api/search-pair",
                    json={"pairCode": "900", "url": "https://www.grube.de/p/x/154773/"})
    assert r.status_code == 200 and r.get_json()["promoted"] is True

    # Expected snapshot DERIVED from current_of on the real hidden-visibility input —
    # not a guessed literal. For productVisibility=hidden, state_of returns 3
    # ("Už sa nebude predávať"), off=True, vis="hidden".
    expected = webapp.current_of("hidden", "", "", "12.50", "19.90", "3")
    assert expected["state"] == 3 and expected["off"] is True and expected["vis"] == "hidden"

    promoted = [p for p in webapp.PRODUCTS if p["key"] == "900"][0]
    assert promoted["current"]["vis"] == expected["vis"]      # "hidden", not ""
    assert promoted["current"]["state"] == expected["state"]  # 3, not 1
    assert promoted["current"]["off"] is expected["off"]      # True, not False
    # the priced fields still flow through (same arg order as the canonical producers)
    assert promoted["current"]["price"] == "12.50" and promoted["current"]["std"] == "19.90"


def test_load_catalog_in_review_keyed_by_paircode_at_real_call_site(tmp_path, monkeypatch):
    """C1 at the REAL module-load call site (app.py line ~92):

        CODE2PAIR, CATALOG = _load_catalog(SRC, {p.get("pairCode") for p in PRODUCTS})

    Every other test in this file monkeypatches CATALOG directly, so none of them
    would fail if line 92 were reverted to `{p.get("key") for p in PRODUCTS}`. This
    test boots the app through importlib.reload against fixture env paths
    (WEBREVIEW_OUT / WEBREVIEW_PRODUCTS — same vars tests/e2e/conftest.py uses), so the
    real comprehension at line 92 runs: a 'GRUBE|425'-keyed review entry has bare
    pairCode '425', the catalog is grouped by the bare pairCode '425', so review_keys
    MUST be the bare pairCodes for the in_review flag to land. Reverting line 92 to
    collect `key` makes review_keys={'GRUBE|425'} → '425' not in it → in_review False
    → this assertion FAILS (verified RED)."""
    out = tmp_path / "out"
    out.mkdir()
    # ONE already-in-review product keyed 'GRUBE|425' (the dominant SUPPLIER|pairCode
    # scheme); its BARE pairCode is '425'. Loaded into PRODUCTS at module load.
    (out / "review_data.json").write_text(json.dumps([{
        "idx": 0, "key": "GRUBE|425", "pairCode": "425", "supplier": "GRUBE",
        "name": "Bunda Tradition", "variant_codes": ["60611/L"], "our_images": [],
        "our_url": "", "ai_status": "manual", "ai_chosen_url": "", "ai_reason": "",
        "candidates": [], "current": {},
    }], ensure_ascii=False), encoding="utf-8")
    # A cp1250 Shoptet-export row whose pairCode is the bare '425' (real column set).
    products_csv = tmp_path / "products.csv"
    header = ("code;pairCode;name;supplier;productVisibility;availabilityInStock;"
              "availabilityOutOfStock;price;standardPrice;stock;defaultImage")
    row = "60611/L;425;Bunda Tradition;GRUBE;visible;Skladom;Vypredané;12,50;15,00;3;i.jpg"
    products_csv.write_text(header + "\r\n" + row + "\r\n", encoding="cp1250")

    monkeypatch.setenv("WEBREVIEW_OUT", str(out))
    monkeypatch.setenv("WEBREVIEW_PRODUCTS", str(products_csv))
    try:
        # REAL boot: PRODUCTS <- review_data.json, then line 92 builds CATALOG with
        # review_keys = {p.get("pairCode") ...} over the freshly loaded PRODUCTS.
        importlib.reload(webapp)
        assert webapp.PRODUCTS[0]["key"] == "GRUBE|425"   # composite key, bare pairCode 425
        assert "425" in webapp.CATALOG                     # catalog grouped by bare pairCode
        assert webapp.CATALOG["425"]["in_review"] is True  # line-92 contract (was False on C1)
    finally:
        # Restore the module to a hermetic empty state. Point the env at MISSING fixture
        # paths so this final reload returns ({}, {}) instantly and NEVER parses the real
        # 56 MB data/products.csv (done while monkeypatch's setenv is still active).
        monkeypatch.setenv("WEBREVIEW_OUT", str(tmp_path / "empty_out"))
        monkeypatch.setenv("WEBREVIEW_PRODUCTS", str(tmp_path / "no_export.csv"))
        importlib.reload(webapp)
