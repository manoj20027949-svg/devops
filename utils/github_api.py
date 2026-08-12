"""
GitPulse - GitHub API wrapper.

A thin, resilient client around the GitHub REST API. It handles:

* Token validation before any request is made.
* Automatic pagination (GitHub caps pages at 100 items).
* Rate-limit detection with a short backoff.
* Clean exception mapping so routes never see raw `requests` errors.

All heavy lifting for the dashboard is provided here: team members,
commits, pull requests, issues, languages and activity scoring.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterator, Optional

import requests

# Import the shared logger. `get_logger` safely returns the "github" logger.
from config.logging_setup import get_logger
from config.settings import settings

logger = get_logger("github")

API_BASE = "https://api.github.com"
# Number of seconds to wait before retrying when the rate limit is hit.
RATE_LIMIT_BACKOFF = 5
# How many times a failing request is retried before giving up.
MAX_RETRIES = 3


class GitHubError(Exception):
    """Raised for any GitHub API failure with a human-readable message."""

    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class GitHubAPI:
    """Authenticated GitHub REST API client."""

    def __init__(self, token: str) -> None:
        self.token = token.strip()
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "GitPulse-Team-Intelligence",
            }
        )

    # ------------------------------------------------------------------
    # Low-level request helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _friendly_status(status_code: int, path: str, detail: str) -> str:
        """
        Map a GitHub API failure to a clear, human-readable message.

        Never includes the token. `detail` is the raw response body
        (GitHub error payloads do not echo tokens).
        """
        if status_code == 401:
            return (
                "Your GitHub token is invalid or has expired. Create a new one at "
                "github.com/settings/tokens and try again."
            )
        if status_code == 403:
            return (
                "GitHub denied access (HTTP 403). Your token may lack the required "
                "'repo' scope, or you have hit the API rate limit. Check your token "
                "permissions at github.com/settings/tokens."
            )
        if status_code == 404:
            return (
                f"Repository or owner not found (HTTP 404 on {path}). Check "
                "that the repository exists and that your token has access "
                "to it."
            )
        return f"GitHub {status_code} on {path}: {detail[:200]}"

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        retries: int = MAX_RETRIES,
        json: Optional[dict] = None,
    ) -> dict:
        """
        Perform one authenticated request with retry + rate-limit handling.

        Returns the JSON body as a dict (or empty dict for 204 responses).
        Client errors (4xx) are raised immediately with a friendly message;
        only server errors (5xx) and network failures are retried.
        """
        url = f"{API_BASE}{path}"
        params = params or {}
        last_error: Optional[GitHubError] = None

        for attempt in range(retries):
            try:
                resp = self.session.request(
                    method, url, params=params, json=json, timeout=30
                )
            except requests.RequestException as exc:  # network failure
                logger.warning("Network error on %s: %s (attempt %d)", path, exc, attempt + 1)
                last_error = GitHubError(f"Network error reaching GitHub: {exc}")
                time.sleep(RATE_LIMIT_BACKOFF)
                continue

            # --- Rate limit exceeded: wait and retry ---
            if resp.status_code == 403 and "rate limit" in resp.text.lower():
                reset = resp.headers.get("X-RateLimit-Reset")
                wait = RATE_LIMIT_BACKOFF
                if reset:
                    wait = max(int(reset) - int(time.time()), 0) + 1
                logger.warning("GitHub rate limit hit, sleeping %ds", wait)
                time.sleep(wait)
                continue

            # --- Success ---
            if resp.status_code == 204:
                return {}
            if 200 <= resp.status_code < 300:
                return resp.json()

            # --- Client errors (4xx): report immediately, never retry ---
            if 400 <= resp.status_code < 500:
                raise GitHubError(
                    self._friendly_status(resp.status_code, path, resp.text),
                    status_code=resp.status_code,
                )

            # --- Server errors (5xx): retry with backoff ---
            last_error = GitHubError(
                f"GitHub {resp.status_code} on {path}: {resp.text[:200]}",
                status_code=resp.status_code,
            )
            time.sleep(RATE_LIMIT_BACKOFF)

        raise last_error or GitHubError(f"Request to {path} failed after retries")

    def _iter_pages(
        self,
        path: str,
        params: Optional[dict] = None,
        per_page: int = 100,
    ) -> Iterator[list[dict]]:
        """
        Yield paginated results as lists of items.

        GitHub caps `per_page` at 100, so larger collections are fetched
        page by page until an empty page is returned.
        """
        page = 1
        while True:
            data = self._request(
                "GET", path, params={**(params or {}), "page": page, "per_page": per_page}
            )
            if not isinstance(data, list):
                break
            yield data
            if len(data) < per_page:
                break
            page += 1

    # ------------------------------------------------------------------
    # Authenticated user + accessible repositories
    # ------------------------------------------------------------------
    def get_authenticated_user(self) -> dict:
        """Return the profile of the currently authenticated user (/user)."""
        return self._request("GET", "/user")

    def get_user_repos(
        self,
        affiliation: str = "owner,collaborator,organization_member",
        sort: str = "updated",
        direction: str = "desc",
        per_page: int = 100,
    ) -> list[dict]:
        """
        Return repositories accessible to the authenticated user.

        `affiliation=owner,collaborator,organization_member` covers repos
        the user owns, contributes to as a collaborator, or can access
        through an organization. Results are normalized to just the fields
        the repository selector needs (no secrets or tokens are included).
        """
        out: list[dict] = []
        for page in self._iter_pages(
            "/user/repos",
            params={
                "affiliation": affiliation,
                "sort": sort,
                "direction": direction,
            },
            per_page=per_page,
        ):
            out.extend(page)
        return [
            {
                "full_name": item.get("full_name", ""),
                "name": item.get("name", ""),
                "owner": (item.get("owner") or {}).get("login", ""),
                "private": item.get("private", False),
                "default_branch": item.get("default_branch", "main"),
                "description": item.get("description", ""),
            }
            for item in out
        ]

    # ------------------------------------------------------------------
    # Token validation
    # ------------------------------------------------------------------
    def validate_token(self) -> Optional[dict]:
        """
        Validate the token by calling the authenticated user endpoint.

        Returns the authenticated user dict, or raises GitHubError with a
        precise reason (bad credentials / missing scope / network failure),
        so the login page never blames a valid token for a network problem.
        """
        try:
            return self.get_authenticated_user()
        except GitHubError as exc:
            logger.error("Token validation failed (status=%s): %s", exc.status_code, exc.message)
            if exc.status_code == 401:
                raise GitHubError(
                    "The token is invalid or has expired. Double-check it or "
                    "create a new one (github.com/settings/tokens).",
                    status_code=401,
                ) from exc
            if exc.status_code == 403:
                raise GitHubError(
                    "The token was accepted but GitHub denied access. It may "
                    "lack the required scope (e.g. 'repo').",
                    status_code=403,
                ) from exc
            if exc.status_code is None:
                raise GitHubError(
                    "Could not reach GitHub. Check your network connection "
                    "and try again.",
                ) from exc
            raise GitHubError(
                f"GitHub could not validate the token (HTTP {exc.status_code}).",
                status_code=exc.status_code,
            ) from exc

    # ------------------------------------------------------------------
    # Single-entity lookups
    # ------------------------------------------------------------------
    def get_user(self, username: str) -> dict:
        """Fetch a public user profile by username."""
        return self._request("GET", f"/users/{username}")

    def get_organization(self, org: str) -> dict:
        """Fetch an organization profile (works only for public orgs/token)."""
        return self._request("GET", f"/orgs/{org}")

    def get_repository(self, owner: str, repo: str) -> dict:
        """Fetch a single repository's metadata."""
        return self._request("GET", f"/repos/{owner}/{repo}")

    def get_branches(self, owner: str, repo: str) -> list[dict]:
        """Return all branches of a repository."""
        out: list[dict] = []
        for page in self._iter_pages(f"/repos/{owner}/{repo}/branches"):
            out.extend(page)
        return out

    def get_repository_languages(self, owner: str, repo: str) -> dict[str, int]:
        """Return the language breakdown (bytes per language) of a repo."""
        return self._request("GET", f"/repos/{owner}/{repo}/languages")

    # ------------------------------------------------------------------
    # Team / contributor data
    # ------------------------------------------------------------------
    def get_contributors(self, owner: str, repo: str, per_page: int = 100) -> list[dict]:
        """
        Return contributors for a repository.

        The `anonymous` flag is turned off so we only get named members.
        """
        out: list[dict] = []
        for page in self._iter_pages(
            f"/repos/{owner}/{repo}/contributors",
            params={"anon": "false"},
            per_page=per_page,
        ):
            out.extend(page)
        return out

    def get_collaborators(self, owner: str, repo: str, per_page: int = 100) -> list[dict]:
        """
        Return the repository's actual collaborators via
        GET /repos/{owner}/{repo}/collaborators (paginated).

        Unlike /contributors (users who have committed), this lists every
        user granted access to the repository, including collaborators who
        have never pushed a commit. Each entry is normalized to the fields
        the dashboard needs: username, avatar, url, role and permissions.

        Requires the authenticated token to have push access to the repo;
        callers should fall back to `get_contributors` on GitHubError.
        """
        out: list[dict] = []
        for page in self._iter_pages(
            f"/repos/{owner}/{repo}/collaborators", per_page=per_page
        ):
            out.extend(page)
        return [
            {
                "username": item.get("login", "unknown"),
                "avatar": item.get("avatar_url", ""),
                "url": item.get("html_url", ""),
                "role": self._collaborator_role(item),
                "permissions": self._collaborator_permissions(item),
            }
            for item in out
        ]

    @staticmethod
    def _collaborator_permissions(item: dict) -> dict[str, bool]:
        """Extract the boolean permission flags GitHub returns for a collaborator."""
        perms = item.get("permissions") or {}
        return {
            "admin": bool(perms.get("admin")),
            "maintain": bool(perms.get("maintain")),
            "push": bool(perms.get("push")),
            "triage": bool(perms.get("triage")),
            "pull": bool(perms.get("pull")),
        }

    @staticmethod
    def _collaborator_role(item: dict) -> str:
        """Derive a readable role from role_name (preferred) or permissions."""
        role = item.get("role_name") or ""
        if role:
            return role
        perms = item.get("permissions") or {}
        if perms.get("admin"):
            return "admin"
        if perms.get("maintain"):
            return "maintain"
        if perms.get("push"):
            return "write"
        if perms.get("triage"):
            return "triage"
        return "read"

    def get_org_members(self, org: str, per_page: int = 100) -> list[dict]:
        """Return members of an organization (requires membership scope)."""
        out: list[dict] = []
        try:
            for page in self._iter_pages(f"/orgs/{org}/members", per_page=per_page):
                out.extend(page)
        except GitHubError as exc:
            logger.warning("Could not list org members (%s); using contributors.", exc.message)
        return out

    # ------------------------------------------------------------------
    # Activity data
    # ------------------------------------------------------------------
    def get_commits(
        self,
        owner: str,
        repo: str,
        author: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        per_page: int = 100,
    ) -> list[dict]:
        """
        Return commits for a repo, optionally filtered by author and date.

        Args:
            owner:   Repository owner.
            repo:    Repository name.
            author:  GitHub login to filter commits for (optional).
            since:   ISO-8601 date to look back from (optional).
            until:   ISO-8601 upper bound (optional).
        """
        params: dict[str, Any] = {}
        if author:
            params["author"] = author
        if since:
            params["since"] = since
        if until:
            params["until"] = until

        out: list[dict] = []
        for page in self._iter_pages(
            f"/repos/{owner}/{repo}/commits", params=params, per_page=per_page
        ):
            out.extend(page)
        return out

    def get_pull_requests(
        self,
        owner: str,
        repo: str,
        state: str = "open",
        per_page: int = 100,
    ) -> list[dict]:
        """Return pull requests filtered by state ('open' | 'closed' | 'all')."""
        out: list[dict] = []
        for page in self._iter_pages(
            f"/repos/{owner}/{repo}/pulls",
            params={"state": state},
            per_page=per_page,
        ):
            out.extend(page)
        return out

    def get_issues(
        self,
        owner: str,
        repo: str,
        state: str = "open",
        per_page: int = 100,
    ) -> list[dict]:
        """Return issues (pull requests excluded) filtered by state."""
        out: list[dict] = []
        for page in self._iter_pages(
            f"/repos/{owner}/{repo}/issues",
            params={"state": state},
            per_page=per_page,
        ):
            out.extend(page)
        return out

    def get_last_activity(
        self,
        owner: str,
        repo: str,
        since: Optional[str] = None,
        per_page: int = 100,
    ) -> list[dict]:
        """Return events across the repo (pushes, PRs, issues, comments)."""
        since = since or (datetime.now(timezone.utc) - timedelta(days=90)).isoformat() + "Z"
        out: list[dict] = []
        for page in self._iter_pages(
            f"/repos/{owner}/{repo}/events", params={"since": since}, per_page=per_page
        ):
            out.extend(page)
        return out

    # ------------------------------------------------------------------
    # Team / organization
    # ------------------------------------------------------------------
    def get_team_members(self, org: str, team_slug: str, per_page: int = 100) -> list[dict]:
        """
        Return members of an organization team.

        Requires a token with read access to the organization. Raises
        GitHubError when the team does not exist or access is denied.
        """
        out: list[dict] = []
        for page in self._iter_pages(
            f"/orgs/{org}/teams/{team_slug}/members", per_page=per_page
        ):
            out.extend(page)
        return out

    def get_team(self, org: str, team_slug: str) -> dict:
        """Return a single team's metadata."""
        return self._request("GET", f"/orgs/{org}/teams/{team_slug}")

    # ------------------------------------------------------------------
    # Activity detail
    # ------------------------------------------------------------------
    def get_commit_details(self, owner: str, repo: str, sha: str) -> dict:
        """Return a single commit with its changed files and diff stats."""
        return self._request("GET", f"/repos/{owner}/{repo}/commits/{sha}")

    def get_pull_request(self, owner: str, repo: str, number: int) -> dict:
        """Return a single pull request with additions/deletions/changed files."""
        return self._request("GET", f"/repos/{owner}/{repo}/pulls/{number}")

    def get_pr_reviews(self, owner: str, repo: str, number: int, per_page: int = 100) -> list[dict]:
        """Return all reviews submitted on a pull request."""
        out: list[dict] = []
        for page in self._iter_pages(
            f"/repos/{owner}/{repo}/pulls/{number}/reviews", per_page=per_page
        ):
            out.extend(page)
        return out

    def get_pr_reviewers(self, owner: str, repo: str, number: int) -> list[dict]:
        """Return the requested reviewers of a pull request."""
        return self._request("GET", f"/repos/{owner}/{repo}/pulls/{number}/requested_reviewers")

    def get_pr_files(self, owner: str, repo: str, number: int, per_page: int = 100) -> list[dict]:
        """Return the files (with patches) changed by a pull request."""
        out: list[dict] = []
        for page in self._iter_pages(
            f"/repos/{owner}/{repo}/pulls/{number}/files", per_page=per_page
        ):
            out.extend(page)
        return out

    # ------------------------------------------------------------------
    # Git objects (used by the AI auto-fix workflow - all via API)
    # ------------------------------------------------------------------
    def get_branch_sha(self, owner: str, repo: str, branch: str) -> str:
        """Return the current commit SHA of a branch."""
        ref = self._request("GET", f"/repos/{owner}/{repo}/git/ref/heads/{branch}")
        return ref.get("object", {}).get("sha", "")

    def get_default_branch(self, owner: str, repo: str) -> str:
        """Return the repository's default branch name."""
        meta = self.get_repository(owner, repo)
        return meta.get("default_branch", "main")

    def create_branch(self, owner: str, repo: str, new_branch: str, base_sha: str) -> dict:
        """Create a branch (ref) pointing at an existing commit SHA."""
        return self._request(
            "POST",
            "/repos/{0}/{1}/git/refs".format(owner, repo),
            json={"ref": f"refs/heads/{new_branch}", "sha": base_sha},
        )

    def _create_blob(self, owner: str, repo: str, content: str) -> str:
        """Create a git blob and return its SHA."""
        blob = self._request(
            "POST",
            f"/repos/{owner}/{repo}/git/blobs",
            json={"content": content, "encoding": "utf-8"},
        )
        return blob.get("sha", "")

    def _create_tree(
        self,
        owner: str,
        repo: str,
        base_tree: str,
        path: str,
        blob_sha: str,
    ) -> str:
        """Create a tree with a single changed file on top of a base tree."""
        tree = self._request(
            "POST",
            f"/repos/{owner}/{repo}/git/trees",
            json={
                "base_tree": base_tree,
                "tree": [{"path": path, "mode": "100644", "type": "blob", "sha": blob_sha}],
            },
        )
        return tree.get("sha", "")

    def _create_commit(
        self,
        owner: str,
        repo: str,
        message: str,
        tree_sha: str,
        parent_sha: str,
    ) -> str:
        """Create a commit and return its SHA."""
        commit = self._request(
            "POST",
            f"/repos/{owner}/{repo}/git/commits",
            json={"message": message, "tree": tree_sha, "parents": [parent_sha]},
        )
        return commit.get("sha", "")

    def _update_branch_ref(self, owner: str, repo: str, branch: str, commit_sha: str) -> dict:
        """Point a branch ref at a new commit SHA."""
        return self._request(
            "PATCH",
            f"/repos/{owner}/{repo}/git/refs/heads/{branch}",
            json={"sha": commit_sha, "force": False},
        )

    def commit_file_via_api(
        self,
        owner: str,
        repo: str,
        branch: str,
        path: str,
        content: str,
        message: str,
    ) -> str:
        """
        Commit a file's new content to a branch using only the Git Data API.

        This never touches the local filesystem and never modifies the
        default branch: the caller is responsible for creating `branch`
        first (a new feature branch). Returns the new commit SHA.
        """
        base_sha = self.get_branch_sha(owner, repo, branch)
        blob_sha = self._create_blob(owner, repo, content)
        tree_sha = self._create_tree(owner, repo, base_sha, path, blob_sha)
        commit_sha = self._create_commit(owner, repo, message, tree_sha, base_sha)
        self._update_branch_ref(owner, repo, branch, commit_sha)
        return commit_sha

    def fetch_file_content(self, owner: str, repo: str, path: str, ref: str) -> str:
        """Return the current text content of a file on a given branch."""
        data = self._request(
            "GET", f"/repos/{owner}/{repo}/contents/{path}", params={"ref": ref}
        )
        content = data.get("content", "")
        if data.get("encoding") == "base64":
            import base64
            content = base64.b64decode(content).decode("utf-8", errors="replace")
        return content

    def create_pull_request(
        self,
        owner: str,
        repo: str,
        title: str,
        head: str,
        base: str,
        body: str = "",
    ) -> dict:
        """Open a pull request. Never merges anything."""
        return self._request(
            "POST",
            f"/repos/{owner}/{repo}/pulls",
            json={"title": title, "head": head, "base": base, "body": body},
        )

    # ------------------------------------------------------------------
    # Dashboard aggregation
    # ------------------------------------------------------------------
    def _since_iso(self, days: int) -> str:
        return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat() + "Z"

    def _collect_commits(self, owner: str, repo: str, since: str):
        """Return (counts, latest_dates, recent_list) for commits since `since`."""
        counts: dict[str, int] = {}
        latest: dict[str, datetime] = {}
        recent: list[dict] = []
        for page in self._iter_pages(
            f"/repos/{owner}/{repo}/commits", params={"since": since}, per_page=100
        ):
            for commit in page:
                author = commit.get("author") or {}
                login = author.get("login")
                date = (commit.get("commit") or {}).get("author", {}).get("date")
                parsed = None
                if date:
                    try:
                        parsed = datetime.fromisoformat(date.replace("Z", "+00:00"))
                    except ValueError:
                        parsed = None
                if login:
                    counts[login] = counts.get(login, 0) + 1
                    if parsed and (login not in latest or parsed > latest[login]):
                        latest[login] = parsed
                if login and parsed:
                    recent.append(
                        {
                            "sha": commit.get("sha", "")[:10],
                            "full_sha": commit.get("sha", ""),
                            "author": login,
                            "message": (commit.get("commit") or {}).get("message", "").split("\n")[0],
                            "date": date,
                            "html_url": commit.get("html_url", ""),
                        }
                    )
        return counts, latest, recent

    def _iter_commits_90d(self, owner: str, repo: str) -> Iterator[dict]:
        """Yield every commit from the last 90 days (paginated)."""
        since = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat() + "Z"
        for page in self._iter_pages(
            f"/repos/{owner}/{repo}/commits",
            params={"since": since},
            per_page=100,
        ):
            for commit in page:
                yield commit

    def build_team_report(self, owner: str, repo: str, days: Optional[int] = None) -> dict[str, Any]:
        """
        Aggregate everything needed by the dashboard into one structure.

        Returns a dict with:
            overview:   {members, active/inactive counts, commits, PRs, issues}
            members:    per-user metrics including the weighted activity score.
            pushes:     most recent commits with file-level detail.
            pull_requests: recent PRs with review status.
            issues:     recent issues.
            languages:  byte counts per language.
            repo:       repository metadata.
        """
        from utils import activity as activity_mod

        days = days or settings.ACTIVITY_WINDOW_DAYS
        since = self._since_iso(days)
        logger.info("Building team report for %s/%s (%dd window)", owner, repo, days)

        # --- Team source: org team if configured, else repo collaborators ---
        # GitHub's /collaborators endpoint returns the actual members of the
        # repository (not just people who have committed), so the dashboard
        # shows everyone granted access. When the token cannot list
        # collaborators we fall back to /contributors.
        members: list[dict[str, Any]] = []
        team_name = settings.GITHUB_TEAM or ""
        source = []
        source_kind = "contributor"
        if team_name:
            try:
                source = self.get_team_members(owner, team_name)
                source_kind = "team member"
            except GitHubError as exc:
                logger.warning(
                    "Team '%s' not accessible (%s); falling back to collaborators.",
                    team_name, exc.message,
                )
                source = []
        if not source:
            try:
                source = self.get_collaborators(owner, repo)
                source_kind = "collaborator"
            except GitHubError as exc:
                logger.warning(
                    "Could not list collaborators for %s/%s (%s); "
                    "falling back to contributors.",
                    owner, repo, exc.message,
                )
                source = []
        if not source:
            try:
                source = self.get_contributors(owner, repo)
                source_kind = "contributor"
            except GitHubError:
                source = []
        for item in source:
            members.append(
                {
                    "username": item.get("login") or item.get("username") or "unknown",
                    "avatar": item.get("avatar_url") or item.get("avatar") or "",
                    "url": item.get("html_url") or item.get("url") or "",
                    "role": item.get("role") or item.get("role_name") or source_kind,
                    "permissions": item.get("permissions") or {},
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
                }
            )

        # --- Commits ---
        commit_counts, last_commit, recent_commits = self._collect_commits(owner, repo, since)
        member_index = {m["username"]: m for m in members}
        for login, count in commit_counts.items():
            if login in member_index:
                member_index[login]["commits"] = count
        latest_by_member: dict[str, datetime] = {}

        # --- Pull requests (all states, filtered to the window) ---
        prs_all = self.get_pull_requests(owner, repo, state="all")
        prs: list[dict[str, Any]] = []
        prs_in_window = [
            pr for pr in prs_all
            if self._within_window(pr.get("created_at") or pr.get("updated_at"), since)
        ]
        for pr in prs_in_window[:40]:
            number = pr.get("number", 0)
            detail = pr
            try:
                detail = self.get_pull_request(owner, repo, number)
            except GitHubError:
                pass
            reviews = self.get_pr_reviews(owner, repo, number)
            reviewers: list[str] = []
            try:
                reviewers = [
                    r.get("login", "")
                    for r in self.get_pr_reviewers(owner, repo, number).get("users", [])
                ]
            except GitHubError:
                pass
            review_states = [r.get("state", "").upper() for r in reviews]
            review_status = "approved" if "APPROVED" in review_states else (
                "changes_requested" if "CHANGES_REQUESTED" in review_states else (
                    "reviewed" if review_states else "no_reviews"
                )
            )
            for review in reviews:
                reviewer = (review.get("user") or {}).get("login")
                if reviewer:
                    self._track_latest(latest_by_member, reviewer, review.get("submitted_at"))
                    if reviewer in member_index:
                        member_index[reviewer]["prs_reviewed"] += 1

            author = (pr.get("user") or {}).get("login", "")
            if author:
                self._track_latest(latest_by_member, author, pr.get("created_at"))
                self._track_latest(latest_by_member, author, pr.get("updated_at"))
                if author in member_index:
                    member_index[author]["prs_created"] += 1
                    member_index[author]["pr_count"] += 1
                    if pr.get("merged_at"):
                        member_index[author]["prs_merged"] += 1
                    elif pr.get("state") == "open":
                        member_index[author]["prs_open"] += 1

            prs.append(
                {
                    "number": number,
                    "title": pr.get("title", ""),
                    "author": author,
                    "state": pr.get("state", ""),
                    "merged": bool(pr.get("merged_at")),
                    "created_at": pr.get("created_at", ""),
                    "updated_at": pr.get("updated_at", ""),
                    "html_url": pr.get("html_url", ""),
                    "additions": detail.get("additions", 0) or 0,
                    "deletions": detail.get("deletions", 0) or 0,
                    "changed_files": detail.get("changed_files", 0) or 0,
                    "review_status": review_status,
                    "reviewers": reviewers,
                }
            )

        # --- Issues (PRs excluded by the issues endpoint) ---
        issues_all = self.get_issues(owner, repo, state="all")
        issues: list[dict[str, Any]] = []
        for issue in issues_all:
            if issue.get("pull_request"):
                continue
            if not self._within_window(issue.get("created_at") or issue.get("updated_at"), since):
                continue
            author = (issue.get("user") or {}).get("login", "")
            if author:
                self._track_latest(latest_by_member, author, issue.get("created_at"))
                self._track_latest(latest_by_member, author, issue.get("updated_at"))
                if author in member_index:
                    member_index[author]["issues_created"] += 1
                    member_index[author]["issue_count"] += 1
                    if issue.get("state") == "closed":
                        member_index[author]["issues_closed"] += 1
            issues.append(
                {
                    "number": issue.get("number", 0),
                    "title": issue.get("title", ""),
                    "author": author,
                    "state": issue.get("state", ""),
                    "labels": [label.get("name", "") for label in issue.get("labels", [])],
                    "created_at": issue.get("created_at", ""),
                    "updated_at": issue.get("updated_at", ""),
                    "html_url": issue.get("html_url", ""),
                    "assignees": [
                        (a or {}).get("login", "")
                        for a in issue.get("assignees", [])
                    ],
                }
            )

        # --- Merge commit + review activity into last-activity ---
        for login, parsed in last_commit.items():
            self._track_latest(latest_by_member, login, parsed.isoformat())

        # --- Languages + repo metadata ---
        try:
            languages = self.get_repository_languages(owner, repo)
        except GitHubError:
            languages = {}
        try:
            repo_meta = self.get_repository(owner, repo)
        except GitHubError:
            repo_meta = {}

        # --- Finalize members: last activity + score ---
        now = datetime.now(timezone.utc)
        active_members = 0
        for member in members:
            last = latest_by_member.get(member["username"])
            if last:
                member["last_active"] = last.isoformat()
                member["last_active_days"] = max(
                    int((now - last).total_seconds() // 86400), 0
                )
            member = activity_mod.enrich_member(member)
            if member["activity_status"] == "ACTIVE":
                active_members += 1
            member["score_reason"] = activity_mod.score_reason(
                member["username"],
                member.get("commits", 0),
                member.get("prs_created", 0),
                member.get("prs_reviewed", 0),
                member.get("issues_created", 0),
            )

        members.sort(key=lambda m: m["activity_score"], reverse=True)

        # --- Recent pushes (commit details for the top N) ---
        pushes: list[dict[str, Any]] = []
        for commit in recent_commits[-15:]:
            try:
                detail = self.get_commit_details(owner, repo, commit["full_sha"])
                if not isinstance(detail, dict):
                    raise GitHubError("Unexpected commit detail payload", status_code=502)
                commit["files"] = [
                    {
                        "filename": f.get("filename", ""),
                        "additions": f.get("additions", 0),
                        "deletions": f.get("deletions", 0),
                        "status": f.get("status", ""),
                    }
                    for f in detail.get("files", [])
                ][:30]
                commit["stats"] = {
                    "additions": detail.get("stats", {}).get("additions", 0),
                    "deletions": detail.get("stats", {}).get("deletions", 0),
                }
            except (GitHubError, AttributeError, TypeError):
                commit["files"] = []
                commit["stats"] = {"additions": 0, "deletions": 0}
            pushes.append(commit)
        pushes.reverse()

        total_commits = sum(commit_counts.values())
        overview = {
            "members": len(members),
            "active_members": active_members,
            "recently_active_members": sum(
                1 for m in members if m["activity_status"] == "RECENTLY ACTIVE"
            ),
            "inactive_members": sum(
                1 for m in members if m["activity_status"] == "INACTIVE"
            ),
            "total_commits": total_commits,
            "open_prs": sum(1 for pr in prs if pr["state"] == "open"),
            "merged_prs": sum(1 for pr in prs if pr["merged"]),
            "open_issues": sum(1 for i in issues if i["state"] == "open"),
        }

        return {
            "owner": owner,
            "repo": repo,
            "team_name": team_name or repo,
            "overview": overview,
            "members": members,
            "pushes": pushes,
            "pull_requests": prs,
            "issues": issues,
            "languages": languages,
            "repo": {
                "name": repo_meta.get("full_name", f"{owner}/{repo}"),
                "description": repo_meta.get("description", ""),
                "stars": repo_meta.get("stargazers_count", 0),
                "forks": repo_meta.get("forks_count", 0),
                "open_issues": repo_meta.get("open_issues_count", 0),
                "default_branch": repo_meta.get("default_branch", "main"),
            },
        }

    @staticmethod
    def _within_window(date_str: Optional[str], since: str) -> bool:
        """True when an ISO date is newer than `since`."""
        if not date_str:
            return False
        try:
            parsed = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except ValueError:
            return False
        try:
            since_parsed = datetime.fromisoformat(since.replace("Z", "+00:00"))
        except ValueError:
            return True
        return parsed >= since_parsed

    @staticmethod
    def _track_latest(
        mapping: dict[str, datetime],
        login: str,
        date_str: Optional[str],
    ) -> None:
        """Record the newest date seen for a member."""
        if not date_str:
            return
        try:
            parsed = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except ValueError:
            return
        if login not in mapping or parsed > mapping[login]:
            mapping[login] = parsed

    def build_member_profile(self, owner: str, repo: str, username: str, days: Optional[int] = None) -> dict[str, Any]:
        """Build a focused profile for a single team member."""
        from utils import activity as activity_mod

        days = days or settings.ACTIVITY_WINDOW_DAYS
        since = self._since_iso(days)

        commits = self.get_commits(owner, repo, author=username, since=since)
        prs = self.get_pull_requests(owner, repo, state="all")
        authored_prs = [pr for pr in prs if (pr.get("user") or {}).get("login") == username]
        reviewed_prs = []
        for pr in authored_prs[:20]:
            for review in self.get_pr_reviews(owner, repo, pr["number"]):
                if (review.get("user") or {}).get("login") == username:
                    reviewed_prs.append(
                        {"pr": pr["number"], "state": review.get("state", ""), "submitted_at": review.get("submitted_at", "")}
                    )
        issues_all = self.get_issues(owner, repo, state="all")
        authored_issues = [
            i for i in issues_all
            if not i.get("pull_request") and (i.get("user") or {}).get("login") == username
        ]
        try:
            languages = self.get_repository_languages(owner, repo)
        except GitHubError:
            languages = {}

        last_active: Optional[datetime] = None
        for c in commits:
            date = (c.get("commit") or {}).get("author", {}).get("date")
            if date:
                try:
                    d = datetime.fromisoformat(date.replace("Z", "+00:00"))
                    if last_active is None or d > last_active:
                        last_active = d
                except ValueError:
                    pass

        member = {
            "username": username,
            "commits": len(commits),
            "pr_count": len(authored_prs),
            "prs_created": len(authored_prs),
            "prs_merged": sum(1 for pr in authored_prs if pr.get("merged_at")),
            "prs_reviewed": len(reviewed_prs),
            "issue_count": len(authored_issues),
            "issues_created": len(authored_issues),
            "issues_closed": sum(1 for i in authored_issues if i["state"] == "closed"),
            "last_active": last_active.isoformat() if last_active else None,
            "last_active_days": (
                max(int((datetime.now(timezone.utc) - last_active).total_seconds() // 86400), 0)
                if last_active else None
            ),
        }
        member = activity_mod.enrich_member(member)

        return {
            "username": username,
            "member": member,
            "commits": [
                {
                    "sha": c.get("sha", "")[:10],
                    "message": (c.get("commit") or {}).get("message", "").split("\n")[0],
                    "date": (c.get("commit") or {}).get("author", {}).get("date", ""),
                }
                for c in commits[:20]
            ],
            "pull_requests": [
                {
                    "number": pr.get("number"),
                    "title": pr.get("title", ""),
                    "state": pr.get("state", ""),
                    "merged": bool(pr.get("merged_at")),
                    "created_at": pr.get("created_at", ""),
                    "html_url": pr.get("html_url", ""),
                }
                for pr in authored_prs[:20]
            ],
            "reviews": reviewed_prs[:20],
            "issues": [
                {
                    "number": i.get("number"),
                    "title": i.get("title", ""),
                    "state": i.get("state", ""),
                    "created_at": i.get("created_at", ""),
                    "html_url": i.get("html_url", ""),
                }
                for i in authored_issues[:20]
            ],
            "languages": languages,
        }



def compute_activity_score(member: dict[str, Any]) -> int:
    """
    Calculate a 0-100 activity score for a developer.

    Weighting (deliberately simple and transparent):
      * Commits      -> up to 50 points
      * Open PRs     -> up to 25 points
      * Open issues  -> up to 15 points (contribution to issues)
      * Recency      -> up to 10 points (touched in last 7 days)

    The thresholds are forgiving so a junior engineer is not punished.
    """
    commits = min(member.get("commits", 0), 50) / 50 * 50
    prs = min(member.get("pr_count", 0), 5) / 5 * 25
    issues = min(member.get("issue_count", 0), 3) / 3 * 15

    last_days = member.get("last_active_days")
    if last_days is None:
        recency = 0.0
    elif last_days <= 7:
        recency = 10.0
    elif last_days <= 14:
        recency = 5.0
    else:
        recency = 0.0

    return int(round(commits + prs + issues + recency))


def validate_token_available(token: str) -> bool:
    """Cheap pre-check: refuse empty or whitespace tokens before any call."""
    return bool(token and token.strip())
