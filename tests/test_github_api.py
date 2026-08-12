"""Tests for the GitHub API wrapper."""

import pytest

from utils.github_api import GitHubAPI, GitHubError, compute_activity_score


class TestActivityScore:
    def test_minimum(self):
        member = {"commits": 0, "pr_count": 0, "issue_count": 0, "last_active_days": None}
        assert compute_activity_score(member) == 0

    def test_maximum_is_capped(self):
        member = {"commits": 500, "pr_count": 99, "issue_count": 99, "last_active_days": 1}
        assert compute_activity_score(member) == 100

    def test_partial_weights(self):
        member = {"commits": 10, "pr_count": 2, "issue_count": 1, "last_active_days": 10}
        assert compute_activity_score(member) == 30

    def test_recency_tier(self):
        assert compute_activity_score({"commits": 0, "pr_count": 0, "issue_count": 0, "last_active_days": 10}) == 5
        assert compute_activity_score({"commits": 0, "pr_count": 0, "issue_count": 0, "last_active_days": 30}) == 0


class TestPagination:
    def test_iter_pages_stops_on_short_page(self, monkeypatch):
        api = GitHubAPI("t")
        requested_pages = []

        def fake_request(method, path, params=None, retries=3):
            page = (params or {}).get("page", 1)
            requested_pages.append(page)
            size = 10 if page < 2 else 1
            return [{"page": page}] * size

        monkeypatch.setattr(api, "_request", fake_request)

        items = [item for page in api._iter_pages("/x", per_page=10) for item in page]

        assert len(items) == 11
        assert requested_pages == [1, 2]


class TestUserRepos:
    def test_get_user_repos_normalizes_fields(self, monkeypatch):
        api = GitHubAPI("t")

        def fake_request(method, path, params=None, retries=3):
            assert path == "/user/repos"
            assert (params or {}).get("affiliation") == "owner,collaborator,organization_member"
            return [
                {
                    "full_name": "acme/app",
                    "name": "app",
                    "owner": {"login": "acme"},
                    "private": True,
                    "default_branch": "develop",
                    "description": "demo",
                }
            ]

        monkeypatch.setattr(api, "_request", fake_request)

        repos = api.get_user_repos()

        assert repos == [
            {
                "full_name": "acme/app",
                "name": "app",
                "owner": "acme",
                "private": True,
                "default_branch": "develop",
                "description": "demo",
            }
        ]

    def test_get_user_repos_propagates_access_denied(self, monkeypatch):
        api = GitHubAPI("t")

        def boom(method, path, params=None, retries=3):
            raise GitHubError("403 Forbidden", status_code=403)

        monkeypatch.setattr(api, "_request", boom)

        with pytest.raises(GitHubError) as exc:
            api.get_user_repos()

        assert exc.value.status_code == 403


class TestBuildTeamReport:
    def test_commits_fetched_exactly_once(self, monkeypatch):
        api = GitHubAPI("t")
        seen_paths = []

        def fake_request(method, path, params=None, retries=3):
            seen_paths.append(path)
            if "collaborators" in path:
                return [
                    {"login": "alice", "role_name": "write",
                     "permissions": {"admin": False, "maintain": False, "push": True, "triage": False, "pull": True}},
                    {"login": "bob", "role_name": "read",
                     "permissions": {"admin": False, "maintain": False, "push": False, "triage": False, "pull": True}},
                ]
            if "contributors" in path:
                return [{"login": "alice"}, {"login": "bob"}]
            if "commits" in path:
                return [{"author": {"login": "alice"}, "commit": {"author": {"date": "2024-01-01T00:00:00Z"}}}]
            if "pulls" in path:
                return []
            if "issues" in path:
                return []
            if "languages" in path:
                return {"Python": 100}
            return {
                "full_name": "o/r",
                "description": "",
                "stargazers_count": 0,
                "forks_count": 0,
                "open_issues_count": 0,
                "default_branch": "main",
            }

        monkeypatch.setattr(api, "_request", fake_request)

        report = api.build_team_report("o", "r")

        assert seen_paths.count("/repos/o/r/commits") == 1
        alice = report["members"][0]
        assert alice["username"] == "alice"
        assert alice["commits"] == 1
        assert alice["last_active"] == "2024-01-01T00:00:00+00:00"
        assert report["overview"]["total_commits"] == 1
        assert report["languages"] == {"Python": 100}

    def test_handles_fractional_second_dates(self, monkeypatch):
        api = GitHubAPI("t")

        def fake_request(method, path, params=None, retries=3):
            if "collaborators" in path:
                return [{"login": "alice", "role_name": "write",
                         "permissions": {"admin": False, "maintain": False, "push": True, "triage": False, "pull": True}}]
            if "contributors" in path:
                return [{"login": "alice"}]
            if "commits" in path:
                return [{"author": {"login": "alice"}, "commit": {"author": {"date": "2024-01-01T00:00:00.123456Z"}}}]
            if "pulls" in path or "issues" in path:
                return []
            if "languages" in path:
                return {}
            return {"full_name": "o/r", "description": "", "stargazers_count": 0, "forks_count": 0, "open_issues_count": 0, "default_branch": "main"}

        monkeypatch.setattr(api, "_request", fake_request)

        report = api.build_team_report("o", "r")

        assert report["members"][0]["last_active"] == "2024-01-01T00:00:00.123456+00:00"
        assert report["members"][0]["last_active_days"] >= 0


class TestCollaborators:
    def test_get_collaborators_paginates_and_normalizes(self, monkeypatch):
        api = GitHubAPI("t")
        requested_pages = []

        def fake_request(method, path, params=None, retries=3):
            assert path == "/repos/o/r/collaborators"
            page = (params or {}).get("page", 1)
            requested_pages.append(page)
            if page == 1:
                return [
                    {
                        "login": "alice",
                        "avatar_url": "https://x/alice.png",
                        "html_url": "https://github.com/alice",
                        "role_name": "admin",
                        "permissions": {"admin": True, "maintain": False, "push": True, "triage": False, "pull": True},
                    }
                    for _ in range(10)
                ]
            return [
                {
                    "login": "bob",
                    "avatar_url": "",
                    "html_url": "",
                    "role_name": "read",
                    "permissions": {"admin": False, "maintain": False, "push": False, "triage": False, "pull": True},
                }
            ]

        monkeypatch.setattr(api, "_request", fake_request)

        collabs = api.get_collaborators("o", "r", per_page=10)

        assert len(collabs) == 11
        assert requested_pages == [1, 2]
        assert collabs[0]["username"] == "alice"
        assert collabs[0]["avatar"] == "https://x/alice.png"
        assert collabs[0]["role"] == "admin"
        assert collabs[0]["permissions"]["push"] is True
        assert collabs[0]["pending"] is False
        assert collabs[10]["username"] == "bob"
        assert collabs[10]["role"] == "read"

    def test_get_collaborators_derives_role_from_permissions(self, monkeypatch):
        api = GitHubAPI("t")

        def fake_request(method, path, params=None, retries=3):
            return [
                {
                    "login": "carol",
                    "avatar_url": "",
                    "html_url": "",
                    "role_name": "",
                    "permissions": {"admin": False, "maintain": False, "push": True, "triage": False, "pull": True},
                }
            ]

        monkeypatch.setattr(api, "_request", fake_request)

        collabs = api.get_collaborators("o", "r")
        assert collabs[0]["role"] == "write"

    def test_get_pending_invitations_normalizes(self, monkeypatch):
        api = GitHubAPI("t")

        def fake_request(method, path, params=None, retries=3):
            assert path == "/repos/o/r/invitations"
            return [
                {
                    "invitee": {
                        "login": "charlie",
                        "avatar_url": "https://x/charlie.png",
                        "html_url": "https://github.com/charlie",
                    },
                    "permissions": "write",
                },
                {
                    "invitee": {
                        "login": "dave",
                        "avatar_url": "",
                        "html_url": "",
                    },
                    "permissions": "read",
                },
            ]

        monkeypatch.setattr(api, "_request", fake_request)

        invites = api.get_pending_invitations("o", "r")

        assert len(invites) == 2
        charlie = invites[0]
        assert charlie["username"] == "charlie"
        assert charlie["avatar"] == "https://x/charlie.png"
        assert charlie["url"] == "https://github.com/charlie"
        assert charlie["role"] == "pending write"
        assert charlie["permissions"]["push"] is True
        assert charlie["pending"] is True
        assert invites[1]["role"] == "pending read"
        assert invites[1]["permissions"]["pull"] is True

    def test_get_pending_invitations_propagates_denied(self, monkeypatch):
        api = GitHubAPI("t")

        def boom(method, path, params=None, retries=3):
            raise GitHubError("403 Forbidden", status_code=403)

        monkeypatch.setattr(api, "_request", boom)

        with pytest.raises(GitHubError) as exc:
            api.get_pending_invitations("o", "r")

        assert exc.value.status_code == 403

    def test_permissions_from_role_maps_all_roles(self):
        assert GitHubAPI._permissions_from_role("admin") == {
            "admin": True, "maintain": False, "push": True, "triage": False, "pull": True,
        }
        assert GitHubAPI._permissions_from_role("maintain")["maintain"] is True
        assert GitHubAPI._permissions_from_role("write")["push"] is True
        assert GitHubAPI._permissions_from_role("triage")["triage"] is True
        assert GitHubAPI._permissions_from_role("read")["pull"] is True
        assert GitHubAPI._permissions_from_role("")["pull"] is True

    def test_build_team_report_includes_collaborator_without_commits(self, monkeypatch):
        """A collaborator who never committed must still appear as a member."""
        api = GitHubAPI("t")

        def fake_request(method, path, params=None, retries=3):
            if "collaborators" in path:
                return [
                    {"login": "alice", "role_name": "write",
                     "permissions": {"admin": False, "maintain": False, "push": True, "triage": False, "pull": True}},
                    {"login": "bob", "role_name": "read",
                     "permissions": {"admin": False, "maintain": False, "push": False, "triage": False, "pull": True}},
                ]
            if "commits" in path:
                return [{"author": {"login": "alice"}, "commit": {"author": {"date": "2024-01-01T00:00:00Z"}}}]
            if "pulls" in path or "issues" in path:
                return []
            if "languages" in path:
                return {"Python": 100}
            return {"full_name": "o/r", "description": "", "stargazers_count": 0, "forks_count": 0, "open_issues_count": 0, "default_branch": "main"}

        monkeypatch.setattr(api, "_request", fake_request)

        report = api.build_team_report("o", "r")

        usernames = {m["username"] for m in report["members"]}
        assert usernames == {"alice", "bob"}
        bob = next(m for m in report["members"] if m["username"] == "bob")
        assert bob["role"] == "read"
        assert bob["permissions"]["pull"] is True
        assert report["overview"]["members"] == 2

    def test_build_team_report_falls_back_to_contributors_when_collaborators_denied(self, monkeypatch):
        api = GitHubAPI("t")

        def fake_request(method, path, params=None, retries=3):
            if "collaborators" in path:
                raise GitHubError("403 Forbidden", status_code=403)
            if "contributors" in path:
                return [{"login": "alice"}]
            if "commits" in path:
                return []
            if "pulls" in path or "issues" in path:
                return []
            if "languages" in path:
                return {}
            return {"full_name": "o/r", "description": "", "stargazers_count": 0, "forks_count": 0, "open_issues_count": 0, "default_branch": "main"}

        monkeypatch.setattr(api, "_request", fake_request)

        report = api.build_team_report("o", "r")

        assert report["members"][0]["username"] == "alice"
        assert report["members"][0]["role"] == "contributor"

    def test_build_team_report_merges_pending_invitations_and_owner(self, monkeypatch):
        """Pending invitees and the repo owner must appear as members even if
        the collaborators endpoint only returns accepted collaborators."""
        api = GitHubAPI("t")

        def fake_request(method, path, params=None, retries=3):
            if "invitations" in path:
                return [
                    {
                        "invitee": {"login": "charlie", "avatar_url": "", "html_url": ""},
                        "permissions": "read",
                    }
                ]
            if "collaborators" in path:
                return [
                    {"login": "alice", "role_name": "write",
                     "permissions": {"admin": False, "maintain": False, "push": True, "triage": False, "pull": True}},
                ]
            if "contributors" in path:
                return [{"login": "alice"}]
            if "commits" in path:
                return [{"author": {"login": "owneruser"}, "commit": {"author": {"date": "2024-01-01T00:00:00Z"}}}]
            if "pulls" in path or "issues" in path:
                return []
            if "languages" in path:
                return {}
            return {
                "full_name": "o/r",
                "description": "",
                "stargazers_count": 0,
                "forks_count": 0,
                "open_issues_count": 0,
                "default_branch": "main",
                "owner": {"login": "owneruser"},
            }

        monkeypatch.setattr(api, "_request", fake_request)

        report = api.build_team_report("o", "r")

        usernames = [m["username"] for m in report["members"]]
        assert set(usernames) == {"alice", "charlie", "owneruser"}
        charlie = next(m for m in report["members"] if m["username"] == "charlie")
        assert charlie["pending"] is True
        assert charlie["role"] == "pending read"
        assert charlie["permissions"]["pull"] is True
        owner = next(m for m in report["members"] if m["username"] == "owneruser")
        assert owner["role"] == "admin"
        assert owner["permissions"]["admin"] is True
        assert owner["pending"] is False
        assert owner["commits"] == 1
        assert report["overview"]["members"] == 3
        assert usernames.count("owneruser") == 1


class TestTokenValidation:
    def test_maps_api_failure_to_friendly_error(self, monkeypatch):
        api = GitHubAPI("t")

        def boom(method, path, params=None, retries=3):
            raise GitHubError("401 Unauthorized", status_code=401)

        monkeypatch.setattr(api, "_request", boom)

        with pytest.raises(GitHubError) as exc:
            api.validate_token()

        assert "invalid or has expired" in str(exc.value)

    def test_reports_missing_scope_for_403(self, monkeypatch):
        api = GitHubAPI("t")

        def boom(method, path, params=None, retries=3):
            raise GitHubError("403 Forbidden", status_code=403)

        monkeypatch.setattr(api, "_request", boom)

        with pytest.raises(GitHubError) as exc:
            api.validate_token()

        assert "scope" in str(exc.value)

    def test_reports_network_failure_as_network_error(self, monkeypatch):
        api = GitHubAPI("t")

        def boom(method, path, params=None, retries=3):
            raise GitHubError("Network error reaching GitHub: timeout", status_code=None)

        monkeypatch.setattr(api, "_request", boom)

        with pytest.raises(GitHubError) as exc:
            api.validate_token()

        assert "Could not reach GitHub" in str(exc.value)
        assert "invalid" not in str(exc.value)
