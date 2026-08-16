"""
GitPulse - GitHub Team Intelligence Platform
Entry point: creates the Flask application and wires every module together.

Run locally with:
    python app.py
Or via gunicorn (production):
    gunicorn --bind 0.0.0.0:8000 --workers 4 'app:create_app()'
"""

import os

from flask import (
    Flask,
    Response,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

# Local modules
from config.logging_setup import setup_logging
from config.settings import settings
from utils import ai_analyzer, fixer, webhooks
from utils.ai_analyzer import generate_suggestions
from utils.auth import (
    clear_session_token,
    create_github_oauth,
    get_selected_repo,
    get_session_token,
    is_login_rate_limited,
    is_logged_in,
    is_user_allowed,
    login_required,
    record_login_attempt,
    set_selected_repo,
    store_session_token,
)
from utils.code_scanner import CodeScanner
from utils.github_api import GitHubAPI, GitHubError
from utils.store import get_store


def create_app() -> Flask:
    """Application factory - Flask's recommended app structure."""
    app = Flask(__name__)
    app.config["SECRET_KEY"] = settings.SECRET_KEY
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = not settings.is_development
    app.config["PERMANENT_SESSION_LIFETIME"] = 60 * 60 * 8  # 8 hours
    app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024  # 1 MB request cap

    # Logging (idempotent - safe to call multiple times in tests).
    setup_logging(log_dir=settings.LOG_DIR, level=settings.LOG_LEVEL)

    # --- OAuth provider -------------------------------------------------
    oauth = create_github_oauth(app)
    app.extensions["gitpulse_oauth"] = oauth

    # --- Runtime state ---------------------------------------------------
    # Simple in-process caches so we don't hammer the GitHub API or rescan
    # the whole repo on every dashboard refresh.
    app.extensions["scan_cache"] = {"data": None}
    # Deterministic static-analysis findings on recently changed files.
    app.extensions["static_analysis"] = {
        "findings": [],
        "summary": {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0, "TOTAL": 0},
        "analyzed_at": None,
    }
    # Timestamps surfaced in the UI ("Last updated / Last AI analysis").
    app.extensions["last_updated"] = None
    app.extensions["last_analyzed"] = None
    # Persistent SQLite store for AI analyses, fix attempts, webhook events.
    app.extensions["store"] = get_store()
    # Recent webhook events (newest first) for the dashboard activity feed.
    app.extensions["recent_activity"] = list(
        app.extensions["store"].list_webhook_events(limit=20)
    )

    # --- Routes ---------------------------------------------------------
    register_routes(app)
    register_api(app)

    # --- Error handlers -------------------------------------------------
    register_error_handlers(app)

    # Startup configuration check: report whether the critical GitHub
    # settings are configured. The token value itself is never printed.
    for name, status in settings.config_status().items():
        app.logger.info("%s: %s", name, status)

    # Surface configuration problems (warn, don't crash).
    for problem in settings.validate():
        app.logger.warning("Configuration warning: %s", problem)

    return app


# ======================================================================
# Route helpers
# ======================================================================
def get_api() -> GitHubAPI:
    """
    Build a GitHubAPI from the session token, falling back to the
    server-configured GITHUB_TOKEN so the dashboard works without a
    login when a token is present in .env.
    """
    return GitHubAPI(get_session_token() or settings.GITHUB_TOKEN)


def _current_repo() -> tuple[str, str]:
    """
    Return (owner, repo) from the repository selected in the session.

    Falls back to the optional GITHUB_OWNER / GITHUB_REPO bootstrap
    defaults from .env (used only when the user has not picked a repo).
    Returns ("", "") when no repository is available.
    """
    selected = get_selected_repo()
    if "/" in selected:
        owner, repo = selected.split("/", 1)
        return owner.strip(), repo.strip()
    return settings.GITHUB_OWNER.strip(), settings.GITHUB_REPO.strip()


def _repo_full_name() -> str:
    """Return the 'owner/repo' of the currently selected repository, or ''."""
    owner, repo = _current_repo()
    if owner and repo:
        return f"{owner}/{repo}"
    return ""


def _render_unauthorized(username: str, reason: str):
    """Render the 403 page with an audit log line."""
    from config.logging_setup import get_logger

    get_logger("auth").warning(
        "Access denied for '%s' (reason: %s)", username, reason
    )
    return (
        render_template("unauthorized.html", username=username, reason=reason, _bare=True),
        403,
    )


def _render_login(status_code: int | None = None):
    """Render the login page as a bare, full-viewport page."""
    kwargs: dict = {"oauth_enabled": settings.oauth_configured, "_bare": True}
    if status_code is None:
        return render_template("login.html", **kwargs)
    return render_template("login.html", **kwargs), status_code


def _load_report() -> tuple[dict | None, str | None]:
    """
    Load the team report for the currently selected repository.

    Returns (report, error). `error` is None on success. Handles
    misconfiguration and GitHub errors so routes never crash.
    """
    owner, repo = _current_repo()
    if not owner or not repo:
        return None, "No repository selected. Pick a repository from the selector in the top bar."
    if not get_session_token() and not settings.GITHUB_TOKEN:
        return None, "No GitHub token available: set GITHUB_TOKEN in .env or log in."

    api = get_api()
    try:
        report = api.build_team_report(owner, repo)
        return report, None
    except GitHubError as exc:
        return None, exc.message
    except Exception as exc:  # noqa: BLE001 - never crash the dashboard
        from config.logging_setup import get_logger

        get_logger("app").exception("Unexpected error while building team report: %s", exc)
        return None, "Unexpected error while loading GitHub data."


def _load_report_view() -> tuple[dict | None, str | None]:
    """
    Build the presentation-ready Team Reports view for the selected range.

    Uses the same `_load_report` source as the dashboard, then shapes it
    with `reports.build_view` and attaches the AI / rule-based analysis.
    Returns (view, error) - `error` is None on success.
    """
    from utils.reports import build_ai_summary, build_view, resolve_range

    report, error = _load_report()
    if error or not report:
        return None, error or "No repository selected. Pick a repository from the selector in the top bar."

    rng = resolve_range(
        period=(request.args.get("period") or "30d"),
        from_str=(request.args.get("from") or ""),
        to_str=(request.args.get("to") or ""),
    )
    view = build_view(report, rng["since"], rng["until"], rng["label"], rng["period"])
    view["ai"] = build_ai_summary(view, rng["label"])
    return view, None


def _utc_now_iso() -> str:
    """Current UTC time as an ISO string for the Last-updated badge."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


MAX_CHANGED_FILES_FOR_ANALYSIS = 40


def _static_analyze_changed_files(
    api: GitHubAPI, owner: str, repo: str, report: dict
) -> tuple[list[dict], dict]:
    """
    Deterministic static analysis of the files touched by recent commits.

    Fetches each changed file from GitHub (through the shared HTTP cache) and
    runs the AST/regex analyzer. Returns (findings, summary). Never raises:
    individual file errors are skipped so a flaky file cannot kill the tab.
    """
    from utils import static_analyzer

    touched: list[str] = []
    seen: set[str] = set()
    for push in report.get("pushes") or []:
        for f in push.get("files") or []:
            path = str(f.get("filename") or "").strip()
            if path and path not in seen:
                seen.add(path)
                touched.append(path)
    touched = touched[:MAX_CHANGED_FILES_FOR_ANALYSIS]

    default_branch = None
    findings: list[dict] = []
    for path in touched:
        ext = "." + path.rsplit(".", 1)[-1].lower() if "." in path else ""
        if ext not in static_analyzer.ANALYZABLE_EXTENSIONS:
            continue
        if default_branch is None:
            try:
                default_branch = api.get_default_branch(owner, repo)
            except GitHubError:
                default_branch = "HEAD"
        try:
            content = api.fetch_file_content(owner, repo, path, ref=default_branch)
        except (GitHubError, Exception):  # noqa: BLE001 - skip unreadable files
            continue
        findings.extend(static_analyzer.analyze_content(path, content))

    findings.sort(key=static_analyzer.severity_sort_key)
    return findings, static_analyzer.summarize(findings)


def _ai_errors_payload(store, scan_findings: list[dict], static_findings: list[dict]) -> list[dict]:
    """
    Merge every source of error findings into one list for the frontend:

      * stored AI/rule-based analyses (severity medium/high/critical)
      * the security scan cache (regex findings)
      * fresh static analysis findings on recently changed files

    Each item exposes a uniform ``result``-style shape plus a source tag.
    """
    from utils.static_analyzer import merge_findings

    stored = []
    for a in store.list_analyses(limit=50):
        if a["result"].get("severity") in ("high", "critical", "medium"):
            stored.append(
                {
                    "id": a["id"],
                    "created_at": a["created_at"],
                    "kind": a["kind"],
                    "target": a["target"],
                    "author": a["author"],
                    "result": a["result"],
                }
            )

    scan_errors = [
        {
            "created_at": None,
            "kind": "scan",
            "target": f.get("filename"),
            "author": None,
            "result": {
                "severity": str(f.get("severity") or "low").lower(),
                "file": f.get("filename"),
                "line": f.get("line_number", 0),
                "error_type": f.get("rule_id"),
                "problem": f.get("description"),
                "explanation": f.get("description"),
                "suggested_fix": f.get("recommendation"),
                "engine": "scan",
            },
        }
        for f in scan_findings
        if str(f.get("severity") or "").lower() in ("critical", "high", "medium")
    ]

    static_errors = [
        {
            "created_at": None,
            "kind": "static",
            "target": f.get("file"),
            "author": None,
            "result": f,
        }
        for f in static_findings
        if str(f.get("severity") or "").lower() in ("critical", "high", "medium")
    ]

    merged = merge_findings(
        [s["result"] for s in stored],
        [s["result"] for s in scan_errors],
        [s["result"] for s in static_errors],
    )
    # Keep the per-source detail on top of the deduplicated set.
    detail_by_key = {
        (str(item["result"].get("error_type") or item["result"].get("rule_id") or ""),
         str(item["result"].get("file") or item["result"].get("filename") or ""),
         int(item["result"].get("line") or item["result"].get("line_number") or 0)): item
        for item in stored + scan_errors + static_errors
    }
    payload = []
    for finding in merged:
        key = (
            str(finding.get("error_type") or finding.get("rule_id") or ""),
            str(finding.get("file") or finding.get("filename") or ""),
            int(finding.get("line") or finding.get("line_number") or 0),
        )
        detail = detail_by_key.get(key) or {}
        payload.append({"detail": detail, "finding": finding})
    return payload


def _fix_preview(api: GitHubAPI, owner: str, repo: str, path: str, ref: str = "") -> dict:
    """
    Generate a fix preview for one file WITHOUT changing anything.

    Returns the analysis plus a unified diff between the original content
    and the AI-proposed fixed content. Never writes to GitHub.
    """
    import difflib

    from utils import static_analyzer

    ref = ref.strip() or api.get_default_branch(owner, repo)
    content = api.fetch_file_content(owner, repo, path, ref=ref)
    analysis = ai_analyzer.analyze_code(path, content, context=f"{owner}/{repo}")
    fixed_code = analysis.get("fixed_code") or ""

    diff_text = ""
    if fixed_code and fixed_code != content:
        original_lines = content.splitlines()
        fixed_lines = fixed_code.splitlines()
        diff_text = "\n".join(
            difflib.unified_diff(
                original_lines,
                fixed_lines,
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
                lineterm="",
            )
        )
    # Also surface the deterministic static findings for context.
    static_findings = []
    ext = "." + path.rsplit(".", 1)[-1].lower() if "." in path else ""
    if ext in static_analyzer.ANALYZABLE_EXTENSIONS:
        static_findings = static_analyzer.analyze_content(path, content)

    return {
        "path": path,
        "ref": ref,
        "analysis": analysis,
        "has_fixed_code": bool(fixed_code),
        "fixed_code": fixed_code,
        "diff": diff_text,
        "diff_lines": len(diff_text.splitlines()) if diff_text else 0,
        "static_findings": static_findings,
        "note": "Preview only - nothing was modified on GitHub.",
    }


def _safe_ai_fix_branch(branch: str) -> bool:
    """Only branches created by the AI-fix workflow may be rolled back."""
    branch = (branch or "").strip()
    return branch.startswith("ai-fix/") and "/" not in branch[len("ai-fix/"):]


# ======================================================================
# Route definitions
# ======================================================================
def register_routes(app: Flask) -> None:
    # --- Root -----------------------------------------------------------
    @app.route("/")
    def index():
        if is_logged_in():
            return redirect(url_for("dashboard"))
        return redirect(url_for("login"))

    # --- Login (GET form + POST PAT) ------------------------------------
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if is_logged_in():
            return redirect(url_for("dashboard"))

        if request.method == "GET":
            return _render_login()

        # POST: Personal Access Token login.
        client_ip = request.remote_addr or "unknown"
        if is_login_rate_limited(client_ip):
            flash("Too many login attempts. Please try again later.", "danger")
            return _render_login(429)

        record_login_attempt(client_ip)
        token = (request.form.get("token") or "").strip()
        if not token:
            flash("Please paste a GitHub token.", "danger")
            return _render_login()

        api = GitHubAPI(token)
        try:
            user = api.validate_token()
        except GitHubError as exc:
            flash(f"Sign-in failed: {exc.message}", "danger")
            return _render_login()

        username = (user or {}).get("login", "")
        if not is_user_allowed(username):
            return _render_unauthorized(username, "not on the ALLOWED_GITHUB_USERS list")

        store_session_token(token, "pat")
        session["github_user"] = username
        session["github_avatar"] = (user or {}).get("avatar_url", "")
        # A different account may be signing in - do not carry over a
        # repository selection that may belong to the previous user. The
        # dashboard lets the user pick from THIS account's repositories.
        set_selected_repo("")
        flash(f"Welcome back, {username}!", "success")
        return redirect(url_for("dashboard"))

    # --- OAuth start -----------------------------------------------------
    @app.route("/auth/login")
    def auth_login():
        if not settings.oauth_configured:
            flash("GitHub OAuth is not configured. Use a Personal Access Token instead.", "warning")
            return redirect(url_for("login"))
        oauth = app.extensions["gitpulse_oauth"]
        return oauth.github.authorize_redirect(settings.GITHUB_REDIRECT_URI)

    # --- OAuth callback --------------------------------------------------
    @app.route("/auth/callback")
    def auth_callback():
        from config.logging_setup import get_logger

        if not settings.oauth_configured:
            return redirect(url_for("login"))

        oauth = app.extensions["gitpulse_oauth"]
        try:
            token = oauth.github.authorize_access_token()
            access_token = token.get("access_token")
        except Exception as exc:  # noqa: BLE001 - bad / forged callback
            get_logger("auth").warning("OAuth callback failed: %s", exc)
            flash("GitHub sign-in failed. Please try again.", "danger")
            return redirect(url_for("login"))

        if not access_token:
            flash("GitHub did not return an access token.", "danger")
            return redirect(url_for("login"))

        # Fetch the profile to check against the allow-list.
        api = GitHubAPI(access_token)
        try:
            user = api.validate_token()
        except GitHubError as exc:
            get_logger("auth").warning("OAuth token validation failed: %s", exc.message)
            flash("Could not validate your GitHub account.", "danger")
            return redirect(url_for("login"))

        username = (user or {}).get("login", "")
        if not is_user_allowed(username):
            return _render_unauthorized(username, "not on the ALLOWED_GITHUB_USERS list")

        store_session_token(access_token, "oauth")
        session["github_user"] = username
        session["github_avatar"] = (user or {}).get("avatar_url", "")
        # Never carry a previous account's repository selection across logins.
        set_selected_repo("")
        flash(f"Signed in as {username}.", "success")
        return redirect(url_for("dashboard"))

    # --- Logout ----------------------------------------------------------
    @app.route("/logout")
    def logout():
        clear_session_token()
        flash("You have been logged out.", "info")
        return redirect(url_for("login"))

    # --- Dashboard -------------------------------------------------------
    @app.route("/dashboard")
    @login_required
    def dashboard():
        report, error = _load_report()

        suggestions = []
        scan_findings = []
        scan_summary = CodeScanner.summarize([])
        if report:
            suggestions = generate_suggestions(report["members"])

        # Attach cached scan results (if any) so the tab shows data.
        scan_cache = app.extensions["scan_cache"]
        scan_findings = scan_cache.get("data") or []
        scan_summary = CodeScanner.summarize(scan_findings)

        store = app.extensions["store"]
        ai_analyses = store.list_analyses(limit=30)
        fix_attempts = store.list_fix_attempts(limit=30)
        recent_activity = list(app.extensions["recent_activity"])
        static_analysis = app.extensions["static_analysis"]

        ai_errors_count = sum(
            1
            for a in ai_analyses
            if a["result"].get("severity") in ("high", "critical", "medium")
        )
        ai_fixed_count = sum(1 for f in fix_attempts if f.get("status") == "created")

        return render_template(
            "dashboard.html",
            report=report,
            suggestions=[s.to_dict() for s in suggestions],
            scan_findings=sorted(scan_findings, key=CodeScanner.severity_sort_key),
            scan_summary=scan_summary,
            error=error,
            selected_repo=_repo_full_name(),
            ai_enabled=settings.anthropic_configured,
            ai_analyses=ai_analyses,
            fix_attempts=fix_attempts,
            recent_activity=recent_activity,
            activity_window=settings.ACTIVITY_WINDOW_DAYS,
            ai_errors_count=ai_errors_count,
            ai_fixed_count=ai_fixed_count,
            webhook_configured=bool(settings.GITHUB_WEBHOOK_SECRET),
            last_updated=app.extensions["last_updated"],
            last_analyzed=app.extensions["last_analyzed"],
            static_findings=static_analysis.get("findings") or [],
            static_summary=static_analysis.get("summary")
            or {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0, "TOTAL": 0},
            static_analyzed_at=static_analysis.get("analyzed_at"),
            poll_interval=settings.AI_POLL_INTERVAL_SECONDS,
        )

    # --- Dashboard refresh (re-fetch collaborators + activity) ------------
    @app.route("/dashboard/refresh", methods=["POST"])
    @login_required
    def refresh_dashboard():
        """Force a fresh fetch of collaborators, commits, PRs and issues."""
        # The report itself is rebuilt from the GitHub API on every dashboard
        # load; refreshing only needs to clear the cached scan findings, then
        # reload the page.
        app.extensions["scan_cache"] = {"data": None}
        from config.logging_setup import get_logger

        get_logger("app").info(
            "Dashboard refresh requested by %s", session.get("github_user")
        )
        flash("Dashboard refreshed - GitHub data re-fetched.", "success")
        return redirect(url_for("dashboard"))
    # --- Member profile --------------------------------------------------
    @app.route("/member/<username>")
    @login_required
    def member_profile(username):
        owner, repo = _current_repo()
        if not owner or not repo:
            flash("No repository selected. Choose one from the selector above.", "warning")
            return redirect(url_for("dashboard"))

        api = get_api()
        try:
            profile = api.build_member_profile(owner, repo, username)
        except GitHubError as exc:
            flash(f"Could not load member profile: {exc.message}", "danger")
            return redirect(url_for("dashboard"))
        except Exception as exc:  # noqa: BLE001
            from config.logging_setup import get_logger

            get_logger("app").exception("Member profile error for %s: %s", username, exc)
            flash("Could not load member profile.", "danger")
            return redirect(url_for("dashboard"))

        member_suggestions = generate_suggestions([profile["member"]])
        return render_template(
            "member.html",
            profile=profile,
            suggestions=[s.to_dict() for s in member_suggestions],
            ai_enabled=settings.anthropic_configured,
            selected_repo=_repo_full_name(),
        )

    # --- Team Members (status-filterable) --------------------------------
    @app.route("/team-members")
    @login_required
    def team_members():
        """
        List every team member, optionally filtered by activity status.

        The filter uses the exact same `is_active` flag that the dashboard
        uses to derive its Active/Inactive counts, so the numbers always
        match. The flag is computed in build_team_report via
        activity_mod.enrich_member (status == "ACTIVE").

        ?status=active    -> only ACTIVE members
        ?status=inactive  -> every non-ACTIVE member
        no parameter      -> all members
        """
        report, error = _load_report()

        status = (request.args.get("status") or "").strip().lower()
        members = list((report or {}).get("members") or [])

        filter_title = "All Members"
        if status == "active":
            members = [m for m in members if m.get("is_active")]
            filter_title = "Active Members"
        elif status == "inactive":
            members = [m for m in members if not m.get("is_active")]
            filter_title = "Inactive Members"
        else:
            status = ""

        return render_template(
            "team_members.html",
            report=report,
            members=members,
            error=error,
            filter_status=status,
            filter_title=filter_title,
            activity_window=settings.ACTIVITY_WINDOW_DAYS,
            selected_repo=_repo_full_name(),
        )

    # --- Team Reports (real page) ----------------------------------------
    @app.route("/reports")
    @login_required
    def reports():
        """Full team performance report with range selection and exports."""
        view, error = _load_report_view()

        range_presets = [
            ("today", "Today"),
            ("7d", "Last 7 Days"),
            ("30d", "Last 30 Days"),
            ("month", "This Month"),
            ("custom", "Custom Range"),
        ]
        range_query = {
            k: v
            for k, v in request.args.items()
            if k in ("period", "from", "to") and v
        }

        return render_template(
            "reports.html",
            view=view,
            error=error,
            repo_name=_repo_full_name(),
            range_presets=range_presets,
            range_query=range_query,
            from_str=(request.args.get("from") or ""),
            to_str=(request.args.get("to") or ""),
            selected_repo=_repo_full_name(),
        )

    @app.route("/reports/export/<fmt>")
    @login_required
    def reports_export(fmt):
        """Download the current report as CSV or PDF."""
        view, error = _load_report_view()
        if error or not view:
            flash(error or "No report available to export.", "warning")
            return redirect(url_for("reports"))

        filename_base = "gitpulse-team-report"

        if fmt == "csv":
            from utils.reports import to_csv

            payload = to_csv(view).encode("utf-8-sig")
            return Response(
                payload,
                mimetype="text/csv",
                headers={"Content-Disposition": f'attachment; filename="{filename_base}.csv"'},
            )

        if fmt == "pdf":
            from utils.reports import to_pdf

            payload = to_pdf(view)
            return Response(
                payload,
                mimetype="application/pdf",
                headers={"Content-Disposition": f'attachment; filename="{filename_base}.pdf"'},
            )

        return jsonify({"error": f"Unsupported export format: {fmt}"}), 404

    @app.route("/code-review")
    @login_required
    def code_review():
        return render_template(
            "coming_soon.html",
            page_title="Code Review",
            icon="◉",
            description="Centralized pull-request review queues and review guidance will live here.",
            selected_repo=_repo_full_name(),
        )

    @app.route("/notifications")
    @login_required
    def notifications():
        return render_template(
            "coming_soon.html",
            page_title="Notifications",
            icon="☼",
            description="Delivery of team alerts and activity digests will be configured here.",
            selected_repo=_repo_full_name(),
        )

    @app.route("/settings")
    @login_required
    def settings_page():
        return render_template(
            "coming_soon.html",
            page_title="Settings",
            icon="⚙",
            description="Application, team and notification preferences will be managed here.",
            selected_repo=_repo_full_name(),
        )

    # --- AI: analyze a file (dashboard form) -----------------------------
    @app.route("/dashboard/analyze", methods=["POST"])
    @login_required
    def analyze_file():
        path = (request.form.get("path") or "").strip()
        ref = (request.form.get("ref") or "").strip() or None
        if not path:
            flash("A file path is required for analysis.", "warning")
            return redirect(url_for("dashboard"))

        owner, repo = _current_repo()
        if not owner or not repo:
            flash("No repository selected. Choose one from the selector above.", "warning")
            return redirect(url_for("dashboard"))

        api = get_api()
        try:
            ref = ref or api.get_default_branch(owner, repo)
            content = api.fetch_file_content(owner, repo, path, ref=ref)
            result = ai_analyzer.analyze_code(path, content, context=f"{owner}/{repo}")
        except GitHubError as exc:
            flash(f"Analysis failed: {exc.message}", "danger")
            return redirect(url_for("dashboard"))

        app.extensions["store"].save_analysis("code", path, result, author=session.get("github_user"))
        if result["severity"] in ("high", "critical"):
            flash(f"AI found a {result['severity']} issue in {path}: {result['problem']}", "warning")
        else:
            flash(f"AI analysis complete for {path}: {result['problem']}", "info")
        return redirect(url_for("dashboard"))

    # --- AI: create a fix PR (dashboard form) ----------------------------
    @app.route("/dashboard/ai-fix", methods=["POST"])
    @login_required
    def ai_fix_route():
        path = (request.form.get("path") or "").strip()
        issue_label = (request.form.get("issue_label") or "").strip() or path
        if not path:
            flash("A file path is required to generate a fix.", "warning")
            return redirect(url_for("dashboard"))

        owner, repo = _current_repo()
        if not owner or not repo:
            flash("No repository selected. Choose one from the selector above.", "warning")
            return redirect(url_for("dashboard"))

        api = get_api()
        try:
            ref = api.get_default_branch(owner, repo)
            content = api.fetch_file_content(owner, repo, path, ref=ref)
            analysis = ai_analyzer.analyze_code(path, content, context=f"{owner}/{repo}")
            fixed_code = analysis.get("fixed_code") or ""
            if not fixed_code:
                flash("AI did not produce a fix for this file.", "warning")
                return redirect(url_for("dashboard"))
            outcome = fixer.create_fix_pull_request(
                api,
                owner,
                repo,
                path,
                issue_label,
                analysis,
                fixed_code,
            )
        except GitHubError as exc:
            flash(f"Fix failed: {exc.message}", "danger")
            return redirect(url_for("dashboard"))

        if outcome.get("status") == "created":
            flash(f"AI fix PR created: {outcome['pr_url']}", "success")
        elif outcome.get("status") == "validation_failed":
            flash(f"AI fix was NOT merged - validation failed: {outcome.get('error', '')}", "danger")
        else:
            flash(f"AI fix failed: {outcome.get('error', 'unknown error')}", "danger")
        return redirect(url_for("dashboard"))

    # --- GitHub webhook --------------------------------------------------
    @app.route("/webhook/github", methods=["POST"])
    def github_webhook():
        body = request.get_data()
        signature = request.headers.get("X-Hub-Signature-256") or request.headers.get(
            "X-Hub-Signature"
        )
        if not webhooks.verify_signature(body, signature):
            return jsonify({"status": "rejected"}), 403

        event = request.headers.get("X-GitHub-Event", "unknown")
        payload = request.get_json(silent=True) or {}
        record = webhooks.handle_event(event, payload)
        store = app.extensions["store"]
        store.save_webhook_event(
            event=record["event"],
            action=record.get("action"),
            sender=record.get("sender"),
            repo=record.get("repo"),
            payload=payload,
        )
        # Keep the in-memory feed fresh (newest first).
        feed = [record] + list(app.extensions["recent_activity"])
        app.extensions["recent_activity"] = feed[:20]

        from config.logging_setup import get_logger

        get_logger("app").info("Webhook received: %s", record.get("summary", event))
        return jsonify({"status": "ok"}), 200

    # --- Security scan (POST triggers, GET returns cached) ----------------
    @app.route("/dashboard/scan", methods=["POST"])
    @login_required
    def run_scan():
        scan_cache = app.extensions["scan_cache"]
        api = get_api()
        scanner = CodeScanner()

        target = (request.form.get("target") or "repo").strip().lower()
        try:
            if target == "local":
                # Scan this project's own source tree (useful for demos).
                local_path = os.path.dirname(os.path.abspath(__file__))
                findings = scanner.scan_path(local_path)
                source_label = f"local project ({local_path})"
            else:
                # Scan the selected GitHub repository via the API.
                owner, repo = _current_repo()
                if not owner or not repo:
                    flash("No repository selected. Choose one from the selector above.", "warning")
                    return redirect(url_for("dashboard"))
                findings = scanner.scan_github_repo(api, owner, repo)
                source_label = f"{owner}/{repo}"
        except GitHubError as exc:
            flash(f"Scan failed: {exc.message}", "danger")
            return redirect(url_for("dashboard"))

        scan_cache["data"] = findings
        flash(
            f"Scan complete ({source_label}): {len(findings)} findings.",
            "success",
        )
        return redirect(url_for("dashboard"))

    # --- Health check (for deployment platforms) ---------------------------
    @app.route("/healthz")
    def healthz():
        return jsonify(
            {"status": "ok", "user": session.get("github_user"), "repo": _repo_full_name()}
        )


# ======================================================================
# JSON API (reused by the frontend)
# ======================================================================
def register_api_routes(app: Flask) -> None:
    """REST-style endpoints powering the dashboard and integrations."""

    def _report_or_error():
        """Return (report, error) or abort with 500 JSON."""
        report, error = _load_report()
        return report, error

    # --- GitHub account + repository selection ---------------------------
    @app.route("/api/github/user")
    @login_required
    def api_github_user():
        """Return the currently authenticated GitHub user (never the token)."""
        api = get_api()
        try:
            user = api.validate_token()
        except GitHubError as exc:
            code = 401 if exc.status_code in (401, 403) else 400
            return jsonify({"error": exc.message}), code
        return jsonify(
            {
                "login": user.get("login", ""),
                "avatar_url": user.get("avatar_url", ""),
                "selected_repo": get_selected_repo(),
            }
        )

    @app.route("/api/github/repos")
    @login_required
    def api_github_repos():
        """Return the repositories accessible to the authenticated user."""
        api = get_api()
        try:
            repos = api.get_user_repos()
        except GitHubError as exc:
            code = 401 if exc.status_code in (401, 403) else 400
            return jsonify({"error": exc.message}), code
        return jsonify({"repos": repos, "selected_repo": get_selected_repo()})

    @app.route("/api/github/select-repo", methods=["POST"])
    @login_required
    def api_github_select_repo():
        """
        Select the repository to monitor for this session.

        The repo is validated against GitHub with the user's own token
        before it is stored, so access-denied / not-found errors are
        caught here with a friendly message.
        """
        data = request.get_json(silent=True) or {}
        full_name = (data.get("repo") or "").strip().strip("/")
        if not full_name or "/" not in full_name:
            return jsonify({"error": "A repository in 'owner/name' format is required."}), 400
        owner, repo = full_name.split("/", 1)
        if not owner or not repo:
            return jsonify({"error": "A repository in 'owner/name' format is required."}), 400

        api = get_api()
        try:
            meta = api.get_repository(owner, repo)
        except GitHubError as exc:
            code = 404 if exc.status_code == 404 else 400
            return jsonify({"error": exc.message}), code

        selected = meta.get("full_name", f"{owner}/{repo}")
        set_selected_repo(selected)
        app.logger.info("Repository selected: %s", selected)
        return jsonify({"ok": True, "repo": selected})

    @app.route("/api/team")
    @login_required
    def api_team():
        report, error = _report_or_error()
        if error:
            return jsonify({"error": error}), 400
        return jsonify(
            {
                "owner": report["owner"],
                "repo": report["repo"],
                "team_name": report["team_name"],
                "overview": report["overview"],
            }
        )

    @app.route("/api/team/members")
    @login_required
    def api_team_members():
        report, error = _report_or_error()
        if error:
            return jsonify({"error": error}), 400
        return jsonify({"members": report["members"]})

    @app.route("/api/team/collaborators")
    @login_required
    def api_team_collaborators():
        """
        Return the repository's actual collaborators.

        The list comes from GET /repos/{owner}/{repo}/collaborators so it
        includes members who have been granted access even if they have
        never committed. Each collaborator carries username, avatar, role,
        permissions and per-member activity metrics.
        """
        report, error = _report_or_error()
        if error:
            return jsonify({"error": error}), 400
        return jsonify(
            {
                "collaborators": report["members"],
                "overview": report["overview"],
            }
        )

    @app.route("/api/team/activity")
    @login_required
    def api_team_activity():
        report, error = _report_or_error()
        if error:
            return jsonify({"error": error}), 400
        return jsonify(
            {
                "members": report["members"],
                "pushes": report["pushes"],
                "recent_activity": app.extensions["recent_activity"],
            }
        )

    @app.route("/api/team/member/<username>")
    @login_required
    def api_team_member(username):
        owner, repo = _current_repo()
        if not owner or not repo:
            return jsonify({"error": "No repository selected."}), 400
        api = get_api()
        try:
            profile = api.build_member_profile(owner, repo, username)
        except GitHubError as exc:
            return jsonify({"error": exc.message}), 400
        return jsonify(profile)

    @app.route("/api/commits")
    @login_required
    def api_commits():
        report, error = _report_or_error()
        if error:
            return jsonify({"error": error}), 400
        return jsonify({"pushes": report["pushes"]})

    @app.route("/api/pull-requests")
    @login_required
    def api_pull_requests():
        report, error = _report_or_error()
        if error:
            return jsonify({"error": error}), 400
        return jsonify({"pull_requests": report["pull_requests"]})

    @app.route("/api/issues")
    @login_required
    def api_issues():
        report, error = _report_or_error()
        if error:
            return jsonify({"error": error}), 400
        return jsonify({"issues": report["issues"]})

    @app.route("/api/repository")
    @login_required
    def api_repository():
        """Return the currently selected repository's metadata."""
        report, error = _report_or_error()
        if error:
            return jsonify({"error": error}), 400
        return jsonify({"repo": report["repo"], "team_name": report["team_name"]})

    @app.route("/api/overview")
    @login_required
    def api_overview():
        """Return the dashboard overview metrics."""
        report, error = _report_or_error()
        if error:
            return jsonify({"error": error}), 400
        return jsonify(
            {
                "owner": report["owner"],
                "repo": report["repo"],
                "overview": report["overview"],
                "languages": report["languages"],
            }
        )

    @app.route("/api/activity")
    @login_required
    def api_activity():
        """
        Return the unified activity feed.

        Query params: category (commit|pull_request|issue|member|other),
        author, q (substring match on title/action), limit (default 50).
        """
        report, error = _report_or_error()
        if error:
            return jsonify({"error": error}), 400
        feed = list(report.get("activity_feed") or [])
        category = (request.args.get("category") or "").strip().lower()
        author = (request.args.get("author") or "").strip().lower()
        query = (request.args.get("q") or "").strip().lower()
        try:
            limit = max(1, min(int(request.args.get("limit") or 50), 200))
        except (TypeError, ValueError):
            limit = 50

        if category:
            feed = [item for item in feed if (item.get("category") or "").lower() == category]
        if author:
            feed = [item for item in feed if (item.get("actor") or "").lower() == author]
        if query:
            feed = [
                item
                for item in feed
                if query in (item.get("title") or "").lower()
                or query in (item.get("action") or "").lower()
            ]
        return jsonify({"activity": feed[:limit]})

    @app.route("/api/commit/<sha>")
    @login_required
    def api_commit_detail(sha):
        """Return a single commit's detail (files, stats, message)."""
        owner, repo = _current_repo()
        if not owner or not repo:
            return jsonify({"error": "No repository selected."}), 400
        api = get_api()
        try:
            detail = api.build_commit_detail(owner, repo, sha)
        except GitHubError as exc:
            return jsonify({"error": exc.message}), 400
        return jsonify(detail)

    @app.route("/api/pull-request/<int:number>")
    @login_required
    def api_pull_request_detail(number):
        """Return a single pull request's rich detail view."""
        owner, repo = _current_repo()
        if not owner or not repo:
            return jsonify({"error": "No repository selected."}), 400
        api = get_api()
        try:
            detail = api.build_pr_detail(owner, repo, number)
        except GitHubError as exc:
            return jsonify({"error": exc.message}), 400
        return jsonify(detail)

    @app.route("/api/issue/<int:number>")
    @login_required
    def api_issue_detail(number):
        """Return a single issue's rich detail view."""
        owner, repo = _current_repo()
        if not owner or not repo:
            return jsonify({"error": "No repository selected."}), 400
        api = get_api()
        try:
            detail = api.build_issue_detail(owner, repo, number)
        except GitHubError as exc:
            return jsonify({"error": exc.message}), 400
        return jsonify(detail)

    @app.route("/api/refresh", methods=["POST"])
    @login_required
    def api_refresh():
        """
        AJAX refresh: clear the in-memory GitHub HTTP cache and the cached
        scan results, then reload the webhook activity list.
        """
        from utils.github_api import clear_http_cache

        clear_http_cache()
        app.extensions["scan_cache"] = {"data": None}
        app.extensions["recent_activity"] = list(
            app.extensions["store"].list_webhook_events(limit=20)
        )
        app.logger.info("API refresh requested by %s", session.get("github_user"))
        return jsonify(
            {
                "ok": True,
                "message": "GitHub data cache cleared. The next request re-fetches from GitHub.",
            }
        )

    @app.route("/api/errors")
    @login_required
    def api_errors():
        store = app.extensions["store"]
        analyses = [
            {
                "id": a["id"],
                "created_at": a["created_at"],
                "kind": a["kind"],
                "target": a["target"],
                "author": a["author"],
                "result": a["result"],
            }
            for a in store.list_analyses(limit=50)
            if a["result"].get("severity") in ("high", "critical", "medium")
        ]
        return jsonify({"errors": analyses})

    @app.route("/api/ai/errors")
    @login_required
    def api_ai_errors():
        """
        Aggregated error list: stored AI analyses + cached security scan
        findings + fresh deterministic static findings on changed files.
        Each entry carries a ``finding`` (normalized result) plus ``detail``
        (the original source record) so the UI can show why/where.
        """
        store = app.extensions["store"]
        scan_findings = app.extensions["scan_cache"].get("data") or []
        static_findings = app.extensions["static_analysis"].get("findings") or []
        payload = _ai_errors_payload(store, scan_findings, static_findings)
        return jsonify(
            {
                "errors": payload,
                "total": len(payload),
                "static_analyzed_at": app.extensions["static_analysis"].get("analyzed_at"),
            }
        )

    @app.route("/api/ai/status")
    @login_required
    def api_ai_status():
        """Lightweight AI status for the frontend poller (cheap, no GitHub calls)."""
        store = app.extensions["store"]
        static_analysis = app.extensions["static_analysis"]
        scan_findings = app.extensions["scan_cache"].get("data") or []
        ai_analyses = store.list_analyses(limit=30)
        fix_attempts = store.list_fix_attempts(limit=30)
        errors = sum(
            1
            for a in ai_analyses
            if a["result"].get("severity") in ("high", "critical", "medium")
        )
        scan_errors = sum(
            1
            for f in scan_findings
            if str(f.get("severity") or "").lower() in ("critical", "high", "medium")
        )
        static_errors = sum(
            1
            for f in (static_analysis.get("findings") or [])
            if str(f.get("severity") or "").lower() in ("critical", "high", "medium")
        )
        return jsonify(
            {
                "ai_enabled": settings.anthropic_configured,
                "last_updated": app.extensions["last_updated"],
                "last_analyzed": app.extensions["last_analyzed"],
                "error_counts": {
                    "ai_errors": errors,
                    "scan_errors": scan_errors,
                    "static_errors": static_errors,
                },
                "error_total": errors + scan_errors + static_errors,
                "fix_attempts_created": sum(
                    1 for f in fix_attempts if f.get("status") == "created"
                ),
                "analyses_count": len(ai_analyses),
                "poll_interval": settings.AI_POLL_INTERVAL_SECONDS,
            }
        )

    @app.route("/api/ai/fix/preview", methods=["POST"])
    @login_required
    def api_ai_fix_preview():
        """
        Preview an AI fix as a diff WITHOUT modifying anything.

        Returns the analysis, the proposed fixed content and a unified diff.
        No GitHub mutation happens here - the caller then decides to apply
        the fix via /api/ai/fix-pr or to roll back.
        """
        data = request.get_json(silent=True) or {}
        path = (data.get("path") or "").strip()
        if not path:
            return jsonify({"error": "A file path is required."}), 400
        owner, repo = _current_repo()
        if not owner or not repo:
            return jsonify({"error": "No repository selected."}), 400
        api = get_api()
        try:
            preview = _fix_preview(api, owner, repo, path, ref=(data.get("ref") or ""))
        except GitHubError as exc:
            return jsonify({"error": exc.message}), 400
        return jsonify(preview)

    @app.route("/api/ai/fix/rollback", methods=["POST"])
    @login_required
    def api_ai_fix_rollback():
        """
        Safely roll back an AI-fix attempt by deleting its feature branch.

        Only ``ai-fix/<slug>-<timestamp>`` branches are accepted, so a
        request can never touch the default branch or any other branch.
        A rollback only makes sense before the PR is merged; once merged the
        change is part of the default branch and must be reverted normally.
        """
        data = request.get_json(silent=True) or {}
        branch = (data.get("branch") or "").strip()
        if not branch:
            return jsonify({"error": "A branch name is required."}), 400
        if not _safe_ai_fix_branch(branch):
            return jsonify(
                {
                    "error": (
                        "Refusing to delete: only branches created by the AI-fix "
                        "workflow (ai-fix/...) can be rolled back."
                    )
                }
            ), 400
        owner, repo = _current_repo()
        if not owner or not repo:
            return jsonify({"error": "No repository selected."}), 400
        api = get_api()
        try:
            api.delete_branch(owner, repo, branch)
        except GitHubError as exc:
            code = 404 if exc.status_code == 404 else 400
            return jsonify({"error": exc.message}), code
        from config.logging_setup import get_logger

        get_logger("app").info(
            "AI-fix rollback: deleted branch %s in %s/%s by %s",
            branch, owner, repo, session.get("github_user"),
        )
        return jsonify({"ok": True, "branch": branch, "message": f"Branch {branch} deleted."})

    @app.route("/api/dashboard/refresh", methods=["POST"])
    @login_required
    def api_dashboard_refresh():
        """
        Full AJAX refresh: bust the GitHub HTTP cache, re-fetch the team
        report, re-run static analysis of changed files and return fresh
        JSON so the frontend can update cards, charts and the activity feed
        WITHOUT a page reload.
        """
        from utils.github_api import clear_http_cache

        clear_http_cache()
        app.extensions["scan_cache"] = {"data": None}
        app.extensions["recent_activity"] = list(
            app.extensions["store"].list_webhook_events(limit=20)
        )

        report, error = _load_report()
        if error or not report:
            return jsonify({"error": error or "Could not refresh dashboard data."}), 400

        app.extensions["last_updated"] = _utc_now_iso()

        # Re-run static analysis so the error numbers are fresh too.
        owner, repo = _current_repo()
        try:
            static_findings, static_summary = _static_analyze_changed_files(
                get_api(), owner, repo, report
            )
        except Exception as exc:  # noqa: BLE001
            from config.logging_setup import get_logger

            get_logger("app").warning("Refresh static analysis skipped: %s", exc)
            static_findings, static_summary = [], {
                "CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0, "TOTAL": 0,
            }
        app.extensions["static_analysis"] = {
            "findings": static_findings,
            "summary": static_summary,
            "analyzed_at": app.extensions["last_updated"],
        }

        analysis = ai_analyzer.analyze_repository(report, deep=True)
        members = report.get("members") or []
        return jsonify(
            {
                "ok": True,
                "last_updated": app.extensions["last_updated"],
                "repo": report.get("repo") or {},
                "overview": report.get("overview") or {},
                "languages": report.get("languages") or {},
                "members": members,
                "pushes": report.get("pushes") or [],
                "activity_feed": report.get("activity_feed") or [],
                "pull_requests": report.get("pull_requests") or [],
                "issues": report.get("issues") or [],
                "ai": {
                    "enabled": settings.anthropic_configured,
                    "health_score": analysis.get("health_score"),
                    "health_label": analysis.get("health_label"),
                    "summary": analysis.get("summary"),
                    "findings": analysis.get("findings") or [],
                    "commit_analyses": analysis.get("commit_analyses") or [],
                    "member_analyses": analysis.get("member_analyses") or [],
                    "last_analyzed": app.extensions["last_analyzed"],
                    "static_summary": static_summary,
                    "static_findings": static_findings,
                    "error_counts": {
                        "ai_errors": sum(
                            1
                            for a in app.extensions["store"].list_analyses(limit=30)
                            if a["result"].get("severity") in ("high", "critical", "medium")
                        ),
                        "scan_errors": sum(
                            1
                            for f in (app.extensions["scan_cache"].get("data") or [])
                            if str(f.get("severity") or "").lower() in ("critical", "high", "medium")
                        ),
                        "static_errors": sum(
                            1
                            for f in static_findings
                            if str(f.get("severity") or "").lower() in ("critical", "high", "medium")
                        ),
                        "ai_fixed_count": sum(
                            1
                            for f in app.extensions["store"].list_fix_attempts(limit=30)
                            if f.get("status") == "created"
                        ),
                    },
                },
            }
        )

    @app.route("/api/ai/analyze", methods=["POST"])
    @login_required
    def api_ai_analyze():
        data = request.get_json(silent=True) or {}
        path = (data.get("path") or "").strip()
        if not path:
            return jsonify({"error": "A file path is required."}), 400
        owner, repo = _current_repo()
        if not owner or not repo:
            return jsonify({"error": "No repository selected."}), 400
        api = get_api()
        try:
            ref = (data.get("ref") or "").strip() or api.get_default_branch(owner, repo)
            content = api.fetch_file_content(owner, repo, path, ref=ref)
            result = ai_analyzer.analyze_code(path, content, context=f"{owner}/{repo}")
        except GitHubError as exc:
            return jsonify({"error": exc.message}), 400
        app.extensions["store"].save_analysis("code", path, result, author=session.get("github_user"))
        return jsonify(result)

    @app.route("/api/ai/fix", methods=["POST"])
    @login_required
    def api_ai_fix():
        data = request.get_json(silent=True) or {}
        path = (data.get("path") or "").strip()
        if not path:
            return jsonify({"error": "A file path is required."}), 400
        owner, repo = _current_repo()
        if not owner or not repo:
            return jsonify({"error": "No repository selected."}), 400
        api = get_api()
        try:
            ref = (data.get("ref") or "").strip() or api.get_default_branch(owner, repo)
            content = api.fetch_file_content(owner, repo, path, ref=ref)
            result = ai_analyzer.analyze_code(path, content, context=f"{owner}/{repo}")
        except GitHubError as exc:
            return jsonify({"error": exc.message}), 400
        return jsonify(
            {
                "path": path,
                "analysis": result,
                "has_fixed_code": bool(result.get("fixed_code")),
                "note": "Call /api/ai/fix-pr to open a reviewable pull request.",
            }
        )

    @app.route("/api/ai/analyze-pr", methods=["POST"])
    @login_required
    def api_ai_analyze_pr():
        data = request.get_json(silent=True) or {}
        try:
            number = int(data.get("number") or 0)
        except (TypeError, ValueError):
            return jsonify({"error": "A valid PR number is required."}), 400
        owner, repo = _current_repo()
        if not owner or not repo:
            return jsonify({"error": "No repository selected."}), 400
        api = get_api()
        try:
            pr = api.get_pull_request(owner, repo, number)
            files = api.get_pr_files(owner, repo, number)
            diff = "\n".join(
                (f.get("patch") or f"# {f.get('filename')}") for f in files[:30]
            )
            result = ai_analyzer.analyze_pull_request(pr, diff=diff)
        except GitHubError as exc:
            return jsonify({"error": exc.message}), 400
        app.extensions["store"].save_analysis(
            "pr", f"#{number}", result, author=session.get("github_user")
        )
        return jsonify(result)

    @app.route("/api/ai/analyze-issue", methods=["POST"])
    @login_required
    def api_ai_analyze_issue():
        data = request.get_json(silent=True) or {}
        try:
            number = int(data.get("number") or 0)
        except (TypeError, ValueError):
            return jsonify({"error": "A valid issue number is required."}), 400
        owner, repo = _current_repo()
        if not owner or not repo:
            return jsonify({"error": "No repository selected."}), 400
        api = get_api()
        try:
            issues = api.get_issues(owner, repo, state="all")
            issue = next((i for i in issues if i["number"] == number), None)
            if not issue:
                return jsonify({"error": f"Issue #{number} not found."}), 404
            result = ai_analyzer.analyze_issue(
                {
                    "title": issue.get("title", ""),
                    "body": issue.get("body", ""),
                    "labels": [label.get("name", "") for label in issue.get("labels", [])],
                }
            )
        except GitHubError as exc:
            return jsonify({"error": exc.message}), 400
        app.extensions["store"].save_analysis(
            "issue", f"#{number}", result, author=session.get("github_user")
        )
        return jsonify(result)

    @app.route("/api/ai/analyze-repo", methods=["POST"])
    @login_required
    def api_ai_analyze_repo():
        """
        Full repository AI analysis: health score, commit classifications,
        member contribution, static error analysis of changed files and
        (when configured) an AI narrative. Results are saved to the store
        and used by the AI Fixes tab. Never modifies the repository.
        """
        report, error = _load_report()
        if error or not report:
            return jsonify({"error": error or "No repository data available."}), 400
        result = ai_analyzer.analyze_repository(report, deep=True)
        if settings.anthropic_configured:
            narrative = ai_analyzer.analyze_repository_ai(report)
            if narrative:
                result["ai_narrative"] = narrative
                result["engine"] = "ai"

        owner, repo = _current_repo()
        try:
            static_findings, static_summary = _static_analyze_changed_files(
                get_api(), owner, repo, report
            )
        except Exception as exc:  # noqa: BLE001 - analysis must never crash the tab
            from config.logging_setup import get_logger

            get_logger("app").warning("Static analysis skipped for %s/%s: %s", owner, repo, exc)
            static_findings, static_summary = [], {
                "CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0, "TOTAL": 0,
            }
        result["static_findings"] = static_findings
        result["code_stats"] = {
            "files_analyzed": len(
                {f.get("file") for f in static_findings if f.get("file")}
            ),
            "findings": static_summary,
        }
        result["last_analyzed"] = _utc_now_iso()

        app.extensions["last_analyzed"] = result["last_analyzed"]
        app.extensions["static_analysis"] = {
            "findings": static_findings,
            "summary": static_summary,
            "analyzed_at": result["last_analyzed"],
        }
        app.extensions["store"].save_analysis(
            "repo", report.get("repo") or f"{report.get('owner')}/{report.get('repo')}",
            result,
            author=session.get("github_user"),
        )
        return jsonify(result)

    @app.route("/api/ai/fix-pr", methods=["POST"])
    @login_required
    def api_ai_fix_pr():
        data = request.get_json(silent=True) or {}
        path = (data.get("path") or "").strip()
        issue_label = (data.get("issue_label") or "").strip() or path
        if not path:
            return jsonify({"error": "A file path is required."}), 400
        owner, repo = _current_repo()
        if not owner or not repo:
            return jsonify({"error": "No repository selected."}), 400
        api = get_api()
        try:
            ref = (data.get("ref") or "").strip() or api.get_default_branch(owner, repo)
            content = api.fetch_file_content(owner, repo, path, ref=ref)
            analysis = ai_analyzer.analyze_code(path, content, context=f"{owner}/{repo}")
            fixed_code = analysis.get("fixed_code") or ""
            if not fixed_code:
                return jsonify({"error": "AI did not produce a fix for this file."}), 400
            outcome = fixer.create_fix_pull_request(
                api,
                owner,
                repo,
                path,
                issue_label,
                analysis,
                fixed_code,
            )
        except GitHubError as exc:
            return jsonify({"error": exc.message}), 400
        code = 201 if outcome.get("status") == "created" else 400
        return jsonify(outcome), code


def register_api(app: Flask) -> None:
    """Attach the JSON API routes to the app."""
    register_api_routes(app)


# ======================================================================
# Error handlers
# ======================================================================
def register_error_handlers(app: Flask) -> None:
    from config.logging_setup import get_logger

    logger = get_logger("app")

    @app.errorhandler(404)
    def not_found(error):
        return render_template("base.html", _error_page=True, _code=404, _message="Page not found"), 404

    @app.errorhandler(403)
    def forbidden(error):
        return _render_unauthorized("unknown", "forbidden")

    @app.errorhandler(500)
    def internal_error(error):
        logger.exception("Unhandled exception: %s", error)
        return render_template("base.html", _error_page=True, _code=500, _message="Internal server error"), 500


# ======================================================================
# Entry point
# ======================================================================
app = create_app()

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=settings.DEBUG)
