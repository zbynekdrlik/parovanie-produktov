"""Web na ručnú kontrolu párovania: vľavo náš produkt, vpravo dodávateľ,
fajka/krížik (matched) alebo ručný výber/URL (unmatched). Rozhodnutia sa
ukladajú do data/out/decisions.json.

Run: PYTHONPATH=src .venv/bin/python webreview/app.py   (počúva na 0.0.0.0:8799)
"""
from __future__ import annotations
import csv
import hmac
import io
import json
import logging
import os
import re
import hashlib
import secrets
import signal
import smtplib
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.utils import formataddr
from urllib.parse import quote, urljoin

import requests
from bs4 import BeautifulSoup
from flask import (Flask, Response, jsonify, redirect, render_template, request,
                   send_from_directory, session)
from werkzeug.security import check_password_hash, generate_password_hash

from parovanie import (
    __version__, config, import_builder, orders_reminder, posta_uncollected,
    restock_skladom, riziko_vypadku, supplier_stock)
from parovanie.automation_runner import Automation, AutomationRunner
from parovanie.catalog_index import (
    build_catalog_index, build_promoted_entry, search_catalog, supplier_from_url)
from parovanie.export_helpers import current_of, resync_current
from parovanie.shoptet_import import parse_import_log

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Data dir is env-overridable so tests/E2E can boot the app against a fixture.
OUT = os.environ.get("WEBREVIEW_OUT") or os.path.join(ROOT, "data", "out")
DATA = os.path.join(OUT, "review_data.json")
DECISIONS = os.path.join(OUT, "decisions.json")
IMGCACHE = os.path.join(OUT, "imgcache")
os.makedirs(IMGCACHE, exist_ok=True)

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
app = Flask(__name__, static_folder="static", template_folder="templates")
_lock = threading.Lock()
_import_lock = threading.Lock()   # one Shoptet import at a time (browser automation)
CRED_PATH = os.environ.get("SHOPTET_CRED") or os.path.join(ROOT, "data", ".shoptet_admin")
IMPORT_SCRIPT = os.path.join(ROOT, "scripts", "shoptet_import.py")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("webreview")
log.info("starting webreview v%s", __version__)

try:
    with open(DATA, encoding="utf-8") as f:
        PRODUCTS = json.load(f)
    log.info("loaded %d products from %s", len(PRODUCTS), DATA)
except FileNotFoundError:
    PRODUCTS = []
    log.warning("review data missing: %s — starting with 0 products", DATA)

# ONE cp1250 pass over the Shoptet export builds BOTH:
#   CODE2PAIR — code -> pairCode (the Shoptet import needs both present), and
#   CATALOG   — the catalog-wide search index grouped per pairCode (canonical
#               build_catalog_index), powering /api/search + promote-on-pair.
SRC = os.environ.get("WEBREVIEW_PRODUCTS") or os.path.join(ROOT, "data", "products.csv")


def _load_catalog(path, review_keys):
    """Single cp1250 pass over the Shoptet export → (code2pair, catalog). Missing
    export → ({}, {}) (the app already tolerates a dataless boot). `rows` is held only
    for the duration of the build, then released."""
    code2pair: dict = {}
    rows: list = []
    if not os.path.exists(path):
        return code2pair, {}
    csv.field_size_limit(10**9)
    with open(path, encoding="cp1250", errors="replace") as _f:
        for _row in csv.DictReader(_f, delimiter=";"):
            _c = (_row.get("code") or "").strip()
            if _c:
                code2pair[_c] = (_row.get("pairCode") or "").strip()
            rows.append(_row)
    return code2pair, build_catalog_index(rows, review_keys)


# review_keys is the COVERAGE set that marks a catalog entry in_review. The index is
# grouped by entry KEY = pairCode-or-code (single-variant products have an EMPTY pairCode
# → keyed by their own code). Most review entries are keyed "SUPPLIER|pairCode" (e.g.
# GRUBE|425), so we cannot collect `key` (C1 — every such product wrongly not-in-review).
# We collect the BARE pairCodes PLUS every variant code; build_catalog_index marks
# in_review via key-or-any-variant-code membership, so a single-variant reviewed product
# (empty pairCode) still matches by its code.
_review_cover = ({p.get("pairCode") for p in PRODUCTS if p.get("pairCode")}
                 | {c for p in PRODUCTS for c in (p.get("variant_codes") or [])})
CODE2PAIR, CATALOG = _load_catalog(SRC, _review_cover)
log.info("catalog: %d products indexed (%d codes) from %s", len(CATALOG), len(CODE2PAIR), SRC)


def _load_decisions() -> dict:
    if os.path.exists(DECISIONS):
        with open(DECISIONS, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_decisions(d: dict) -> None:
    tmp = DECISIONS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DECISIONS)


# --------------------------------------------------------------------------- #
# Auth (#91): email+password login (Flask session), user store, reset tokens.
# The WHOLE app + every /api/* endpoint sits behind the login gate; the only
# public surface is /login, /forgot, /reset/<token>, static assets, /favicon,
# /api/version (login-page footer) and the /api/n8n/* machine endpoints (those
# carry their OWN bearer auth — n8n has no session).
# --------------------------------------------------------------------------- #


def _load_env_file(path):
    """KEY=VALUE lines from a gitignored creds file → os.environ DEFAULTS (a real
    env var always wins). Lets the systemd service keep auth/mail config in
    data/.auth_env + data/.mail_env (chmod 600) instead of unit files."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


_load_env_file(os.path.join(ROOT, "data", ".auth_env"))
_load_env_file(os.path.join(ROOT, "data", ".mail_env"))
# #106 — OPENAI_API_KEY for the „Dodávateľský sklad" scraper's LLM fallback.
# Gitignored, chmod 600; absent = LLM fallback degrades gracefully (static-only).
_load_env_file(os.path.join(ROOT, "data", ".ai_env"))
# #115 — GITHUB_TOKEN + GITHUB_REPO for the „Vývoj" tab (list issues) + the idea
# lightbulb (create issue). Gitignored, chmod 600; absent = the tab + lightbulb
# degrade gracefully („GitHub nedostupný"), never crash. The token is a repo-write
# credential — it lives ONLY in the server-side Authorization header (never sent to
# the browser, never in a URL or log).
_load_env_file(os.path.join(ROOT, "data", ".gh_env"))


def _secret_key():
    """Stable session-signing key: SECRET_KEY env (data/.auth_env) wins; else a
    generated key persisted in OUT/.auth_secret (0600), so sessions survive
    restarts even with zero config (a fresh key each boot would log everyone
    out on every deploy)."""
    env = os.environ.get("SECRET_KEY")
    if env:
        return env
    path = os.path.join(OUT, ".auth_secret")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            key = f.read().strip()
        if key:
            return key
    key = secrets.token_hex(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(key)
    log.info("auth: generated new session secret at %s", path)
    return key


app.secret_key = _secret_key()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    # The public site is https (Cloudflare tunnel) → AUTH_COOKIE_SECURE=1 lives
    # in data/.auth_env there; plain-http dev/E2E boots leave it off.
    SESSION_COOKIE_SECURE=os.environ.get("AUTH_COOKIE_SECURE") == "1",
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)

USERS = os.path.join(OUT, "users.json")          # email -> {pw_hash,is_admin,created_at}
RESET_TOKENS = os.path.join(OUT, "reset_tokens.json")   # sha256(token) -> {email,exp}
RESET_TTL = 2 * 3600                             # reset link validity: 2 hours
PW_MIN_LEN = 8
LOGIN_MAX_FAILS = 5                              # failed logins per IP…
LOGIN_WINDOW = 15 * 60                           # …within 15 minutes → 429
_login_fails: dict = {}                          # ip -> [fail timestamps]
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Burned on login attempts for UNKNOWN emails so they cost the same as a wrong
# password (no account enumeration via response latency).
_DUMMY_HASH = generate_password_hash(secrets.token_hex(16))


def _load_users() -> dict:
    if os.path.exists(USERS):
        with open(USERS, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_json_0600(path, d) -> None:
    """Atomic write with 0600 perms — users.json holds password hashes and
    reset_tokens.json holds live reset-token hashes."""
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _save_users(d: dict) -> None:
    _save_json_0600(USERS, d)


def _load_reset_tokens() -> dict:
    if os.path.exists(RESET_TOKENS):
        with open(RESET_TOKENS, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_reset_tokens(d: dict) -> None:
    _save_json_0600(RESET_TOKENS, d)


def _norm_email(e) -> str:
    return (e or "").strip().lower()


def _bootstrap_admin() -> None:
    """First-run admin from ADMIN_EMAIL/ADMIN_PW (data/.auth_env), so the manager
    is never locked out after a deploy. Creates the account only when missing —
    a password changed later in the UI is NEVER overwritten by a restart."""
    email = _norm_email(os.environ.get("ADMIN_EMAIL"))
    pw = os.environ.get("ADMIN_PW") or ""
    if not email or not pw:
        return
    with _lock:
        users = _load_users()
        if email in users:
            return
        users[email] = {
            "pw_hash": generate_password_hash(pw), "is_admin": True,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        _save_users(users)
    log.info("auth: bootstrapped admin %s from env", email)


_bootstrap_admin()


def _current_user():
    """Session → live user record. Re-checks the store on EVERY request, so a
    deleted user (or a stale cookie) loses access immediately."""
    email = session.get("user")
    if not email:
        return None
    u = _load_users().get(email)
    if not u:
        return None
    return {"email": email, "is_admin": bool(u.get("is_admin"))}


_PUBLIC_ENDPOINTS = {"login", "forgot_password", "reset_password", "favicon",
                     "api_version", "static", "static_files"}


@app.before_request
def _require_login():
    """Default-deny login gate: every endpoint (present and future) is protected
    unless explicitly public. /api/n8n/* keep their own bearer auth."""
    if request.endpoint in _PUBLIC_ENDPOINTS or request.path.startswith("/api/n8n/"):
        return None
    if _current_user():
        return None
    log.info("auth: unauthenticated %s %s from %s", request.method, request.path,
             _client_ip())
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    nxt = request.full_path if request.query_string else request.path
    return redirect("/login?next=" + quote(nxt))


def _csrf_token() -> str:
    tok = session.get("_csrf")
    if not tok:
        tok = secrets.token_hex(16)
        session["_csrf"] = tok
    return tok


def _csrf_ok() -> bool:
    tok = session.get("_csrf") or ""
    sent = request.form.get("_csrf") or ""
    # compare BYTES — compare_digest raises TypeError on non-ASCII str, and the
    # form value is attacker-controlled (must yield 400, never a 500)
    return bool(tok) and hmac.compare_digest(tok.encode(), sent.encode())


def _rate_limited(ip) -> bool:
    now = time.time()
    fails = [t for t in _login_fails.get(ip, []) if now - t < LOGIN_WINDOW]
    if fails:
        _login_fails[ip] = fails
    else:
        _login_fails.pop(ip, None)   # no lingering entry per visitor IP
    return len(fails) >= LOGIN_MAX_FAILS


def _note_fail(ip) -> None:
    _login_fails.setdefault(ip, []).append(time.time())


def _safe_next(nxt) -> str:
    """Post-login redirect target: same-site paths only (no open redirect)."""
    if nxt and nxt.startswith("/") and not nxt.startswith("//"):
        return nxt
    return "/"


def _login_page(error=None, status=200):
    return render_template(
        "login.html", csrf=_csrf_token(), version=__version__, error=error,
        nxt=request.values.get("next", ""),
        reset_done=request.args.get("reset") == "1"), status


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        if _current_user():
            return redirect("/")
        return _login_page()
    ip = _client_ip()
    if not _csrf_ok():
        log.warning("auth: login with bad/missing CSRF from %s", ip)
        return _login_page("Neplatná relácia — skús to znova.", 400)
    if _rate_limited(ip):
        log.warning("auth: login rate-limited ip=%s", ip)
        return _login_page("Priveľa pokusov — počkaj 15 minút a skús znova.", 429)
    email = _norm_email(request.form.get("email"))
    pw = request.form.get("password") or ""
    u = _load_users().get(email)
    # unknown email verifies against a dummy hash → same cost as a wrong
    # password (no enumeration via timing); malformed stored hash → False
    if not check_password_hash((u or {}).get("pw_hash") or _DUMMY_HASH, pw) or not u:
        _note_fail(ip)
        log.warning("auth: failed login email=%s ip=%s", email, ip)
        return _login_page("Nesprávny e-mail alebo heslo.", 401)
    session.clear()
    session["user"] = email
    session["_csrf"] = secrets.token_hex(16)   # fresh token for the fresh session
    session.permanent = True
    log.info("auth: login ok %s ip=%s", email, ip)
    return redirect(_safe_next(request.form.get("next")))


@app.route("/logout", methods=["POST"])
def logout():
    email = session.get("user")
    session.clear()
    log.info("auth: logout %s", email)
    return redirect("/login")


@app.route("/api/me")
def api_me():
    return jsonify(_current_user())   # the login gate guarantees a user here


def _send_mail(to, subject, body) -> bool:
    """Plain-text mail via SMTP from data/.mail_env (MAIL_HOST/PORT/USER/PASS/FROM).
    Unconfigured or failing SMTP is LOGGED and reported False — the forgot page
    never 500s and never leaks whether a mail actually left.

    Always BCCs MAIL_BCC (data/.mail_env) when set — the "BCC vždy" convention
    (Marek, comment on #105/#126/#127): every mail the app sends is BCC'd to
    the owner. _send_mail_html already applies this; #127 closed the gap for
    this (reset-password) path. bcc is envelope-only (no header), matching
    _send_mail_html."""
    bcc = os.environ.get("MAIL_BCC") or None
    host = os.environ.get("MAIL_HOST")
    if not host:
        log.error("auth: SMTP not configured (data/.mail_env) — mail to %s NOT sent", to)
        return False
    try:
        # config parsing INSIDE the try: a malformed MAIL_PORT in .mail_env must
        # log-and-degrade like any other send failure, never 500 the forgot page
        port = int(os.environ.get("MAIL_PORT", "587"))
        user = os.environ.get("MAIL_USER", "")
        pw = os.environ.get("MAIL_PASS", "")
        sender = os.environ.get("MAIL_FROM") or user
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = to
        rcpt = [to] + ([bcc] if bcc else [])
        if port == 465:
            smtp = smtplib.SMTP_SSL(host, port, timeout=20)
        else:
            smtp = smtplib.SMTP(host, port, timeout=20)
            smtp.starttls()
        if user:
            smtp.login(user, pw)
        smtp.sendmail(sender, rcpt, msg.as_string())
        smtp.quit()
        log.info("auth: reset mail sent to %s (bcc %s) via %s:%s", to, bcc or "-", host, port)
        return True
    except Exception as e:  # noqa: BLE001 — log full context + degrade, never 500
        log.error("auth: SMTP send to %s via %s:%s failed: %r",
                  to, host, os.environ.get("MAIL_PORT", "587"), e)
        return False


def _send_mail_html(to, subject, html_body, bcc=None) -> bool:
    """HTML mail via the same SMTP config (data/.mail_env) as _send_mail — used
    by the automations (#93 customer notifications). Sender defaults to the
    SMTP account's MAIL_FROM; POSTA_MAIL_FROM (data/.mail_env) overrides it if
    the SMTP server allows the eshop@ alias the old n8n workflow used. bcc is
    envelope-only (no header), matching the n8n emailSend behavior.

    bcc defaults to MAIL_BCC (data/.mail_env) when the caller doesn't pass one
    — the "BCC vždy" convention (Marek, comment on #105/#126): every
    automation e-mail is BCC'd to the owner. Pass bcc="" explicitly to opt a
    specific send out of that default. Failure is logged + False — the
    automation records it and retries next run."""
    if bcc is None:
        bcc = os.environ.get("MAIL_BCC") or None
    host = os.environ.get("MAIL_HOST")
    if not host:
        log.error("mail: SMTP not configured (data/.mail_env) — mail '%s' to %s NOT sent",
                  subject, to)
        return False
    try:
        port = int(os.environ.get("MAIL_PORT", "587"))
        user = os.environ.get("MAIL_USER", "")
        pw = os.environ.get("MAIL_PASS", "")
        sender = (os.environ.get("POSTA_MAIL_FROM")
                  or os.environ.get("MAIL_FROM") or user)
        msg = MIMEText(html_body, "html", "utf-8")
        msg["Subject"] = subject
        msg["From"] = formataddr(("Forestshop.sk", sender))
        msg["To"] = to
        rcpt = [to] + ([bcc] if bcc else [])
        if port == 465:
            smtp = smtplib.SMTP_SSL(host, port, timeout=20)
        else:
            smtp = smtplib.SMTP(host, port, timeout=20)
            smtp.starttls()
        if user:
            smtp.login(user, pw)
        smtp.sendmail(sender, rcpt, msg.as_string())
        smtp.quit()
        log.info("mail: sent '%s' to %s (bcc %s) via %s:%s", subject, to,
                 bcc or "-", host, port)
        return True
    except Exception as e:  # noqa: BLE001 — log full context + degrade, never crash the run
        log.error("mail: send '%s' to %s via %s:%s failed: %r",
                  subject, to, host, os.environ.get("MAIL_PORT", "587"), e)
        return False


def _base_url() -> str:
    """Absolute base for reset links: APP_BASE_URL (data/.auth_env — the public
    tunnel URL) wins; request.url_root is the dev/test fallback."""
    return (os.environ.get("APP_BASE_URL") or request.url_root).rstrip("/")


def _forgot_page(sent, error=None, status=200):
    return render_template("forgot.html", csrf=_csrf_token(), version=__version__,
                           sent=sent, error=error), status


@app.route("/forgot", methods=["GET", "POST"])
def forgot_password():
    if request.method == "GET":
        return _forgot_page(sent=False)
    ip = _client_ip()
    if not _csrf_ok():
        log.warning("auth: forgot with bad/missing CSRF from %s", ip)
        return _forgot_page(sent=False, error="Neplatná relácia — skús to znova.",
                            status=400)
    if _rate_limited(ip):   # shares the login fail budget — brakes mail-bombing too
        log.warning("auth: forgot rate-limited ip=%s", ip)
        return _forgot_page(sent=False,
                            error="Priveľa pokusov — počkaj 15 minút.", status=429)
    email = _norm_email(request.form.get("email"))
    if email in _load_users():
        token = secrets.token_urlsafe(32)
        th = hashlib.sha256(token.encode()).hexdigest()
        now = time.time()
        with _lock:
            toks = {k: v for k, v in _load_reset_tokens().items()
                    if v.get("exp", 0) > now}        # purge expired on the way
            toks[th] = {"email": email, "exp": now + RESET_TTL}
            _save_reset_tokens(toks)
        link = _base_url() + "/reset/" + token
        sent_ok = _send_mail(
            email, "Obnova hesla — Párovanie Forestshop",
            "Na nastavenie nového hesla klikni na tento odkaz (platí 2 hodiny a "
            f"funguje iba raz):\n\n{link}\n\nAk si o obnovu hesla nežiadal, tento "
            "e-mail ignoruj — heslo sa nemení.")
        log.info("auth: reset token issued for %s ip=%s mail_sent=%s",
                 email, ip, sent_ok)
    else:
        _note_fail(ip)   # unknown-email probing eats the same budget
        log.info("auth: forgot for unknown email=%s ip=%s", email, ip)
    # identical answer whether the account exists or not (no enumeration)
    return _forgot_page(sent=True)


def _reset_page(valid, token, error=None, status=200):
    return render_template("reset.html", valid=valid, token=token,
                           csrf=_csrf_token(), version=__version__,
                           error=error), status


@app.route("/reset/<token>", methods=["GET", "POST"])
def reset_password(token):
    th = hashlib.sha256(token.encode()).hexdigest()
    rec = _load_reset_tokens().get(th)
    if not rec or rec.get("exp", 0) < time.time():
        log.info("auth: reset with invalid/expired token from %s", _client_ip())
        return _reset_page(valid=False, token="", status=410)
    if request.method == "GET":
        return _reset_page(valid=True, token=token)
    if not _csrf_ok():
        return _reset_page(valid=True, token=token,
                           error="Neplatná relácia — skús to znova.", status=400)
    pw = request.form.get("password") or ""
    pw2 = request.form.get("password2") or ""
    if len(pw) < PW_MIN_LEN:
        return _reset_page(valid=True, token=token,
                           error=f"Heslo musí mať aspoň {PW_MIN_LEN} znakov.",
                           status=400)
    if pw != pw2:
        return _reset_page(valid=True, token=token,
                           error="Heslá sa nezhodujú.", status=400)
    with _lock:
        toks = _load_reset_tokens()
        rec = toks.pop(th, None)                     # single-use: consume NOW
        if not rec or rec.get("exp", 0) < time.time():
            return _reset_page(valid=False, token="", status=410)
        _save_reset_tokens(toks)
        users = _load_users()
        u = users.get(rec["email"])
        if u:
            u["pw_hash"] = generate_password_hash(pw)
            _save_users(users)
    log.info("auth: password reset completed for %s from %s",
             rec["email"], _client_ip())
    return redirect("/login?reset=1")


# ── admin user management (sekcia „Užívatelia") ──────────────────────────────


def _admin_or_none():
    u = _current_user()
    return u if (u and u["is_admin"]) else None


def _forbidden():
    log.warning("auth: non-admin %s denied on %s", session.get("user"), request.path)
    return jsonify({"ok": False, "error": "forbidden"}), 403


@app.route("/api/users", methods=["GET", "POST"])
def api_users():
    me = _admin_or_none()
    if not me:
        return _forbidden()
    if request.method == "GET":
        return jsonify({"users": [
            {"email": e, "is_admin": bool(r.get("is_admin")),
             "created_at": r.get("created_at", "")}
            for e, r in sorted(_load_users().items())]})
    d = request.get_json(silent=True) or {}
    email = _norm_email(d.get("email"))
    pw = d.get("password") or ""
    if not _EMAIL_RE.match(email):
        return jsonify({"ok": False, "error": "neplatný e-mail"}), 400
    if len(pw) < PW_MIN_LEN:
        return jsonify({"ok": False,
                        "error": f"heslo musí mať aspoň {PW_MIN_LEN} znakov"}), 400
    with _lock:
        users = _load_users()
        if email in users:
            return jsonify({"ok": False, "error": "používateľ už existuje"}), 409
        users[email] = {
            "pw_hash": generate_password_hash(pw),
            "is_admin": bool(d.get("is_admin")),
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        _save_users(users)
    log.info("auth: %s created user %s (admin=%s)",
             me["email"], email, bool(d.get("is_admin")))
    return jsonify({"ok": True})


@app.route("/api/users/delete", methods=["POST"])
def api_users_delete():
    me = _admin_or_none()
    if not me:
        return _forbidden()
    email = _norm_email((request.get_json(silent=True) or {}).get("email"))
    if email == me["email"]:
        # also guarantees ≥1 admin always remains (you can't remove yourself)
        return jsonify({"ok": False, "error": "nemôžeš zmazať vlastný účet"}), 400
    with _lock:
        users = _load_users()
        if email not in users:
            return jsonify({"ok": False, "error": "používateľ neexistuje"}), 404
        del users[email]
        _save_users(users)
    log.info("auth: %s deleted user %s", me["email"], email)
    return jsonify({"ok": True})


@app.route("/api/users/admin", methods=["POST"])
def api_users_admin():
    me = _admin_or_none()
    if not me:
        return _forbidden()
    d = request.get_json(silent=True) or {}
    email = _norm_email(d.get("email"))
    is_admin = bool(d.get("is_admin"))
    if email == me["email"] and not is_admin:
        # self-demotion off → the last admin can never disappear
        return jsonify({"ok": False,
                        "error": "nemôžeš odobrať admina sám sebe"}), 400
    with _lock:
        users = _load_users()
        if email not in users:
            return jsonify({"ok": False, "error": "používateľ neexistuje"}), 404
        users[email]["is_admin"] = is_admin
        _save_users(users)
    log.info("auth: %s set admin=%s for %s", me["email"], is_admin, email)
    return jsonify({"ok": True})


@app.route("/api/users/password", methods=["POST"])
def api_users_password():
    me = _admin_or_none()
    if not me:
        return _forbidden()
    d = request.get_json(silent=True) or {}
    email = _norm_email(d.get("email"))
    pw = d.get("password") or ""
    if len(pw) < PW_MIN_LEN:
        return jsonify({"ok": False,
                        "error": f"heslo musí mať aspoň {PW_MIN_LEN} znakov"}), 400
    with _lock:
        users = _load_users()
        if email not in users:
            return jsonify({"ok": False, "error": "používateľ neexistuje"}), 404
        users[email]["pw_hash"] = generate_password_hash(pw)
        _save_users(users)
    log.info("auth: %s set new password for %s", me["email"], email)
    return jsonify({"ok": True})


# Per-line "objednané" state for the Na-objednanie tab (key = '<orderCode>|<itemCode>').
ORDERED = os.path.join(OUT, "ordered_items.json")


def _load_ordered() -> dict:
    if os.path.exists(ORDERED):
        with open(ORDERED, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_ordered(d: dict) -> None:
    tmp = ORDERED + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, ORDERED)


# Inline pairings entered on the Na-objednanie tab: {forestshop_code: supplier_url}.
# Lets the manager paste a reorder URL straight onto an order line he's ordering —
# covers ANY ordered code, not only the review-dataset subset (decisions.json). Same
# safe load/save as ordered/decisions; NEVER pruned (an order code may be outside the
# review set, so a prune would wrongly drop it). Gitignored data/out → survives deploy.
ORDER_PAIRINGS = os.path.join(OUT, "order_pairings.json")


def _load_order_pairings() -> dict:
    if os.path.exists(ORDER_PAIRINGS):
        with open(ORDER_PAIRINGS, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_order_pairings(d: dict) -> None:
    tmp = ORDER_PAIRINGS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, ORDER_PAIRINGS)


# Per-line "čaká sa" flag (key='<orderCode>|<itemCode>'): an ACTIVE order line that
# can't be stocked yet — waiting on the supplier, batching more items, or deferred by
# agreement with the customer. Independent of "objednané". Same safe gitignored store;
# NEVER pruned → survives deploy.
WAITING = os.path.join(OUT, "waiting_items.json")


def _load_waiting() -> dict:
    if os.path.exists(WAITING):
        with open(WAITING, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_waiting(d: dict) -> None:
    tmp = WAITING + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, WAITING)


# Per-line "skladom" / "nedostupné" flags (key='<orderCode>|<itemCode>') — two more
# independent to-order markers, same shape as ordered/waiting. "skladom" = we already
# have it in stock / it's been restocked; "nedostupné" = the supplier can't deliver it.
# The manager toggles each on its own; a row can carry any combination. Same safe
# gitignored stores; NEVER pruned → survive deploy.
INSTOCK = os.path.join(OUT, "instock_items.json")
UNAVAIL = os.path.join(OUT, "unavailable_items.json")


def _load_instock() -> dict:
    try:
        with open(INSTOCK, encoding="utf-8") as f:
            d = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return d if isinstance(d, dict) else {}


def _save_instock(d: dict) -> None:
    tmp = INSTOCK + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, INSTOCK)


def _load_unavailable() -> dict:
    try:
        with open(UNAVAIL, encoding="utf-8") as f:
            d = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return d if isinstance(d, dict) else {}


def _save_unavailable(d: dict) -> None:
    tmp = UNAVAIL + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, UNAVAIL)


# Free-form notes for the "📝 Poznámky" tab — a Discord replacement for ad-hoc
# reminders ("objednať na výmenu betelavo", "pridať spreje do roy"). A plain list of
# {id, text, done, ts}, newest-first. Same safe gitignored store, atomic save, tolerant
# of a missing/corrupt file. NOT written to any CSV/import → no formula-injection guard
# needed, just a length cap on the free text.
NOTES = os.path.join(OUT, "notes.json")
NOTE_MAX_LEN = 5000


def _load_notes() -> list:
    try:
        with open(NOTES, encoding="utf-8") as f:
            d = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    return d if isinstance(d, list) else []


def _save_notes(d: list) -> None:
    tmp = NOTES + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, NOTES)


# Supplier assigned on the Na-objednanie tab for an order line that arrived WITHOUT a
# supplier: {forestshop_code: supplier_name}. Keyed by code (a property of the product,
# like order_pairings) so it applies across every order line of that product and is the
# natural key for the eshop write-back. Same safe gitignored store; NEVER pruned →
# survives deploy. Written back to the eshop `supplier` field by the nightly upload.
SUPPLIER_ASSIGN = os.path.join(OUT, "supplier_assignments.json")


def _load_supplier_assign() -> dict:
    if os.path.exists(SUPPLIER_ASSIGN):
        with open(SUPPLIER_ASSIGN, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_supplier_assign(d: dict) -> None:
    tmp = SUPPLIER_ASSIGN + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, SUPPLIER_ASSIGN)


# GRUBE per-size externalCode store (durable, built by scripts/build_grube_codes.py):
# {code: {itemId, size, deUrl, productId}}. Read-only here — feeds the externalCode
# write-back CSV. Missing/corrupt → {} (the file may not exist until the first gather).
GRUBE_CODES = os.path.join(OUT, "grube_codes.json")


def _load_grube_codes() -> dict:
    try:
        with open(GRUBE_CODES, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _attach_grube(r, store=None):
    """Attach the GRUBE per-size order code + grube.de link to an order row, keyed by
    its forestshop variant code (r['itemCode']). Mutates and returns r so it's both a
    tiny unit-testable helper and usable inline in the api_orders loop.

    - r['grubeItemId'] = the per-size grube itemId (copyable code) or '' (non-grube /
      unmatched line — most rows).
    - r['grubeDeUrl']  = the grube.de order link, but ONLY if it is https:// (it lands
      in an <a href> on the client; a non-https value is dropped server-side so a
      javascript:/data:/http url can never reach the DOM).

    `store` (the grube_codes map) may be passed once per request; else loaded here."""
    if store is None:
        store = _load_grube_codes()
    g = store.get((r.get("itemCode") or "").strip()) or {}
    r["grubeItemId"] = str(g.get("itemId", "") or "")
    de = str(g.get("deUrl", "") or "")
    r["grubeDeUrl"] = de if de.startswith("https://") else ""
    return r


# at startup, prune orphan decisions whose key matches no product (e.g. a stale
# 'None'/'bad' from before stable keys) so the progress count == the import count
_VALID_KEYS = {p.get("key") for p in PRODUCTS}
with _lock:
    _d0 = _load_decisions()
    _d1 = {k: v for k, v in _d0.items() if k in _VALID_KEYS}
    if len(_d1) != len(_d0):
        log.info("pruned %d orphan decisions at startup", len(_d0) - len(_d1))
        _save_decisions(_d1)


_IMG_NOISE = ("logo", "/producer/", ".svg", "/svg/", "placeholder", "no-image",
              "banner", "/img/m/")  # m/ = presta related-product thumbs


def _extract_images(html: str, base: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    imgs: list[str] = []

    def add(s):
        if not s:
            return
        u = urljoin(base, s)
        low = u.lower()
        if any(x in low for x in _IMG_NOISE):
            return
        if u not in imgs:
            imgs.append(u)

    # og:image is reliably THE product's main image on both supplier platforms.
    # Gallery selectors leak related/carousel products (user-confirmed), so we
    # trust ONLY og:image, with a single product-detail image as fallback.
    og = soup.find("meta", attrs={"property": "og:image"})
    if og:
        add(og.get("content"))
    if not imgs:
        for sel in [".p-detail img", ".product-detail img", ".product-images img",
                    "[itemprop='image']"]:
            el = soup.select_one(sel)
            if el:
                add(el.get("src") or el.get("data-src") or el.get("data-zoom-image"))
                if imgs:
                    break
    return imgs[:1]


@app.after_request
def _no_cache(resp):
    # tool is actively developed + the index/decisions must always be fresh
    resp.headers["Cache-Control"] = "no-cache, must-revalidate, max-age=0"
    return resp


_AVAIL_WORDS = ("Skladom", "Na sklade", "Vypredané", "Momentálne nedostupné",
                "Na objednávku", "Posledný kus", "Predaj výrobku skončil", "Na dotaz")


def _supplier_meta(html: str):
    """Best-effort price + availability from a supplier product page."""
    price = ""
    m = re.search(r'(?:product:price:amount|og:price:amount)"\s+content="([0-9]+(?:[.,][0-9]+)?)"', html)
    if not m:
        m = re.search(r'"price"\s*:\s*"?([0-9]+(?:[.,][0-9]+)?)', html)
    if m:
        price = m.group(1).replace(".", ",")   # match our EUR formatting (5,41)
    avail = next((w for w in _AVAIL_WORDS if w in html), "")
    return price, avail


# --------------------------------------------------------------------------- #
# Na objednanie: forestshop "Vybavuje sa" orders → supplier reorder links
# --------------------------------------------------------------------------- #
ORDERS_CACHE = os.path.join(OUT, "orders_cache.csv")
ORDERS_MAXAGE = 1800  # s — refresh the cached orders export at most every 30 min (Marek: raz za pol hodinu stačí)


def _cred(key: str):
    """Read a single KEY=value from the gitignored creds file (data/.shoptet_admin).
    None if missing — callers degrade/refuse rather than crash."""
    try:
        with open(CRED_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith(key + "=") and "=" in line:
                    return line.split("=", 1)[1].strip().strip("'\"") or None
    except FileNotFoundError:
        return None
    return None


def build_to_order_rows(orders_csv, products, decisions, code2pair):
    """Forestshop orders.csv (cp1250 bytes or str) → to-order rows.

    Keeps statusName=='Vybavuje sa', drops SHIPPING*/BILLING* pseudo-items, and joins
    each item code to its supplier reorder URL via the canonical
    import_builder.link_rows() (code -> internalNote). One row per order line; row
    key = '<orderCode>|<itemCode>'. Pure (no network) -> unit-testable."""
    text = (orders_csv.decode("cp1250", errors="replace")
            if isinstance(orders_csv, bytes) else orders_csv)
    code2url = {r[0]: r[2] for r in import_builder.link_rows(products, decisions, code2pair)}
    rows = []
    for r in csv.DictReader(io.StringIO(text), delimiter=";"):
        if (r.get("statusName") or "").strip() != "Vybavuje sa":
            continue
        code = (r.get("itemCode") or "").strip()
        if not code or re.match(r"^(SHIPPING|BILLING)", code, re.I):
            continue
        order = (r.get("code") or "").strip()
        rows.append({
            "key": f"{order}|{code}",
            "orderCode": order,
            "orderDate": (r.get("date") or "").strip()[:10],   # YYYY-MM-DD (drop time)
            "itemCode": code,
            "size": (r.get("itemVariantName") or "").strip(),
            "qty": (r.get("itemAmount") or "").strip(),
            "supplier": (r.get("itemSupplier") or "").strip(),
            "name": (r.get("itemName") or "").strip(),
            "supplierUrl": code2url.get(code, ""),
        })
    return rows


def _fetch_orders_csv() -> bytes:
    base = _cred("SHOPTET_ORDERS_URL")
    if not base:
        raise RuntimeError(f"SHOPTET_ORDERS_URL chýba v {CRED_PATH}")
    today = time.strftime("%Y-%m-%d")
    frm = time.strftime("%Y-%m-%d", time.localtime(time.time() - 90 * 86400))
    sep = "&" if "?" in base else "?"
    r = requests.get(f"{base}{sep}dateFrom={frm}&dateUntil={today}",
                     headers={"User-Agent": UA}, timeout=60)
    r.raise_for_status()
    return r.content


def _orders_csv_cached() -> bytes:
    if (os.path.exists(ORDERS_CACHE)
            and time.time() - os.path.getmtime(ORDERS_CACHE) < ORDERS_MAXAGE):
        with open(ORDERS_CACHE, "rb") as f:
            return f.read()
    data = _fetch_orders_csv()
    tmp = ORDERS_CACHE + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, ORDERS_CACHE)
    return data


def _fetch_export_csv() -> bytes:
    """Full Shoptet catalog export (pattern 14, cp1250 bytes) — the same URL
    scripts/shoptet_import.py downloads as an import-time backup, reused here
    for the hourly read-only refresh (#119)."""
    url = _cred("SHOPTET_EXPORT_URL")
    if not url:
        raise RuntimeError(f"SHOPTET_EXPORT_URL chýba v {CRED_PATH}")
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=120)
        r.raise_for_status()
    except requests.RequestException as e:
        # NEvkladaj `e`/URL do hlášky ani do reťazenej výnimky — obsahuje partner
        # hash (rovnaký dôvod ako scripts/shoptet_import.py::_backup_export).
        # `from None` navyše potlačí chained traceback, aby URL neunikla ani cez
        # log.exception() v automation_runner._execute (last_error v UI je aj
        # tak už len táto sanitizovaná správa, nie surová `e`).
        raise RuntimeError(f"stiahnutie katalógového exportu zlyhalo: {type(e).__name__} "
                           "(URL skrytá — over SHOPTET_EXPORT_URL)") from None
    if not r.content:
        raise RuntimeError("stiahnutý export katalógu je prázdny")
    return r.content


@app.route("/")
def index():
    return send_from_directory("templates", "index.html")


@app.route("/favicon.ico")
def favicon():
    return ("", 204)


@app.route("/api/version")
def api_version():
    """Deployed version (single source: parovanie.__version__) — shown in the
    footer for post-deploy verification."""
    return Response(f"v{__version__}", content_type="text/plain; charset=utf-8")


def _grube_de_display(products, decisions):
    """Serve-time DISPLAY normalization for /api/products: a GRUBE product's
    supplier URLs are rebuilt to the canonical grube.DE detail URL in the RESPONSE
    only (review card AND search tab both render these hrefs). GRUBE == grube.de
    (German availability); the eshop internalNote + 'Na objednanie' chip already
    normalize via import_builder.link_rows — this mirrors the SAME rebuild on the
    display path (import_builder.to_grube_de, productId-based).

    The manager's stored .sk pairings are PRESERVED: in-memory PRODUCTS is never
    mutated and decisions.json is never rewritten — only SHALLOW COPIES of the
    GRUBE entries are swapped. Non-GRUBE products/decisions are returned unchanged.
    Fallback to the raw URL when to_grube_de can't parse a productId."""
    to_de = import_builder.to_grube_de
    out_products = []
    grube_keys = set()
    for p in products:
        if p.get("supplier") != "GRUBE":
            out_products.append(p)
            continue
        grube_keys.add(p.get("key"))
        q = dict(p)                                   # shallow copy — don't mutate PRODUCTS
        cands = p.get("candidates")
        if cands:
            new_cands = []
            for c in cands:
                url = c.get("url")
                if url:
                    c = {**c, "url": to_de(url) or url}
                new_cands.append(c)                   # url-less candidate kept as-is
            q["candidates"] = new_cands
        ai_url = p.get("ai_chosen_url")
        if ai_url:
            q["ai_chosen_url"] = to_de(ai_url) or ai_url
        out_products.append(q)
    out_decisions = {}
    for k, d in decisions.items():
        if k in grube_keys and isinstance(d, dict) and (d.get("url") or "").strip():
            d = {**d, "url": to_de(d["url"]) or d["url"]}   # shallow copy of GRUBE decision
        out_decisions[k] = d
    return {"products": out_products, "decisions": out_decisions}


@app.route("/api/products")
def api_products():
    return jsonify(_grube_de_display(PRODUCTS, _load_decisions()))


@app.route("/api/images")
def api_images():
    """Title + images for any supplier URL (so a manually entered link pulls its
    data). Cached on disk."""
    url = request.args.get("url", "").strip()
    if not url.startswith("http"):
        return jsonify({"title": "", "images": []})
    key = hashlib.sha1(url.encode()).hexdigest()
    cache = os.path.join(IMGCACHE, key + ".json")
    if os.path.exists(cache):
        with open(cache, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):              # legacy cache format
            data = {"title": "", "images": data}
        data.setdefault("price", "")
        data.setdefault("availability", "")
        return jsonify(data)
    try:
        # Short timeout (was 20s): under a fast-scroll burst the client caps concurrent
        # /api/images calls (#74), but a slow/unresponsive supplier can still tie up a
        # worker for the full timeout — 8s sheds a hung supplier fast enough that the
        # queued requests behind it drain well inside Cloudflare's edge timeout instead
        # of all piling up and failing with 524.
        r = requests.get(url, headers={"User-Agent": UA}, timeout=8)
        if r.ok:
            from parovanie.verify import extract_page
            title = extract_page(r.text).get("title", "")
            imgs = _extract_images(r.text, url)
            price, avail = _supplier_meta(r.text)
        else:
            log.warning("image fetch non-OK url=%s status=%s", url, r.status_code)
            title, imgs, price, avail = "", [], "", ""
    except Exception as e:  # noqa: BLE001 — best-effort scrape; log cause and degrade
        log.warning("image fetch failed url=%s: %r", url, e)
        title, imgs, price, avail = "", [], "", ""
    data = {"title": title, "images": imgs, "price": price, "availability": avail}
    with open(cache, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return jsonify(data)


@app.route("/api/decision", methods=["POST"])
def api_decision():
    body = request.get_json(force=True)
    key = str(body.get("key"))
    status = body.get("status")
    with _lock:
        d = _load_decisions()
        if status in (None, "", "undo"):          # undo / un-decide
            d.pop(key, None)
        else:
            d[key] = {"status": status, "url": body.get("url", "").strip()}
        _save_decisions(d)
    log.info("decision key=%s status=%s url=%s", key, status, body.get("url", ""))
    return jsonify({"ok": True})


# CSV/spreadsheet formula-injection guard. A cell beginning with one of these is a
# live formula when the file is opened in Excel/LibreOffice. Real forestshop codes,
# pairCodes and http(s) URLs never start with these, so legit cells are untouched
# (Shoptet matching unaffected); a malicious cell is prefixed with ' → inert text.
_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value):
    s = str(value)
    return "'" + s if s[:1] in _FORMULA_LEAD else s


def _csv_response(header, rows, filename):
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    w.writerow(header)
    w.writerows(rows)
    # UTF-8 with BOM — universal, avoids the cp1250 'č'→'è' mojibake. Import into
    # Shoptet as UTF-8.
    data = buf.getvalue().encode("utf-8-sig")
    return Response(data, content_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.route("/api/import")
def api_import():
    # TWO files (Shoptet wipes empty cells, so columns are split — see import_builder):
    #   import_links.csv  = code;pairCode;internalNote (reorder URL in the private field)
    #   import_states.csv = code;pairCode;productVisibility;stock;availability (Vypredané / Predaj skončil)
    dec = _load_decisions()
    # reviewed pairings (decisions) + inline pairings from the Na-objednanie tab.
    # A reviewed decision is authoritative, so inline rows skip any code it already
    # covers (Shoptet aborts on a duplicate code).
    link = import_builder.link_rows(PRODUCTS, dec, CODE2PAIR)
    link += import_builder.order_pairing_rows(
        _load_order_pairings(), CODE2PAIR, exclude_codes={r[0] for r in link})
    files = [
        ("import_links.csv", import_builder.LINK_HEADER, link),
        ("import_states.csv", import_builder.STATE_HEADER,
         import_builder.state_rows(PRODUCTS, dec, CODE2PAIR)),
        # supplier write-back: only code;pairCode;supplier (own file → can't wipe
        # internalNote/state). Independent column from the link rows, so no exclude.
        ("import_suppliers.csv", import_builder.SUPPLIER_HEADER,
         import_builder.supplier_rows(_load_supplier_assign(), CODE2PAIR)),
        # GRUBE per-size externalCode write-back: only code;pairCode;externalCode (own
        # file → can't wipe internalNote/state). Independent column, so no exclude.
        ("import_externalcode.csv", import_builder.EXTERNALCODE_HEADER,
         import_builder.externalcode_rows(_load_grube_codes(), CODE2PAIR)),
    ]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, header, rows in files:
            s = io.StringIO()
            w = csv.writer(s, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
            w.writerow(header)
            w.writerows([_csv_safe(c) for c in row] for row in rows)   # formula-injection guard
            z.writestr(name, s.getvalue().encode("utf-8-sig"))
    return Response(buf.getvalue(), content_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="import_forestshop.zip"'})


@app.route("/api/export")
def api_export():
    """All decisions joined to products — for building the corrected import +
    the unavailable list. Stable key = supplier|pairCode."""
    dec = _load_decisions()
    rows = []
    for p in PRODUCTS:
        d = dec.get(p.get("key"))
        if not d:
            continue
        rows.append({"key": p.get("key"), "supplier": p["supplier"], "name": p["name"],
                     "variant_codes": p["variant_codes"], "status": d.get("status"),
                     "url": d.get("url", "")})
    return jsonify({"decisions": rows})


# --------------------------------------------------------------------------- #
# Catalog search + promote-on-pair (CATALOG built at startup from the export)
# --------------------------------------------------------------------------- #
def _save_products(products) -> None:
    """Atomic write of review_data.json (tmp + os.replace). Mirrors the other _save_*
    stores; ensure_ascii=False to keep the Slovak names readable, like build_review_data."""
    tmp = DATA + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(products, f, ensure_ascii=False)
    os.replace(tmp, DATA)


def _current_for_entry(ce: dict) -> dict:
    """Build the eshop-side `current` snapshot for a freshly paired catalog product by
    scanning the Shoptet export for the FIRST matching row — matched by pairCode (when
    the entry has one) OR by variant code (single-variant products have an EMPTY pairCode,
    so they must be matched by their code). A rare manual action, so a one-off cp1250 scan
    is acceptable. Column mapping mirrors build_review_data's current_of() call. Missing
    export / no matching row -> {} (the card just renders without our-side state — never
    a 500)."""
    pc = (ce.get("pairCode") or "").strip()
    codes = set(ce.get("variant_codes") or [])
    if not os.path.exists(SRC):
        return {}
    csv.field_size_limit(10**9)
    try:
        with open(SRC, encoding="cp1250", errors="replace") as f:
            for r in csv.DictReader(f, delimiter=";"):
                rpc = (r.get("pairCode") or "").strip()
                rc = (r.get("code") or "").strip()
                if (pc and rpc == pc) or (rc and rc in codes):
                    # Column names + arg order MUST match build_review_data.py /
                    # resync_export.py (productVisibility — there is NO "visibility"
                    # column; reading the wrong one left vis="" so hidden/blocked
                    # products never got state 3 — snapshot drift).
                    return current_of(
                        (r.get("productVisibility") or "").strip(),
                        (r.get("availabilityInStock") or "").strip(),
                        (r.get("availabilityOutOfStock") or "").strip(),
                        (r.get("price") or "").strip(),
                        (r.get("standardPrice") or "").strip(),
                        (r.get("stock") or "").strip(),
                    )
    except (OSError, csv.Error) as e:
        # Best-effort contract: a missing/unreadable export OR a malformed row
        # (csv.Error — NUL byte / oversized field) degrades to {}, never a 500.
        log.warning("current_for_entry scan failed key=%s: %r", ce.get("key"), e)
    return {}


# Lazily-built {code: ORIG_URL} from the marketing XML — None = not yet attempted.
_CODE2URL = None


def _our_url_for_entry(ce: dict):
    """Best-effort forestshop our_url for a promoted product, from the marketing XML's
    ORIG_URL (the authoritative eshop URL) by exact variant code. Built once and cached.
    ANY failure (missing XML, parse error, scripts not importable) -> None, which is an
    acceptable result (the UI falls back to a search link)."""
    global _CODE2URL
    if not ce or not ce.get("variant_codes"):
        return None
    if _CODE2URL is None:
        _CODE2URL = {}
        try:
            mx = os.path.join(OUT, "marketing.xml")
            if os.path.exists(mx):
                # scripts/ is not on sys.path; load the pure function from the file.
                import importlib.util
                _p = os.path.join(ROOT, "scripts", "url_from_marketing_xml.py")
                _spec = importlib.util.spec_from_file_location("_uxml", _p)
                _mod = importlib.util.module_from_spec(_spec)
                _spec.loader.exec_module(_mod)
                _CODE2URL = _mod.build_code2url(mx)
                log.info("our_url: marketing XML loaded (%d codes)", len(_CODE2URL))
        except Exception as e:  # noqa: BLE001 — best-effort; our_url=None is acceptable
            log.warning("our_url marketing-XML resolve failed: %r", e)
            _CODE2URL = {}
    for c in ce["variant_codes"]:
        if c in _CODE2URL:
            return _CODE2URL[c]
    return None


def _review_products_for(e: dict, by_paircode=None, by_code=None) -> list:
    """ALL in-review products matching a catalog entry — by pairCode (most review entries
    are keyed "SUPPLIER|pairCode", so a key==pairCode test missed them, C1) PLUS any
    sharing a variant code (single-variant products have an empty pairCode, so matching an
    empty e["pairCode"] against PRODUCTS would wrongly hit every other empty-pairCode
    product; match those by code instead).

    #64: the SAME pairCode can be reviewed under TWO+ DIFFERENT suppliers (e.g.
    GRUBE|425 AND WETLAND|425 for one forestshop product, when it was matched against
    candidates from more than one supplier) — a "first match wins" lookup silently hid
    every duplicate past the first, and its decision could never be repaired via search.
    Returns every distinct match (by identity — a product matched by BOTH pairCode and a
    shared code counts once), in stable PRODUCTS order. Lookup maps are built once per
    request by the caller."""
    seen: set = set()
    out: list = []
    pc = e.get("pairCode")
    if pc and by_paircode is not None:
        for p in by_paircode.get(pc, []):
            if id(p) not in seen:
                seen.add(id(p))
                out.append(p)
    if by_code is not None:
        for c in e.get("variant_codes") or []:
            for p in by_code.get(c, []):
                if id(p) not in seen:
                    seen.add(id(p))
                    out.append(p)
    return out


def _search_results_for_entry(e: dict, decisions=None, by_paircode=None, by_code=None) -> list:
    """Shape one catalog entry into one-or-more /api/search result rows. `key`
    (pairCode-or-code) is the catalog identity the client promotes-and-pairs by.

    Normally a catalog entry matches at most one in-review product, so this returns a
    single row. #64: when the pairCode is reviewed under MULTIPLE suppliers, EVERY
    matching review product gets its OWN row — own `idx`/`our_url`/`paired_url` and a
    `review_key` (that product's REAL key) the client uses to open/repair THAT specific
    one, instead of always landing on the first duplicate. A catalog entry with zero
    review matches still returns exactly one "not yet paired" row (unchanged shape).

    price/stock/state come from the catalog entry (the manager's "almost no data"
    complaint) and are the same on every row of a duplicated entry. `paired_url` = the
    matching product's CURRENT decision URL (good/manual only), read under its REAL key;
    a GRUBE product's URL is DISPLAY-normalized to grube.de (mirrors /api/products —
    storage untouched). `decisions` is loaded ONCE per request by the caller."""
    base = {
        "key": e["key"],
        "pairCode": e["pairCode"],
        "name": e["name"],
        "supplier": e["supplier"],
        "codes": e["variant_codes"],
        "image": e["image"],
        "in_review": e["in_review"],
        "price": e.get("price", ""),
        "stock": e.get("stock", 0),
        "state": e.get("state", 1),
    }
    matches = _review_products_for(e, by_paircode, by_code)
    if not matches:
        return [dict(base, our_url=None, idx=None, paired_url=None, review_key=None,
                      review_supplier=None)]
    rows = []
    for p in matches:
        paired_url = None
        if decisions is not None:
            d = decisions.get(p.get("key"))
            if isinstance(d, dict) and d.get("status") in ("good", "manual"):
                url = (d.get("url") or "").strip()
                if url:
                    if p.get("supplier") == "GRUBE":
                        url = import_builder.to_grube_de(url) or url
                    paired_url = url
        # review_supplier = THIS matching product's OWN supplier (may differ from the
        # catalog entry's generic `supplier` column) — #64: when a pairCode is reviewed
        # under 2+ suppliers, every row shares the same catalog `supplier`, so the client
        # needs the per-match supplier to tell duplicate rows apart.
        rows.append(dict(base, our_url=p.get("our_url"), idx=p.get("idx"),
                          paired_url=paired_url, review_key=p.get("key"),
                          review_supplier=p.get("supplier")))
    return rows


def _product_lookups():
    """{pairCode: [products]}, {code: [products]} over PRODUCTS — built once per
    /api/search so _search_results_for_entry finds EVERY in-review product matching a
    catalog entry (by pairCode or shared code), not just the first. #64: a pairCode CAN
    be reviewed under more than one product (different suppliers) — collecting lists
    (instead of first-writer-wins) is what lets every duplicate surface as its own row."""
    by_paircode: dict = {}
    by_code: dict = {}
    for p in PRODUCTS:
        pc = p.get("pairCode")
        if pc:
            by_paircode.setdefault(pc, []).append(p)
        for c in (p.get("variant_codes") or []):
            by_code.setdefault(c, []).append(p)
    return by_paircode, by_code


@app.route("/api/search")
def api_search():
    """Accent-insensitive catalog search over the whole per-product blob (name / supplier
    / codes / externalCode / description / category / manufacturer / ean / productNumber)
    — pure search_catalog over the startup CATALOG. Empty/short query -> no results.
    A catalog entry reviewed under more than one supplier (#64) yields more than one
    result row — see _search_results_for_entry."""
    q = request.args.get("q", "")
    dec = _load_decisions()   # once per request, not per result
    by_paircode, by_code = _product_lookups()
    results = []
    for e in search_catalog(CATALOG, q):
        results.extend(_search_results_for_entry(e, dec, by_paircode, by_code))
    return jsonify({"results": results})


@app.route("/api/search-pair", methods=["POST"])
def api_search_pair():
    """Manually pair a catalog product to a supplier URL from the search box. Identified
    by `key` (the catalog entry's pairCode-or-code; legacy `pairCode` accepted as a
    fallback). If the product is not yet in the review set it is PROMOTED (a minimal
    review_data entry built from the catalog row + the export `current` snapshot +
    best-effort our_url), appended to PRODUCTS and persisted; then a `manual` decision is
    recorded. The URL must be http(s) (else 400); an unknown key -> 404.

    #64: `review_key` (optional) targets a SPECIFIC review product's REAL key —
    required to repair one of several review entries duplicated under the same catalog
    key (the same pairCode reviewed under two+ different suppliers, e.g. GRUBE|425 AND
    WETLAND|425). Without it, "first PRODUCTS match wins" is ambiguous and can silently
    fix the wrong duplicate. An unknown `review_key` -> 404 (never falls back to the
    ambiguous scan)."""
    body = request.get_json(silent=True) or {}
    key = str(body.get("key") or body.get("pairCode") or "").strip()
    url = str(body.get("url") or "").strip()
    review_key = str(body.get("review_key") or "").strip()
    # authoritative URL guard (matches /api/order-pair) — blocks javascript:/data: and
    # malformed values from reaching the import's internalNote / a CSV cell.
    if not re.match(r"^https?://", url):
        return jsonify({"ok": False, "error": "url must start with http(s)://"}), 400
    ce = CATALOG.get(key)
    if not ce:
        return jsonify({"ok": False, "error": "unknown key"}), 404
    if review_key and not any(p.get("key") == review_key for p in PRODUCTS):
        return jsonify({"ok": False, "error": "unknown review_key"}), 404
    # Match an already-in-review product by pairCode (when the entry has one) OR by a
    # shared variant code. Most review entries are keyed "SUPPLIER|pairCode" (e.g.
    # GRUBE|425); a key==entry test missed every such entry → it wrongly promoted a
    # DUPLICATE entry AND wrote the decision under a key link_rows never reads, silently
    # dropping the manager's corrected URL (C1). Single-variant products (empty pairCode)
    # match by code — an empty-pairCode == test would collide with every other such item.
    pc = (ce.get("pairCode") or "").strip()
    entry_codes = set(ce.get("variant_codes") or [])

    def _find_existing():
        if review_key:
            # explicit target (#64) — bypasses the ambiguous first-match scan below,
            # which is exactly what's needed when the same pairCode is duplicated
            # across suppliers. Validity already checked above (404 if unknown).
            return next((p for p in PRODUCTS if p.get("key") == review_key), None)
        for p in PRODUCTS:
            if pc and p.get("pairCode") == pc:
                return p
            if entry_codes & set(p.get("variant_codes") or []):
                return p
        return None

    in_review = _find_existing() is not None
    # The two heavy read-only scans (55 MB cp1250 export + 59 MB marketing XML) depend
    # ONLY on the catalog entry, never on mutable state → compute them OUTSIDE the lock so
    # a promote never stalls every other write endpoint for seconds. Needed only when
    # promoting a genuinely NEW catalog product; an existing entry just gets its decision
    # rewritten.
    if not in_review:
        snapshot = _current_for_entry(ce)
        our_url = _our_url_for_entry(ce)
        supplier = supplier_from_url(url, config.SUPPLIERS)
    with _lock:
        # re-check under the lock (append-only store → monotonic; a tiny TOCTOU on
        # concurrent same-key promotes is fine — single manager user — and this dedups)
        existing = _find_existing()
        if existing is None:
            entry = build_promoted_entry(ce, snapshot, our_url, supplier, len(PRODUCTS))
            PRODUCTS.append(entry)
            _save_products(PRODUCTS)
            ce["in_review"] = True   # keep the catalog snapshot consistent for re-search
            target_key = entry["key"]  # promoted entry's key == pairCode-or-code
            promoted = True
            log.info("search-pair promoted key=%s supplier=%s codes=%d our_url=%s",
                     target_key, entry["supplier"], len(entry["variant_codes"]),
                     entry["our_url"])
        else:
            target_key = existing["key"]   # write under the REAL key (e.g. GRUBE|425)
            promoted = False
        dec = _load_decisions()
        dec[target_key] = {"status": "manual", "url": url}
        _save_decisions(dec)
    log.info("search-pair decision key=%s url=%s promoted=%s", target_key, url, promoted)
    return jsonify({"ok": True, "promoted": promoted, "key": target_key})


@app.route("/api/ordered", methods=["GET", "POST"])
def api_ordered():
    """Per-line 'objednané' state (key='<orderCode>|<itemCode>'), persisted like
    decisions. GET -> the map; POST {key, ordered} toggles a single line."""
    if request.method == "GET":
        return jsonify({"ordered": _load_ordered()})
    body = request.get_json(force=True)
    key = str(body.get("key"))
    ordered = bool(body.get("ordered"))
    with _lock:
        d = _load_ordered()
        if ordered:
            d[key] = True
        else:
            d.pop(key, None)
        _save_ordered(d)
    log.info("ordered key=%s ordered=%s", key, ordered)
    return jsonify({"ok": True})


@app.route("/api/waiting", methods=["GET", "POST"])
def api_waiting():
    """Per-line 'čaká sa' flag (key='<orderCode>|<itemCode>'): active order line that
    can't be stocked yet. GET -> the map; POST {key, waiting} toggles a single line.
    Same shape as /api/ordered, independent state."""
    if request.method == "GET":
        return jsonify({"waiting": _load_waiting()})
    body = request.get_json(force=True)
    key = str(body.get("key"))
    waiting = bool(body.get("waiting"))
    with _lock:
        d = _load_waiting()
        if waiting:
            d[key] = True
        else:
            d.pop(key, None)
        _save_waiting(d)
    log.info("waiting key=%s waiting=%s", key, waiting)
    return jsonify({"ok": True})


@app.route("/api/instock", methods=["GET", "POST"])
def api_instock():
    """Per-line 'skladom' flag (key='<orderCode>|<itemCode>') — independent of
    ordered/waiting/unavailable. GET -> the map; POST {key, instock} toggles one line."""
    if request.method == "GET":
        return jsonify({"instock": _load_instock()})
    body = request.get_json(force=True)
    key = str(body.get("key") or "").strip()
    if not key:
        return jsonify({"ok": False, "error": "key required"}), 400
    instock = bool(body.get("instock"))
    with _lock:
        d = _load_instock()
        if instock:
            d[key] = True
        else:
            d.pop(key, None)
        _save_instock(d)
    log.info("instock key=%s instock=%s", key, instock)
    return jsonify({"ok": True})


@app.route("/api/unavailable", methods=["GET", "POST"])
def api_unavailable():
    """Per-line 'nedostupné' flag (key='<orderCode>|<itemCode>') — independent of
    ordered/waiting/instock. GET -> the map; POST {key, unavailable} toggles one line."""
    if request.method == "GET":
        return jsonify({"unavailable": _load_unavailable()})
    body = request.get_json(force=True)
    key = str(body.get("key") or "").strip()
    if not key:
        return jsonify({"ok": False, "error": "key required"}), 400
    unavailable = bool(body.get("unavailable"))
    with _lock:
        d = _load_unavailable()
        if unavailable:
            d[key] = True
        else:
            d.pop(key, None)
        _save_unavailable(d)
    log.info("unavailable key=%s unavailable=%s", key, unavailable)
    return jsonify({"ok": True})


@app.route("/api/notes", methods=["GET", "POST"])
def api_notes():
    """Free-form notes list ('📝 Poznámky' tab). GET -> newest-first list; POST {text}
    appends a new note. Not written to any CSV/import, so no formula-injection guard —
    just a length cap on the free text."""
    if request.method == "GET":
        return jsonify({"notes": _load_notes()})
    body = request.get_json(force=True)
    text = str(body.get("text") or "").strip()
    if not text or len(text) > NOTE_MAX_LEN:
        return jsonify({"ok": False, "error": f"text must be 1..{NOTE_MAX_LEN} chars"}), 400
    note = {"id": uuid.uuid4().hex, "text": text, "done": False, "ts": time.time()}
    with _lock:
        d = _load_notes()
        d.insert(0, note)          # newest-first
        _save_notes(d)
    log.info("note added id=%s len=%d", note["id"], len(text))
    return jsonify({"note": note})


@app.route("/api/note", methods=["POST"])
def api_note():
    """Toggle 'done' or delete a single note by id. Unknown id -> 404."""
    body = request.get_json(force=True)
    nid = str(body.get("id") or "")
    with _lock:
        d = _load_notes()
        idx = next((i for i, n in enumerate(d) if n.get("id") == nid), None)
        if idx is None:
            return jsonify({"ok": False, "error": "unknown id"}), 404
        if body.get("delete"):
            d.pop(idx)
        elif "done" in body:
            d[idx]["done"] = bool(body.get("done"))
        _save_notes(d)
    log.info("note update id=%s delete=%s done=%s", nid, body.get("delete"), body.get("done"))
    return jsonify({"ok": True})


# --------------------------------------------------------------------------- #
# „Vývoj" tab (#115): list this repo's GitHub issues (open + closed, PRs filtered)
# + the idea lightbulb (create an issue). ALL GitHub traffic is proxied through the
# backend — the repo-write token (data/.gh_env) NEVER reaches the browser. It lives
# only in the server-side Authorization header (never a URL, never a log), so an
# error string carries no secret. No token/repo configured → every path degrades
# gracefully to „GitHub nedostupný" (available=False), never a 500.
# --------------------------------------------------------------------------- #
GITHUB_API = (os.environ.get("GITHUB_API_BASE") or "https://api.github.com").rstrip("/")
GH_TIMEOUT = 15                 # s per GitHub API call
GH_LIST_PER_PAGE = 100          # GitHub max page size
GH_MAX_PAGES = 5                # bounded pagination (≤500 items): /issues returns
#                                 issues AND PRs, so a single page could push older
#                                 issues off after PR-filtering — page through so the
#                                 boss sees every issue (incl. all the closed/done ones).
IDEA_TITLE_MAX = 200
IDEA_BODY_MAX = 5000
IDEA_RATE_MAX = 20              # ideas per user per window (anti-spam / runaway guard)
IDEA_RATE_WINDOW = 300          # 5 min
_idea_times: dict = {}          # user email -> [timestamps]


def _gh_config():
    """(token, repo) from data/.gh_env — or (None, None) when unconfigured, so the
    Vývoj tab + lightbulb degrade gracefully instead of crashing."""
    token = (os.environ.get("GITHUB_TOKEN") or "").strip()
    repo = (os.environ.get("GITHUB_REPO") or "").strip()
    if not token or not repo:
        return None, None
    return token, repo


def _gh_headers(token):
    """GitHub REST headers — the token is a Bearer credential in the HEADER only."""
    return {"Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "parovanie-webreview"}


def _slim_issue(it: dict) -> dict:
    """GitHub issue JSON → the slim shape the frontend renders (no token, no PII)."""
    return {
        "number": it.get("number"),
        "title": it.get("title") or "",
        "state": it.get("state") or "open",
        "labels": [lbl.get("name") for lbl in (it.get("labels") or [])
                   if isinstance(lbl, dict) and lbl.get("name")],
        "updated_at": it.get("updated_at") or "",
        "html_url": it.get("html_url") or "",
        "comments": it.get("comments") or 0,
    }


def _do_dev_issues():
    """(payload, status) — this repo's GitHub issues (open + closed), PRs filtered
    out (the /issues endpoint returns PRs too — they carry a `pull_request` key).
    No token → graceful „unavailable" (never 500); an upstream/network error is
    caught and also degrades gracefully so the tab never crashes."""
    token, repo = _gh_config()
    if not token:
        return {"ok": False, "available": False, "issues": [],
                "error": "GitHub nedostupný — token nie je nastavený"}, 200
    try:
        issues = []
        for page in range(1, GH_MAX_PAGES + 1):
            r = requests.get(
                f"{GITHUB_API}/repos/{repo}/issues",
                params={"state": "all", "per_page": GH_LIST_PER_PAGE,
                        "sort": "updated", "direction": "desc", "page": page},
                headers=_gh_headers(token), timeout=GH_TIMEOUT)
            if r.status_code != 200:
                log.warning("gh issues: HTTP %s for %s (page %d)", r.status_code, repo, page)
                if page == 1:
                    return {"ok": False, "available": False, "issues": [],
                            "error": f"GitHub API vrátil {r.status_code}"}, 200
                break                            # keep the pages already collected
            batch = r.json()
            if not isinstance(batch, list) or not batch:
                break
            issues.extend(_slim_issue(it) for it in batch
                          if isinstance(it, dict) and "pull_request" not in it)
            if len(batch) < GH_LIST_PER_PAGE:    # last page reached
                break
        return {"ok": True, "available": True, "issues": issues}, 200
    except Exception as e:  # noqa: BLE001 — the tab must never crash on GitHub trouble
        log.warning("gh issues fetch failed: %r", e)
        return {"ok": False, "available": False, "issues": [],
                "error": "GitHub nedostupný"}, 200


def _do_create_idea(title: str, description: str, author: str = ""):
    """(payload, status) — create a GitHub issue from a manager idea. No token →
    graceful „unavailable"; an upstream/network error is caught (never 500)."""
    token, repo = _gh_config()
    if not token:
        return {"ok": False, "available": False,
                "error": "GitHub nedostupný — token nie je nastavený"}, 200
    body = description
    if author:
        body = (body + "\n\n" if body else "") + f"_Nápad cez appku (Vývoj) — {author}_"
    try:
        r = requests.post(
            f"{GITHUB_API}/repos/{repo}/issues",
            json={"title": title, "body": body},
            headers=_gh_headers(token), timeout=GH_TIMEOUT)
        if r.status_code not in (200, 201):
            log.warning("gh create idea: HTTP %s for %s", r.status_code, repo)
            return {"ok": False, "error": f"GitHub API vrátil {r.status_code}"}, 200
        log.info("gh idea created by %s: %r", author or "?", title)
        return {"ok": True, "issue": _slim_issue(r.json())}, 201
    except Exception as e:  # noqa: BLE001 — never crash on GitHub trouble
        log.warning("gh create idea failed: %r", e)
        return {"ok": False, "error": "GitHub nedostupný"}, 200


def _idea_rate_limited(key: str) -> bool:
    """Coarse anti-spam guard on idea creation (per user). Records this attempt."""
    now = time.time()
    times = [t for t in _idea_times.get(key, []) if now - t < IDEA_RATE_WINDOW]
    if len(times) >= IDEA_RATE_MAX:
        _idea_times[key] = times
        return True
    times.append(now)
    _idea_times[key] = times
    return False


@app.route("/api/dev/issues")
def api_dev_issues():
    """List this repo's GitHub issues (open + closed) for the „Vývoj" tab."""
    payload, status = _do_dev_issues()
    return jsonify(payload), status


@app.route("/api/dev/idea", methods=["POST"])
def api_dev_idea():
    """Create a GitHub issue from a manager idea (the lightbulb). Validates the
    title (required, capped); the token is used server-side only."""
    body = request.get_json(force=True, silent=True) or {}
    title = str(body.get("title") or "").strip()
    if not title:
        return jsonify({"ok": False, "error": "názov nápadu je povinný"}), 400
    if len(title) > IDEA_TITLE_MAX:
        return jsonify({"ok": False,
                        "error": f"názov môže mať najviac {IDEA_TITLE_MAX} znakov"}), 400
    desc = str(body.get("description") or "").strip()
    if len(desc) > IDEA_BODY_MAX:
        return jsonify({"ok": False,
                        "error": f"popis môže mať najviac {IDEA_BODY_MAX} znakov"}), 400
    u = _current_user()
    email = u["email"] if u else ""
    if _idea_rate_limited(email):
        return jsonify({"ok": False,
                        "error": "priveľa nápadov za krátky čas — skús o chvíľu"}), 429
    payload, status = _do_create_idea(title, desc, email)
    return jsonify(payload), status


@app.route("/api/order-pair", methods=["POST"])
def api_order_pair():
    """Save/clear an inline supplier reorder URL for a forestshop order code
    (keyed by itemCode). Mirrors /api/decision but keyed by the forestshop product
    code, so it covers order lines that are NOT in the review dataset. Empty url
    clears the pairing. The URL then shows as the row's reorder link and is included
    in the import (import_builder.order_pairing_rows)."""
    body = request.get_json(force=True)
    code = str(body.get("code") or "").strip()
    url = str(body.get("url") or "").strip()
    if not code:
        return jsonify({"ok": False, "error": "missing code"}), 400
    # forestshop codes always start alphanumeric — a leading formula char (=,+,-,@,…)
    # is either malformed or a CSV-injection attempt; reject it at the source.
    if code[:1] in _FORMULA_LEAD:
        return jsonify({"ok": False, "error": "invalid code"}), 400
    # authoritative URL guard (matches the client) — only real http(s) links reach
    # the import's internalNote; blocks javascript:/data: and malformed 'httpfoo'.
    if url and not re.match(r"^https?://", url):
        return jsonify({"ok": False, "error": "url must start with http(s)://"}), 400
    with _lock:
        d = _load_order_pairings()
        if url:
            d[code] = url
        else:
            d.pop(code, None)
        _save_order_pairings(d)
    log.info("order-pair code=%s url=%s", code, url)
    return jsonify({"ok": True})


@app.route("/api/order-supplier", methods=["POST"])
def api_order_supplier():
    """Assign/clear a supplier name for a forestshop order code (keyed by itemCode).
    Lets the manager fill in the supplier for an order line that arrived without one;
    the row then regroups under that supplier on the tab and the name is written back
    to the eshop `supplier` field by the nightly upload. Empty supplier clears it.
    Mirrors /api/order-pair (same code guard); the supplier name reaches a CSV, so a
    leading formula char is rejected here AND escaped at the CSV sink (_csv_safe)."""
    body = request.get_json(force=True)
    code = str(body.get("code") or "").strip()
    supplier = str(body.get("supplier") or "").strip()
    if not code:
        return jsonify({"ok": False, "error": "missing code"}), 400
    # forestshop codes always start alphanumeric — a leading formula char (=,+,-,@,…)
    # is malformed or a CSV-injection attempt; reject at the source.
    if code[:1] in _FORMULA_LEAD:
        return jsonify({"ok": False, "error": "invalid code"}), 400
    # supplier name is written verbatim into the import CSV's `supplier` column — a
    # leading formula char would be a CSV-injection vector; real names start
    # alphanumeric, so reject it here too (belt-and-braces with _csv_safe at the sink).
    if supplier and supplier[:1] in _FORMULA_LEAD:
        return jsonify({"ok": False, "error": "invalid supplier"}), 400
    with _lock:
        d = _load_supplier_assign()
        if supplier:
            d[code] = supplier
        else:
            d.pop(code, None)
        _save_supplier_assign(d)
    log.info("order-supplier code=%s supplier=%s", code, supplier)
    return jsonify({"ok": True})


@app.route("/api/orders")
def api_orders():
    """To-order list: forestshop 'Vybavuje sa' items joined to supplier reorder
    links, with the per-line 'ordered' state merged in. Degrades to [] on fetch
    error so the tab still renders."""
    try:
        csv_bytes = _orders_csv_cached()
    except Exception as e:  # noqa: BLE001 — degrade to empty list, log the cause
        log.warning("orders fetch failed: %r", e)
        return jsonify({"orders": [], "error": str(e)})
    rows = build_to_order_rows(csv_bytes, PRODUCTS, _load_decisions(), CODE2PAIR)
    ordered = _load_ordered()
    waiting = _load_waiting()
    instock = _load_instock()
    unavail = _load_unavailable()
    pairings = _load_order_pairings()
    assigns = _load_supplier_assign()
    grube = _load_grube_codes()                      # loaded once per request
    for r in rows:
        r["ordered"] = bool(ordered.get(r["key"]))
        r["waiting"] = bool(waiting.get(r["key"]))   # 'čaká sa' — deferred active line
        r["instock"] = bool(instock.get(r["key"]))         # 'skladom' — máme/naskladnené
        r["unavailable"] = bool(unavail.get(r["key"]))     # 'nedostupné' — u dodávateľa
        # supplierUrl stays the reviewed-decision link (read-only); pairUrl is the
        # inline-entered one (editable on the tab). A row is "paired" if either is set.
        r["pairUrl"] = pairings.get(r["itemCode"], "")
        # supplier manually assigned for an order line that arrived without one — the
        # tab groups by (assignedSupplier OR supplier), so this regroups the row.
        r["assignedSupplier"] = assigns.get(r["itemCode"], "")
        # GRUBE per-size code chip + .de link (empty for every non-GRUBE / unmatched row)
        _attach_grube(r, grube)
    return jsonify({"orders": rows})


@app.route("/static/<path:p>")
def static_files(p):
    return send_from_directory("static", p)


# --------------------------------------------------------------------------- #
# n8n → Shoptet auto-import (vypredané → skladom)
# --------------------------------------------------------------------------- #
def _import_token():
    """Bearer token for the import endpoint, from the gitignored creds file
    (key N8N_IMPORT_TOKEN). None if not configured → endpoint refuses all calls."""
    return _cred("N8N_IMPORT_TOKEN")


MAX_IMPORT_BYTES = 5 * 1024 * 1024   # restock CSVs are a few kB; cap the in-memory read


def _safe_unlink(*paths):
    for p in paths:
        try:
            os.remove(p)
        except OSError:
            pass


def _client_ip():
    """Real caller IP behind the Cloudflare tunnel (so the unauthorized-attempt
    log is useful, not just the tunnel/local address)."""
    return (request.headers.get("CF-Connecting-IP")
            or request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            or request.remote_addr)


def run_import(csv_path, dry_run=False, timeout=300):
    """Run the existing careful import script as a subprocess (catalog backup +
    safe-mode + result read-back). Returns (returncode, stdout, stderr). Started in
    its own session so a timeout kills the WHOLE group (the Playwright/Chromium it
    spawns too), never an orphaned browser mid-import. `timeout` scales with the CSV
    size — a few thousand pairing rows legitimately take longer than a small restock.
    Stubbable in tests."""
    cmd = [sys.executable, IMPORT_SCRIPT, "--file", csv_path, "--yes"]
    if dry_run:
        cmd.append("--dry-run")
    env = {**os.environ, "PYTHONPATH": os.path.join(ROOT, "src")}
    p = subprocess.Popen(cmd, cwd=ROOT, env=env, text=True,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         start_new_session=True)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        p.communicate()
        raise
    return p.returncode, out, err


@app.route("/api/n8n/shoptet-import", methods=["POST"])
def n8n_shoptet_import():
    """n8n posts a restock CSV (multipart 'file', or raw body); we whitelist it to
    the safe restock columns and run the careful Shoptet import. Bearer-auth'd.
    Pass dry_run=1 (form/query) to reach the import form without changing anything."""
    token = _import_token()
    auth = request.headers.get("Authorization", "")
    expected = f"Bearer {token}".encode() if token else b""
    # compare bytes — a non-ASCII Authorization header must 401, not raise (latin-1
    # is how WSGI decodes the header; compare_digest rejects non-ASCII str args)
    if not token or not hmac.compare_digest(auth.encode("latin-1", "ignore"), expected):
        log.warning("n8n import: unauthorized call from %s", _client_ip())
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    f = request.files.get("file")
    raw = f.read() if f else request.get_data()
    if not raw:
        log.warning("n8n import: empty body")
        return jsonify({"ok": False, "error": "empty body"}), 400
    if len(raw) > MAX_IMPORT_BYTES:
        log.warning("n8n import: payload too large (%d B)", len(raw))
        return jsonify({"ok": False, "error": "payload too large"}), 413

    os.makedirs(OUT, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    # unique names (mkstemp) so two same-second calls never clobber each other's
    # file while a subprocess is reading it
    raw_fd, raw_path = tempfile.mkstemp(prefix=f"n8n_restock_{ts}_", suffix="_raw.csv", dir=OUT)
    out_fd, out_path = tempfile.mkstemp(prefix=f"n8n_restock_{ts}_", suffix=".csv", dir=OUT)
    os.close(out_fd)
    with os.fdopen(raw_fd, "wb") as w:
        w.write(raw)
    try:
        rows = import_builder.sanitize_csv(raw_path, out_path)
    except (ValueError, UnicodeDecodeError) as e:
        log.warning("n8n import: bad CSV: %s", e)
        _safe_unlink(raw_path, out_path)
        return jsonify({"ok": False, "error": str(e)}), 400
    finally:
        _safe_unlink(raw_path)   # sanitized file is the audit record; raw is transient
    if rows == 0:
        log.info("n8n import: 0 restock rows — nothing to import")
        _safe_unlink(out_path)
        return jsonify({"ok": True, "rows": 0, "message": "no restock rows"}), 200

    dry = str(request.values.get("dry_run", "")).lower() in ("1", "true", "yes")
    if not _import_lock.acquire(blocking=False):
        log.warning("n8n import: another import already running")
        _safe_unlink(out_path)
        return jsonify({"ok": False, "error": "import already running"}), 409
    log.info("n8n import: %d rows, dry_run=%s, file=%s", rows, dry, out_path)
    try:
        rc, out, err = run_import(out_path, dry_run=dry)
    except subprocess.TimeoutExpired:
        log.error("n8n import: subprocess timeout — killed import group")
        return jsonify({"ok": False, "error": "import timeout"}), 504
    finally:
        _import_lock.release()

    parsed = parse_import_log(out)
    result = {"ok": rc == 0, "exit_code": rc, "rows": rows, "dry_run": dry,
              "processed": parsed.get("processed"), "updated": parsed.get("updated"),
              "failed": parsed.get("failed"), "error_detail": parsed.get("error_detail"),
              "stdout_tail": (out or "")[-800:]}
    log.info("n8n import: rc=%s processed=%s updated=%s failed=%s",
             rc, parsed.get("processed"), parsed.get("updated"), parsed.get("failed"))
    if rc != 0:
        log.error("n8n import FAILED rc=%s stderr=%s", rc, (err or "")[-400:])
    return jsonify(result), (200 if rc == 0 else 502)


# --------------------------------------------------------------------------- #
# n8n → nightly upload of worker pairings (reorder links → eshop internalNote)
# --------------------------------------------------------------------------- #
PAIRINGS_STATE = os.path.join(OUT, "uploaded_pairings.json")


def _load_uploaded():
    """{key: url} of pairings already uploaded — so the nightly job only sends new
    or changed ones. Missing/corrupt → empty (treat everything as new)."""
    try:
        with open(PAIRINGS_STATE, encoding="utf-8") as f:
            d = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    # always a {key: url} map — a stray JSON array could repeat a key and break the
    # "total_uploaded never exceeds total_products" invariant in _pairing_summary
    return d if isinstance(d, dict) else {}


def _save_uploaded(d):
    tmp = PAIRINGS_STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PAIRINGS_STATE)


# Public URL of the review web — handed to the n8n notifier so the single summary
# Discord message can link straight to the pairing app.
PUBLIC_URL = os.environ.get("WEBREVIEW_PUBLIC_URL", "https://parovanie-forestshop.newlevel.media")


def _pairing_summary(uploaded):
    """Totals for the n8n summary notification: how many pairings are uploaded to the
    eshop in total, how many of our products still have none, and the review link.
    ``uploaded`` is the post-run map so ``total_uploaded`` already includes this run.
    Only keys still present in the current review set count, so a product removed
    since its upload can't push the ratio past total (e.g. avoid "Spolu 105 / 100")."""
    valid = {p.get("key") for p in PRODUCTS}
    total = len(valid)  # distinct product keys (de-dups), same unit as `up` below
    up = sum(1 for k in uploaded if k in valid)
    return {"total_products": total, "total_uploaded": up,
            "remaining": max(0, total - up), "review_url": PUBLIC_URL}


def _do_upload_pairings(dry):
    """Core of the nightly pairings upload — the SINGLE place the pairing-upload
    logic lives (NEkopíruj logiku). Reads the review decisions, builds the link
    import (code;pairCode;internalNote) for only the pairings not yet uploaded,
    runs the careful import, records what went up, and returns (result, status)
    for the caller to serialize. Shared by the n8n HTTP endpoint (below) and the
    in-app „Párovania → eshop" automation (#109) — no auth / Flask request access
    here. Visibility/stock are NOT touched — the morning restock job turns a
    product on once the supplier has it in stock."""
    dec = _load_decisions()
    uploaded = _load_uploaded()
    new_keys = import_builder.new_pairing_keys(dec, uploaded)
    by_key = {p.get("key"): p for p in PRODUCTS}
    products = [{"name": by_key.get(k, {}).get("name", ""),
                 "our_url": by_key.get(k, {}).get("our_url", ""),
                 "supplier_url": dec[k].get("url", "")} for k in new_keys]
    if not new_keys:
        log.info("n8n pairings: 0 new pairings")
        return {"ok": True, "count": 0, "products": [],
                **_pairing_summary(uploaded)}, 200

    rows = import_builder.link_rows(PRODUCTS, {k: dec[k] for k in new_keys}, CODE2PAIR)
    if not rows:
        log.warning("n8n pairings: %d new keys but 0 import rows (codes missing)", len(new_keys))
        # paired but un-uploadable (variant codes missing) — surface so the notifier
        # warns instead of staying silent (count:0 alone would send nothing)
        return {"ok": True, "count": 0, "products": products,
                "message": "no import rows", "blocked": len(new_keys),
                **_pairing_summary(uploaded)}, 200

    # surface a real data inconsistency: the same variant code paired to two different
    # supplier URLs (a code can hold only one link, so first-wins drops the rest)
    code_urls = {}
    for k in new_keys:
        for c in by_key.get(k, {}).get("variant_codes", []):
            code_urls.setdefault(c, set()).add((dec[k].get("url") or "").strip())
    conflicts = [c for c, u in code_urls.items() if len(u) > 1]
    if conflicts:
        log.warning("n8n pairings: %d codes paired to conflicting URLs (first wins): %s",
                    len(conflicts), conflicts[:10])

    os.makedirs(OUT, exist_ok=True)
    out_fd, out_path = tempfile.mkstemp(prefix="import_links_", suffix=".csv", dir=OUT)
    with os.fdopen(out_fd, "w", encoding="utf-8-sig", newline="") as f:
        from parovanie.writer import shoptet_writer
        w = shoptet_writer(f)
        w.writerow(import_builder.LINK_HEADER)
        w.writerows(rows)

    # Not every key in new_keys necessarily landed a row: a product can have zero
    # variant codes, or ALL its codes can be the "seen"-deduped loser of an earlier
    # key sharing the same code (link_rows keeps only the first writer per code).
    # Only keys that actually got at least one code written to the CSV may ever be
    # marked uploaded — a key with none stays "new" so the next run retries it,
    # instead of being silently lost forever (#49).
    written_codes = {r[0] for r in rows}
    uploaded_keys = [k for k in new_keys
                     if written_codes & set(by_key.get(k, {}).get("variant_codes") or [])]
    blocked_keys = [k for k in new_keys if k not in uploaded_keys]
    if blocked_keys:
        log.warning("n8n pairings: %d of %d keys generated no row (codes missing/deduped): %s",
                    len(blocked_keys), len(new_keys), blocked_keys[:10])

    if not _import_lock.acquire(blocking=False):
        log.warning("n8n pairings: another import already running")
        _safe_unlink(out_path)
        return {"ok": False, "error": "import already running"}, 409
    log.info("n8n pairings: %d products, %d rows, dry_run=%s", len(new_keys), len(rows), dry)
    try:
        # pairing CSVs can be large (an initial bulk of thousands of rows) → more time
        rc, out, err = run_import(out_path, dry_run=dry, timeout=900)
        parsed = parse_import_log(out)
        ok = rc == 0
        if ok and not dry:                   # record only after a real success (inside the lock)
            for k in uploaded_keys:          # ONLY keys that actually got a row — never blocked_keys
                uploaded[k] = (dec[k].get("url") or "").strip()
            _save_uploaded(uploaded)
    except subprocess.TimeoutExpired:
        log.error("n8n pairings: subprocess timeout — killed import group")
        _safe_unlink(out_path)
        return {"ok": False, "error": "import timeout"}, 504
    finally:
        _import_lock.release()

    if ok:
        _safe_unlink(out_path)               # success → drop the temp CSV (the catalog backup is the audit record)
    result = {"ok": ok, "exit_code": rc, "count": len(uploaded_keys), "rows": len(rows),
              "dry_run": dry, "processed": parsed.get("processed"),
              "updated": parsed.get("updated"), "failed": parsed.get("failed"),
              "error_detail": parsed.get("error_detail"),
              "products": products, "stdout_tail": (out or "")[-800:],
              "blocked": len(blocked_keys),
              **_pairing_summary(uploaded)}
    log.info("n8n pairings: rc=%s processed=%s products=%d", rc, parsed.get("processed"), len(uploaded_keys))
    if not ok:
        log.error("n8n pairings FAILED rc=%s stderr=%s", rc, (err or "")[-400:])
    return result, (200 if ok else 502)


@app.route("/api/n8n/upload-pairings", methods=["POST"])
def n8n_upload_pairings():
    """n8n's nightly caller: Bearer-auth then delegate to _do_upload_pairings.
    dry_run=1 reaches the import without changing anything."""
    token = _import_token()
    auth = request.headers.get("Authorization", "")
    expected = f"Bearer {token}".encode() if token else b""
    if not token or not hmac.compare_digest(auth.encode("latin-1", "ignore"), expected):
        log.warning("n8n pairings: unauthorized call from %s", _client_ip())
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    dry = str(request.values.get("dry_run", "")).lower() in ("1", "true", "yes")
    result, status = _do_upload_pairings(dry)
    return jsonify(result), status


# --------------------------------------------------------------------------- #
# n8n → nightly upload of assigned supplier names (→ eshop `supplier` field)
# --------------------------------------------------------------------------- #
SUPPLIERS_STATE = os.path.join(OUT, "uploaded_suppliers.json")


def _load_uploaded_suppliers():
    """{code: supplier} already written back to the eshop — so the nightly job only
    sends new or changed assignments. Missing/corrupt → empty (everything is new).
    Always a dict (a stray array could repeat a code and break the summary invariant)."""
    try:
        with open(SUPPLIERS_STATE, encoding="utf-8") as f:
            d = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return d if isinstance(d, dict) else {}


def _save_uploaded_suppliers(d):
    tmp = SUPPLIERS_STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, SUPPLIERS_STATE)


def _supplier_summary(uploaded, assigns):
    """Totals for the n8n summary notification: assigned codes, how many are already
    written back (uploaded value still matches the current assignment), how many remain.
    A changed name counts as remaining (uploaded != current), matching new_supplier_keys."""
    valid = {c for c, s in assigns.items() if (c or "").strip() and (s or "").strip()}
    total = len(valid)
    up = sum(1 for c in valid if uploaded.get(c) == assigns.get(c))
    return {"total_assigned": total, "total_uploaded": up,
            "remaining": max(0, total - up), "review_url": PUBLIC_URL}


def _do_upload_suppliers(dry):
    """Core of the nightly supplier write-back — the SINGLE place the logic lives
    (NEkopíruj logiku). Reads the supplier assignments, builds code;pairCode;supplier
    for only the codes not yet uploaded (or whose name changed), runs the careful
    import, records what went up, and returns (result, status). Shared by the n8n
    HTTP endpoint (below) and the in-app „Párovania → eshop" automation (#109) — no
    auth / Flask request access here. Touches ONLY the `supplier` column —
    links/state/prices are left untouched."""
    assigns = _load_supplier_assign()
    uploaded = _load_uploaded_suppliers()
    new_codes = import_builder.new_supplier_keys(assigns, uploaded)
    products = [{"code": c, "supplier": assigns[c]} for c in new_codes]
    if not new_codes:
        log.info("n8n suppliers: 0 new assignments")
        return {"ok": True, "count": 0, "products": [],
                **_supplier_summary(uploaded, assigns)}, 200

    rows = import_builder.supplier_rows({c: assigns[c] for c in new_codes}, CODE2PAIR)
    if not rows:
        log.warning("n8n suppliers: %d new codes but 0 import rows", len(new_codes))
        return {"ok": True, "count": 0, "products": products,
                "message": "no import rows", "blocked": len(new_codes),
                **_supplier_summary(uploaded, assigns)}, 200

    os.makedirs(OUT, exist_ok=True)
    out_fd, out_path = tempfile.mkstemp(prefix="import_suppliers_", suffix=".csv", dir=OUT)
    with os.fdopen(out_fd, "w", encoding="utf-8-sig", newline="") as f:
        from parovanie.writer import shoptet_writer
        w = shoptet_writer(f)
        w.writerow(import_builder.SUPPLIER_HEADER)
        # formula-injection guard at the sink (defense-in-depth alongside the endpoint
        # reject) — the supplier name is free text written into a CSV cell
        w.writerows([_csv_safe(c) for c in row] for row in rows)

    if not _import_lock.acquire(blocking=False):
        log.warning("n8n suppliers: another import already running")
        _safe_unlink(out_path)
        return {"ok": False, "error": "import already running"}, 409
    log.info("n8n suppliers: %d codes, %d rows, dry_run=%s", len(new_codes), len(rows), dry)
    try:
        rc, out, err = run_import(out_path, dry_run=dry, timeout=900)
        parsed = parse_import_log(out)
        ok = rc == 0
        if ok and not dry:                   # record only after a real success (inside the lock)
            for c in new_codes:
                uploaded[c] = (assigns[c] or "").strip()
            _save_uploaded_suppliers(uploaded)
    except subprocess.TimeoutExpired:
        log.error("n8n suppliers: subprocess timeout — killed import group")
        _safe_unlink(out_path)
        return {"ok": False, "error": "import timeout"}, 504
    finally:
        _import_lock.release()

    if ok:
        _safe_unlink(out_path)
    result = {"ok": ok, "exit_code": rc, "count": len(new_codes), "rows": len(rows),
              "dry_run": dry, "processed": parsed.get("processed"),
              "updated": parsed.get("updated"), "failed": parsed.get("failed"),
              "error_detail": parsed.get("error_detail"),
              "products": products, "stdout_tail": (out or "")[-800:],
              **_supplier_summary(uploaded, assigns)}
    log.info("n8n suppliers: rc=%s processed=%s codes=%d", rc, parsed.get("processed"), len(new_codes))
    if not ok:
        log.error("n8n suppliers FAILED rc=%s stderr=%s", rc, (err or "")[-400:])
    return result, (200 if ok else 502)


@app.route("/api/n8n/upload-suppliers", methods=["POST"])
def n8n_upload_suppliers():
    """n8n's nightly caller: Bearer-auth then delegate to _do_upload_suppliers.
    dry_run=1 reaches the import without changing anything."""
    token = _import_token()
    auth = request.headers.get("Authorization", "")
    expected = f"Bearer {token}".encode() if token else b""
    if not token or not hmac.compare_digest(auth.encode("latin-1", "ignore"), expected):
        log.warning("n8n suppliers: unauthorized call from %s", _client_ip())
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    dry = str(request.values.get("dry_run", "")).lower() in ("1", "true", "yes")
    result, status = _do_upload_suppliers(dry)
    return jsonify(result), status


# --------------------------------------------------------------------------- #
# In-app automations (#93): generic runner + the Pošta SK uncollected-shipments
# automation. New automations (#105-#111) register themselves in AUTOMATIONS_REG
# below — the runner, endpoints and sidebar section are shared.
# --------------------------------------------------------------------------- #
AUTOMATIONS_STATE = os.path.join(OUT, "automations.json")
POSTA_STATE = os.path.join(OUT, "posta_uncollected.json")
ORDERS_REMINDER_STATE = os.path.join(OUT, "orders_reminder.json")   # #105 dedup + display


def _load_posta_state() -> dict:
    try:
        with open(POSTA_STATE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_posta_state(d: dict) -> None:
    tmp = POSTA_STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, POSTA_STATE)


def _fetch_tracking(pkg: str) -> dict:
    """Pošta SK tracking for one package — 3 tries (n8n: retryOnFail maxTries=3,
    3s between), 60s timeout. Raises after the last failure so the run records
    the shipment under errors instead of silently skipping it."""
    url = posta_uncollected.TRACKING_API.format(q=quote(pkg))
    for attempt in range(1, 4):
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=60)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001 — retried; the last failure propagates
            log.warning("posta: tracking %s attempt %d/3 failed: %r", pkg, attempt, e)
            if attempt == 3:
                raise
            time.sleep(3)
    raise RuntimeError("unreachable")


def run_posta_uncollected() -> dict:
    """One check run (daily 09:00 or 'Spustiť teraz'): shipments from the app's
    orders export → Pošta SK tracking per shipment → escalation e-mails to
    customers per the n8n cadence → full display state for the tab persisted
    to data/out/posta_uncollected.json. Returns the summary the runner stores."""
    csv_bytes = _orders_csv_cached()
    shipments = posta_uncollected.shipments_from_orders_csv(csv_bytes)
    with _lock:
        esc = dict(_load_posta_state().get("escalation") or {})
    uncollected, invalid, errors = [], [], []
    sent = failed = 0
    for s in shipments:
        try:
            tj = _fetch_tracking(s["packageNumber"])
        except Exception as e:  # noqa: BLE001 — recorded per shipment, run continues
            log.error("posta: tracking %s (obj. %s) FAILED after retries: %r",
                      s["packageNumber"], s["code"], e)
            errors.append({"orderCode": s["code"],
                           "packageNumber": s["packageNumber"], "error": str(e)})
            continue
        r = posta_uncollected.evaluate_shipment(s, tj, esc.get(s["code"], ""))
        if r["invalid"]:
            # The exact class of package numbers that silently broke the n8n
            # workflow (13-14 digit numeric labels) — surfaced, never skipped.
            log.warning("posta: INVALID_FORMAT balík %s (obj. %s) — Pošta SK ho "
                        "nevie sledovať, treba preveriť ručne", r["packageNumber"], r["orderCode"])
            invalid.append({k: r[k] for k in (
                "orderCode", "packageNumber", "name", "admin_link")})
            continue
        if r["send"]:
            if not r["email"]:
                log.error("posta: obj. %s (%s) nemá e-mail — upozornenie nemožno poslať",
                          r["orderCode"], r["packageNumber"])
                mail_ok = False
            else:
                # bcc omitted -> _send_mail_html defaults it to MAIL_BCC (#126)
                mail_ok = _send_mail_html(r["email"], r["email_subject"], r["email_body"])
            if mail_ok:
                esc[r["orderCode"]] = r["new_state_value"]
                sent += 1
                log.info("posta: email #%d for obj. %s (%s) sent to %s",
                         r["count"], r["orderCode"], r["packageNumber"], r["email"])
                # persist the bump IMMEDIATELY — a crash later in the run must
                # never lose a sent-mail record (that would double-send tomorrow)
                with _lock:
                    st = _load_posta_state()
                    st.setdefault("escalation", {})[r["orderCode"]] = r["new_state_value"]
                    _save_posta_state(st)
            else:
                failed += 1          # state NOT bumped → retried next run
                prev_count, prev_last = posta_uncollected.parse_notified(
                    esc.get(r["orderCode"], ""))
                r["count"] = prev_count
                r["last_sent"] = prev_last.isoformat() if prev_last else ""
                r["call_needed"] = prev_count >= posta_uncollected.MAX_EMAILS
        if r["uncollected"]:
            uncollected.append({k: r[k] for k in (
                "orderCode", "packageNumber", "name", "phone", "email",
                "office_name", "office_addr", "retained_till", "notified_since",
                "days_at_post", "count", "last_sent", "call_needed",
                "tracking_link", "admin_link")})
    # prune escalation state for orders that left the 30-day source window
    codes = {s["code"] for s in shipments}
    esc = {k: v for k, v in esc.items() if k in codes}
    stats = {"checked": len(shipments), "uncollected": len(uncollected),
             "invalid": len(invalid), "errors": len(errors),
             "emails_sent": sent, "emails_failed": failed}
    with _lock:
        _save_posta_state({
            "escalation": esc,
            "last_check": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "uncollected": uncollected, "invalid": invalid, "errors": errors,
            "stats": stats,
        })
    log.info("posta: run done %s", stats)
    return stats


def run_shoptet_sync() -> dict:
    """Hourly refresh (#119): re-pulls the forestshop orders export (bypassing the
    30-min ORDERS_MAXAGE cache window — an hourly GUARANTEED pull, not just
    "whenever someone opens Na objednanie") AND the full Shoptet catalog export
    (data/products.csv), then rebuilds the in-memory CODE2PAIR/CATALOG search
    index and resyncs each review card's price/stock snapshot
    (export_helpers.resync_current — the same logic scripts/resync_export.py
    runs manually). Passive READ-ONLY refresh: never touches the manager
    decision stores (decisions/ordered_items/waiting_items/order_pairings/
    supplier_assignments — those are the manager's live work, untouched here).

    Fetch-then-swap (temp file + atomic os.replace) throughout: a failed/partial
    fetch raises BEFORE anything on disk changes, so the runner's existing
    try/except (automation_runner._execute) records the error and the app keeps
    serving the previous cache/catalog/review data untouched — degrade, never
    crash, never a half-written file."""
    global CODE2PAIR, CATALOG

    orders_bytes = _fetch_orders_csv()
    tmp = ORDERS_CACHE + ".tmp"
    with open(tmp, "wb") as f:
        f.write(orders_bytes)
    os.replace(tmp, ORDERS_CACHE)

    export_bytes = _fetch_export_csv()
    tmp2 = SRC + ".tmp"
    with open(tmp2, "wb") as f:
        f.write(export_bytes)
    os.replace(tmp2, SRC)

    # rebuild the in-memory search index from the fresh export — same single
    # cp1250-pass helper the app uses at startup, no restart needed.
    with _lock:
        review_keys = ({p.get("pairCode") for p in PRODUCTS if p.get("pairCode")}
                       | {c for p in PRODUCTS for c in (p.get("variant_codes") or [])})
        CODE2PAIR, CATALOG = _load_catalog(SRC, review_keys)

    rows = []
    with open(SRC, encoding="cp1250", errors="replace") as f:
        for row in csv.DictReader(f, delimiter=";"):
            rows.append(row)
    with _lock:
        counts = resync_current(rows, PRODUCTS, set(config.SUPPLIERS))
        tmp3 = DATA + ".tmp"
        with open(tmp3, "w", encoding="utf-8") as f:
            json.dump(PRODUCTS, f, ensure_ascii=False)
        os.replace(tmp3, DATA)

    result = {
        "orders_bytes": len(orders_bytes),
        "catalog_products": len(CATALOG),
        "catalog_codes": len(CODE2PAIR),
        "review_synced": counts["synced"],
        "review_stale": counts["stale"],
    }
    log.info("shoptet_sync: run OK %s", result)
    return result


def run_parovania_eshop() -> dict:
    """Nightly push (daily 21:00) of the workers' NEW pairings (reorder links →
    internalNote) + newly assigned suppliers (→ supplier field) to the Shoptet
    eshop — the in-app migration of the n8n „Forestshop — Párovania → eshop"
    workflow (YuDugCCOnwejRfva, #109). Reuses the SAME careful upload path as the
    two n8n endpoints (_do_upload_pairings / _do_upload_suppliers — no Shoptet
    logic reimplemented). The write stays IDEMPOTENT: already-uploaded pairings/
    suppliers are skipped via uploaded_pairings.json / uploaded_suppliers.json,
    so a re-run never double-uploads. Records combined counts for the tab. Both
    steps run sequentially (mirroring the n8n chain); a step that completes with
    ok:false (import failed) or blocked is surfaced in the returned `status`
    without crashing the run. A genuine exception propagates to the runner, which
    records last_status='error' and keeps the app alive (degrade, never crash).

    Reads ONLY the manager's decision/assignment stores (what to push) — never
    modifies them; its own progress lives in the two uploaded_*.json state files."""
    pairings, _ps = _do_upload_pairings(dry=False)
    suppliers, _ss = _do_upload_suppliers(dry=False)

    def _blocked(d):
        return int(d.get("blocked") or 0)

    p_ok = bool(pairings.get("ok"))
    s_ok = bool(suppliers.get("ok"))
    if not (p_ok and s_ok):
        status = "failed"          # an import (or lock/timeout) failed → red row
    elif _blocked(pairings) or _blocked(suppliers):
        status = "blocked"         # paired but un-uploadable (missing codes) → orange row
    else:
        status = "ok"

    result = {
        "status": status,
        "pairings": {
            "count": pairings.get("count", 0),
            "total_uploaded": pairings.get("total_uploaded", 0),
            "total_products": pairings.get("total_products", 0),
            "remaining": pairings.get("remaining", 0),
            "blocked": _blocked(pairings),
            "ok": p_ok,
            "error": pairings.get("error", ""),
        },
        "suppliers": {
            "count": suppliers.get("count", 0),
            "total_uploaded": suppliers.get("total_uploaded", 0),
            "total_assigned": suppliers.get("total_assigned", 0),
            "remaining": suppliers.get("remaining", 0),
            "blocked": _blocked(suppliers),
            "ok": s_ok,
            "error": suppliers.get("error", ""),
        },
        "review_url": PUBLIC_URL,
    }
    log.info("parovania_eshop: run done status=%s pairings=%d suppliers=%d",
             status, result["pairings"]["count"], result["suppliers"]["count"])
    return result


# --------------------------------------------------------------------------- #
# #106 — „Dodávateľský sklad": daily supplier availability/price scraper (in-app
# migration of the n8n „Forestshop — Dodávateľský scraper" 6kn7jzBXTjbmbiVa).
# For each product's supplier link (internalNote) it fetches the supplier page,
# extracts availability + price via a STATIC tier (JSON-LD → meta → text keywords)
# and, only when static can't decide, an OpenAI gpt-4o-mini fallback. Results land
# in data/out/supplier_stock.json — the input for #107/#108. Pure logic lives in
# parovanie.supplier_stock; this wires it to the network, OpenAI and the store.
# --------------------------------------------------------------------------- #
SUPPLIER_STOCK_STATE = os.path.join(OUT, "supplier_stock.json")
# Refetch only links not checked in the last N hours (skip fresh OK rows) — saves
# HTTP + paid LLM cost. Chosen < 24h so a daily run still re-checks everything once.
SUPPLIER_STOCK_MAX_AGE_H = 20.0
SUPPLIER_FETCH_DELAY_S = 1.0      # per-domain politeness gap (don't hammer one shop)
SUPPLIER_FETCH_TIMEOUT = 30      # s per supplier page
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_TIMEOUT = 60              # s per LLM call


def _load_supplier_stock() -> dict:
    try:
        with open(SUPPLIER_STOCK_STATE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_supplier_stock(d: dict) -> None:
    tmp = SUPPLIER_STOCK_STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, SUPPLIER_STOCK_STATE)


def _read_export_for_links() -> str:
    """The on-disk Shoptet catalog export (data/products.csv), cp1250-decoded — the
    source of supplier links. Refreshed hourly by the „Sync zo Shoptetu" automation
    and at startup; a missing file simply yields 0 links (never crashes)."""
    try:
        with open(SRC, "rb") as f:
            return f.read().decode("cp1250", errors="replace")
    except FileNotFoundError:
        return ""


def _fetch_supplier_html(url: str) -> str:
    """GET one supplier product page (3 tries, exponential-ish backoff). Raises on
    the final failure so the run records THAT link as an error row and continues.
    The URL is a public product page (no secret) — safe to keep in error text."""
    for attempt in range(1, 4):
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=SUPPLIER_FETCH_TIMEOUT)
            r.raise_for_status()
            if not r.encoding or r.encoding.lower() == "iso-8859-1":
                r.encoding = r.apparent_encoding or r.encoding
            return r.text
        except Exception as e:  # noqa: BLE001 — retried; the last failure propagates
            log.warning("supplier_stock: fetch %s attempt %d/3 failed: %r", url, attempt, e)
            if attempt == 3:
                raise
            time.sleep(2 * attempt)
    raise RuntimeError("unreachable")


def _llm_extract(text: str, url: str) -> dict:
    """OpenAI gpt-4o-mini structured extraction of availability/price from page text.
    Requires OPENAI_API_KEY (data/.ai_env) — the caller only reaches here when the
    key is present. The key lives in the Authorization header (never the URL), so an
    error string carries no secret."""
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY nie je nastavený")
    payload = {"model": supplier_stock.LLM_MODEL,
               "messages": supplier_stock.build_llm_messages(text, url),
               "temperature": 0,
               "response_format": {"type": "json_object"}}
    r = requests.post(OPENAI_URL, json=payload, timeout=OPENAI_TIMEOUT,
                      headers={"Authorization": f"Bearer {key}",
                               "Content-Type": "application/json"})
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"]
    return supplier_stock.parse_llm_json(content)


def _politeness_wait(domain: str, last_ts: dict) -> None:
    """Sleep so consecutive fetches to the SAME supplier domain are ≥ the politeness
    gap apart (different domains never wait on each other)."""
    prev = last_ts.get(domain)
    if prev is not None and SUPPLIER_FETCH_DELAY_S > 0:
        gap = SUPPLIER_FETCH_DELAY_S - (time.monotonic() - prev)
        if gap > 0:
            time.sleep(gap)
    last_ts[domain] = time.monotonic()


def run_supplier_stock() -> dict:
    """One scraper run (daily 05:00 or „Spustiť teraz"): supplier links from the
    export → per link fetch + STATIC extraction (+ LLM fallback when static can't
    decide AND a key is configured) → upsert into data/out/supplier_stock.json.

    Cost-aware: recently-checked OK links are skipped (stale-skip); the LLM is
    called ONLY when static fails; per-domain politeness spreads same-shop hits.
    Robust: a failing fetch / LLM call for one link is recorded as an error row and
    the run continues — one bad supplier never crashes the run or the app. Reads
    ONLY its own store + the export; never touches the manager's decision stores."""
    csv_text = _read_export_for_links()
    links = supplier_stock.links_from_export(csv_text, config.SUPPLIERS)
    prev = {r.get("link"): r for r in (_load_supplier_stock().get("rows") or [])}
    now = datetime.now(timezone.utc).astimezone()
    have_key = bool(os.environ.get("OPENAI_API_KEY"))
    last_ts: dict[str, float] = {}
    rows = []
    stats = {"total": len(links), "checked": 0, "skipped": 0, "static": 0, "llm": 0,
             "available": 0, "unavailable": 0, "unknown": 0, "errors": 0, "llm_calls": 0}

    for lk in links:
        link = lk["link"]
        prow = prev.get(link)
        if supplier_stock.is_recently_checked(prow, now, SUPPLIER_STOCK_MAX_AGE_H):
            rows.append(prow)                       # keep the fresh result untouched
            stats["skipped"] += 1
            continue
        row = {"link": link, "supplier": lk.get("supplier", ""), "name": lk.get("name", ""),
               "codes": lk.get("codes", []), "product_count": lk.get("count", 0)}
        try:
            _politeness_wait(supplier_stock.host_of(link), last_ts)
            html = _fetch_supplier_html(link)
            static = supplier_stock.extract_static(html, link)
            available = static["available"]
            price = static["price"]
            currency = static["currency"]
            variants = static["variants"]
            avail_text = static["availabilityText"]
            extracted_by = static["extractedBy"]
            if supplier_stock.need_llm(static):
                if have_key:
                    llm = _llm_extract(supplier_stock.page_text(html), link)
                    stats["llm_calls"] += 1
                    extracted_by = "llm"
                    if llm["available"] is not None:
                        available = llm["available"]
                    if llm["price"] is not None:
                        price = llm["price"]
                    currency = llm["currency"] or currency
                    variants = llm["variants"] or variants
                    avail_text = llm["availabilityText"] or avail_text
                else:
                    # no key → don't call LLM; keep whatever static found, flag it
                    extracted_by = "static-only"
            row.update(ok=True, error="", available=available, price=price,
                       currency=currency, availabilityText=avail_text, variants=variants,
                       extractedBy=extracted_by, checkedAt=now.isoformat(timespec="seconds"))
            stats["checked"] += 1
            stats["llm" if extracted_by == "llm" else "static"] += 1
            if available is True:
                stats["available"] += 1
            elif available is False:
                stats["unavailable"] += 1
            else:
                stats["unknown"] += 1
        except Exception as e:  # noqa: BLE001 — per-link error recorded, run continues
            log.warning("supplier_stock: link %s FAILED: %r", link, e)
            row.update(ok=False, error=str(e)[:300], available=None, price=None,
                       currency="", availabilityText="", variants=[], extractedBy="error",
                       checkedAt=now.isoformat(timespec="seconds"))
            stats["errors"] += 1
        rows.append(row)

    _save_supplier_stock({"last_check": now.isoformat(timespec="seconds"),
                          "rows": rows, "stats": stats})
    log.info("supplier_stock: run done %s", stats)
    return stats


# --------------------------------------------------------------------------- #
# #107 — „Riziko výpadku": daily supply-risk report (in-app migration of the
# n8n workflow „Forestshop — Riziko výpadku" 7ujLZ4WDNphSgsuj). READ-ONLY /
# advisory — writes NOTHING to the eshop, ever. JOINS our catalog export
# against #106's ALREADY-SCRAPED data/out/supplier_stock.json (same
# internalNote link both automations share) — this automation does not scrape
# anything itself. Pure logic lives in parovanie.riziko_vypadku; this wires it
# to the store + the display/CSV endpoints.
# --------------------------------------------------------------------------- #
RIZIKO_STATE = os.path.join(OUT, "riziko_vypadku.json")


def _load_riziko() -> dict:
    try:
        with open(RIZIKO_STATE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_riziko(d: dict) -> None:
    tmp = RIZIKO_STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, RIZIKO_STATE)


def run_riziko_vypadku() -> dict:
    """One check run (daily ~06:15 or „Spustiť teraz"): join OUR catalog export
    (same on-disk data/products.csv #106 reads) against the „Dodávateľský sklad"
    scraper's LAST result (data/out/supplier_stock.json) — no network calls of
    its own. When the scraper has never run yet (no rows persisted), this
    surfaces has_supplier_data=False instead of silently flagging nothing as
    'no risk' (which would look like a false all-clear)."""
    with _lock:
        stock = _load_supplier_stock()
    supplier_rows = stock.get("rows") or []
    has_data = bool(supplier_rows)
    csv_text = _read_export_for_links()          # same cp1250 export reader as #106
    risks = riziko_vypadku.compute_risk(csv_text, supplier_rows) if has_data else []
    now = datetime.now(timezone.utc).astimezone()
    with _lock:
        _save_riziko({
            "last_check": now.isoformat(timespec="seconds"),
            "has_supplier_data": has_data,
            "supplier_last_check": stock.get("last_check", ""),
            "risks": risks,
        })
    stats = {"risks": len(risks), "has_supplier_data": has_data}
    log.info("riziko_vypadku: run done %s", stats)
    return stats


# --------------------------------------------------------------------------- #
# #108 — „Vypredané → Skladom": daily restock (in-app migration of the LIVE n8n
# workflow „Forestshop — Vypredané → Skladom v2" KN1BE18HLdM8mfTc). WRITES to the
# live eshop: JOINS our catalog export (Vypredané + visible products, state 2)
# against #106's ALREADY-SCRAPED supplier_stock.json and, for every product whose
# supplier now has FRESH confirmed stock, builds the restock rows (both availability
# fields → Skladom, visible, stock) and pushes them through the SAME careful Shoptet
# import path the n8n endpoint uses (import_builder.restock_rows → run_import →
# #23-hardened read-back). Detection logic lives in parovanie.restock_skladom; this
# wires it to the store + the import + the display endpoint.
# --------------------------------------------------------------------------- #
RESTOCK_STATE = os.path.join(OUT, "restock_skladom.json")


def _load_restock() -> dict:
    try:
        with open(RESTOCK_STATE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_restock(d: dict) -> None:
    tmp = RESTOCK_STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, RESTOCK_STATE)


def _write_restock_csv(path: str, rows: list) -> None:
    """Write the restock rows in the canonical Shoptet import dialect (utf-8-sig BOM,
    ';', CRLF), header = import_builder.RESTOCK_COLS — same dialect as the split
    import files, so the careful run_import script + Shoptet parse it identically."""
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
        w.writerow(import_builder.RESTOCK_COLS)
        w.writerows(rows)


def run_restock_skladom() -> dict:
    """One restock run (daily ~06:00 or „Spustiť teraz"): join OUR catalog export
    (Vypredané + visible products, state 2) against the „Dodávateľský sklad" scraper's
    LAST result (data/out/supplier_stock.json) and flip back to Skladom every product
    whose supplier now has FRESH confirmed stock — by building the restock import rows
    (both availability fields → Skladom, visible, stock 5 via
    import_builder.restock_rows) and pushing them through the SAME careful Shoptet
    import path the n8n endpoint uses (run_import → parse_import_log read-back). WRITES
    to the live eshop.

    Safe by construction: no supplier data (scraper never ran) → flips NOTHING and
    surfaces has_supplier_data=False; a candidate needs ok+available+FRESH (checkedAt
    within 48h), so a stale or negative supplier confirmation never flips a product.
    Idempotent — only state-2 (Vypredané) products are candidates, so a product already
    Skladom is never re-flipped once the export refreshes. A failed import is detected
    via the #23-hardened read-back and recorded status='error', not a silent success
    (the run itself never raises on an import failure — it degrades to a red row, like
    parovania_eshop). Reads ONLY its own store + the export + supplier_stock; never
    touches the manager's decision stores."""
    with _lock:
        stock = _load_supplier_stock()
    supplier_rows = stock.get("rows") or []
    has_data = bool(supplier_rows)
    csv_text = _read_export_for_links()          # same cp1250 export reader as #106
    now = datetime.now(timezone.utc).astimezone()
    candidates = (restock_skladom.compute_candidates(csv_text, supplier_rows, now)
                  if has_data else [])
    rows = import_builder.restock_rows(candidates, CODE2PAIR)

    status = "ok"
    processed = updated = failed = None
    error_detail = ""
    if rows:
        os.makedirs(OUT, exist_ok=True)
        fd, csv_path = tempfile.mkstemp(prefix="restock_", suffix=".csv", dir=OUT)
        os.close(fd)
        _write_restock_csv(csv_path, rows)
        if not _import_lock.acquire(blocking=False):
            log.warning("restock_skladom: iný import práve beží — beh preskočený")
            status, error_detail = "busy", "iný import práve beží"
        else:
            try:
                rc, out, err = run_import(csv_path)
            except subprocess.TimeoutExpired:
                rc, out, err = 1, "", "import timeout"
            finally:
                _import_lock.release()
            parsed = parse_import_log(out)
            processed, updated, failed = (parsed.get("processed"),
                                          parsed.get("updated"), parsed.get("failed"))
            if rc == 0:
                status = "ok"
            else:
                status = "error"
                error_detail = parsed.get("error_detail") or (err or "")[-300:]
                log.error("restock_skladom: import FAILED rc=%s stderr=%s",
                          rc, (err or "")[-400:])

    # `candidates` are always stored (what WOULD be flipped) so the tab shows them
    # even on a failed import; on success they ARE what was flipped.
    with _lock:
        _save_restock({
            "last_check": now.isoformat(timespec="seconds"),
            "has_supplier_data": has_data,
            "supplier_last_check": stock.get("last_check", ""),
            "status": status,
            "candidates": candidates,
            "processed": processed, "updated": updated, "failed": failed,
            "error_detail": error_detail,
        })
    stats = {"candidates": len(candidates), "imported_rows": len(rows),
             "status": status, "processed": processed, "updated": updated,
             "failed": failed, "has_supplier_data": has_data}
    log.info("restock_skladom: run done %s", stats)
    return stats


# --------------------------------------------------------------------------- #
# #105 — „Pripomienky objednávok" (migrated from n8n „Forestshop orders",
# MnskuiOdu3i5GKlF). Daily: „Vybavuje sa" orders older than 4 days →
#   • NO internal note (shopRemark empty)  → RED „nikto sa jej nedotkol" alert
#     (was Discord red) — no e-mail.
#   • HAS a note → AI-classify the note (contacted vs not) → if NOT contacted,
#     send ONE reminder e-mail to the customer (max once per order, deduped via
#     data/out/orders_reminder.json), ORANGE „pripomienka odoslaná" (was Discord
#     orange); if contacted, just log skipped_contacted.
# SENDS real customer e-mails + costs OpenAI → starts DISABLED (#93 contract).
# --------------------------------------------------------------------------- #
def _load_orders_reminder() -> dict:
    try:
        with open(ORDERS_REMINDER_STATE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_orders_reminder(d: dict) -> None:
    tmp = ORDERS_REMINDER_STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, ORDERS_REMINDER_STATE)


def _classify_contacted(shop_remark: str) -> bool:
    """OpenAI gpt-4o-mini classification of an order's internal shop note (#105) → True when the
    customer was ALREADY contacted (skip the reminder), False when NOT (send it). Mirrors the
    #106 supplier-scraper OpenAI call pattern: requests.post, JSON-object output, key in the
    Authorization header (never the URL → no secret in any error text). Requires OPENAI_API_KEY —
    the caller only reaches here when the key is present."""
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY nie je nastavený")
    payload = {"model": supplier_stock.LLM_MODEL,
               "messages": orders_reminder.build_classifier_messages(shop_remark),
               "temperature": 0,
               "response_format": {"type": "json_object"}}
    r = requests.post(OPENAI_URL, json=payload, timeout=OPENAI_TIMEOUT,
                      headers={"Authorization": f"Bearer {key}",
                               "Content-Type": "application/json"})
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"]
    return orders_reminder.parse_classification(content)


def run_orders_reminder() -> dict:
    """One check run (daily 08:00 or „Spustiť teraz"): „Vybavuje sa" orders >4d from the app's
    cached orders export → red (no note) / AI-classified (with note) → one reminder e-mail per
    not-yet-contacted order, max once per order (data/out/orders_reminder.json). Returns the
    summary the runner stores; the full red/orange snapshot is persisted for the tab.

    Robust: a failing OpenAI or SMTP call for ONE order is logged and skipped (that order is not
    recorded → retried next run), never crashing the scheduler. No key → the AI branch degrades
    gracefully (never e-mails blind). Reads ONLY its own store + the orders export; never touches
    the manager's decision / to-order stores."""
    csv_bytes = _orders_csv_cached()
    orders = orders_reminder.select_orders(csv_bytes)
    with _lock:
        done = dict(_load_orders_reminder().get("orders") or {})   # code -> {status, ...}
    have_key = bool(os.environ.get("OPENAI_API_KEY"))
    now_iso = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    red, orange, skipped = [], [], []
    emailed_now = skipped_now = ai_unavailable = errors = 0

    def _persist_done(code: str, entry: dict) -> None:
        # persist the dedup record IMMEDIATELY — a crash later in the run must never lose a
        # sent-mail record (that would double-send tomorrow), mirroring run_posta_uncollected.
        done[code] = entry
        with _lock:
            st = _load_orders_reminder()
            st.setdefault("orders", {})[code] = entry
            _save_orders_reminder(st)

    for o in orders:
        code = o["code"]
        if not o["has_note"]:
            red.append({k: o[k] for k in ("code", "billFullName", "phone", "email",
                                          "itemName", "days", "admin_link")})
            continue
        prev = done.get(code)
        if prev:                                   # already processed once — reflect its status
            row = {k: o[k] for k in ("code", "billFullName", "email", "itemName",
                                     "shopRemark", "days", "admin_link")}
            row["sent_date"] = prev.get("date", "")
            (orange if prev.get("status") == "emailed" else skipped).append(row)
            continue
        if not have_key:
            ai_unavailable += 1
            log.warning("orders_reminder: obj. %s má poznámku, ale OPENAI_API_KEY nie je "
                        "nastavený — AI nedostupné, pripomienku neposielam (skúsim ďalší beh)", code)
            continue                                # do NOT record → retried when key is present
        try:
            contacted = _classify_contacted(o["shopRemark"])
        except Exception as e:  # noqa: BLE001 — recorded per order, run continues
            errors += 1
            log.error("orders_reminder: klasifikácia obj. %s zlyhala: %r", code, e)
            continue
        base = {"name": o["billFullName"], "email": o["email"],
                "itemName": o["itemName"], "note": o["shopRemark"], "date": now_iso}
        if contacted:
            _persist_done(code, {**base, "status": "skipped_contacted"})
            skipped_now += 1
            log.info("orders_reminder: obj. %s — AI: zákazník už kontaktovaný, skipped_contacted", code)
            row = {k: o[k] for k in ("code", "billFullName", "email", "itemName",
                                     "shopRemark", "days", "admin_link")}
            row["sent_date"] = now_iso
            skipped.append(row)
            continue
        if not o["email"]:
            log.error("orders_reminder: obj. %s nemá e-mail — pripomienku nemožno poslať", code)
            errors += 1
            continue
        subject, html = orders_reminder.build_reminder_email(o["billFullName"], code)
        # bcc omitted → _send_mail_html defaults it to MAIL_BCC (the „BCC vždy" convention #105)
        if _send_mail_html(o["email"], subject, html):
            _persist_done(code, {**base, "status": "emailed"})
            emailed_now += 1
            log.info("orders_reminder: obj. %s — pripomienka odoslaná zákazníkovi %s", code, o["email"])
            row = {k: o[k] for k in ("code", "billFullName", "email", "itemName",
                                     "shopRemark", "days", "admin_link")}
            row["sent_date"] = now_iso
            orange.append(row)
        else:
            errors += 1                             # SMTP failed → not recorded → retried next run

    emailed_total = sum(1 for v in done.values() if v.get("status") == "emailed")
    stats = {"orders_4d": len(orders), "no_note": len(red),
             "with_note": len(orders) - len(red),
             "emailed_now": emailed_now, "emailed_total": emailed_total,
             "skipped_now": skipped_now, "ai_unavailable": ai_unavailable,
             "errors": errors}
    with _lock:
        _save_orders_reminder({
            "orders": done,
            "last_check": now_iso,
            "red": red, "orange": orange, "skipped": skipped,
            "stats": stats,
        })
    log.info("orders_reminder: run done %s", stats)
    return stats


AUTOMATIONS_REG = [
    Automation(key="posta_uncollected",
               name="Nevyzdvihnuté zásielky — Pošta SK",
               schedule={"daily_at": "09:00", "tz": "Europe/Bratislava"},
               run_fn=run_posta_uncollected),
    # #119 — hourly guaranteed refresh of the orders export + full catalog export.
    # SAFETY (#93 contract): starts DISABLED like every automation; the manager
    # clicks ▶ Štart. Passive/read-only (no e-mails, no customer side-effects),
    # so it is SAFE to enable immediately once deployed — the deploy itself just
    # never auto-enables anything on its own.
    Automation(key="shoptet_sync",
               name="Sync zo Shoptetu",
               schedule={"interval_minutes": 60, "tz": "Europe/Bratislava"},
               run_fn=run_shoptet_sync),
    # #109 — nightly push of new pairings + assigned suppliers to the Shoptet
    # eshop (migrated from n8n YuDugCCOnwejRfva). SAFETY (#93 contract): starts
    # DISABLED — this one WRITES to the live production eshop, so it runs ONLY
    # after the manager clicks ▶ Štart; a deploy never auto-pushes on its own.
    Automation(key="parovania_eshop",
               name="Párovania → eshop",
               schedule={"daily_at": "21:00", "tz": "Europe/Bratislava"},
               run_fn=run_parovania_eshop),
    # #106 — daily supplier availability/price scraper. SAFETY (#93 contract):
    # starts DISABLED — a run makes MANY external HTTP calls AND costs money via
    # OpenAI, so it runs ONLY after the manager clicks ▶ Štart; a deploy never
    # scrapes or spends on its own.
    Automation(key="dodavatelsky_sklad",
               name="Dodávateľský sklad",
               schedule={"daily_at": "05:00", "tz": "Europe/Bratislava"},
               run_fn=run_supplier_stock),
    # #107 — daily supply-risk report (products we still show as Skladom but our
    # supplier has sold out). SAFETY (#93 contract): starts DISABLED like every
    # other automation, for consistency — even though it is purely READ-ONLY /
    # advisory (no e-mail, no eshop write, no cost) it runs ONLY after the manager
    # clicks ▶ Štart, same as shoptet_sync; a deploy never auto-enables anything.
    Automation(key="riziko_vypadku",
               name="Riziko výpadku",
               schedule={"daily_at": "06:15", "tz": "Europe/Bratislava"},
               run_fn=run_riziko_vypadku),
    # #108 — daily restock (products WE show as Vypredané but our supplier has stock
    # again → flip back to Skladom). SAFETY (#93 contract): starts DISABLED — this one
    # WRITES to the live production eshop, so it runs ONLY after the manager clicks
    # ▶ Štart; a deploy never auto-restocks on its own. Scheduled after the 05:00
    # supplier scrape (fresh data) and the 06:15 riziko report brackets it.
    Automation(key="restock_skladom",
               name="Vypredané → Skladom",
               schedule={"daily_at": "06:00", "tz": "Europe/Bratislava"},
               run_fn=run_restock_skladom),
    # #105 — daily „Vybavuje sa" >4d orders → red (no note) / AI-classified reminder e-mail
    # (with note). SAFETY (#93 contract): starts DISABLED — it SENDS real customer e-mails AND
    # costs money via OpenAI, so it runs ONLY after the manager clicks ▶ Štart; a deploy never
    # e-mails or spends on its own. Scheduled 08:00 like the original n8n workflow.
    Automation(key="orders_reminder",
               name="Pripomienky objednávok",
               schedule={"daily_at": "08:00", "tz": "Europe/Bratislava"},
               run_fn=run_orders_reminder),
]
RUNNER = AutomationRunner(AUTOMATIONS_STATE, AUTOMATIONS_REG)


@app.route("/api/automations")
def api_automations():
    """Status of every registered automation (sidebar + tab header). Session-
    gated by the default-deny before_request like every other endpoint."""
    return jsonify({"automations": RUNNER.status()})


@app.route("/api/automations/<key>/toggle", methods=["POST"])
def api_automation_toggle(key):
    body = request.get_json(silent=True) or {}
    enabled = bool(body.get("enabled"))
    try:
        RUNNER.set_enabled(key, enabled)
    except KeyError:
        return jsonify({"ok": False, "error": "neznáma automatizácia"}), 404
    log.info("automations: %s -> %s (user %s)", key,
             "enabled" if enabled else "disabled", session.get("user"))
    return jsonify({"ok": True, "enabled": enabled})


@app.route("/api/automations/<key>/run", methods=["POST"])
def api_automation_run(key):
    try:
        started = RUNNER.run_now(key)
    except KeyError:
        return jsonify({"ok": False, "error": "neznáma automatizácia"}), 404
    log.info("automations: manual run of %s by %s (started=%s)",
             key, session.get("user"), started)
    return jsonify({"ok": True, "started": started})


@app.route("/api/posta-uncollected")
def api_posta_uncollected():
    """Display data for the 'Nevyzdvihnuté zásielky' tab — the last run's full
    result (uncollected + invalid-format + per-shipment errors)."""
    with _lock:
        st = _load_posta_state()
    return jsonify({
        "last_check": st.get("last_check", ""),
        "uncollected": st.get("uncollected") or [],
        "invalid": st.get("invalid") or [],
        "errors": st.get("errors") or [],
        "stats": st.get("stats") or {},
    })


@app.route("/api/orders-reminder")
def api_orders_reminder():
    """Display data for the „Pripomienky objednávok" tab (#105) — the last run's red list (no-note
    >4d orders), orange list (reminder e-mail sent) + summary stats."""
    with _lock:
        st = _load_orders_reminder()
    return jsonify({
        "last_check": st.get("last_check", ""),
        "red": st.get("red") or [],
        "orange": st.get("orange") or [],
        "skipped": st.get("skipped") or [],
        "stats": st.get("stats") or {},
    })


@app.route("/api/supplier-stock")
def api_supplier_stock():
    """Display data for the „Dodávateľský sklad" tab — the last scraper run's rows
    (availability / price / source / last-checked / errors) + summary stats."""
    with _lock:
        st = _load_supplier_stock()
    return jsonify({
        "last_check": st.get("last_check", ""),
        "rows": st.get("rows") or [],
        "stats": st.get("stats") or {},
    })


@app.route("/api/riziko-vypadku")
def api_riziko_vypadku():
    """Display data for the „Riziko výpadku" tab — the last join's risk rows +
    whether the '#106 Dodávateľský sklad' scraper has ever produced data at all
    (has_supplier_data=False -> the tab shows 'spusti Dodávateľský sklad first',
    never a misleading empty 'no risk' list)."""
    with _lock:
        st = _load_riziko()
    return jsonify({
        "last_check": st.get("last_check", ""),
        "has_supplier_data": bool(st.get("has_supplier_data")),
        "supplier_last_check": st.get("supplier_last_check", ""),
        "risks": st.get("risks") or [],
    })


@app.route("/api/riziko-vypadku/csv")
def api_riziko_vypadku_csv():
    """Optional CSV download of the last run's risk rows (per the digest — ready
    to eyeball or hand to someone deciding which to flip to 'Vypredané'). Never
    imported back automatically by this app, but formula-injection-guarded like
    every other CSV sink here (_csv_safe)."""
    with _lock:
        st = _load_riziko()
    header = ["kod", "parovaci_kod", "nazov", "dodavatel", "nasa_cena", "nas_sklad",
              "dostupnost_u_dodavatela", "link", "kontrolovane"]
    rows = [[_csv_safe(r.get(k, "")) for k in riziko_vypadku.RISK_FIELDS]
            for r in (st.get("risks") or [])]
    return _csv_response(header, rows, "riziko_vypadku.csv")


@app.route("/api/restock-skladom")
def api_restock_skladom():
    """Display data for the „Vypredané → Skladom" tab — the last restock run's
    candidate products (kód, názov, naša cena vs cena dodávateľa, linky), the import
    outcome (spracované / naskladnené / zlyhania), and whether the '#106 Dodávateľský
    sklad' scraper has produced data at all (has_supplier_data=False → the tab shows
    'najprv spusti Dodávateľský sklad', never a misleading empty list)."""
    with _lock:
        st = _load_restock()
    return jsonify({
        "last_check": st.get("last_check", ""),
        "has_supplier_data": bool(st.get("has_supplier_data")),
        "supplier_last_check": st.get("supplier_last_check", ""),
        "status": st.get("status", ""),
        "candidates": st.get("candidates") or [],
        "processed": st.get("processed"),
        "updated": st.get("updated"),
        "failed": st.get("failed"),
        "error_detail": st.get("error_detail", ""),
    })


if __name__ == "__main__":
    RUNNER.start()
    app.run(host="0.0.0.0", port=int(os.environ.get("WEBREVIEW_PORT", "8801")),
            threaded=True)
