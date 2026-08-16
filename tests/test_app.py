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
    for path in ("/reports", "/reports/export/csv", "/reports/export/pdf",
                 "/code-review", "/notifications", "/settings"):
        response = client.get(path)
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/login")


def test_placeholder_pages_render_coming_soon(client):
    pages = [
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


# ======================================================================
# Team Members page (status filter)
# ======================================================================
def _member_report():
    """A report with 5 members: 3 ACTIVE and 2 INACTIVE.

    `is_active` mirrors what build_team_report sets (activity_status ==
    "ACTIVE"), so the dashboard counts and the page filter use one source
    of truth.
    """
    def member(username, is_active):
        return {
            "username": username,
            "avatar": "",
            "url": "",
            "role": "collaborator",
            "permission": "read",
            "commits": 5,
            "commits_all_time": 10,
            "pr_count": 2,
            "prs_created": 2,
            "prs_merged": 1,
            "prs_reviewed": 1,
            "issue_count": 1,
            "issues_created": 1,
            "issues_closed": 0,
            "activity_score": 70 if is_active else 20,
            "activity_status": "ACTIVE" if is_active else "INACTIVE",
            "is_active": is_active,
            "last_active_text": "Today" if is_active else "30 days ago",
        }

    return {
        "overview": {
            "members": 5,
            "active_members": 3,
            "inactive_members": 2,
            "total_commits": 25,
            "open_prs": 0,
            "merged_prs": 0,
            "open_issues": 0,
        },
        "members": [
            member("alice", True),
            member("bob", True),
            member("carol", True),
            member("dave", False),
            member("eve", False),
        ],
        "languages": {},
        "repo": {"name": "o/r", "description": "", "stars": 0, "forks": 0, "open_issues": 0, "default_branch": "main"},
    }


def _get_team_members(client, path, monkeypatch):
    monkeypatch.setattr(GitHubAPI, "build_team_report", lambda self, o, r: _member_report())
    with client.session_transaction() as sess:
        sess["github_token"] = "t"
        sess["github_user"] = "alice"
        sess["selected_repo"] = "o/r"
    return client.get(path)


def test_team_members_requires_login(client):
    response = client.get("/team-members")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_team_members_shows_all_members(client, monkeypatch):
    response = _get_team_members(client, "/team-members", monkeypatch)
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "All Members (5)" in html
    for username in ("alice", "bob", "carol", "dave", "eve"):
        assert username in html
    assert "Clear Filter" not in html


def test_team_members_active_filter_shows_only_active(client, monkeypatch):
    response = _get_team_members(client, "/team-members?status=active", monkeypatch)
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Active Members (3)" in html
    for username in ("alice", "bob", "carol"):
        assert f"/member/{username}" in html
    assert "/member/dave" not in html
    assert "/member/eve" not in html
    assert "Clear Filter" in html
    assert "/team-members" in html


def test_team_members_inactive_filter_shows_only_inactive(client, monkeypatch):
    response = _get_team_members(client, "/team-members?status=inactive", monkeypatch)
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Inactive Members (2)" in html
    for username in ("dave", "eve"):
        assert f"/member/{username}" in html
    assert "/member/alice" not in html
    assert "/member/bob" not in html
    assert "/member/carol" not in html
    assert "Clear Filter" in html


def test_team_members_active_count_matches_dashboard_overview(client, monkeypatch):
    report = _member_report()
    monkeypatch.setattr(GitHubAPI, "build_team_report", lambda self, o, r: report)
    with client.session_transaction() as sess:
        sess["github_token"] = "t"
        sess["github_user"] = "alice"
        sess["selected_repo"] = "o/r"

    html = client.get("/team-members?status=active").get_data(as_text=True)

    assert f"Active Members ({report['overview']['active_members']})" in html
    assert f"All Members ({report['overview']['members']})" not in html


def test_team_members_page_links_to_member_profiles(client, monkeypatch):
    response = _get_team_members(client, "/team-members", monkeypatch)
    html = response.get_data(as_text=True)

    assert "/member/alice" in html
    assert "/member/eve" in html


def test_team_members_has_back_button_to_dashboard(client, monkeypatch):
    for path in ("/team-members", "/team-members?status=active", "/team-members?status=inactive"):
        response = _get_team_members(client, path, monkeypatch)
        html = response.get_data(as_text=True)

        assert response.status_code == 200
        assert 'href="/dashboard"' in html
        assert "← Back" in html
        assert "Back to Team Dashboard" in html


def test_dashboard_member_cards_link_to_team_members_page(client, monkeypatch):
    monkeypatch.setattr(GitHubAPI, "build_team_report", lambda self, o, r: _member_report())
    with client.session_transaction() as sess:
        sess["github_token"] = "t"
        sess["github_user"] = "alice"
        sess["selected_repo"] = "o/r"

    html = client.get("/dashboard").get_data(as_text=True)

    assert 'href="/team-members"' in html
    assert 'href="/team-members?status=active"' in html
    assert 'href="/team-members?status=inactive"' in html
    assert "Total Members" in html
    assert "Active Members" in html
    assert "Inactive Members" in html


# ======================================================================
# Team Reports page + exports
# ======================================================================
def _get_reports(client, path, monkeypatch):
    monkeypatch.setattr(GitHubAPI, "build_team_report", lambda self, o, r: _member_report())
    with client.session_transaction() as sess:
        sess["github_token"] = "t"
        sess["github_user"] = "alice"
        sess["selected_repo"] = "o/r"
    return client.get(path)


def test_reports_page_requires_login(client):
    response = client.get("/reports")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_reports_page_renders_summary_members_and_analysis(client, monkeypatch):
    response = _get_reports(client, "/reports", monkeypatch)
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Report Period" in html
    assert "Member Performance" in html
    assert "Top Contributors" in html
    assert "Team Insights" in html
    assert "Team Analysis" in html
    # Summary stats come from the real report data.
    assert "5" in html  # total members
    assert "alice" in html
    # Export buttons are present.
    assert "/reports/export/csv" in html
    assert "/reports/export/pdf" in html


def test_reports_page_range_selector_preserves_query(client, monkeypatch):
    response = _get_reports(client, "/reports?period=7d", monkeypatch)
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Last 7 Days" in html
    assert 'value="7d" selected' in html


def test_reports_export_csv(client, monkeypatch):
    response = _get_reports(client, "/reports/export/csv", monkeypatch)

    assert response.status_code == 200
    assert response.mimetype == "text/csv"
    assert response.headers["Content-Disposition"].startswith("attachment;")
    assert ".csv" in response.headers["Content-Disposition"]
    text = response.get_data(as_text=True)
    assert "Member" in text
    assert "alice" in text
    assert "GitPulse Team Report" in text


def test_reports_export_pdf(client, monkeypatch):
    response = _get_reports(client, "/reports/export/pdf", monkeypatch)

    assert response.status_code == 200
    assert response.mimetype == "application/pdf"
    assert response.headers["Content-Disposition"].startswith("attachment;")
    assert ".pdf" in response.headers["Content-Disposition"]
    data = response.data
    assert data[:5] == b"%PDF-"
    assert data.rstrip().endswith(b"%%EOF")


def test_reports_export_unknown_format_returns_404(client, monkeypatch):
    response = _get_reports(client, "/reports/export/docx", monkeypatch)
    assert response.status_code == 404


# ======================================================================
# AI Analysis endpoints
# ======================================================================
def _fake_fetch_content(self, owner, repo, path, ref=""):
    """Deterministic file content for static-analysis endpoint tests."""
    if path.endswith(".py"):
        return "import os\n\n\ndef run(x):\n    return x\n"
    return "// javascript\nconst a = 1;\n"


def _session_with_repo(client):
    with client.session_transaction() as sess:
        sess["github_token"] = "t"
        sess["github_user"] = "alice"
        sess["selected_repo"] = "o/r"


def test_api_ai_errors_returns_list(client):
    _session_with_repo(client)
    response = client.get("/api/ai/errors")
    assert response.status_code == 200
    payload = response.get_json()
    assert "errors" in payload
    assert "total" in payload
    assert "static_analyzed_at" in payload


def test_api_ai_status_reports_counts(client):
    _session_with_repo(client)
    response = client.get("/api/ai/status")
    assert response.status_code == 200
    payload = response.get_json()
    assert "ai_enabled" in payload
    assert "error_counts" in payload
    assert "error_total" in payload
    assert "poll_interval" in payload


def test_api_ai_fix_preview_requires_path(client):
    _session_with_repo(client)
    response = client.post("/api/ai/fix/preview", json={})
    assert response.status_code == 400


def test_api_ai_fix_preview_returns_diff(client, monkeypatch):
    monkeypatch.setattr(GitHubAPI, "fetch_file_content", _fake_fetch_content)
    monkeypatch.setattr(
        GitHubAPI, "get_default_branch", lambda self, o, r: "main"
    )
    _session_with_repo(client)
    response = client.post("/api/ai/fix/preview", json={"path": "src/app.py"})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["path"] == "src/app.py"
    assert "analysis" in payload
    assert "has_fixed_code" in payload
    assert "diff" in payload
    assert "diff_lines" in payload


def test_api_ai_fix_rollback_rejects_non_ai_fix_branch(client):
    _session_with_repo(client)
    response = client.post("/api/ai/fix/rollback", json={"branch": "main"})
    assert response.status_code == 400
    assert "Refusing" in response.get_json()["error"]


def test_api_ai_fix_rollback_deletes_ai_fix_branch(client, monkeypatch):
    deleted = []

    def fake_delete(self, owner, repo, branch):
        deleted.append(branch)
        return True

    monkeypatch.setattr(GitHubAPI, "delete_branch", fake_delete)
    _session_with_repo(client)
    response = client.post("/api/ai/fix/rollback", json={"branch": "ai-fix/null-check-1698000000"})
    assert response.status_code == 200
    assert response.get_json()["ok"] is True
    assert deleted == ["ai-fix/null-check-1698000000"]


def test_api_ai_fix_rollback_requires_branch(client):
    _session_with_repo(client)
    response = client.post("/api/ai/fix/rollback", json={})
    assert response.status_code == 400


def _ai_report():
    """A report that exercises commit + member + static analysis paths."""
    return {
        "overview": {
            "members": 2,
            "active_members": 1,
            "inactive_members": 0,
            "total_commits": 5,
            "open_prs": 1,
            "merged_prs": 0,
            "open_issues": 0,
        },
        "members": [
            {"username": "alice", "commits": 4, "pr_count": 1, "last_active_days": 1},
            {"username": "bob", "commits": 1, "pr_count": 0, "last_active_days": 10},
        ],
        "pushes": [
            {
                "message": "Add parser",
                "sha": "abc123",
                "author": "alice",
                "date": "2024-01-01T00:00:00Z",
                "files": [{"filename": "src/app.py", "additions": 5, "deletions": 1}],
                "stats": {"additions": 5, "deletions": 1},
            }
        ],
        "pull_requests": [],
        "issues": [],
        "languages": {"Python": 100},
        "repo": {
            "name": "o/r", "description": "", "stars": 0, "forks": 0,
            "open_issues": 0, "default_branch": "main",
        },
        "activity_feed": [],
    }


def test_api_ai_analyze_repo_returns_deep_analysis(client, monkeypatch):
    monkeypatch.setattr(GitHubAPI, "build_team_report", lambda self, o, r: _ai_report())
    monkeypatch.setattr(GitHubAPI, "fetch_file_content", _fake_fetch_content)
    monkeypatch.setattr(
        GitHubAPI, "get_default_branch", lambda self, o, r: "main"
    )
    _session_with_repo(client)
    response = client.post("/api/ai/analyze-repo", json={})
    assert response.status_code == 200
    payload = response.get_json()
    assert "health_score" in payload
    assert "health_label" in payload
    assert isinstance(payload["commit_analyses"], list)
    assert payload["commit_analyses"][0]["sha"] == "abc123"
    assert isinstance(payload["member_analyses"], list)
    assert payload["member_analyses"][0]["username"] == "alice"
    assert "code_stats" in payload
    assert "last_analyzed" in payload


def test_api_dashboard_refresh_returns_json(client, monkeypatch):
    monkeypatch.setattr(GitHubAPI, "build_team_report", lambda self, o, r: _ai_report())
    monkeypatch.setattr(GitHubAPI, "fetch_file_content", _fake_fetch_content)
    monkeypatch.setattr(
        GitHubAPI, "get_default_branch", lambda self, o, r: "main"
    )
    _session_with_repo(client)
    response = client.post("/api/dashboard/refresh")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert "last_updated" in payload
    assert payload["overview"]["total_commits"] == 5
    assert "ai" in payload
    assert payload["ai"]["health_label"] in (
        "Excellent", "Good", "Needs Attention", "Critical",
    )
    assert "static_findings" in payload["ai"]
    assert payload["languages"] == {"Python": 100}
    assert client.application.extensions["last_updated"] == payload["last_updated"]
    # error_counts must be present so dashboard.js can update the stat cards.
    assert "error_counts" in payload["ai"]
    assert "ai_errors" in payload["ai"]["error_counts"]
    assert "ai_fixed_count" in payload["ai"]["error_counts"]
    assert payload["ai"]["error_counts"]["static_errors"] >= 0


def test_api_pull_requests_requires_login(client):
    response = client.get("/api/pull-requests")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_api_pull_requests_success_format(client, monkeypatch):
    prs = [{"number": 2, "title": "Fix login", "author": "alice", "state": "open"}]
    monkeypatch.setattr(
        GitHubAPI, "get_pull_requests_summary",
        lambda self, o, r, state="open": prs,
    )
    _session_with_repo(client)

    response = client.get("/api/pull-requests")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["success"] is True
    assert payload["count"] == 1
    assert payload["pull_requests"] == prs
    assert payload["error"] is None


def test_api_pull_requests_defaults_to_open_and_forwards_state(client, monkeypatch):
    captured = {}

    def fake_summary(self, o, r, state="open"):
        captured["state"] = state
        return []

    monkeypatch.setattr(GitHubAPI, "get_pull_requests_summary", fake_summary)
    _session_with_repo(client)

    client.get("/api/pull-requests")
    assert captured["state"] == "open"

    client.get("/api/pull-requests?state=all")
    assert captured["state"] == "all"

    client.get("/api/pull-requests?state=bogus")
    assert captured["state"] == "open"


def test_api_pull_requests_requires_repo(client, monkeypatch):
    from config.settings import settings

    monkeypatch.setattr(settings, "GITHUB_OWNER", "")
    monkeypatch.setattr(settings, "GITHUB_REPO", "")
    with client.session_transaction() as sess:
        sess["github_token"] = "t"

    response = client.get("/api/pull-requests")
    payload = response.get_json()

    assert response.status_code == 400
    assert payload["success"] is False
    assert payload["pull_requests"] == []
    assert payload["count"] == 0
    assert "No repository selected" in payload["error"]


def test_api_pull_requests_requires_token(client, monkeypatch):
    from config.settings import settings

    monkeypatch.setattr(settings, "GITHUB_TOKEN", "")
    monkeypatch.setattr("utils.auth.is_logged_in", lambda: True)
    with client.session_transaction() as sess:
        sess["selected_repo"] = "o/r"

    response = client.get("/api/pull-requests")
    payload = response.get_json()

    assert response.status_code == 400
    assert payload["success"] is False
    assert "No GitHub token configured" in payload["error"]


def test_api_pull_requests_returns_friendly_error(client, monkeypatch):
    from utils.github_api import GitHubError

    def reject(self, owner, repo, state="open"):
        raise GitHubError(
            "Repository or owner not found (HTTP 404 on /repos/o/r/pulls). "
            "Check that the repository exists and that your token has access to it.",
            status_code=404,
        )

    monkeypatch.setattr(GitHubAPI, "get_pull_requests_summary", reject)
    _session_with_repo(client)

    response = client.get("/api/pull-requests")
    payload = response.get_json()

    assert response.status_code == 400
    assert payload["success"] is False
    assert "HTTP 404" in payload["error"]
