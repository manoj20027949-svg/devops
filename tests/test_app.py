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
        sess["selected_repo"] = "o/r"

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
        sess["selected_repo"] = "o/r"

    response = client.post("/dashboard/scan", data={"target": "repo"})

    assert response.status_code == 302
    assert app.extensions["scan_cache"]["data"] == []


def test_refresh_route_clears_scan_cache_and_redirects(app, client, monkeypatch):
    """Refresh busts the scan cache so GitHub data re-fetches on reload."""
    app.extensions["scan_cache"] = {"data": [{"rule": "stale"}]}
    with client.session_transaction() as sess:
        sess["github_token"] = "t"
        sess["github_user"] = "alice"

    response = client.post("/dashboard/refresh")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")
    assert app.extensions["scan_cache"]["data"] is None


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
        sess["selected_repo"] = "o/r"

    html = client.get("/dashboard").get_data(as_text=True)

    assert "sidebar-nav" in html
    assert "Team Dashboard" in html


def test_dashboard_sidebar_has_section_labels(client, monkeypatch):
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
        sess["selected_repo"] = "o/r"

    html = client.get("/dashboard").get_data(as_text=True)

    for label in ("Overview", "Team", "Development", "AI Tools", "Security", "System"):
        assert label in html
    assert "Team Reports" in html
    assert "Code Review" in html
    assert "Notifications" in html
    assert "Settings" in html
    assert "data-route=" in html


def test_placeholder_pages_require_login(client):
    for path in ("/reports", "/code-review", "/notifications", "/settings"):
        response = client.get(path)
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/login")


def test_placeholder_pages_render_coming_soon(client):
    pages = [
        ("/reports", "Team Reports"),
        ("/code-review", "Code Review"),
        ("/notifications", "Notifications"),
        ("/settings", "Settings"),
    ]
    for path, title in pages:
        with client.session_transaction() as sess:
            sess["github_token"] = "t"
            sess["github_user"] = "alice"

        response = client.get(path)
        html = response.get_data(as_text=True)

        assert response.status_code == 200
        assert title in html
        assert "Coming Soon" in html
        assert "sidebar-nav" in html


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
        sess["selected_repo"] = "o/r"

    response = client.get("/dashboard")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "AI Auto-Fix" in html
    assert "Coaching Suggestions" in html
    assert "ai-pr-btn" in html
    assert "ai-issue-btn" in html


# ======================================================================
# Dynamic repository selection
# ======================================================================
def test_dashboard_shows_repo_selector(client):
    with client.session_transaction() as sess:
        sess["github_token"] = "t"
        sess["github_user"] = "alice"
        sess["selected_repo"] = "acme/app"

    html = client.get("/dashboard").get_data(as_text=True)

    assert 'id="repoSelect"' in html
    assert "data-current" in html
    assert "acme/app" in html


def test_dashboard_errors_when_no_repo_selected(client, monkeypatch):
    monkeypatch.setattr(GitHubAPI, "build_team_report", lambda self, o, r: {})
    with client.session_transaction() as sess:
        sess["github_token"] = "t"
        sess["github_user"] = "alice"

    response = client.get("/dashboard")

    assert response.status_code == 200
    assert b"No repository selected" in response.data


def test_api_github_user_requires_login(client):
    response = client.get("/api/github/user")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_api_github_user_returns_account(client, monkeypatch):
    monkeypatch.setattr(
        GitHubAPI, "validate_token", lambda self: {"login": "alice", "avatar_url": "https://x/a.png"}
    )
    with client.session_transaction() as sess:
        sess["github_token"] = "t"
        sess["github_user"] = "alice"

    response = client.get("/api/github/user")

    assert response.status_code == 200
    assert response.get_json()["login"] == "alice"


def test_api_github_repos_returns_accessible_repos(client, monkeypatch):
    def fake_repos(self, affiliation=None, sort=None, direction=None, per_page=100):
        return [
            {"full_name": "alice/repo-a", "name": "repo-a", "owner": "alice",
             "private": False, "default_branch": "main", "description": ""},
            {"full_name": "acme/repo-b", "name": "repo-b", "owner": "acme",
             "private": True, "default_branch": "main", "description": ""},
        ]

    monkeypatch.setattr(GitHubAPI, "get_user_repos", fake_repos)
    with client.session_transaction() as sess:
        sess["github_token"] = "t"
        sess["github_user"] = "alice"

    response = client.get("/api/github/repos")
    payload = response.get_json()

    assert response.status_code == 200
    assert [r["full_name"] for r in payload["repos"]] == ["alice/repo-a", "acme/repo-b"]


def test_api_github_repos_handles_token_failure(client, monkeypatch):
    def boom(self):
        from utils.github_api import GitHubError

        raise GitHubError("The token is invalid or has expired.", status_code=401)

    monkeypatch.setattr(GitHubAPI, "get_user_repos", boom)
    with client.session_transaction() as sess:
        sess["github_token"] = "t"
        sess["github_user"] = "alice"

    response = client.get("/api/github/repos")

    assert response.status_code == 401
    assert "invalid" in response.get_json()["error"]


def test_api_github_select_repo_stores_selection(client, monkeypatch):
    monkeypatch.setattr(
        GitHubAPI,
        "get_repository",
        lambda self, owner, repo: {
            "full_name": "acme/app", "default_branch": "main",
        },
    )
    with client.session_transaction() as sess:
        sess["github_token"] = "t"
        sess["github_user"] = "alice"

    response = client.post("/api/github/select-repo", json={"repo": "acme/app"})

    assert response.status_code == 200
    assert response.get_json()["ok"] is True
    with client.session_transaction() as sess:
        assert sess["selected_repo"] == "acme/app"


def test_api_github_select_repo_requires_owner_name(client):
    with client.session_transaction() as sess:
        sess["github_token"] = "t"
        sess["github_user"] = "alice"

    response = client.post("/api/github/select-repo", json={"repo": "no-slash"})

    assert response.status_code == 400


def test_api_github_select_repo_reports_access_denied(client, monkeypatch):
    from utils.github_api import GitHubError

    def deny(self, owner, repo):
        raise GitHubError("Repository or owner not found (HTTP 404 on /repos/acme/app).", status_code=404)

    monkeypatch.setattr(GitHubAPI, "get_repository", deny)
    with client.session_transaction() as sess:
        sess["github_token"] = "t"
        sess["github_user"] = "alice"

    response = client.post("/api/github/select-repo", json={"repo": "acme/app"})

    assert response.status_code == 404
    assert "not found" in response.get_json()["error"]
    with client.session_transaction() as sess:
        assert "selected_repo" not in sess


def test_api_team_collaborators_returns_collaborators(client, monkeypatch):
    fake_report = {
        "overview": {
            "members": 2,
            "active_members": 1,
            "recently_active_members": 0,
            "inactive_members": 1,
            "total_commits": 5,
            "open_prs": 0,
            "merged_prs": 0,
            "open_issues": 0,
        },
        "members": [
            {
                "username": "alice",
                "role": "admin",
                "permissions": {"admin": True, "maintain": False, "push": True, "triage": False, "pull": True},
                "commits": 5,
                "pr_count": 0,
                "prs_created": 0,
                "prs_open": 0,
                "prs_merged": 0,
                "prs_reviewed": 0,
                "issue_count": 0,
                "issues_created": 0,
                "issues_closed": 0,
                "last_active": "2024-01-01T00:00:00Z",
                "last_active_days": 1,
            },
            {
                "username": "bob",
                "role": "read",
                "permissions": {"admin": False, "maintain": False, "push": False, "triage": False, "pull": True},
                "commits": 0,
                "pr_count": 0,
                "prs_created": 0,
                "prs_open": 0,
                "prs_merged": 0,
                "prs_reviewed": 0,
                "issue_count": 0,
                "issues_created": 0,
                "issues_closed": 0,
                "last_active": None,
                "last_active_days": None,
            },
        ],
        "languages": {},
        "repo": {"name": "o/r", "description": "", "stars": 0, "forks": 0, "open_issues": 0, "default_branch": "main"},
    }
    monkeypatch.setattr(GitHubAPI, "build_team_report", lambda self, o, r: fake_report)
    with client.session_transaction() as sess:
        sess["github_token"] = "t"
        sess["github_user"] = "alice"
        sess["selected_repo"] = "o/r"

    response = client.get("/api/team/collaborators")

    assert response.status_code == 200
    payload = response.get_json()
    assert [c["username"] for c in payload["collaborators"]] == ["alice", "bob"]
    assert payload["collaborators"][0]["role"] == "admin"
    assert payload["collaborators"][1]["permissions"]["pull"] is True
    assert payload["overview"]["members"] == 2


def test_api_team_collaborators_requires_login(client):
    response = client.get("/api/team/collaborators")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")
