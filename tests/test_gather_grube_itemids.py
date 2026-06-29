import json

from scripts.gather_grube_itemids import gather_itemids

FAKE = {  # productId -> html with schema.org Offer objects (matches parse_variants)
  "154773": '"name":"Größe S.","price":"1","priceCurrency":"EUR","sku":"1547734523"'
            '"name":"Größe M.","price":"1","priceCurrency":"EUR","sku":"1547734598"',
}


def test_gather_itemids_writes_map(tmp_path):
    calls = []

    def fetch(url, wait_selector=None):
        calls.append(url)
        pid = url.rstrip("/").split("/")[-1]
        return FAKE[pid]

    out = gather_itemids([("GRUBE|395", "https://www.grube.sk/p/x/154773/?q=a#itemId=1")],
                         fetch, checkpoint=str(tmp_path / "cp.json"))
    assert out["154773"] == {"S": "1547734523", "M": "1547734598"}
    assert calls == ["https://www.grube.de/p/x/154773/"]   # normalized to .de


def test_gather_itemids_resumes_from_checkpoint(tmp_path):
    cp = tmp_path / "cp.json"
    cp.write_text(json.dumps({"154773": {"S": "X"}}))

    def fetch(*a, **k):
        raise AssertionError("should not fetch")

    out = gather_itemids([("GRUBE|395", "https://www.grube.sk/p/x/154773/")],
                         fetch, checkpoint=str(cp))
    assert out["154773"] == {"S": "X"}     # skipped, no fetch


def test_gather_itemids_404_continues(tmp_path):
    def fetch(url, wait_selector=None):
        raise RuntimeError("404")

    out = gather_itemids([("GRUBE|9", "https://www.grube.sk/p/x/999999/")],
                         fetch, checkpoint=str(tmp_path / "cp.json"))
    assert out == {}     # error tolerated, batch did not crash
