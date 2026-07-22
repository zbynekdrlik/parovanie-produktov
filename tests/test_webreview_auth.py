"""Auth suite for webreview (#91): email+password login (Flask session), whole-app
login_required gate, admin-only user management, forgot-password reset tokens
(time-limited + single-use), CSRF on auth forms, login rate-limit, no-plaintext
password storage.

Mirrors tests/test_webreview.py: the app module is imported once (sys.path) and the
per-test fixture monkeypatches the module globals (USERS / RESET_TOKENS /
_login_fails) so each test runs against an isolated tmp user store.
"""
import json
import os
import re
import sys
import time

import pytest
from werkzeug.security import generate_password_hash

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webreview"))
import app as webapp  # noqa: E402

ADMIN = "admin@test.sk"
ADMIN_PW = "admin-heslo-123"
USER = "clen@test.sk"
USER_PW = "clen-heslo-123"


@pytest.fixture
def aclient(tmp_path, monkeypatch):
    """Fresh test client + isolated auth stores seeded with one admin and one
    regular (non-admin) user."""
    users = {
        ADMIN: {"pw_hash": generate_password_hash(ADMIN_PW), "is_admin": True,
                "created_at": "2026-01-01T00:00:00+00:00"},
        USER: {"pw_hash": generate_password_hash(USER_PW), "is_admin": False,
               "created_at": "2026-01-01T00:00:00+00:00"},
    }
    up = tmp_path / "users.json"
    up.write_text(json.dumps(users), encoding="utf-8")
    monkeypatch.setattr(webapp, "USERS", str(up))
    monkeypatch.setattr(webapp, "RESET_TOKENS", str(tmp_path / "reset_tokens.json"))
    monkeypatch.setattr(webapp, "_login_fails", {})
    return webapp.app.test_client()


def _login(c, email, pw):
    """Real login through the form: GET /login primes the CSRF token, POST submits."""
    c.get("/login")
    with c.session_transaction() as s:
        csrf = s["_csrf"]
    return c.post("/login", data={"email": email, "password": pw, "_csrf": csrf})


def _csrf_of(c, path):
    c.get(path)
    with c.session_transaction() as s:
        return s["_csrf"]


# ── login gate ────────────────────────────────────────────────────────────────

def test_anonymous_api_gets_401(aclient):
    r = aclient.get("/api/products")
    assert r.status_code == 401
    assert r.get_json()["error"] == "unauthorized"
    r = aclient.post("/api/decision", json={"key": "X", "status": "good", "url": ""})
    assert r.status_code == 401
    assert aclient.get("/api/orders").status_code == 401
    assert aclient.get("/api/me").status_code == 401


def test_anonymous_page_redirects_to_login(aclient):
    r = aclient.get("/")
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]


def test_public_endpoints_stay_open(aclient):
    assert aclient.get("/api/version").status_code == 200
    assert aclient.get("/login").status_code == 200
    assert aclient.get("/favicon.ico").status_code == 204
    # n8n machine endpoints keep their OWN bearer auth (n8n has no session):
    # no session AND no/wrong bearer → their own 401 JSON, never a login redirect.
    r = aclient.post("/api/n8n/shoptet-import", json={})
    assert r.status_code == 401
    assert r.get_json()["error"] == "unauthorized"


# ── login / logout ────────────────────────────────────────────────────────────

def test_login_ok_sets_session(aclient):
    r = _login(aclient, ADMIN, ADMIN_PW)
    assert r.status_code == 302
    me = aclient.get("/api/me")
    assert me.status_code == 200
    assert me.get_json() == {"email": ADMIN, "is_admin": True}
    assert aclient.get("/api/products").status_code == 200


def test_login_wrong_password_rejected(aclient):
    assert _login(aclient, ADMIN, "zle-heslo").status_code == 401
    assert aclient.get("/api/me").status_code == 401
    assert _login(aclient, "neexistuje@test.sk", "cokolvek1").status_code == 401


def test_login_requires_csrf(aclient):
    r = aclient.post("/login", data={"email": ADMIN, "password": ADMIN_PW})
    assert r.status_code == 400
    assert aclient.get("/api/me").status_code == 401


def test_login_rate_limited_after_failures(aclient):
    for _ in range(webapp.LOGIN_MAX_FAILS):
        _login(aclient, ADMIN, "zle-heslo")
    # even the CORRECT password is refused once the IP window is exhausted
    assert _login(aclient, ADMIN, ADMIN_PW).status_code == 429


def test_session_cookie_flags(aclient):
    r = _login(aclient, ADMIN, ADMIN_PW)
    cookie = r.headers.get("Set-Cookie", "")
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie


def test_no_plaintext_passwords_on_disk(aclient):
    raw = open(webapp.USERS, encoding="utf-8").read()
    assert ADMIN_PW not in raw and USER_PW not in raw
    for rec in json.loads(raw).values():
        assert rec["pw_hash"].startswith(("pbkdf2:", "scrypt:"))


def test_logout_clears_session(aclient):
    _login(aclient, ADMIN, ADMIN_PW)
    assert aclient.get("/api/me").status_code == 200
    r = aclient.post("/logout")
    assert r.status_code == 302
    assert aclient.get("/api/me").status_code == 401


# ── forgot password / reset tokens ────────────────────────────────────────────

@pytest.fixture
def mailbox(monkeypatch):
    """Capture outgoing reset mails (no real SMTP in tests)."""
    sent = []
    monkeypatch.setattr(
        webapp, "_send_mail",
        lambda to, subject, body: sent.append((to, subject, body)) or True)
    return sent


def test_forgot_known_email_creates_token_and_mails(aclient, mailbox):
    csrf = _csrf_of(aclient, "/forgot")
    r = aclient.post("/forgot", data={"email": USER, "_csrf": csrf})
    assert r.status_code == 200
    toks = json.load(open(webapp.RESET_TOKENS, encoding="utf-8"))
    assert len(toks) == 1
    (rec,) = toks.values()
    assert rec["email"] == USER
    assert rec["exp"] > time.time()
    assert len(mailbox) == 1
    to, _subject, body = mailbox[0]
    assert to == USER and "/reset/" in body
    # the RAW token must never be stored — only its hash keys the store
    raw_token = re.search(r"/reset/([\w\-]+)", body).group(1)
    assert raw_token not in toks


def test_forgot_unknown_email_no_token_no_enumeration(aclient, mailbox):
    csrf = _csrf_of(aclient, "/forgot")
    r_known = aclient.post("/forgot", data={"email": USER, "_csrf": csrf})
    csrf = _csrf_of(aclient, "/forgot")
    r_unknown = aclient.post("/forgot", data={"email": "nikto@test.sk", "_csrf": csrf})
    assert r_unknown.status_code == r_known.status_code == 200
    # same user-visible answer for both (no account enumeration)
    assert r_unknown.data == r_known.data
    toks = json.load(open(webapp.RESET_TOKENS, encoding="utf-8"))
    assert all(rec["email"] == USER for rec in toks.values())
    assert all(to == USER for to, _s, _b in mailbox)


def _request_reset_token(aclient, mailbox, email=USER):
    csrf = _csrf_of(aclient, "/forgot")
    aclient.post("/forgot", data={"email": email, "_csrf": csrf})
    return re.search(r"/reset/([\w\-]+)", mailbox[-1][2]).group(1)


def test_reset_flow_changes_password_single_use(aclient, mailbox):
    tok = _request_reset_token(aclient, mailbox)
    assert aclient.get(f"/reset/{tok}").status_code == 200
    csrf = _csrf_of(aclient, f"/reset/{tok}")
    new_pw = "nove-heslo-456"
    r = aclient.post(f"/reset/{tok}",
                     data={"password": new_pw, "password2": new_pw, "_csrf": csrf})
    assert r.status_code == 302
    # old password dead, new password live
    fresh = webapp.app.test_client()
    assert _login(fresh, USER, USER_PW).status_code == 401
    fresh2 = webapp.app.test_client()
    assert _login(fresh2, USER, new_pw).status_code == 302
    # single-use: the token is consumed
    assert aclient.get(f"/reset/{tok}").status_code == 410


def test_reset_expired_token_rejected(aclient, monkeypatch, mailbox):
    tok = _request_reset_token(aclient, mailbox)
    toks = json.load(open(webapp.RESET_TOKENS, encoding="utf-8"))
    for rec in toks.values():
        rec["exp"] = time.time() - 1
    with open(webapp.RESET_TOKENS, "w", encoding="utf-8") as f:
        json.dump(toks, f)
    assert aclient.get(f"/reset/{tok}").status_code == 410
    csrf = _csrf_of(aclient, "/forgot")
    r = aclient.post(f"/reset/{tok}",
                     data={"password": "x-heslo-789", "password2": "x-heslo-789",
                           "_csrf": csrf})
    assert r.status_code == 410
    # password unchanged
    fresh = webapp.app.test_client()
    assert _login(fresh, USER, USER_PW).status_code == 302


def test_reset_short_password_rejected_token_survives(aclient, mailbox):
    tok = _request_reset_token(aclient, mailbox)
    csrf = _csrf_of(aclient, f"/reset/{tok}")
    r = aclient.post(f"/reset/{tok}",
                     data={"password": "abc", "password2": "abc", "_csrf": csrf})
    assert r.status_code == 400
    # token NOT consumed by the failed attempt
    assert aclient.get(f"/reset/{tok}").status_code == 200


# ── admin user management ─────────────────────────────────────────────────────

def test_user_management_forbidden_for_nonadmin(aclient):
    _login(aclient, USER, USER_PW)
    assert aclient.get("/api/users").status_code == 403
    assert aclient.post("/api/users", json={
        "email": "x@test.sk", "password": "heslo-123x", "is_admin": False,
    }).status_code == 403
    assert aclient.post("/api/users/delete", json={"email": ADMIN}).status_code == 403
    assert aclient.post("/api/users/admin",
                        json={"email": USER, "is_admin": True}).status_code == 403
    assert aclient.post("/api/users/password", json={
        "email": ADMIN, "password": "hacknute-1",
    }).status_code == 403


def test_admin_lists_users_without_hashes(aclient):
    _login(aclient, ADMIN, ADMIN_PW)
    r = aclient.get("/api/users")
    assert r.status_code == 200
    users = r.get_json()["users"]
    assert {u["email"] for u in users} == {ADMIN, USER}
    assert "pw_hash" not in r.data.decode()


def test_admin_creates_and_deletes_user(aclient):
    _login(aclient, ADMIN, ADMIN_PW)
    r = aclient.post("/api/users", json={
        "email": "Novy@Test.sk", "password": "nove-heslo-1", "is_admin": False})
    assert r.status_code == 200
    # normalized to lowercase, immediately able to log in
    fresh = webapp.app.test_client()
    assert _login(fresh, "novy@test.sk", "nove-heslo-1").status_code == 302
    # duplicate → 409; garbage email → 400; short password → 400
    assert aclient.post("/api/users", json={
        "email": "novy@test.sk", "password": "ine-heslo-1"}).status_code == 409
    assert aclient.post("/api/users", json={
        "email": "nie-je-email", "password": "nove-heslo-1"}).status_code == 400
    assert aclient.post("/api/users", json={
        "email": "kratke@test.sk", "password": "abc"}).status_code == 400
    r = aclient.post("/api/users/delete", json={"email": "novy@test.sk"})
    assert r.status_code == 200
    emails = {u["email"] for u in aclient.get("/api/users").get_json()["users"]}
    assert "novy@test.sk" not in emails


def test_admin_cannot_delete_or_demote_self(aclient):
    _login(aclient, ADMIN, ADMIN_PW)
    assert aclient.post("/api/users/delete", json={"email": ADMIN}).status_code == 400
    assert aclient.post("/api/users/admin",
                        json={"email": ADMIN, "is_admin": False}).status_code == 400
    assert aclient.get("/api/me").get_json()["is_admin"] is True


def test_admin_toggles_admin_and_sets_password(aclient):
    _login(aclient, ADMIN, ADMIN_PW)
    r = aclient.post("/api/users/admin", json={"email": USER, "is_admin": True})
    assert r.status_code == 200
    users = {u["email"]: u for u in aclient.get("/api/users").get_json()["users"]}
    assert users[USER]["is_admin"] is True
    r = aclient.post("/api/users/password",
                     json={"email": USER, "password": "spravcove-heslo-9"})
    assert r.status_code == 200
    fresh = webapp.app.test_client()
    assert _login(fresh, USER, "spravcove-heslo-9").status_code == 302
    fresh2 = webapp.app.test_client()
    assert _login(fresh2, USER, USER_PW).status_code == 401


def test_deleted_user_session_dies(aclient):
    """A logged-in user whose account the admin deletes loses access on the very
    next request (the gate re-checks the store, not just the cookie)."""
    user_client = webapp.app.test_client()
    _login(user_client, USER, USER_PW)
    assert user_client.get("/api/me").status_code == 200
    _login(aclient, ADMIN, ADMIN_PW)
    assert aclient.post("/api/users/delete", json={"email": USER}).status_code == 200
    assert user_client.get("/api/me").status_code == 401


# ── bootstrap ─────────────────────────────────────────────────────────────────

def test_bootstrap_admin_from_env(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "USERS", str(tmp_path / "users.json"))
    monkeypatch.setenv("ADMIN_EMAIL", "Boss@Test.sk")
    monkeypatch.setenv("ADMIN_PW", "bootstrap-heslo-1")
    webapp._bootstrap_admin()
    users = json.load(open(tmp_path / "users.json", encoding="utf-8"))
    assert users["boss@test.sk"]["is_admin"] is True
    assert users["boss@test.sk"]["pw_hash"].startswith(("pbkdf2:", "scrypt:"))
    first_hash = users["boss@test.sk"]["pw_hash"]
    # re-run (every service restart) must NOT overwrite an existing account
    monkeypatch.setenv("ADMIN_PW", "ine-heslo-2")
    webapp._bootstrap_admin()
    users = json.load(open(tmp_path / "users.json", encoding="utf-8"))
    assert users["boss@test.sk"]["pw_hash"] == first_hash
