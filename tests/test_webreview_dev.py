"""Tests for the „Vývoj" tab endpoints (#115): list this repo's GitHub issues +
create an issue from the idea lightbulb.

Hermetic: the GitHub REST calls (webapp.requests.get / .post) are monkeypatched —
NO real network, NO real GitHub. The autouse guard below ALSO strips any real
GITHUB_TOKEN the dev box loaded from data/.gh_env at import, so a forgotten mock
can never hit the live repo; every test opts in with its own fake + token.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webreview"))
import app as webapp  # noqa: E402

from tests.conftest import authed_client as _client  # noqa: E402 — logged-in session (#91)


class _Resp:
    """Minimal stand-in for a requests.Response."""
    def __init__(self, status, data):
        self.status_code = status
        self._data = data

    def json(self):
        return self._data


@pytest.fixture(autouse=True)
def _no_real_github(monkeypatch):
    """Hard guard: never touch real GitHub. The dev box's data/.gh_env loads a real
    token into os.environ at import — strip it, and make requests.get/post raise so
    a test that forgot to mock fails loudly instead of calling the live repo."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPO", raising=False)

    def _boom(*a, **k):
        raise AssertionError("real GitHub call in a hermetic test")

    monkeypatch.setattr(webapp.requests, "get", _boom)
    monkeypatch.setattr(webapp.requests, "post", _boom)


def _configure(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("GITHUB_REPO", "owner/repo")


# --------------------------------------------------------------------------- #
# /api/dev/issues — list open + closed, PRs filtered out
# --------------------------------------------------------------------------- #
def test_dev_issues_requires_login():
    c = webapp.app.test_client()          # no session → default-deny gate
    r = c.get("/api/dev/issues")
    assert r.status_code == 401


def test_dev_issues_lists_open_and_closed_filtering_prs(monkeypatch):
    _configure(monkeypatch)
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        captured["headers"] = headers
        return _Resp(200, [
            {"number": 5, "title": "Otvorená úloha", "state": "open",
             "labels": [{"name": "bug"}, {"name": "prio:bounce"}],
             "updated_at": "2026-07-20T10:00:00Z",
             "html_url": "https://github.com/owner/repo/issues/5", "comments": 2},
            {"number": 4, "title": "Hotová úloha", "state": "closed", "labels": [],
             "updated_at": "2026-07-19T10:00:00Z",
             "html_url": "https://github.com/owner/repo/issues/4", "comments": 0},
            {"number": 3, "title": "Toto je pull request", "state": "open", "labels": [],
             "updated_at": "2026-07-18T10:00:00Z",
             "html_url": "https://github.com/owner/repo/pull/3", "comments": 0,
             "pull_request": {"url": "https://api.github.com/.../pulls/3"}},
        ])

    monkeypatch.setattr(webapp.requests, "get", fake_get)
    r = _client().get("/api/dev/issues")
    assert r.status_code == 200
    j = r.get_json()
    assert j["ok"] is True and j["available"] is True
    nums = [i["number"] for i in j["issues"]]
    assert nums == [5, 4]                           # PR #3 filtered out
    assert j["issues"][0]["state"] == "open"
    assert j["issues"][1]["state"] == "closed"
    assert j["issues"][0]["labels"] == ["bug", "prio:bounce"]
    # request asks for BOTH states
    assert captured["params"]["state"] == "all"
    # the token lives ONLY in the Authorization header — never in the URL
    assert "test-token" not in captured["url"]
    assert captured["headers"]["Authorization"] == "Bearer test-token"


def test_dev_issues_token_missing_degrades_gracefully(monkeypatch):
    # no GITHUB_TOKEN configured (the autouse guard already delenv'd it)
    r = _client().get("/api/dev/issues")
    assert r.status_code == 200                      # graceful, NOT a 500
    j = r.get_json()
    assert j["ok"] is False and j["available"] is False
    assert j["issues"] == []
    assert j["error"]


def test_dev_issues_upstream_error_degrades_gracefully(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(webapp.requests, "get",
                        lambda *a, **k: _Resp(401, {"message": "Bad credentials"}))
    r = _client().get("/api/dev/issues")
    assert r.status_code == 200                      # never 500
    j = r.get_json()
    assert j["ok"] is False and j["available"] is False
    assert j["issues"] == []


def test_dev_issues_network_error_degrades_gracefully(monkeypatch):
    _configure(monkeypatch)

    def boom(*a, **k):
        raise webapp.requests.exceptions.ConnectionError("down")

    monkeypatch.setattr(webapp.requests, "get", boom)
    r = _client().get("/api/dev/issues")
    assert r.status_code == 200                      # never 500
    assert r.get_json()["available"] is False


# --------------------------------------------------------------------------- #
# /api/dev/idea — create an issue (with validation)
# --------------------------------------------------------------------------- #
def test_dev_idea_creates_issue(monkeypatch):
    _configure(monkeypatch)
    sent = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        sent["url"] = url
        sent["json"] = json
        sent["headers"] = headers
        return _Resp(201, {
            "number": 42, "title": json["title"], "state": "open", "labels": [],
            "updated_at": "2026-07-22T12:00:00Z",
            "html_url": "https://github.com/owner/repo/issues/42", "comments": 0})

    monkeypatch.setattr(webapp.requests, "post", fake_post)
    r = _client().post("/api/dev/idea",
                       json={"title": "Nový nápad", "description": "detaily nápadu"})
    assert r.status_code == 201
    j = r.get_json()
    assert j["ok"] is True
    assert j["issue"]["number"] == 42
    assert j["issue"]["title"] == "Nový nápad"
    # posted to the configured repo's issues endpoint, token only in the header
    assert sent["url"].endswith("/repos/owner/repo/issues")
    assert sent["json"]["title"] == "Nový nápad"
    assert "detaily nápadu" in sent["json"]["body"]
    assert "test-token" not in sent["url"]
    assert sent["headers"]["Authorization"] == "Bearer test-token"


def test_dev_idea_empty_title_rejected_before_github(monkeypatch):
    _configure(monkeypatch)
    # requests.post is still the raising guard → a 400 must come BEFORE any call
    r = _client().post("/api/dev/idea", json={"title": "   "})
    assert r.status_code == 400
    assert r.get_json()["ok"] is False


def test_dev_idea_missing_title_rejected(monkeypatch):
    _configure(monkeypatch)
    r = _client().post("/api/dev/idea", json={"description": "bez názvu"})
    assert r.status_code == 400


def test_dev_idea_title_too_long_rejected(monkeypatch):
    _configure(monkeypatch)
    r = _client().post("/api/dev/idea", json={"title": "x" * 5000})
    assert r.status_code == 400


def test_dev_idea_token_missing_degrades_gracefully(monkeypatch):
    # no token; requests.post is the raising guard → must NOT be called
    r = _client().post("/api/dev/idea", json={"title": "Nápad bez tokenu"})
    assert r.status_code != 500                       # graceful, never a 500
    j = r.get_json()
    assert j["ok"] is False and j["available"] is False
    assert j["error"]
