import json
import os
import tempfile
from parovanie.models import Product, Candidate
from parovanie import candidates_io


def _make_product():
    return Product(
        supplier="BETALOV",
        pair_key="BETALOV|OB570",
        external_code="OB570",
        name="Nohavice HART RANDO XHP",
        variant_codes=["60177/46", "60177/48"],
    )


def _make_candidates():
    return [
        Candidate("HART RANDO XHP", "https://betalov.sk/hart-rando-ob570"),
        Candidate("HART RANDO 2", "https://betalov.sk/hart-rando-2"),
    ]


def test_record_has_required_keys():
    p = _make_product()
    cands = _make_candidates()
    queries = ["OB570", "HART RANDO XHP"]
    rec = candidates_io.record(p, queries, cands)

    assert rec["pair_key"] == "BETALOV|OB570"
    assert rec["supplier"] == "BETALOV"
    assert rec["external_code"] == "OB570"
    assert rec["name"] == "Nohavice HART RANDO XHP"
    assert rec["variant_codes"] == ["60177/46", "60177/48"]
    assert rec["queries"] == queries
    assert isinstance(rec["candidates"], list)
    assert len(rec["candidates"]) == 2


def test_record_candidates_shape():
    p = _make_product()
    cands = _make_candidates()
    rec = candidates_io.record(p, ["OB570"], cands)

    first = rec["candidates"][0]
    assert set(first.keys()) == {"name", "url"}
    assert first["name"] == "HART RANDO XHP"
    assert first["url"] == "https://betalov.sk/hart-rando-ob570"


def test_write_read_roundtrip():
    p = _make_product()
    cands = _make_candidates()
    queries = ["OB570", "Nohavice HART RANDO XHP", "HART RANDO XHP"]
    records = [candidates_io.record(p, queries, cands)]

    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "candidates.json")
        candidates_io.write_candidates(records, path)

        # File must exist and be valid JSON
        assert os.path.exists(path)
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        assert raw == records

        # read_candidates helper must return the same data
        loaded = candidates_io.read_candidates(path)
        assert loaded == records


def test_write_read_multiple_records():
    p1 = _make_product()
    p2 = Product("WETLAND", "WETLAND|W123", "W123", "Bunda DEERHUNTER Strike", [])
    cands1 = _make_candidates()
    cands2 = [Candidate("Bunda Deerhunter Strike", "https://wetland.sk/bunda")]
    records = [
        candidates_io.record(p1, ["OB570"], cands1),
        candidates_io.record(p2, ["W123", "Bunda DEERHUNTER Strike"], cands2),
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "candidates.json")
        candidates_io.write_candidates(records, path)
        loaded = candidates_io.read_candidates(path)

    assert len(loaded) == 2
    assert loaded[0]["pair_key"] == "BETALOV|OB570"
    assert loaded[1]["pair_key"] == "WETLAND|W123"
    assert loaded[1]["candidates"][0]["url"] == "https://wetland.sk/bunda"
