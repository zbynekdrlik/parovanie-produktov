"""App-brand lock (#94): the UI brand + page <title> read "Forestaci", not the
old "Forestshop". These assert the app's OWN display name only — references to the
real eshop forestshop.sk elsewhere are intentionally untouched.

The index page ("/") is behind the login gate (#91) → served via a logged-in
session; the login/auth shell ("/login") is a public endpoint → served anonymously.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webreview"))
import app as webapp  # noqa: E402

from tests.conftest import authed_client as _client  # noqa: E402 — logged-in session (#91)


def test_index_brand_and_title_are_forestaci():
    html = _client().get("/").get_data(as_text=True)
    # Sidebar brand text (app's own name) is rebranded…
    assert '<span class="brandtxt">Forestaci' in html
    # …and the old app brand is gone (locks the rebrand — reverting fails this).
    assert '<span class="brandtxt">Forestshop' not in html
    # Page <title> carries the new app name.
    assert "<title>Kontrola párovania — Forestaci</title>" in html


def test_login_shell_brand_is_forestaci():
    html = webapp.app.test_client().get("/login").get_data(as_text=True)
    assert "<b>Forestaci" in html
    assert "<b>Forestshop" not in html
    # Auth-shell <title> ends with the new app name.
    assert "— Forestaci</title>" in html
