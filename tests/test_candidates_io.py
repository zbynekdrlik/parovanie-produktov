import json
import os
import tempfile

import pytest

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


# --- join_verdicts: candidates.json <-> ai_verdicts.json must join by pair_key,
# NEVER by array position/idx (#43) ---------------------------------------------


def _rec(pair_key, name="X"):
    p = Product("SUP", pair_key, None, name, [])
    return candidates_io.record(
        p, [], [Candidate("cand-a", "https://x/a"), Candidate("cand-b", "https://x/b")]
    )


def test_join_verdicts_attaches_by_pair_key_not_position():
    """candidates.json got reordered between gather and verify (e.g. a re-gather ran
    in a different order) — the join must still land each verdict on the product it
    was made for, never on whatever happens to sit at that array index."""
    rec_a = _rec("SUP|A", "Product A")
    rec_b = _rec("SUP|B", "Product B")
    recs = [rec_a, rec_b]  # candidates.json order at merge time: A, B

    # verdicts were produced against the REVERSED order (B, A) — same idx 0/1 as
    # before, but now pointing at DIFFERENT products than recs[0]/recs[1].
    verdicts = [
        {"idx": 0, "pair_key": "SUP|B", "chosen_i": 1, "reason": "matched B"},
        {"idx": 1, "pair_key": "SUP|A", "chosen_i": 0, "reason": "matched A"},
    ]

    joined = candidates_io.join_verdicts(recs, verdicts)

    # correct keyed result: recs[0] (A) gets A's verdict, recs[1] (B) gets B's.
    assert joined[0]["reason"] == "matched A"
    assert joined[0]["chosen_i"] == 0
    assert joined[1]["reason"] == "matched B"
    assert joined[1]["chosen_i"] == 1

    # prove a POSITIONAL join (the old bug) would have mismapped — this is exactly
    # what `{v["idx"]: v for v in verdicts}` + `verds.get(i)` used to do.
    positional = {v["idx"]: v for v in verdicts}
    assert positional[0]["reason"] == "matched B"  # WRONG product for recs[0]==A
    assert positional[0]["reason"] != joined[0]["reason"]


def test_join_verdicts_survives_a_filtered_candidates_list():
    """A product dropped from candidates.json after verdicts were produced (e.g. a
    re-gather filtered it out) must not shift every later verdict onto the wrong
    neighbour — each surviving product still gets ITS OWN verdict, by key."""
    rec_a = _rec("SUP|A", "Product A")
    rec_c = _rec("SUP|C", "Product C")
    # verdicts were produced when candidates.json was [A, B, C] (idx 0,1,2) — B is
    # intentionally NOT built into a rec here: it is the product later filtered out.
    verdicts = [
        {"idx": 0, "pair_key": "SUP|A", "chosen_i": 0, "reason": "for A"},
        {"idx": 1, "pair_key": "SUP|B", "chosen_i": 0, "reason": "for B"},
        {"idx": 2, "pair_key": "SUP|C", "chosen_i": 0, "reason": "for C"},
    ]
    # candidates.json at merge time has B filtered out: [A, C]
    recs = [rec_a, rec_c]

    joined = candidates_io.join_verdicts(recs, verdicts)

    assert joined[0]["reason"] == "for A"  # recs[0]==A -> A's own verdict
    assert joined[1]["reason"] == "for C"  # recs[1]==C -> C's own verdict, NOT B's
    # (a positional join would have given recs[1] (C) the idx=1 verdict, i.e. B's)


def test_join_verdicts_unknown_pair_key_is_skipped_not_misapplied():
    rec_a = _rec("SUP|A", "Product A")
    verdicts = [{"idx": 0, "pair_key": "SUP|GHOST", "chosen_i": 0, "reason": "ghost"}]

    joined = candidates_io.join_verdicts([rec_a], verdicts)

    assert joined[0] is None  # not attached to A just because it's the only record


def test_join_verdicts_refuses_a_verdict_without_pair_key():
    """A verdict missing pair_key gives no safe way to know which product it is
    for — must fail loudly, never silently fall back to array position."""
    rec_a = _rec("SUP|A", "Product A")
    verdicts = [{"idx": 0, "chosen_i": 0, "reason": "no pair_key at all"}]

    with pytest.raises(SystemExit):
        candidates_io.join_verdicts([rec_a], verdicts)
