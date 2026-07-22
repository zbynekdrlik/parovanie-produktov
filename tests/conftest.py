"""Shared backend-test plumbing. The webreview app (imported as `app` by the web
test files) sits behind a whole-app login gate since #91 — the legacy suites keep
their exact pre-auth behavior by running as a real logged-in session:

- `authed_client()` returns a test client whose session carries TEST_USER;
- the autouse fixture below points the app's user store at a tmp file seeded with
  that user (the gate re-validates the session against the store on EVERY request,
  so the session alone would not be enough — and the real data/out/users.json must
  never be touched by tests).
"""
import json
import sys

import pytest

TEST_USER = "tester@example.com"


def authed_client():
    """Flask test client with a real logged-in session (legacy pre-#91 suites)."""
    import app as webapp  # webreview/app.py — the web test files put it on sys.path
    c = webapp.app.test_client()
    with c.session_transaction() as s:
        s["user"] = TEST_USER
    return c


@pytest.fixture(autouse=True)
def _webreview_user_store(tmp_path, monkeypatch):
    """Isolate + seed the user store whenever the webreview module is loaded.
    pw_hash is never checked for an already-established session, so a dummy
    value keeps the fixture fast (no per-test password hashing)."""
    webapp = sys.modules.get("app")
    if webapp is None or not hasattr(webapp, "USERS"):
        yield
        return
    p = tmp_path / "users.json"
    p.write_text(json.dumps({TEST_USER: {
        "pw_hash": "!", "is_admin": True,
        "created_at": "2026-01-01T00:00:00+00:00"}}), encoding="utf-8")
    monkeypatch.setattr(webapp, "USERS", str(p))
    yield
