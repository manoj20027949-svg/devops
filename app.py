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
    get_session_token,
    is_login_rate_limited,
    is_logged_in,
    is_user_allowed,
    login_required,
    record_login_attempt,
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
    Load the team report for the configured repository.

    Returns (report, error). `error` is None on success. Handles
    misconfiguration and GitHub errors so routes never crash.
    """
    owner = settings.GITHUB_OWNER
    repo = settings.GITHUB_REPO
    if not owner or not repo:
        return None, "Configuration missing: set GITHUB_OWNER and GITHUB_REPO in .env."
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
            ai_enabled=settings.anthropic_configured,
            ai_analyses=ai_analyses,
            fix_attempts=fix_attempts,
            recent_activity=recent_activity,
            activity_window=settings.ACTIVITY_WINDOW_DAYS,
            ai_errors_count=ai_errors_count,
            ai_fixed_count=ai_fixed_count,
        )

    # --- Member profile --------------------------------------------------
    @app.route("/member/<username>")
    @login_required
    def member_profile(username):
        api = get_api()
        try:
            profile = api.build_member_profile(
                settings.GITHUB_OWNER, settings.GITHUB_REPO, username
            )
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

        api = get_api()
        try:
            ref = ref or api.get_default_branch(settings.GITHUB_OWNER, settings.GITHUB_REPO)
            content = api.fetch_file_content(settings.GITHUB_OWNER, settings.GITHUB_REPO, path, ref=ref)
            result = ai_analyzer.analyze_code(path, content, context=f"{settings.GITHUB_OWNER}/{settings.GITHUB_REPO}")
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

        api = get_api()
        try:
            ref = api.get_default_branch(settings.GITHUB_OWNER, settings.GITHUB_REPO)
            content = api.fetch_file_content(settings.GITHUB_OWNER, settings.GITHUB_REPO, path, ref=ref)
            analysis = ai_analyzer.analyze_code(path, content, context=f"{settings.GITHUB_OWNER}/{settings.GITHUB_REPO}")
            fixed_code = analysis.get("fixed_code") or ""
            if not fixed_code:
                flash("AI did not produce a fix for this file.", "warning")
                return redirect(url_for("dashboard"))
            outcome = fixer.create_fix_pull_request(
                api,
                settings.GITHUB_OWNER,
                settings.GITHUB_REPO,
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
                # Scan the configured GitHub repository via the API.
                findings = scanner.scan_github_repo(
                    api, settings.GITHUB_OWNER, settings.GITHUB_REPO
                )
                source_label = f"{settings.GITHUB_OWNER}/{settings.GITHUB_REPO}"
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
        return jsonify({"status": "ok", "user": session.get("github_user")})


# ======================================================================
# JSON API (reused by the frontend)
# ======================================================================
def register_api_routes(app: Flask) -> None:
    """REST-style endpoints powering the dashboard and integrations."""

    def _report_or_error():
        """Return (report, error) or abort with 500 JSON."""
        report, error = _load_report()
        return report, error

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
        api = get_api()
        try:
            profile = api.build_member_profile(settings.GITHUB_OWNER, settings.GITHUB_REPO, username)
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

    @app.route("/api/ai/analyze", methods=["POST"])
    @login_required
    def api_ai_analyze():
        data = request.get_json(silent=True) or {}
        path = (data.get("path") or "").strip()
        if not path:
            return jsonify({"error": "A file path is required."}), 400
        api = get_api()
        try:
            ref = (data.get("ref") or "").strip() or api.get_default_branch(
                settings.GITHUB_OWNER, settings.GITHUB_REPO
            )
            content = api.fetch_file_content(settings.GITHUB_OWNER, settings.GITHUB_REPO, path, ref=ref)
            result = ai_analyzer.analyze_code(
                path, content, context=f"{settings.GITHUB_OWNER}/{settings.GITHUB_REPO}"
            )
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
        api = get_api()
        try:
            ref = (data.get("ref") or "").strip() or api.get_default_branch(
                settings.GITHUB_OWNER, settings.GITHUB_REPO
            )
            content = api.fetch_file_content(settings.GITHUB_OWNER, settings.GITHUB_REPO, path, ref=ref)
            result = ai_analyzer.analyze_code(
                path, content, context=f"{settings.GITHUB_OWNER}/{settings.GITHUB_REPO}"
            )
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
        api = get_api()
        try:
            pr = api.get_pull_request(settings.GITHUB_OWNER, settings.GITHUB_REPO, number)
            files = api.get_pr_files(settings.GITHUB_OWNER, settings.GITHUB_REPO, number)
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
        api = get_api()
        try:
            issues = api.get_issues(settings.GITHUB_OWNER, settings.GITHUB_REPO, state="all")
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

    @app.route("/api/ai/fix-pr", methods=["POST"])
    @login_required
    def api_ai_fix_pr():
        data = request.get_json(silent=True) or {}
        path = (data.get("path") or "").strip()
        issue_label = (data.get("issue_label") or "").strip() or path
        if not path:
            return jsonify({"error": "A file path is required."}), 400
        api = get_api()
        try:
            ref = (data.get("ref") or "").strip() or api.get_default_branch(
                settings.GITHUB_OWNER, settings.GITHUB_REPO
            )
            content = api.fetch_file_content(settings.GITHUB_OWNER, settings.GITHUB_REPO, path, ref=ref)
            analysis = ai_analyzer.analyze_code(
                path, content, context=f"{settings.GITHUB_OWNER}/{settings.GITHUB_REPO}"
            )
            fixed_code = analysis.get("fixed_code") or ""
            if not fixed_code:
                return jsonify({"error": "AI did not produce a fix for this file."}), 400
            outcome = fixer.create_fix_pull_request(
                api,
                settings.GITHUB_OWNER,
                settings.GITHUB_REPO,
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
