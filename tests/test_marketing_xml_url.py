"""Regression: forestshop our_url must come from the marketing-XML ORIG_URL by exact
code (the manager saw products linking to ?string= search because drop-ship products
aren't in the sitemap). Tests the streaming parser incl. malformed-token tolerance."""
import importlib.util
import os

_p = os.path.join(os.path.dirname(__file__), "..", "scripts", "url_from_marketing_xml.py")
_spec = importlib.util.spec_from_file_location("uxml", _p)
uxml = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(uxml)


def test_build_code2url_maps_product_and_variant_codes(tmp_path):
    xml = tmp_path / "m.xml"
    xml.write_text(
        '<SHOP><SHOPITEM><CODE>16058</CODE><ORIG_URL>https://www.forestshop.sk/jacket-571/</ORIG_URL>'
        '<VARIANTS><VARIANT><CODE>16058/M</CODE></VARIANT><VARIANT><CODE>16058/L</CODE></VARIANT></VARIANTS>'
        '<VISIBILITY>detailOnly</VISIBILITY></SHOPITEM>'
        '<SHOPITEM><CODE>999</CODE><ORIG_URL>https://www.forestshop.sk/other/</ORIG_URL></SHOPITEM></SHOP>',
        encoding="utf-8")
    m = uxml.build_code2url(str(xml))
    assert m["16058"] == "https://www.forestshop.sk/jacket-571/"
    assert m["16058/M"] == "https://www.forestshop.sk/jacket-571/"   # variant → same product URL
    assert m["16058/L"] == "https://www.forestshop.sk/jacket-571/"
    assert m["999"] == "https://www.forestshop.sk/other/"


def test_tolerates_malformed_token(tmp_path):
    # a raw control char (0x0c) is what made the strict parser fail on the live XML
    xml = tmp_path / "bad.xml"
    xml.write_bytes(
        b'<SHOP><SHOPITEM><CODE>A</CODE><SEO>bad\x0ctoken</SEO>'
        b'<ORIG_URL>https://www.forestshop.sk/a/</ORIG_URL></SHOPITEM></SHOP>')
    m = uxml.build_code2url(str(xml))
    assert m.get("A") == "https://www.forestshop.sk/a/"
