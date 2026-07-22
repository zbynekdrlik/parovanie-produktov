"""Regression + unit tests for scripts.resolve_urls (issue #15).

`resolve_urls.py` is the background HTTP-probe resolver for `detailOnly`
products (drop-ship pages absent from the sitemap). It used to be a SEPARATE,
untested mechanism from `parovanie.url_resolver` — no image-filename
disambiguation and no cross-product dedup, so it could in principle repeat the
exact "two different products share one URL" bug url_resolver was built to
fix. This file proves:

  1. resolve_urls REUSES url_resolver's dedup()/disambiguate()/assign_urls —
     not a re-implementation.
  2. The canonical sitemap resolver runs FIRST; only the genuine no-sitemap
     remainder gets HTTP-probed.
  3. Multiple HTTP-confirmed slug guesses for ONE product are disambiguated by
     the product's own image filename (never "first 200 wins" blindly).
  4. Two DIFFERENT products never end up sharing a probed URL — even if their
     guesses coincidentally both 200.
  5. Two catalog entries with the SAME name (a genuine duplicate) may share a
     URL — that's not a collision.
  6. A probed URL never displaces a URL some OTHER product already has.

Offline only — a fake `fetch(slug) -> confirmed_url | None` callable stands in
for the live HTTP probe (per CLAUDE.md: no live network in tests).
"""
from parovanie import url_resolver
from scripts.resolve_urls import candidates, dedup, disambiguate, assign_urls, resolve_batch, resolve_probe

CDN = "https://cdn.myshoptet.com/usr/www.forestshop.sk/user/shop/orig/"


def _img(name):
    return [CDN + name]


def test_reuses_url_resolver_functions_not_a_reimplementation():
    """resolve_urls must import the CANONICAL functions, never redefine them."""
    assert dedup is url_resolver.dedup
    assert disambiguate is url_resolver.disambiguate
    assert assign_urls is url_resolver.assign_urls


def test_resolve_probe_single_confirmed_hit():
    def fetch(c):
        return "https://www.forestshop.sk/noz-mikov-390/" if c == "noz-mikov-390" else None

    url, strength, name_slug = resolve_probe("Nôž Mikov 390", [], fetch)
    assert url == "https://www.forestshop.sk/noz-mikov-390/"
    assert strength > 0
    assert name_slug == "noz-mikov-390"


def test_resolve_probe_no_confirmed_hit_returns_none():
    url, strength, _ = resolve_probe("Úplne iný produkt XYZ", [], lambda c: None)
    assert url is None
    assert strength == 0


def test_resolve_probe_multiple_hits_disambiguated_by_image_not_first_hit():
    """Two of this product's OWN candidate slugs both 200 — a decoy that comes
    FIRST in probe order (the base slug) and the true page (the 'polovnicke-'
    prefixed variant). A naive 'keep the first 200' resolver (the old
    behavior) would wrongly keep the decoy. The image filename must pick the
    real one, exactly like url_resolver.disambiguate does for sitemap slugs."""
    decoy = "https://www.forestshop.sk/vesta-deerhunter-moor/"
    real = "https://www.forestshop.sk/polovnicke-vesta-deerhunter-moor/"

    def fetch(c):
        if c == "vesta-deerhunter-moor":       # base — probed FIRST, a decoy 200
            return decoy
        if c == "polovnicke-vesta-deerhunter-moor":  # the actual product page
            return real
        return None

    images = _img("12345_polovnicke-vesta-deerhunter-moor.jpg")
    url, strength, _ = resolve_probe("Vesta Deerhunter Moor", images, fetch)
    assert url == real
    assert url != decoy
    assert strength > 0


def test_resolve_probe_multiple_hits_no_image_signal_returns_none():
    """Multiple confirmed hits and no usable image → refuse to guess (a wrong
    link is worse than no link) rather than blindly keeping the first 200."""
    def fetch(c):
        if c == "vesta-deerhunter-moor":
            return "https://www.forestshop.sk/vesta-deerhunter-moor/"
        if c == "polovnicke-vesta-deerhunter-moor":
            return "https://www.forestshop.sk/polovnicke-vesta-deerhunter-moor/"
        return None

    url, strength, _ = resolve_probe("Vesta Deerhunter Moor", [], fetch)
    assert url is None
    assert strength == 0


def test_sitemap_pass_resolves_before_any_http_probe():
    """A todo product that the canonical sitemap resolver can already match
    must NOT be HTTP-probed at all — proves the sitemap pass runs first."""
    rd = [{"name": "Poľovnícke rukavice DEERHUNTER Muflon Pro Light Gloves",
           "our_images": [], "our_url": None}]
    sitemap = ["polovnicke-rukavice-deerhunter-muflon-pro-light-gloves"]

    def fetch(c):
        raise AssertionError(f"should not HTTP-probe {c} — sitemap already resolved it")

    from_sitemap, from_probe, remaining = resolve_batch(rd, sitemap, fetch)
    assert from_sitemap == 1
    assert from_probe == 0
    assert remaining == 0
    assert rd[0]["our_url"] == "https://www.forestshop.sk/polovnicke-rukavice-deerhunter-muflon-pro-light-gloves/"


def test_two_different_products_never_share_a_probed_url():
    """THE hardening this issue asks for: two DIFFERENT products whose guesses
    both 200 to the SAME url must never both end up assigned to it."""
    rd = [
        {"name": "Produkt A", "our_images": [], "our_url": None},
        {"name": "Produkt B", "our_images": [], "our_url": None},
    ]
    shared = "https://www.forestshop.sk/shared-page/"

    def fetch(c):
        return shared if c in ("produkt-a", "produkt-b") else None

    resolve_batch(rd, [], fetch)
    urls = [p["our_url"] for p in rd]
    non_none = [u for u in urls if u]
    assert len(non_none) == len(set(non_none))  # never both


def test_identical_name_products_keep_the_shared_url():
    """Two catalog entries with the SAME name are a genuine duplicate product —
    both legitimately probing to the one page is not a collision to break."""
    rd = [
        {"name": "Produkt Duplikát", "our_images": [], "our_url": None},
        {"name": "Produkt Duplikát", "our_images": [], "our_url": None},
    ]
    shared = "https://www.forestshop.sk/produkt-duplikat/"

    def fetch(c):
        return shared if c == "produkt-duplikat" else None

    resolve_batch(rd, [], fetch)
    assert rd[0]["our_url"] == rd[1]["our_url"] == shared


def test_probed_url_never_displaces_an_already_assigned_product():
    """A product that already has our_url (assigned earlier, e.g. from the
    marketing XML) must never lose it to a newly-probed different product that
    happens to guess the same URL — and the newcomer must not get it either."""
    already = "https://www.forestshop.sk/existing-page/"
    rd = [
        {"name": "Existing Product", "our_images": [], "our_url": already},
        {"name": "Newcomer Product", "our_images": [], "our_url": None},
    ]

    def fetch(c):
        return already if c == "newcomer-product" else None

    resolve_batch(rd, [], fetch)
    assert rd[0]["our_url"] == already          # untouched
    assert rd[1]["our_url"] is None             # never took the taken URL


def test_resolve_batch_counts():
    rd = [
        {"name": "In Sitemap Product", "our_images": [], "our_url": None},
        {"name": "Probed Product", "our_images": [], "our_url": None},
        {"name": "Already Linked", "our_images": [], "our_url": "https://www.forestshop.sk/x/"},
    ]
    sitemap = ["in-sitemap-product"]

    def fetch(c):
        return "https://www.forestshop.sk/probed-product/" if c == "probed-product" else None

    from_sitemap, from_probe, remaining = resolve_batch(rd, sitemap, fetch)
    assert from_sitemap == 1
    assert remaining == 1
    assert from_probe == 1
    assert rd[0]["our_url"] == "https://www.forestshop.sk/in-sitemap-product/"
    assert rd[1]["our_url"] == "https://www.forestshop.sk/probed-product/"


def test_candidates_include_generic_word_stripped_variant():
    """Unchanged behavior — candidates() still generates the stripped variant
    (needed for the disambiguation test above to exercise a real collision)."""
    cs = candidates("Vesta Deerhunter Moor")
    assert "vesta-deerhunter-moor" in cs
    assert "deerhunter-moor" in cs
