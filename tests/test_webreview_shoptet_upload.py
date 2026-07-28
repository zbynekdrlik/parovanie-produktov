import json
import pytest
from webreview import app as webapp


@pytest.fixture
def pend(tmp_path, monkeypatch):
    p = tmp_path / "pending_shoptet.json"
    monkeypatch.setattr(webapp, "PENDING_SHOPTET", str(p))
    return p


@pytest.fixture
def claim(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "CYCLE_CLAIM", str(tmp_path / ".cycle.lock"))


def test_queue_shoptet_fields_persists_and_counts(pend):
    n = webapp.queue_shoptet_fields(
        "parovania_eshop", "code;pairCode;internalNote",
        [["A", "P", "https://x"]])
    assert n == 1
    d = json.loads(pend.read_text(encoding="utf-8"))
    assert d["A"]["fields"]["internalNote"]["value"] == "https://x"
    assert d["A"]["fields"]["internalNote"]["queued_at"]


def test_queueing_twice_keeps_both_sources(pend):
    webapp.queue_shoptet_fields("parovania_eshop", "code;pairCode;internalNote",
                                [["A", "P", "https://x"]])
    webapp.queue_shoptet_fields("restock_skladom",
                                "code;pairCode;availabilityInStock",
                                [["A", "P", "Skladom"]])
    d = webapp._load_pending()
    assert set(d["A"]["fields"]) == {"internalNote", "availabilityInStock"}


def test_an_unreadable_table_refuses_the_write_rather_than_wiping_it(pend):
    """A table we cannot parse is NOT an empty table — writing on top of it would
    silently drop everything already queued. The refusal comes from
    `_atomic_write_json(protect=True)` inside `_save_pending`
    (`webapp.StoreWipeRefused`), not from a hand-rolled check in
    `queue_shoptet_fields` — so this pins the exact exception type, that the file on
    disk is left byte-for-byte untouched, and that the unreadable bytes are preserved
    in a quarantine copy before the write is refused."""
    pend.write_text("{ this is not json", encoding="utf-8")
    before = pend.read_bytes()
    with pytest.raises(webapp.StoreWipeRefused):
        webapp.queue_shoptet_fields("s", "code;pairCode;internalNote",
                                    [["A", "P", "u"]])
    assert pend.read_bytes() == before                      # original untouched
    assert list(pend.parent.glob("pending_shoptet.json.corrupt-*")), \
        "the unreadable bytes must be preserved before the write is refused"


def test_queueing_a_new_code_does_not_wipe_what_is_already_waiting(pend):
    """The whole point of the table: independent producers queue at any time, so
    queueing a DIFFERENT code must never disturb what an EARLIER call already put on
    disk and is waiting for the next hourly upload (the risk #299 exists to end)."""
    webapp.queue_shoptet_fields("parovania_eshop", "code;pairCode;internalNote",
                                [["B", "P", "https://existing"]])
    existing_b = webapp._load_pending()["B"]

    webapp.queue_shoptet_fields("restock_skladom",
                                "code;pairCode;availabilityInStock",
                                [["A", "P", "Skladom"]])

    d = webapp._load_pending()
    assert d["B"] == existing_b
    assert d["A"]["fields"]["availabilityInStock"]["value"] == "Skladom"


def test_the_whole_read_modify_write_runs_under_the_store_lock(pend, monkeypatch):
    """Pins the CAUSE, not the symptom. `_lock` is depth-counted, so a spy on
    `_save_pending` observes depth 0 the moment the write is moved outside its
    `with _lock:` block — the exact shape a future refactor could introduce."""
    depths = []
    real = webapp._save_pending

    def spy(d):
        depths.append(webapp._lock._depth)
        return real(d)
    monkeypatch.setattr(webapp, "_save_pending", spy)

    n = webapp.queue_shoptet_fields(
        "parovania_eshop", "code;pairCode;internalNote",
        [["A", "P", "https://x"]])

    assert n == 1
    assert depths == [1], (
        "_save_pending ran while the store lock was NOT held", depths)


def test_the_claim_is_exclusive_and_reports_busy_while_held(claim):
    with webapp._shoptet_cycle_claim() as got:
        assert got is True
        assert webapp._cycle_busy() is True
        with webapp._shoptet_cycle_claim() as second:
            assert second is False
    assert webapp._cycle_busy() is False


def test_the_claim_is_released_even_when_the_body_raises(claim):
    with pytest.raises(ValueError):
        with webapp._shoptet_cycle_claim():
            raise ValueError("boom")
    assert webapp._cycle_busy() is False
