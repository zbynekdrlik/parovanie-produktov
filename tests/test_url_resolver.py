"""Regression + unit tests for parovanie.url_resolver.

The reported bug: two different forestshop products whose names differ only by a
number ("Moor Padded 367" vs "393") both resolved to the SAME product URL, so
clicking "our product" in the review app opened the wrong product. The number is
dropped as a digit token, leaving identical name-token sets; the image filename
is the disambiguator the old resolver ignored. A wrong link is worse than no
link, so ambiguous cases must return None, and two differently-named products
must never share a URL.

Offline only — a tiny synthetic sitemap, no live network (per CLAUDE.md).
"""
from parovanie.url_resolver import assign_urls, resolve, build_index

# A slice of the real forestshop sitemap (the DEERHUNTER Moor Padded family).
SITEMAP = [
    "panska-polovnicka-vesta-deerhunter-moor-padded",
    "polovnicka-vesta-deerhunter-moor-padded-waistcoat",
    "polovnicka-detska-vesta-deerhunter-youth-moor-padded-waistcoat",
    "damska-zateplena-polovnicka-vesta-deerhunter-lady-moor-padded-waistcoat",
    "polovnicka-bunda-deerhunter-moor-padded-jacket",
    "polovnicke-rukavice-deerhunter-muflon-pro-light-gloves",
]
CDN = "https://cdn.myshoptet.com/usr/www.forestshop.sk/user/shop/orig/"


def _img(name):
    return [CDN + name]


def test_regression_distinct_products_same_name_tokens_do_not_collide():
    """THE reported bug. vesta-393 (image: panska...) and vesta-367 (image:
    ...waistcoat...) must resolve to their OWN distinct, correct URLs."""
    products = [
        {  # idx 265 — pairCode 1082
            "name": "Poľovnícka vesta Deerhunter Moor Padded 393",
            "our_images": _img("9576_panska-polovnicka-vesta-deerhunter-moor-padded-002.jpg?6501dfc4"),
        },
        {  # idx 746 — pairCode 1564 (the product in the bug report)
            "name": "Poľovnícka vesta - DEERHUNTER Moor Padded -367",
            "our_images": _img("15233_deerhunter-moor-padded-waistcoat-vesta.jpg?68c418e5"),
        },
    ]
    urls = assign_urls(products, SITEMAP)
    assert urls[0] == "https://www.forestshop.sk/panska-polovnicka-vesta-deerhunter-moor-padded/"
    assert urls[1] == "https://www.forestshop.sk/polovnicka-vesta-deerhunter-moor-padded-waistcoat/"
    assert urls[0] != urls[1]  # used to be the SAME wrong URL


def test_brand_logo_image_does_not_break_disambiguation():
    """A secondary brand-logo image (…_logo-deerhunter.png) must not pollute the
    image signal and force the product to None."""
    products = [{
        "name": "Poľovnícka vesta Deerhunter Moor Padded 393",
        "our_images": [
            CDN + "9576_panska-polovnicka-vesta-deerhunter-moor-padded-002.jpg",
            CDN + "9576_logo-deerhunter.png",
        ],
    }]
    urls = assign_urls(products, SITEMAP)
    assert urls[0] == "https://www.forestshop.sk/panska-polovnicka-vesta-deerhunter-moor-padded/"


def test_exact_name_slug_match_wins():
    url, strength, _ = resolve(
        "Poľovnícke rukavice DEERHUNTER Muflon Pro Light Gloves", [], *build_index(SITEMAP))
    assert url == "https://www.forestshop.sk/polovnicke-rukavice-deerhunter-muflon-pro-light-gloves/"
    assert strength == 3


def test_ambiguous_without_image_signal_returns_none():
    """Multiple token-superset candidates and no usable image → don't guess."""
    url, strength, _ = resolve(
        "Poľovnícka vesta Deerhunter Moor Padded 367", [], *build_index(SITEMAP))
    assert url is None
    assert strength == 0


def test_two_different_products_never_share_a_url():
    """Even if both name-match a single candidate, the dedup pass keeps only the
    strongest and Nones the rest — never the same URL on two different names."""
    products = [
        {"name": "Deerhunter Muflon Light polovnicke rukavice", "our_images": []},
        {"name": "Poľovnícke rukavice DEERHUNTER Muflon Pro Light Gloves", "our_images": []},
    ]
    urls = assign_urls(products, SITEMAP)
    chosen = [u for u in urls.values() if u]
    assert len(chosen) == len(set(chosen))  # no two products share a URL


def test_identical_name_products_are_kept_as_genuine_dupes():
    """Two catalog entries with the SAME name slug are a real duplicate product;
    both legitimately point to the one page (not a collision to break)."""
    products = [
        {"name": "Poľovnícka bunda DEERHUNTER Moor Padded Jacket", "our_images": []},
        {"name": "Poľovnícka bunda DEERHUNTER Moor Padded Jacket", "our_images": []},
    ]
    urls = assign_urls(products, SITEMAP)
    assert urls[0] == urls[1] == "https://www.forestshop.sk/polovnicka-bunda-deerhunter-moor-padded-jacket/"


def test_no_candidate_returns_none():
    url, strength, _ = resolve("Úplne iný produkt XYZ", [], *build_index(SITEMAP))
    assert url is None
    assert strength == 0


def test_image_supported_tie_does_not_guess():
    """Two image-supported candidates with EQUAL extra-token counts → the
    fewest-extra tiebreak must refuse to guess (return None), not pick one."""
    sitemap = [
        "vesta-deerhunter-moor-padded-red",
        "vesta-deerhunter-moor-padded-blue",
    ]
    # image tokens {vesta,deerhunter,moor,padded} are a subset of BOTH; each slug
    # adds exactly one extra token (red / blue) → symmetric tie.
    url, strength, _ = resolve(
        "Vesta Deerhunter Moor Padded",
        [CDN + "100_vesta-deerhunter-moor-padded.jpg"],
        *build_index(sitemap))
    assert url is None
    assert strength == 0


def test_dedup_tie_among_different_products_drops_all():
    """When the strongest match to one URL is a tie between DIFFERENT products,
    neither gets the link (don't hand a wrong link to either)."""
    sitemap = ["polovnicke-rukavice-deerhunter-muflon-pro-light-gloves"]
    products = [  # both single-superset (strength 1), different names → tie → drop both
        {"name": "Deerhunter Muflon Light rukavice", "our_images": []},
        {"name": "Rukavice Deerhunter Muflon Gloves", "our_images": []},
    ]
    urls = assign_urls(products, sitemap)
    assert urls[0] is None
    assert urls[1] is None


def test_none_images_does_not_crash():
    url, _, _ = resolve("Poľovnícka vesta Deerhunter Moor Padded 367", None, *build_index(SITEMAP))
    assert url is None
