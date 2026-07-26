"""Shared backend-test plumbing. The webreview app (imported as `app` by the web
test files) sits behind a whole-app login gate since #91 — the legacy suites keep
their exact pre-auth behavior by running as a real logged-in session:

- `authed_client()` returns a test client whose session carries TEST_USER;
- the autouse fixture below points the app's user store at a tmp file seeded with
  that user (the gate re-validates the session against the store on EVERY request,
  so the session alone would not be enough — and the real data/out/users.json must
  never be touched by tests).

#261 — HARD ISOLATION OF THE LIVE DATA DIR. The block below runs at conftest IMPORT
time, i.e. before pytest collects (and therefore before any test module imports the
app), and points `WEBREVIEW_OUT` + `WEBREVIEW_PRODUCTS` at a throwaway directory for
the WHOLE backend suite. It is the belt to the app-side braces (paths derived from
the current OUT): even a test helper that forgets to patch anything at all now writes
into a temp dir instead of over the manager's live work. On 2026-07-26 a helper that
patched `OUT` but not the frozen `DECISIONS` wiped all 2831 review decisions; nothing
in a test run may be able to reach data/out again.
"""
import atexit
import json
import os
import shutil
import sys
import tempfile

import pytest

_TEST_DATA = tempfile.mkdtemp(prefix="webreview-tests-data-")
_TEST_OUT = os.path.join(_TEST_DATA, "out")
os.makedirs(_TEST_OUT, exist_ok=True)
os.environ["WEBREVIEW_OUT"] = _TEST_OUT
# `run_shoptet_sync` rewrites the export in place — keep the real data/products.csv
# out of reach too, and make the dev box behave like CI (which has no data/ tree).
# It sits BESIDE the out dir, exactly as in production (data/products.csv + data/out/).
os.environ["WEBREVIEW_PRODUCTS"] = os.path.join(_TEST_DATA, "products.csv")
atexit.register(shutil.rmtree, _TEST_DATA, ignore_errors=True)

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
