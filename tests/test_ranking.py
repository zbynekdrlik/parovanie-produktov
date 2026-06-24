from parovanie.models import Product, Candidate
from parovanie.ranking import rank, pick_best


def _p(name, ext=None):
    return Product("BETALOV", "k", ext, name, ["c"])


def test_code_match_is_high_confidence():
    p = _p("Nohavice HART RANDO XHP", "OB570")
    cands = [Candidate("Iné", "https://h/ine"),
             Candidate("HART RANDO XHP nohavice", "https://h/hart-rando-ob570")]
    best, conf = pick_best(p, cands)
    assert conf == "high"
    assert "ob570" in best.url.lower()


def test_name_match_ranks_closest_first():
    p = _p("Strike Nohavice DEERHUNTER 3989-388")
    cands = [Candidate("Ciapka iná", "https://w/ciapka"),
             Candidate("Strike Nohavice Deerhunter 3989", "https://w/strike-deerhunter-3989")]
    ranked = rank(p, cands)
    assert "deerhunter" in ranked[0].url.lower()
    best, conf = pick_best(p, cands)
    assert conf in {"high", "medium", "low"}
    assert best.url == "https://w/strike-deerhunter-3989"


def test_empty_candidates_returns_none():
    best, conf = pick_best(_p("X"), [])
    assert best is None and conf == "none"
