"""GRUBE display normalization on the review/search path.

Bug: the review web served RAW grube.SK candidate/decision URLs, so clicking a
GRUBE supplier link opened grube.sk. GRUBE == grube.DE (German availability); the
eshop internalNote + 'Na objednanie' chip already normalize via
import_builder.link_rows, but /api/products (review + search display) did not.

Fix is DISPLAY-ONLY: the response is normalized to grube.de, but the manager's
stored .sk pairings (in-memory PRODUCTS + on-disk decisions.json) are PRESERVED.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webreview"))
import app as webapp  # noqa: E402

from tests.conftest import authed_client  # noqa: E402 — logged-in session (#91)

_SK = "https://www.grube.sk/p/slug/619850/"
_DE = "https://www.grube.de/p/x/619850/"
_WET = "https://www.wetland.sk/p/thing/"


def test_api_products_normalizes_grube_urls_to_de_display_only(monkeypatch, tmp_path):
    grube = {
        "key": "GRUBE|619850", "supplier": "GRUBE", "pairCode": "619850",
        "name": "GRUBE prod", "variant_codes": ["619850/L"],
        "candidates": [{"name": "x", "url": _SK}],
        "ai_chosen_url": _SK,
    }
    wetland = {  # non-GRUBE control — must be returned unchanged
        "key": "WETLAND|500", "supplier": "WETLAND", "pairCode": "500",
        "name": "Wetland prod", "variant_codes": ["500/M"],
        "candidates": [{"name": "w", "url": _WET}],
        "ai_chosen_url": _WET,
    }
    decisions = {
        "GRUBE|619850": {"status": "manual", "url": _SK},
        "WETLAND|500": {"status": "manual", "url": _WET},
    }
    dec_path = tmp_path / "decisions.json"
    dec_path.write_text(json.dumps(decisions), encoding="utf-8")
    monkeypatch.setattr(webapp, "PRODUCTS", [grube, wetland])
    monkeypatch.setattr(webapp, "DECISIONS", str(dec_path))

    j = authed_client().get("/api/products").get_json()
    prods = {p["key"]: p for p in j["products"]}
    served = j["decisions"]

    # GRUBE — every display surface normalized to .de
    g = prods["GRUBE|619850"]
    assert g["candidates"][0]["url"] == _DE
    assert g["ai_chosen_url"] == _DE
    assert served["GRUBE|619850"]["url"] == _DE

    # non-GRUBE control — untouched
    w = prods["WETLAND|500"]
    assert w["candidates"][0]["url"] == _WET
    assert w["ai_chosen_url"] == _WET
    assert served["WETLAND|500"]["url"] == _WET

    # data integrity — storage (in-memory PRODUCTS + on-disk decisions.json) stays .sk
    assert webapp.PRODUCTS[0]["candidates"][0]["url"] == _SK
    assert webapp.PRODUCTS[0]["ai_chosen_url"] == _SK
    on_disk = json.loads(dec_path.read_text(encoding="utf-8"))
    assert on_disk["GRUBE|619850"]["url"] == _SK


def test_api_products_grube_candidate_without_url_untouched(monkeypatch, tmp_path):
    # a candidate with no url must survive normalization (no KeyError, no swap)
    grube = {
        "key": "GRUBE|700", "supplier": "GRUBE", "pairCode": "700",
        "name": "GRUBE nourl", "variant_codes": ["700/L"],
        "candidates": [{"name": "nolink"}, {"name": "x", "url": _SK}],
        "ai_chosen_url": "",
    }
    dec_path = tmp_path / "decisions.json"
    dec_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(webapp, "PRODUCTS", [grube])
    monkeypatch.setattr(webapp, "DECISIONS", str(dec_path))

    j = authed_client().get("/api/products").get_json()
    cands = j["products"][0]["candidates"]
    assert "url" not in cands[0]                    # url-less candidate intact
    assert cands[1]["url"] == _DE                   # linked candidate normalized
    assert j["products"][0]["ai_chosen_url"] == ""  # empty ai_chosen_url stays empty
