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
