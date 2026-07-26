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
    monkeypatch.setattr(webapp.requests, "delete", _boom)
    monkeypatch.setattr(webapp.requests, "patch", _boom)   # #243 — the edit verb


def _configure(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("GITHUB_REPO", "owner/repo")


# GitHub serves PULL REQUESTS from /issues/{n} too, so every path that acts on a
# number the user typed has to READ the issue first and refuse a PR. That read is why
# note/priority/detail all need `requests.get` armed now.
_PLAIN_ISSUE = {"number": 5, "title": "Úloha", "state": "open", "body": "",
                "labels": [], "html_url": "https://example.test/issues/5"}
_PULL_REQUEST = dict(_PLAIN_ISSUE, number=7, title="Nejaké PR",
                     pull_request={"url": "https://api.test/pulls/7"})


def _arm_read(monkeypatch, issue=None):
    """Arm the pre-write read every by-number endpoint performs (the PR guard)."""
    payload = dict(issue if issue is not None else _PLAIN_ISSUE)
    monkeypatch.setattr(webapp.requests, "get",
                        lambda url, **k: _Resp(200, [])
                        if "/comments" in url else _Resp(200, dict(payload)))


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


def test_dev_issues_paginates_until_short_page(monkeypatch):
    """/issues returns issues AND PRs interleaved — a single page can push older
    issues off after filtering, so the endpoint pages through (bounded)."""
    _configure(monkeypatch)
    full = [{"number": n, "title": f"i{n}", "state": "open", "labels": [],
             "updated_at": "2026-07-20T10:00:00Z",
             "html_url": f"https://github.com/owner/repo/issues/{n}", "comments": 0}
            for n in range(webapp.GH_LIST_PER_PAGE)]      # exactly one full page
    tail = [{"number": 999, "title": "posledna", "state": "closed", "labels": [],
             "updated_at": "2026-07-01T10:00:00Z",
             "html_url": "https://github.com/owner/repo/issues/999", "comments": 0}]
    seen_pages = []

    def fake_get(url, params=None, headers=None, timeout=None):
        page = params["page"]
        seen_pages.append(page)
        if page == 1:
            return _Resp(200, full)          # full → the loop must fetch page 2
        if page == 2:
            return _Resp(200, tail)          # short → stop
        raise AssertionError(f"paged too far: {page}")

    monkeypatch.setattr(webapp.requests, "get", fake_get)
    r = _client().get("/api/dev/issues")
    j = r.get_json()
    assert seen_pages == [1, 2]
    assert len(j["issues"]) == webapp.GH_LIST_PER_PAGE + 1
    assert j["issues"][-1]["number"] == 999


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


# --------------------------------------------------------------------------- #
# /api/dev/issues — priority label is lifted into `priority` + stripped from view
# --------------------------------------------------------------------------- #
def test_dev_issues_priority_label_lifted_and_hidden(monkeypatch):
    _configure(monkeypatch)

    def fake_get(url, params=None, headers=None, timeout=None):
        return _Resp(200, [
            {"number": 7, "title": "Súrne", "state": "open",
             "labels": [{"name": "bug"}, {"name": "prio:soon"}],
             "updated_at": "2026-07-22T10:00:00Z",
             "html_url": "https://github.com/owner/repo/issues/7", "comments": 1},
            {"number": 6, "title": "Počká", "state": "open",
             "labels": [{"name": "prio:later"}],
             "updated_at": "2026-07-21T10:00:00Z",
             "html_url": "https://github.com/owner/repo/issues/6", "comments": 0},
        ])

    monkeypatch.setattr(webapp.requests, "get", fake_get)
    j = _client().get("/api/dev/issues").get_json()
    a, b = j["issues"]
    assert a["priority"] == "soon" and a["labels"] == ["bug"]   # prio label hidden
    assert b["priority"] == "later" and b["labels"] == []
    assert "prio:soon" not in a["labels"] and "prio:later" not in b["labels"]


# --------------------------------------------------------------------------- #
# /api/dev/issue/<n>/note — append a detail as a GitHub comment
# --------------------------------------------------------------------------- #
def test_dev_note_requires_login():
    c = webapp.app.test_client()
    r = c.post("/api/dev/issue/5/note", json={"text": "detail"})
    assert r.status_code == 401


def test_dev_note_posts_comment(monkeypatch):
    _configure(monkeypatch)
    _arm_read(monkeypatch)
    sent = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        sent["url"] = url
        sent["json"] = json
        sent["headers"] = headers
        return _Resp(201, {"id": 111})

    monkeypatch.setattr(webapp.requests, "post", fake_post)
    r = _client().post("/api/dev/issue/5/note", json={"text": "šéfov detail k úlohe"})
    assert r.status_code == 201
    assert r.get_json()["ok"] is True
    # posted to the issue's COMMENTS endpoint — the body is never overwritten
    assert sent["url"].endswith("/repos/owner/repo/issues/5/comments")
    assert "šéfov detail k úlohe" in sent["json"]["body"]
    assert "test-token" not in sent["url"]
    assert sent["headers"]["Authorization"] == "Bearer test-token"


def test_dev_note_empty_rejected_before_github(monkeypatch):
    _configure(monkeypatch)
    # requests.post is still the raising guard → 400 must come BEFORE any call
    r = _client().post("/api/dev/issue/5/note", json={"text": "   "})
    assert r.status_code == 400
    assert r.get_json()["ok"] is False


def test_dev_note_too_long_rejected(monkeypatch):
    _configure(monkeypatch)
    r = _client().post("/api/dev/issue/5/note", json={"text": "x" * (webapp.NOTE_MAX + 1)})
    assert r.status_code == 400


def test_dev_note_on_a_pull_request_is_refused(monkeypatch):
    """The refusal was added to /edit only, so any logged-in shop user could comment
    on ANY pull request of the repo just by typing its number — GitHub's /issues/{n}
    serves PRs under the same numbering. The rule is „every endpoint that WRITES by
    number", not „the one we happened to be fixing"."""
    _configure(monkeypatch)
    _arm_read(monkeypatch, _PULL_REQUEST)
    # a RECORDING stub, not a raising one: the endpoints swallow every exception into
    # a generic „GitHub nedostupný", which is also non-ASCII — a raising stub would
    # make this test pass while the write still went through in production
    calls = []
    monkeypatch.setattr(webapp.requests, "post",
                        lambda *a, **k: (calls.append(a), _Resp(201, {"id": 1}))[1])
    r = _client().post("/api/dev/issue/7/note", json={"text": "detail"})
    assert calls == [], "commented on a pull request"
    assert r.status_code == 200 and r.get_json()["ok"] is False
    assert r.get_json()["error"] == "toto číslo nepatrí úlohe"


def test_dev_priority_on_a_pull_request_is_refused(monkeypatch):
    """Same hole in the labelling path: prio labels could be added to and removed
    from any PR of the repo by number alone."""
    _configure(monkeypatch)
    _arm_read(monkeypatch, _PULL_REQUEST)
    calls = []                                    # recording, not raising — see above
    monkeypatch.setattr(webapp.requests, "post",
                        lambda *a, **k: (calls.append(("POST",) + a), _Resp(200, []))[1])
    monkeypatch.setattr(webapp.requests, "delete",
                        lambda *a, **k: (calls.append(("DELETE",) + a), _Resp(200, []))[1])
    r = _client().post("/api/dev/issue/7/priority", json={"priority": "soon"})
    assert calls == [], "touched a pull request's labels"
    assert r.status_code == 200 and r.get_json()["ok"] is False
    assert r.get_json()["error"] == "toto číslo nepatrí úlohe"


def test_dev_issue_detail_of_a_pull_request_is_refused(monkeypatch):
    """Lower harm than the writes, but the same confusion: the boss's task list never
    shows PRs, so serving one's body and comments on a hand-typed number is a leak of
    something he is not meant to see through this tab at all."""
    _configure(monkeypatch)
    _arm_read(monkeypatch, dict(_PULL_REQUEST, body="interné PR telo"))
    r = _client().get("/api/dev/issue/7")
    assert r.status_code == 200
    j = r.get_json()
    assert j["ok"] is False and "interné PR telo" not in str(j)
    assert not j["error"].isascii()


def test_dev_note_token_missing_degrades_gracefully(monkeypatch):
    # no token; requests.post is the raising guard → must NOT be called
    r = _client().post("/api/dev/issue/5/note", json={"text": "detail bez tokenu"})
    assert r.status_code != 500
    j = r.get_json()
    assert j["ok"] is False and j["available"] is False


# --------------------------------------------------------------------------- #
# /api/dev/issue/<n>/priority — manage hidden prio labels
# --------------------------------------------------------------------------- #
def test_dev_priority_requires_login():
    c = webapp.app.test_client()
    r = c.post("/api/dev/issue/5/priority", json={"priority": "soon"})
    assert r.status_code == 401


def test_dev_priority_soon_adds_label_and_removes_opposite(monkeypatch):
    _configure(monkeypatch)
    _arm_read(monkeypatch)
    calls = []

    def fake_delete(url, headers=None, timeout=None):
        calls.append(("DELETE", url))
        return _Resp(200, [])

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(("POST", url, json))
        return _Resp(200, [{"name": "prio:soon"}])

    monkeypatch.setattr(webapp.requests, "delete", fake_delete)
    monkeypatch.setattr(webapp.requests, "post", fake_post)
    r = _client().post("/api/dev/issue/5/priority", json={"priority": "soon"})
    assert r.status_code == 200
    assert r.get_json() == {"ok": True, "priority": "soon"}
    # the opposite label was deleted from the issue (name URL-encoded)
    deletes = [c[1] for c in calls if c[0] == "DELETE"]
    assert any(u.endswith("/issues/5/labels/prio%3Alater") for u in deletes)
    # the chosen label was added to the issue (labels endpoint, token in header only)
    add = [c for c in calls if c[0] == "POST" and c[1].endswith("/issues/5/labels")]
    assert add and add[0][2] == {"labels": ["prio:soon"]}


def test_dev_priority_none_clears_both_and_adds_nothing(monkeypatch):
    _configure(monkeypatch)
    _arm_read(monkeypatch)
    calls = []

    def fake_delete(url, headers=None, timeout=None):
        calls.append(("DELETE", url))
        return _Resp(200, [])

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(("POST", url, json))
        return _Resp(201, {})

    monkeypatch.setattr(webapp.requests, "delete", fake_delete)
    monkeypatch.setattr(webapp.requests, "post", fake_post)
    r = _client().post("/api/dev/issue/5/priority", json={"priority": "none"})
    assert r.status_code == 200
    assert r.get_json() == {"ok": True, "priority": ""}
    # both prio labels removed, NO label-add / label-create POST
    deletes = [u for m, u in calls if m == "DELETE"]
    assert any("prio%3Asoon" in u for u in deletes)
    assert any("prio%3Alater" in u for u in deletes)
    assert not [c for c in calls if c[0] == "POST"]


def test_dev_priority_delete_failure_reported_not_silent(monkeypatch):
    """A failed opposite-label DELETE (403 secondary rate-limit / 5xx) must be
    surfaced as ok:False — never reported as success with the label still on the
    issue. And the add-label POST must NOT run after a failed delete."""
    _configure(monkeypatch)
    _arm_read(monkeypatch)

    def fake_delete(url, headers=None, timeout=None):
        return _Resp(403, {"message": "secondary rate limit"})

    def fake_post(url, json=None, headers=None, timeout=None):
        raise AssertionError("add-label must not run after a failed delete")

    monkeypatch.setattr(webapp.requests, "delete", fake_delete)
    monkeypatch.setattr(webapp.requests, "post", fake_post)
    r = _client().post("/api/dev/issue/5/priority", json={"priority": "soon"})
    assert r.status_code == 200
    j = r.get_json()
    assert j["ok"] is False                       # failure surfaced, NOT silent ok
    assert "403" in (j.get("error") or "")


def test_dev_priority_delete_404_is_ok(monkeypatch):
    """DELETE 404 means the opposite label simply wasn't set — that is success,
    not a failure; the chosen label still gets added."""
    _configure(monkeypatch)
    _arm_read(monkeypatch)

    def fake_delete(url, headers=None, timeout=None):
        return _Resp(404, {"message": "Label does not exist"})

    def fake_post(url, json=None, headers=None, timeout=None):
        return _Resp(200, [{"name": "prio:soon"}])

    monkeypatch.setattr(webapp.requests, "delete", fake_delete)
    monkeypatch.setattr(webapp.requests, "post", fake_post)
    r = _client().post("/api/dev/issue/5/priority", json={"priority": "soon"})
    assert r.get_json() == {"ok": True, "priority": "soon"}


def test_dev_priority_none_delete_failure_reported(monkeypatch):
    """Clearing to 'none' with a failing DELETE must NOT report ok unconditionally
    — the pre-fix bug returned ok:True even when the labels weren't removed."""
    _configure(monkeypatch)
    _arm_read(monkeypatch)

    def fake_delete(url, headers=None, timeout=None):
        return _Resp(500, {"message": "server error"})

    monkeypatch.setattr(webapp.requests, "delete", fake_delete)
    # requests.post stays the raising guard — 'none' must not POST anything
    r = _client().post("/api/dev/issue/5/priority", json={"priority": "none"})
    assert r.status_code == 200
    assert r.get_json()["ok"] is False


def test_dev_priority_invalid_rejected_before_github(monkeypatch):
    _configure(monkeypatch)
    # requests.* are raising guards → a 400 must come BEFORE any GitHub call
    r = _client().post("/api/dev/issue/5/priority", json={"priority": "urgent"})
    assert r.status_code == 400
    assert r.get_json()["ok"] is False


def test_dev_priority_token_missing_degrades_gracefully(monkeypatch):
    # no token; requests.* are raising guards → must NOT be called
    r = _client().post("/api/dev/issue/5/priority", json={"priority": "soon"})
    assert r.status_code != 500
    j = r.get_json()
    assert j["ok"] is False and j["available"] is False


# --------------------------------------------------------------------------- #
# GET /api/dev/issue/<n> — issue body + all comments (the boss reads everything)
# --------------------------------------------------------------------------- #
def test_dev_issue_detail_requires_login():
    c = webapp.app.test_client()
    r = c.get("/api/dev/issue/5")
    assert r.status_code == 401


def test_dev_issue_detail_returns_body_and_comments(monkeypatch):
    _configure(monkeypatch)

    def fake_get(url, params=None, headers=None, timeout=None):
        if url.endswith("/issues/5/comments"):
            return _Resp(200, [
                {"body": "prvý detail", "created_at": "2026-07-22T10:00:00Z"},
                {"body": "druhý detail", "created_at": "2026-07-22T11:00:00Z"},
            ])
        if url.endswith("/issues/5"):
            return _Resp(200, {"number": 5, "body": "zadanie úlohy"})
        raise AssertionError(f"unexpected GET {url}")

    monkeypatch.setattr(webapp.requests, "get", fake_get)
    j = _client().get("/api/dev/issue/5").get_json()
    assert j["ok"] is True
    assert j["body"] == "zadanie úlohy"
    assert [c["body"] for c in j["comments"]] == ["prvý detail", "druhý detail"]
    assert "test-token" not in "".join([j["body"], j["comments"][0]["body"]])


def test_dev_issue_detail_upstream_error_degrades_gracefully(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(webapp.requests, "get",
                        lambda *a, **k: _Resp(404, {"message": "Not Found"}))
    r = _client().get("/api/dev/issue/5")
    assert r.status_code == 200                      # never 500
    assert r.get_json()["ok"] is False


def test_dev_issue_detail_token_missing_degrades_gracefully():
    # no token; requests.get is the raising guard → must NOT be called
    r = _client().get("/api/dev/issue/5")
    assert r.status_code == 200
    j = r.get_json()
    assert j["ok"] is False and j["available"] is False


# --- #243: editing an already-submitted request ----------------------------- #
# The boss could add a comment but never CORRECT what he had already sent, so a request
# that came out wrong was simply abandoned („nedá sa, tak som sa na to vykašlal").
# `gh issue edit` cannot do this on this repo (deprecated classic-Projects GraphQL), so
# the endpoint is a plain REST PATCH.
_ISSUE_WITH_MARKER = {
    "number": 5, "title": "Pôvodný názov", "state": "open",
    "body": "Pôvodný text požiadavky.\n\n_Nápad cez appku (Vývoj) — sef@forestshop.sk_",
    "labels": [], "updated_at": "2026-07-26T09:00:00Z",
    "html_url": "https://example.test/issues/5", "comments": 0,
}


def _arm_edit(monkeypatch, current=None, patch_status=200):
    """GET returns the current issue, PATCH records what was sent."""
    _configure(monkeypatch)
    sent = {}
    monkeypatch.setattr(webapp.requests, "get",
                        lambda *a, **k: _Resp(200, dict(current or _ISSUE_WITH_MARKER)))

    def fake_patch(url, json=None, headers=None, timeout=None):
        sent["url"] = url
        sent["json"] = json
        sent["headers"] = headers
        merged = dict(current or _ISSUE_WITH_MARKER)
        merged.update(json or {})
        return _Resp(patch_status, merged)

    monkeypatch.setattr(webapp.requests, "patch", fake_patch)
    return sent


def test_dev_edit_requires_login(monkeypatch):
    _configure(monkeypatch)
    r = webapp.app.test_client().post("/api/dev/issue/5/edit",
                                      json={"title": "X", "text": "Y"})
    assert r.status_code == 401


def test_dev_edit_rewrites_title_and_body(monkeypatch):
    sent = _arm_edit(monkeypatch)
    r = _client().post("/api/dev/issue/5/edit",
                       json={"title": "Opravený názov", "text": "Doplnený text."})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert sent["url"].endswith("/repos/owner/repo/issues/5")
    assert sent["json"]["title"] == "Opravený názov"
    assert sent["json"]["body"].startswith("Doplnený text.")
    assert "test-token" not in sent["url"]
    assert sent["headers"]["Authorization"] == "Bearer test-token"


def test_dev_edit_keeps_who_first_raised_the_request(monkeypatch):
    """An edit must not erase the author marker — it is the only record of who asked."""
    sent = _arm_edit(monkeypatch)
    _client().post("/api/dev/issue/5/edit", json={"title": "T", "text": "Nový text."})
    assert "_Nápad cez appku (Vývoj) — sef@forestshop.sk_" in sent["json"]["body"]


def test_dev_edit_marker_is_rewritten_not_stacked(monkeypatch):
    """Repeated edits must not grow a changelog nobody asked for: the „Upravené" line
    is replaced each time, never appended."""
    already = dict(_ISSUE_WITH_MARKER)
    already["body"] = ("Text.\n\n_Nápad cez appku (Vývoj) — sef@forestshop.sk_"
                       "\n_Upravené cez appku (Vývoj) — admin@e2e.test_")
    sent = _arm_edit(monkeypatch, already)
    _client().post("/api/dev/issue/5/edit", json={"title": "T", "text": "Iný text."})
    body = sent["json"]["body"]
    assert body.count("_Upravené cez appku (Vývoj)") == 1
    assert body.count("_Nápad cez appku (Vývoj)") == 1


def test_dev_edit_does_not_make_the_boss_retype_the_markers(monkeypatch):
    """The detail hands back `editable` (body minus the app's own signature lines), so
    what he edits is only ever his own text."""
    _configure(monkeypatch)
    monkeypatch.setattr(webapp.requests, "get",
                        lambda url, **k: _Resp(200, dict(_ISSUE_WITH_MARKER))
                        if "/comments" not in url else _Resp(200, []))
    j = _client().get("/api/dev/issue/5").get_json()
    assert j["title"] == "Pôvodný názov"
    assert j["editable"] == "Pôvodný text požiadavky."      # marker stripped
    assert "_Nápad cez appku" in j["body"]                  # full body still shown


def test_dev_edit_of_a_closed_issue_is_refused(monkeypatch):
    """Silently rewriting a request somebody already acted on would be invisible to
    them — a comment is the right tool there."""
    closed = dict(_ISSUE_WITH_MARKER, state="closed")
    sent = _arm_edit(monkeypatch, closed)
    r = _client().post("/api/dev/issue/5/edit", json={"title": "T", "text": "X"})
    assert r.status_code == 200 and r.get_json()["ok"] is False
    assert sent == {}, "must not PATCH a closed issue"


def test_dev_edit_of_a_pull_request_is_refused(monkeypatch):
    """GitHub's /issues/{n} also covers PULL REQUESTS, so a logged-in user could
    rewrite a PR's title and body through this endpoint just by typing its number.
    The list endpoint filters PRs out; this one must too."""
    pr = dict(_ISSUE_WITH_MARKER, pull_request={"url": "https://api.test/pulls/5"})
    sent = _arm_edit(monkeypatch, pr)
    r = _client().post("/api/dev/issue/5/edit", json={"title": "T", "text": "X"})
    assert r.status_code == 200 and r.get_json()["ok"] is False
    assert sent == {}, "must not PATCH a pull request"


def test_dev_edit_reads_the_issue_once(monkeypatch):
    """`_do_edit_issue` parsed the same response body three times."""
    _configure(monkeypatch)
    calls = {"n": 0}

    class _Counting(_Resp):
        def json(self):
            calls["n"] += 1
            return self._data

    monkeypatch.setattr(webapp.requests, "get",
                        lambda *a, **k: _Counting(200, dict(_ISSUE_WITH_MARKER)))
    monkeypatch.setattr(webapp.requests, "patch",
                        lambda url, json=None, headers=None, timeout=None:
                        _Resp(200, dict(_ISSUE_WITH_MARKER)))
    _client().post("/api/dev/issue/5/edit", json={"title": "T", "text": "X"})
    assert calls["n"] == 1, f"read the GET body {calls['n']}× instead of once"


def test_dev_edit_empty_title_rejected_before_github(monkeypatch):
    _configure(monkeypatch)   # requests.get/patch are still the raising guards
    r = _client().post("/api/dev/issue/5/edit", json={"title": "   ", "text": "X"})
    assert r.status_code == 400 and r.get_json()["ok"] is False


def test_dev_edit_too_long_rejected_before_github(monkeypatch):
    _configure(monkeypatch)
    r = _client().post("/api/dev/issue/5/edit",
                       json={"title": "T" * 201, "text": "X"})
    assert r.status_code == 400
    r = _client().post("/api/dev/issue/5/edit",
                       json={"title": "T", "text": "X" * 5001})
    assert r.status_code == 400


def test_dev_edit_upstream_error_degrades_gracefully(monkeypatch):
    _arm_edit(monkeypatch, patch_status=422)
    r = _client().post("/api/dev/issue/5/edit", json={"title": "T", "text": "X"})
    assert r.status_code == 200                      # never 500
    assert r.get_json()["ok"] is False


def test_dev_edit_token_missing_degrades_gracefully():
    # no token; requests.get/patch are the raising guards → must NOT be called
    r = _client().post("/api/dev/issue/5/edit", json={"title": "T", "text": "X"})
    assert r.status_code == 200
    j = r.get_json()
    assert j["ok"] is False and j["available"] is False


def test_split_app_markers_is_conservative():
    """Pure helper. Only the app's OWN trailing signature lines are stripped — an
    italic line the boss wrote himself must survive, or editing would eat his text."""
    text, markers = webapp._split_app_markers(
        "Prvý riadok.\n\n_toto napísal šéf kurzívou_\n\n"
        "_Nápad cez appku (Vývoj) — sef@forestshop.sk_\n")
    assert text == "Prvý riadok.\n\n_toto napísal šéf kurzívou_"
    assert markers == ["_Nápad cez appku (Vývoj) — sef@forestshop.sk_"]
    assert webapp._split_app_markers("") == ("", [])
    assert webapp._split_app_markers(None) == ("", [])
