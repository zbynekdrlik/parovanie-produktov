"""App-brand lock (#120): the UI brand + page <title> read "Forestshop", not the
interim "Forestaci" (#94, since reverted). These assert the app's OWN display name
only — references to the real eshop forestshop.sk elsewhere are intentionally untouched.

The index page ("/") is behind the login gate (#91) → served via a logged-in
session; the login/auth shell ("/login") is a public endpoint → served anonymously.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webreview"))
import app as webapp  # noqa: E402

from tests.conftest import authed_client as _client  # noqa: E402 — logged-in session (#91)


def test_index_brand_and_title_are_forestshop():
    html = _client().get("/").get_data(as_text=True)
    # Sidebar brand text (app's own name) is back to Forestshop…
    assert '<span class="brandtxt">Forestshop' in html
    # …and the interim "Forestaci" brand is gone (locks the revert — reverting fails this).
    assert '<span class="brandtxt">Forestaci' not in html
    # Page <title> is the GENERIC app name (#175) — the tool grew into a whole
    # system, so the per-view "Kontrola párovania …" title was dropped.
    assert "<title>Forestshop</title>" in html
    assert "Kontrola párovania" not in html.split("</title>")[0]


def test_login_shell_brand_is_forestshop():
    html = webapp.app.test_client().get("/login").get_data(as_text=True)
    assert "<b>Forestshop" in html
    assert "<b>Forestaci" not in html
    # Auth-shell <title> ends with the app name.
    assert "— Forestshop</title>" in html
