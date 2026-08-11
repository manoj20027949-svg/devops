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


class TestBuildTeamReport:
    def test_commits_fetched_exactly_once(self, monkeypatch):
        api = GitHubAPI("t")
        seen_paths = []

        def fake_request(method, path, params=None, retries=3):
            seen_paths.append(path)
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
