"""Route-level tests using Flask's test client (no real network)."""

from utils.auth import _login_attempts
from utils.github_api import GitHubAPI


def test_index_redirects_authenticated_users(client):
    with client.session_transaction() as sess:
        sess["github_token"] = "t"
        sess["github_user"] = "alice"

    response = client.get("/")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")


def test_login_page_renders(client):
    response = client.get("/login")
    assert response.status_code == 200


def test_login_page_has_no_app_shell(client):
    html = client.get("/login").get_data(as_text=True)
    assert "sidebar-nav" not in html
    assert "Logout" not in html
    assert "Personal Access Token" in html


def test_login_page_offers_token_creation_links(client):
    html = client.get("/login").get_data(as_text=True)
    assert "github.com/settings/tokens/new" in html
    assert "github.com/settings/personal-access-tokens/new" in html


def test_dashboard_requires_login(client):
    response = client.get("/dashboard")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"


def test_unknown_route_returns_404(client):
    response = client.get("/does-not-exist")
    assert response.status_code == 404


def test_pat_login_stores_token_and_redirects(client, monkeypatch):
    _login_attempts.clear()
    monkeypatch.setattr(
        GitHubAPI, "validate_token", lambda self: {"login": "alice", "avatar_url": ""}
    )

    response = client.post("/login", data={"token": "test-token"})

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")
    with client.session_transaction() as sess:
        assert sess["github_token"] == "test-token"
        assert sess["github_user"] == "alice"


def test_pat_login_rejects_bad_token(client, monkeypatch):
    _login_attempts.clear()
    from utils.github_api import GitHubError

    def reject(self):
        raise GitHubError("401 Unauthorized", status_code=401)

    monkeypatch.setattr(GitHubAPI, "validate_token", reject)

    response = client.post("/login", data={"token": "bad-token"})

    assert response.status_code == 200
    assert b"Sign-in failed" in response.data


def test_dashboard_renders_with_mock_report(client, monkeypatch):
    fake_report = {
        "overview": {"members": 1, "total_commits": 1, "open_prs": 0, "open_issues": 0},
        "members": [
            {
                "username": "alice",
                "commits": 1,
                "pr_count": 0,
                "issue_count": 0,
                "last_active_days": 1,
                "activity_score": 60,
            }
        ],
        "languages": {},
        "repo": {
            "name": "o/r",
            "description": "",
            "stars": 0,
            "forks": 0,
            "open_issues": 0,
            "default_branch": "main",
        },
    }
    monkeypatch.setattr(GitHubAPI, "build_team_report", lambda self, o, r: fake_report)
    with client.session_transaction() as sess:
        sess["github_token"] = "t"
        sess["github_user"] = "alice"

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert b"alice" in response.data


def test_scan_route_runs_and_caches(app, client, monkeypatch):
    def fake_request(self, method, path, params=None, retries=3):
        return {"tree": []}

    monkeypatch.setattr(GitHubAPI, "_request", fake_request)
    with client.session_transaction() as sess:
        sess["github_token"] = "t"
        sess["github_user"] = "alice"

    response = client.post("/dashboard/scan", data={"target": "repo"})

    assert response.status_code == 302
    assert app.extensions["scan_cache"]["data"] == []


def test_unauthorized_page_has_no_app_shell(client, monkeypatch):
    from config.settings import settings

    monkeypatch.setattr(GitHubAPI, "validate_token", lambda self: {"login": "denied"})
    monkeypatch.setattr(settings, "ALLOWED_GITHUB_USERS", ["someone-else"])

    response = client.post("/login", data={"token": "x"})

    assert response.status_code == 403
    html = response.get_data(as_text=True)
    assert "sidebar-nav" not in html
    assert "Access Denied" in html


def test_dashboard_keeps_app_shell(client, monkeypatch):
    fake_report = {
        "overview": {"members": 0, "total_commits": 0, "open_prs": 0, "open_issues": 0},
        "members": [],
        "languages": {},
        "repo": {
            "name": "o/r",
            "description": "",
            "stars": 0,
            "forks": 0,
            "open_issues": 0,
            "default_branch": "main",
        },
    }
    monkeypatch.setattr(GitHubAPI, "build_team_report", lambda self, o, r: fake_report)
    with client.session_transaction() as sess:
        sess["github_token"] = "t"
        sess["github_user"] = "alice"

    html = client.get("/dashboard").get_data(as_text=True)

    assert "sidebar-nav" in html
    assert "Team Dashboard" in html


def test_dashboard_renders_ai_tabs_with_rich_report(client, monkeypatch):
    """The new AI-powered dashboard tabs render without template errors."""
    fake_report = {
        "overview": {
            "members": 2,
            "active_members": 1,
            "recently_active_members": 1,
            "inactive_members": 0,
            "total_commits": 30,
            "open_prs": 2,
            "merged_prs": 1,
            "open_issues": 3,
            "scanner_findings": 1,
            "ai_errors_count": 0,
            "ai_fixed_count": 0,
        },
        "members": [
            {
                "username": "alice",
                "avatar": "",
                "url": "",
                "role": "team member",
                "commits": 20,
                "pr_count": 2,
                "prs_created": 2,
                "prs_open": 1,
                "prs_merged": 1,
                "prs_reviewed": 3,
                "issue_count": 2,
                "issues_created": 2,
                "issues_closed": 1,
                "last_active": "2024-01-01T00:00:00Z",
                "last_active_days": 1,
                "activity_score": 90,
                "activity_label": "Highly Active",
                "activity_status": "ACTIVE",
                "score_reason": "High activity because of 20 commits.",
            },
            {
                "username": "bob",
                "avatar": "",
                "url": "",
                "role": "contributor",
                "commits": 10,
                "pr_count": 0,
                "prs_created": 0,
                "prs_open": 0,
                "prs_merged": 0,
                "prs_reviewed": 0,
                "issue_count": 1,
                "issues_created": 1,
                "issues_closed": 0,
                "last_active": "2024-01-20T00:00:00Z",
                "last_active_days": 15,
                "activity_score": 40,
                "activity_label": "Low Activity",
                "activity_status": "RECENTLY ACTIVE",
                "score_reason": "10 commits.",
            },
        ],
        "pushes": [
            {
                "message": "Add parser",
                "sha": "abc123",
                "full_sha": "abc123def456",
                "date": "2024-01-01T00:00:00Z",
                "author": "alice",
                "files": [{"filename": "src/app.py", "additions": 5, "deletions": 1, "status": "modified"}],
                "stats": {"additions": 5, "deletions": 1},
            }
        ],
        "pull_requests": [
            {
                "number": 2,
                "title": "Fix login",
                "author": "alice",
                "state": "open",
                "merged": False,
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
                "html_url": "https://github.com/o/r/pull/2",
                "additions": 5,
                "deletions": 2,
                "changed_files": 1,
                "review_status": "approved",
                "reviewers": ["bob"],
            }
        ],
        "issues": [
            {
                "number": 3,
                "title": "Crash on empty input",
                "author": "bob",
                "state": "open",
                "labels": ["bug"],
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
                "html_url": "https://github.com/o/r/issues/3",
                "assignees": ["alice"],
            }
        ],
        "languages": {"Python": 100},
        "repo": {
            "name": "o/r",
            "description": "demo",
            "stars": 1,
            "forks": 0,
            "open_issues": 3,
            "default_branch": "main",
        },
    }
    monkeypatch.setattr(GitHubAPI, "build_team_report", lambda self, o, r: fake_report)
    with client.session_transaction() as sess:
        sess["github_token"] = "t"
        sess["github_user"] = "alice"

    response = client.get("/dashboard")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "AI Auto-Fix" in html
    assert "Coaching Suggestions" in html
    assert "ai-pr-btn" in html
    assert "ai-issue-btn" in html
