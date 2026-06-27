from parovanie.models import Candidate, Product, Match
from parovanie import config


def test_candidate_defaults():
    c = Candidate(name="X", url="https://e/x")
    assert c.code is None and c.price is None and c.raw_score == 0.0


def test_product_holds_variants():
    p = Product(supplier="WETLAND", pair_key="123", external_code=None,
                name="Bunda", variant_codes=["61246/46", "61246/48"])
    assert p.variant_codes == ["61246/46", "61246/48"]


def test_match_optional_chosen():
    p = Product("WETLAND", "123", None, "Bunda", ["61246/46"])
    m = Match(product=p, query="Bunda", chosen=None, confidence="none", candidate_count=0)
    assert m.chosen is None


def test_suppliers_configured():
    assert {"BETALOV", "WETLAND", "ODIMON"} <= set(config.SUPPLIERS)
    assert config.SUPPLIERS["WETLAND"].base_url.startswith("https://www.wetland.sk")
    assert config.SUPPLIERS["BETALOV"].base_url.startswith("https://www.huntingshop.eu")
    assert config.SUPPLIERS["ODIMON"].base_url.startswith("https://www.odimon.sk")
    # every configured supplier has a registered parser
    from parovanie.client import PARSERS
    assert set(config.SUPPLIERS) <= set(PARSERS)
