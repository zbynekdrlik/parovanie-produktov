from parovanie.models import Product
from parovanie.normalize import build_query, clean_name


def _p(name, ext=None):
    return Product("WETLAND", "k", ext, name, ["c"])


def test_query_prefers_external_code():
    assert build_query(_p("Nohavice HART RANDO XHP", "OB570")) == "OB570"


def test_query_uses_clean_name_without_code():
    assert build_query(_p("Strike Nohavice DEERHUNTER 3989-388")) == \
        "Strike Nohavice DEERHUNTER 3989-388"


def test_clean_name_strips_leading_index():
    assert clean_name("01 Ponožky BOBR - jar/jeseň") == "Ponožky BOBR - jar/jeseň"


def test_clean_name_collapses_whitespace():
    assert clean_name("Bunda   FOREST\t1003") == "Bunda FOREST 1003"
