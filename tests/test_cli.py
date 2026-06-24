import os
from parovanie.models import Candidate
from parovanie.cli import run

FIX = "tests/fixtures/sample_products.csv"


class FakeClient:
    def search(self, supplier, query):
        if supplier == "BETALOV":
            return [Candidate("HART RANDO XHP", "https://h/hart-ob570")]
        return [Candidate("Strike Deerhunter 3989", "https://w/strike-3989")]


def test_run_writes_three_outputs(tmp_path):
    out = tmp_path / "out"
    matches = run(FIX, str(out), {"BETALOV", "WETLAND"}, client=FakeClient())
    assert os.path.exists(out / "import_betalov_wetland.csv")
    assert os.path.exists(out / "match_report.csv")
    assert os.path.exists(out / "unmatched.csv")
    assert len(matches) == 2
    assert all(m.chosen is not None for m in matches)


def test_resume_skips_already_matched(tmp_path):
    out = tmp_path / "out"
    ck = tmp_path / "ck.json"

    class CountingClient:
        def __init__(self): self.calls = 0
        def search(self, supplier, query):
            self.calls += 1
            from parovanie.models import Candidate
            return [Candidate("X", f"https://x/{supplier.lower()}")]

    c1 = CountingClient()
    run(FIX, str(out), {"BETALOV", "WETLAND"}, client=c1, checkpoint=str(ck))
    assert c1.calls > 0
    # second run with same checkpoint: every product already done -> zero searches
    c2 = CountingClient()
    matches = run(FIX, str(out), {"BETALOV", "WETLAND"}, client=c2, checkpoint=str(ck))
    assert c2.calls == 0
    assert all(m.chosen is not None for m in matches)
