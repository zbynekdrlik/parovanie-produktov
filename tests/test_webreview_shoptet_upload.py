import json
import pytest
from webreview import app as webapp


@pytest.fixture
def pend(tmp_path, monkeypatch):
    p = tmp_path / "pending_shoptet.json"
    monkeypatch.setattr(webapp, "PENDING_SHOPTET", str(p))
    return p


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
    pend.write_text("{ this is not json", encoding="utf-8")
    with pytest.raises(Exception):
        webapp.queue_shoptet_fields("s", "code;pairCode;internalNote",
                                    [["A", "P", "u"]])
