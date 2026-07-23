"""Tests for #173: admin-set custom nav/automation display names
(data/out/ui_labels.json, /api/ui-labels + /api/ui-label) and the plain-language
automation descriptions surfaced on /api/automations.

Mirrors test_webreview_auth.py's admin/non-admin fixture pattern (own isolated
users store + session_transaction login — no form/CSRF needed for a session
that's already established) rather than the shared `authed_client()` (always
admin=True), since several tests here need BOTH an admin and a non-admin.
"""
import json
import os
import sys

import pytest
from werkzeug.security import generate_password_hash

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webreview"))
import app as webapp  # noqa: E402

ADMIN = "admin@test.sk"
ADMIN_PW = "admin-heslo-123"
USER = "clen@test.sk"
USER_PW = "clen-heslo-123"


@pytest.fixture
def iso(tmp_path, monkeypatch):
    """Isolated ui_labels.json + users.json (admin + non-admin) + isolated
    automation state — never touches the real data/out."""
    monkeypatch.setattr(webapp, "UI_LABELS", str(tmp_path / "ui_labels.json"))
    monkeypatch.setattr(webapp.RUNNER, "state_path", str(tmp_path / "automations.json"))
    users = {
        ADMIN: {"pw_hash": generate_password_hash(ADMIN_PW), "is_admin": True,
                "created_at": "2026-01-01T00:00:00+00:00"},
        USER: {"pw_hash": generate_password_hash(USER_PW), "is_admin": False,
               "created_at": "2026-01-01T00:00:00+00:00"},
    }
    up = tmp_path / "users.json"
    up.write_text(json.dumps(users), encoding="utf-8")
    monkeypatch.setattr(webapp, "USERS", str(up))
    return tmp_path


def _client_as(email):
    c = webapp.app.test_client()
    with c.session_transaction() as s:
        s["user"] = email
    return c


# ── auth gate ──────────────────────────────────────────────────────────────

def test_anon_gets_401(iso):
    c = webapp.app.test_client()
    assert c.get("/api/ui-labels").status_code == 401
    assert c.post("/api/ui-label", json={"key": "toorder", "label": "x"}).status_code == 401


def test_non_admin_forbidden_on_post(iso):
    c = _client_as(USER)
    r = c.post("/api/ui-label", json={"key": "toorder", "label": "Objednávky"})
    assert r.status_code == 403
    # GET stays open to a non-admin (everyone must see a renamed tab's new name)
    assert c.get("/api/ui-labels").status_code == 200


# ── GET/POST round trip ──────────────────────────────────────────────────────

def test_get_empty_by_default(iso):
    c = _client_as(ADMIN)
    assert c.get("/api/ui-labels").get_json() == {"labels": {}}


def test_admin_set_get_and_clear(iso):
    c = _client_as(ADMIN)
    r = c.post("/api/ui-label", json={"key": "toorder", "label": "Moje objednávky"})
    assert r.status_code == 200
    assert r.get_json() == {"ok": True, "label": "Moje objednávky"}
    assert c.get("/api/ui-labels").get_json() == {"labels": {"toorder": "Moje objednávky"}}

    # a second key coexists with the first
    c.post("/api/ui-label", json={"key": "review", "label": "Kontrola"})
    assert c.get("/api/ui-labels").get_json()["labels"] == {
        "toorder": "Moje objednávky", "review": "Kontrola"}

    # empty (or whitespace-only) label clears the override
    r2 = c.post("/api/ui-label", json={"key": "toorder", "label": "   "})
    assert r2.status_code == 200
    assert r2.get_json() == {"ok": True, "label": ""}
    assert c.get("/api/ui-labels").get_json()["labels"] == {"review": "Kontrola"}


def test_leading_trailing_whitespace_trimmed(iso):
    c = _client_as(ADMIN)
    c.post("/api/ui-label", json={"key": "notes", "label": "  Zápisník  "})
    assert c.get("/api/ui-labels").get_json()["labels"]["notes"] == "Zápisník"


# ── validation ───────────────────────────────────────────────────────────────

def test_unknown_key_rejected(iso):
    c = _client_as(ADMIN)
    r = c.post("/api/ui-label", json={"key": "nope-does-not-exist", "label": "x"})
    assert r.status_code == 400
    assert c.get("/api/ui-labels").get_json() == {"labels": {}}


def test_label_too_long_rejected(iso):
    c = _client_as(ADMIN)
    r = c.post("/api/ui-label",
                json={"key": "review", "label": "x" * (webapp.UI_LABEL_MAX + 1)})
    assert r.status_code == 400
    assert c.get("/api/ui-labels").get_json() == {"labels": {}}


def test_label_at_max_length_accepted(iso):
    c = _client_as(ADMIN)
    label = "x" * webapp.UI_LABEL_MAX
    r = c.post("/api/ui-label", json={"key": "review", "label": label})
    assert r.status_code == 200


# ── automation keys are renameable too (same key namespace as automation tabs) ──

def test_automation_key_renameable(iso):
    c = _client_as(ADMIN)
    r = c.post("/api/ui-label", json={"key": "shoptet_sync", "label": "Rýchlejší sync"})
    assert r.status_code == 200
    assert c.get("/api/ui-labels").get_json()["labels"] == {"shoptet_sync": "Rýchlejší sync"}


# The nav keys the frontend actually renders a rename pencil for — app.js's
# TABS + AUTOMATION_TABS, mirrored here verbatim. Deliberately NOT the same as
# {a.key for a in AUTOMATIONS_REG}: the "Nevyzdvihnuté zásielky" tab's nav key
# is the legacy "posta", while its Automation.key is "posta_uncollected" — only
# the NAV key is ever sent to /api/ui-label.
NAV_TAB_KEYS = [
    "toorder", "search", "notes", "review",
    "posta", "orders_reminder", "shoptet_sync", "parovania_eshop",
    "dodavatelsky_sklad", "riziko_vypadku", "restock_skladom", "image_health",
    "users", "dev",
]


def test_every_nav_tab_key_is_renameable(iso):
    # A key present in app.js's TABS/AUTOMATION_TABS but rejected by
    # /api/ui-label would silently make that one tab un-renameable.
    c = _client_as(ADMIN)
    for key in NAV_TAB_KEYS:
        r = c.post("/api/ui-label", json={"key": key, "label": "x"})
        assert r.status_code == 200, key
        c.post("/api/ui-label", json={"key": key, "label": ""})   # clean up


def test_automation_registry_key_rejected_for_posta(iso):
    # Regression for the posta/posta_uncollected key-namespace split above:
    # the Automation.key ("posta_uncollected") must NOT be accepted — nothing
    # on the frontend would ever read a label stored under it.
    c = _client_as(ADMIN)
    r = c.post("/api/ui-label", json={"key": "posta_uncollected", "label": "x"})
    assert r.status_code == 400


# ── /api/automations carries a description for every automation (#173) ──────

def test_automations_all_carry_description(iso):
    c = _client_as(ADMIN)
    autos = c.get("/api/automations").get_json()["automations"]
    assert len(autos) == len(webapp.AUTOMATIONS_REG)
    for a in autos:
        assert isinstance(a.get("description"), str) and a["description"], a["key"]
