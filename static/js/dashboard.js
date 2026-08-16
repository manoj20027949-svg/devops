/* ============================================================
   GitPulse - dashboard page behaviour
   Clickable stat cards, activity/commit/PR/issue filters,
   detail modals, AJAX refresh and repository health analysis.
   ============================================================ */
(function () {
    "use strict";

    // ---------- Modal ----------
    var modal = document.getElementById("gitpulseModal");
    var modalTitle = document.getElementById("gpModalTitle");
    var modalBody = document.getElementById("gpModalBody");
    var modalLink = document.getElementById("gpModalLink");

    function openModal(title, url) {
        if (!modal) return;
        modalTitle.textContent = title;
        modalLink.href = url || "#";
        modal.classList.add("open");
        modal.setAttribute("aria-hidden", "false");
        document.body.classList.add("modal-open-gp");
    }

    function closeModal() {
        if (!modal) return;
        modal.classList.remove("open");
        modal.setAttribute("aria-hidden", "true");
        document.body.classList.remove("modal-open-gp");
    }

    if (modal) {
        modal.addEventListener("click", function (event) {
            if (event.target.closest("[data-gp-close]")) closeModal();
        });
        document.addEventListener("keydown", function (event) {
            if (event.key === "Escape") closeModal();
        });
    }

    function esc(value) {
        return String(value == null ? "" : value)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    function renderError(msg) {
        return '<p class="muted mb-0">' + esc(msg) + "</p>";
    }

    // ---------- Tab navigation (mirrors main.js for stat cards) ----------
    function activateTab(targetId) {
        document.querySelectorAll(".tab-pane").forEach(function (pane) {
            pane.classList.toggle("active", pane.id === targetId);
        });
        document.querySelectorAll(".sidebar-nav .nav-link").forEach(function (link) {
            link.classList.toggle("active", link.dataset.tab === targetId);
        });
    }

    document.querySelectorAll(".stat-card-link").forEach(function (card) {
        card.addEventListener("click", function (event) {
            var goto = card.dataset.goto;
            if (!goto) return; // Real page navigation (member cards link to /team-members).
            event.preventDefault();
            activateTab(goto);
        });
    });

    // ---------- Refresh (AJAX, no page reload) ----------
    var lastUpdatedBadge = document.getElementById("lastUpdatedBadge");

    function setStatValue(name, value) {
        var el = document.querySelector('[data-refresh="' + name + '"]');
        if (el) el.textContent = String(value);
    }

    function updateOverview(overview) {
        if (!overview) return;
        setStatValue("members", overview.members || 0);
        setStatValue("active_members", overview.active_members || 0);
        setStatValue("inactive_members", overview.inactive_members || 0);
        setStatValue("total_commits", overview.total_commits || 0);
        setStatValue("open_prs", overview.open_prs || 0);
        setStatValue("merged_prs", overview.merged_prs || 0);
        setStatValue("total_prs", overview.total_prs || 0);
        setStatValue("open_issues", overview.open_issues || 0);
        setStatValue("contributors_count", overview.contributors_count || 0);
        setStatValue(
            "lines_changed",
            "+" + (overview.total_additions || 0) + " / −" + (overview.total_deletions || 0)
        );
    }

    function updateAiStats(ai) {
        if (!ai) return;
        if (ai.error_counts) {
            setStatValue("ai_errors_count", ai.error_counts.ai_errors || 0);
            setStatValue("ai_fixed_count", ai.error_counts.ai_fixed_count || 0);
        }
        if (ai.static_summary) {
            setStatValue("static_critical", ai.static_summary.CRITICAL || 0);
            setStatValue("static_high", ai.static_summary.HIGH || 0);
            setStatValue("static_medium", ai.static_summary.MEDIUM || 0);
            setStatValue("static_low", ai.static_summary.LOW || 0);
        }
    }

    function updateCharts(payload) {
        var charts = window.GitPulseCharts;
        if (!charts) return;
        if (payload.languages) {
            charts.update("languages", {
                labels: Object.keys(payload.languages),
                values: Object.keys(payload.languages).map(function (k) { return payload.languages[k]; }),
            });
        }
        if (payload.overview) {
            charts.update("status", {
                active: payload.overview.active_members || 0,
                inactive: payload.overview.inactive_members || 0,
            });
        }
        if (payload.members) {
            charts.update("commits", {
                labels: payload.members.map(function (m) { return m.username; }),
                values: payload.members.map(function (m) { return m.commits || 0; }),
            });
            charts.update("score", {
                labels: payload.members.map(function (m) { return m.username; }),
                values: payload.members.map(function (m) { return m.activity_score || 0; }),
            });
        }
    }

    function updateActivityFeed(events) {
        var feed = document.getElementById("activityFeed");
        var count = document.getElementById("activityCount");
        if (!feed) return;
        events = events || [];
        if (count) count.textContent = String(events.length);
        if (events.length === 0) {
            feed.innerHTML = '<p class="muted p-3 mb-0">No activity in the selected period.</p>';
            return;
        }
        feed.innerHTML = events.map(function (ev) {
            var iconClass = ev.type === "push" ? "ic-green"
                : (ev.type === "pull_request" ? "ic-purple" : "ic-orange");
            var icon = ev.type === "push" ? "⌥"
                : (ev.type === "pull_request" ? "⇄" : "◎");
            var link = ev.url
                ? '<a class="ext-link" href="' + esc(ev.url) + '" target="_blank" rel="noopener" title="View on GitHub">↗</a>'
                : "";
            return '<div class="feed-item" data-category="' + esc(ev.category || ev.type) +
                '" data-actor="' + esc(ev.actor || "") + '" data-search="' +
                esc((ev.title || "") + " " + (ev.action || "")).toLowerCase() + '">' +
                '<div class="feed-icon ' + iconClass + '">' + icon + "</div>" +
                '<div class="feed-body">' +
                '<div class="feed-title"><strong>@' + esc(ev.author) + "</strong>" +
                '<span class="muted"> ' + esc(ev.action || "") + "</span> " + link + "</div>" +
                '<div class="feed-meta">' + esc(ev.title || "") +
                (ev.sha ? " · <code>" + esc(ev.sha) + "</code>" : "") +
                '<span class="muted float-end">' + esc(ev.relative || (ev.date || "").slice(0, 10)) + "</span>" +
                "</div></div></div>";
        }).join("");
        if (window.applyActivityFilters) window.applyActivityFilters();
    }

    function updateCommitTable(pushes) {
        var body = document.getElementById("commitsBody");
        var count = document.getElementById("commitCount");
        if (!body) return;
        pushes = pushes || [];
        if (count) count.textContent = String(pushes.length);
        if (pushes.length === 0) {
            body.innerHTML = '<tr><td colspan="6" class="muted text-center py-4">No commits in the selected period.</td></tr>';
            return;
        }
        body.innerHTML = pushes.map(function (p) {
            var files = p.files || [];
            var stats = p.stats || {};
            return '<tr class="hover-row commit-row" data-commit-sha="' + esc(p.full_sha || p.sha) +
                '" data-author="' + esc(p.author || "") + '" data-search="' +
                esc((p.message || "") + " " + (p.sha || "")).toLowerCase() + '" title="Click to view commit details">' +
                "<td><strong>@" + esc(p.author) + "</strong></td>" +
                '<td class="find-desc">' + esc(p.message || "") + "</td>" +
                '<td class="text-center">' + files.length + "</td>" +
                '<td class="text-center"><span class="text-success">+' + (stats.additions || 0) +
                '</span> <span class="text-danger">−' + (stats.deletions || 0) + "</span></td>" +
                '<td class="muted">' + esc((p.date || "").slice(0, 16).replace("T", " ")) + "</td>" +
                "<td><code>" + esc(p.sha) + "</code></td></tr>";
        }).join("");
    }

    function refreshDashboard() {
        var btn = document.getElementById("refreshBtn");
        if (btn) {
            btn.disabled = true;
            btn.textContent = "Refreshing…";
        }
        fetch("/api/dashboard/refresh", { method: "POST" })
            .then(function (resp) {
                return resp.json().then(function (payload) {
                    return { ok: resp.ok, payload: payload };
                });
            })
            .then(function (res) {
                var payload = res.payload || {};
                if (!res.ok) {
                    throw new Error(payload.error || "Refresh failed");
                }
                updateOverview(payload.overview);
                updateAiStats({ static_summary: payload.ai && payload.ai.static_summary });
                updateCharts(payload);
                updateActivityFeed(payload.activity_feed);
                updateCommitTable(payload.pushes);
                if (window.refreshPullRequests) window.refreshPullRequests();
                setStatValue("ai_errors_count", (payload.ai && payload.ai.error_counts && payload.ai.error_counts.ai_errors) || 0);
                setStatValue("ai_fixed_count", (payload.ai && payload.ai.error_counts && payload.ai.error_counts.ai_fixed_count) || 0);
                if (lastUpdatedBadge && payload.last_updated) {
                    lastUpdatedBadge.textContent = "Last updated " + String(payload.last_updated).slice(0, 16).replace("T", " ") + " UTC";
                    lastUpdatedBadge.title = "When GitHub data was last fetched";
                }
                if (btn) {
                    btn.disabled = false;
                    btn.textContent = "✓ Refreshed";
                    setTimeout(function () { btn.textContent = "↻ Refresh"; }, 1500);
                }
            })
            .catch(function (err) {
                if (btn) {
                    btn.disabled = false;
                    btn.textContent = "↻ Refresh";
                }
                console.warn("Dashboard refresh failed:", err.message);
            });
    }

    var refreshBtn = document.getElementById("refreshBtn");
    if (refreshBtn) {
        refreshBtn.addEventListener("click", refreshDashboard);
    }

    // ---------- Auto-refresh polling (30s default) ----------
    var pollInterval = parseInt(window.GITPULSE_POLL_INTERVAL, 10) || 30;
    function startAutoRefresh() {
        var timer = window.setInterval(refreshDashboard, pollInterval * 1000);
        // Pause polling while the tab is hidden to save bandwidth.
        document.addEventListener("visibilitychange", function () {
            if (document.hidden) {
                window.clearInterval(timer);
            } else {
                timer = window.setInterval(refreshDashboard, pollInterval * 1000);
            }
        });
    }
    if (window.location.pathname.indexOf("/dashboard") !== -1 || document.getElementById("refreshBtn")) {
        startAutoRefresh();
    }

    // ---------- Activity filters (client-side) ----------
    var feed = document.getElementById("activityFeed");
    var categorySel = document.getElementById("activityCategory");
    var authorSel = document.getElementById("activityAuthor");
    var searchInput = document.getElementById("activitySearch");
    var activityCount = document.getElementById("activityCount");
    var applyBtn = document.getElementById("activityApply");

    function applyActivityFilters() {
        if (!feed) return;
        var cat = (categorySel && categorySel.value) || "";
        var author = (authorSel && authorSel.value) || "";
        var query = (searchInput && searchInput.value.trim().toLowerCase()) || "";
        var visible = 0;
        feed.querySelectorAll(".feed-item").forEach(function (item) {
            var show = true;
            if (cat && item.dataset.category !== cat) show = false;
            if (show && author && item.dataset.actor !== author) show = false;
            if (show && query && item.dataset.search.indexOf(query) === -1) show = false;
            item.style.display = show ? "" : "none";
            if (show) visible += 1;
        });
        if (activityCount) activityCount.textContent = String(visible);
    }

    if (applyBtn) applyBtn.addEventListener("click", applyActivityFilters);
    if (categorySel) categorySel.addEventListener("change", applyActivityFilters);
    if (authorSel) authorSel.addEventListener("change", applyActivityFilters);
    if (searchInput) {
        searchInput.addEventListener("input", function () {
            window.clearTimeout(searchInput._timer);
            searchInput._timer = window.setTimeout(applyActivityFilters, 250);
        });
    }
    window.applyActivityFilters = applyActivityFilters;

    // ---------- Commits: filter + detail modal ----------
    var commitSearch = document.getElementById("commitSearch");
    var commitAuthor = document.getElementById("commitAuthor");
    var commitsBody = document.getElementById("commitsBody");
    var commitCount = document.getElementById("commitCount");

    function applyCommitFilters() {
        if (!commitsBody) return;
        var query = (commitSearch && commitSearch.value.trim().toLowerCase()) || "";
        var author = (commitAuthor && commitAuthor.value) || "";
        var visible = 0;
        commitsBody.querySelectorAll(".commit-row").forEach(function (row) {
            var show = true;
            if (author && row.dataset.author !== author) show = false;
            if (show && query && row.dataset.search.indexOf(query) === -1) show = false;
            row.style.display = show ? "" : "none";
            if (show) visible += 1;
        });
        if (commitCount) commitCount.textContent = String(visible);
    }

    if (commitSearch) commitSearch.addEventListener("input", applyCommitFilters);
    if (commitAuthor) commitAuthor.addEventListener("change", applyCommitFilters);

    if (commitsBody) {
        commitsBody.addEventListener("click", function (event) {
            if (event.target.closest("a, button")) return;
            var row = event.target.closest(".commit-row");
            if (!row) return;
            fetchCommitDetail(row.dataset.commitSha);
        });
    }

    function fetchCommitDetail(sha) {
        openModal("Commit " + sha, "https://github.com/" + window.GITPULSE_REPO + "/commit/" + sha);
        modalBody.innerHTML = '<p class="muted">Loading commit details…</p>';
        fetch("/api/commit/" + encodeURIComponent(sha))
            .then(function (resp) { return resp.json(); })
            .then(function (data) {
                if (data.error) {
                    modalBody.innerHTML = renderError(data.error);
                    return;
                }
                modalTitle.textContent = esc(data.short_sha || sha) + " · " + esc(data.message);
                var rows = (data.files || []).map(function (f) {
                    return "<tr><td class='file-cell'><code>" + esc(f.filename) + "</code></td>" +
                        "<td class='text-center'><span class='badge badge-glass'>" + esc(f.status) + "</span></td>" +
                        "<td class='text-center text-success'>+" + esc(f.additions) + "</td>" +
                        "<td class='text-center text-danger'>-" + esc(f.deletions) + "</td></tr>";
                }).join("");
                modalBody.innerHTML =
                    "<div class='gp-detail-meta'>" +
                    "<span><strong>Author</strong> @" + esc(data.author_login || data.author || "unknown") + "</span>" +
                    "<span><strong>Date</strong> " + esc((data.date || "").replace("T", " ").slice(0, 19)) + "</span>" +
                    "<span><strong>Changes</strong> +" + esc(data.stats.additions) + " / -" + esc(data.stats.deletions) + "</span>" +
                    "</div>" +
                    "<p class='muted'>" + esc(data.full_message || data.message || "") + "</p>" +
                    "<h3 class='h6 mt-3'>Files changed (" + esc(data.files.length) + ")</h3>" +
                    "<div class='table-responsive'><table class='table table-pulse table-sm align-middle'>" +
                    "<thead><tr><th>File</th><th class='text-center'>Status</th><th class='text-center'>+</th><th class='text-center'>-</th></tr></thead>" +
                    "<tbody>" + rows + "</tbody></table></div>";
            })
            .catch(function () {
                modalBody.innerHTML = renderError("Network error while loading commit details.");
            });
    }

    // ---------- Pull requests: filter + detail modal ----------
    var prSearch = document.getElementById("prSearch");
    var prState = document.getElementById("prState");
    var prAuthor = document.getElementById("prAuthor");
    var prsBody = document.getElementById("prsBody");
    var prCount = document.getElementById("prCount");

    function applyPrFilters() {
        if (!prsBody) return;
        var query = (prSearch && prSearch.value.trim().toLowerCase()) || "";
        var state = (prState && prState.value) || "";
        var author = (prAuthor && prAuthor.value) || "";
        var visible = 0;
        prsBody.querySelectorAll(".pr-row").forEach(function (row) {
            var show = true;
            if (state && row.dataset.state !== state) show = false;
            if (author && row.dataset.author !== author) show = false;
            if (show && query && row.dataset.search.indexOf(query) === -1) show = false;
            row.style.display = show ? "" : "none";
            if (show) visible += 1;
        });
        if (prCount) prCount.textContent = String(visible);
    }

    if (prSearch) prSearch.addEventListener("input", applyPrFilters);
    if (prState) prState.addEventListener("change", applyPrFilters);
    if (prAuthor) prAuthor.addEventListener("change", applyPrFilters);

    // Exposed so pull_requests.js can re-apply client filters after it
    // re-renders the PR table from the live GitHub data.
    window.applyPrFilters = applyPrFilters;

    if (prsBody) {
        prsBody.addEventListener("click", function (event) {
            if (event.target.closest("a, button")) return;
            var row = event.target.closest(".pr-row");
            if (!row) return;
            fetchPrDetail(row.dataset.pr);
        });
    }

    function fetchPrDetail(number) {
        openModal("Pull Request #" + number, "https://github.com/" + window.GITPULSE_REPO + "/pull/" + number);
        modalBody.innerHTML = '<p class="muted">Loading pull request details…</p>';
        fetch("/api/pull-request/" + encodeURIComponent(number))
            .then(function (resp) { return resp.json(); })
            .then(function (data) {
                if (data.error) {
                    modalBody.innerHTML = renderError(data.error);
                    return;
                }
                var stateBadge = data.merged
                    ? "<span class='badge badge-status st-merged'>Merged</span>"
                    : data.state === "open"
                        ? "<span class='badge badge-status st-open'>Open</span>"
                        : "<span class='badge badge-status st-closed'>Closed</span>";
                modalTitle.textContent = "PR #" + number + " · " + data.title;
                var commits = (data.commits || []).map(function (c) {
                    return "<li><code>" + esc(c.sha) + "</code> " + esc(c.message) + "</li>";
                }).join("");
                var reviews = (data.reviews || []).map(function (r) {
                    return "<li><strong>@" + esc(r.author) + "</strong> (" + esc(r.state) + ") · " + esc((r.submitted_at || "").slice(0, 10)) + "</li>";
                }).join("");
                modalBody.innerHTML =
                    "<div class='gp-detail-meta'>" + stateBadge +
                    "<span><strong>Author</strong> @" + esc(data.author) + "</span>" +
                    "<span><strong>Created</strong> " + esc((data.created_at || "").slice(0, 10)) + "</span>" +
                    "<span><strong>Commits</strong> " + esc(data.commits_count) + "</span>" +
                    "<span><strong>Changes</strong> +" + esc(data.additions) + " / -" + esc(data.deletions) + " (" + esc(data.changed_files) + " files)</span>" +
                    "</div>" +
                    (data.base ? "<p class='muted'>" + esc(data.base) + " ← " + esc(data.head) + "</p>" : "") +
                    (data.body ? "<p class='find-desc'>" + esc(data.body) + "</p>" : "") +
                    (commits ? "<h3 class='h6 mt-3'>Commits</h3><ul class='gp-list'>" + commits + "</ul>" : "") +
                    (reviews ? "<h3 class='h6 mt-3'>Reviews</h3><ul class='gp-list'>" + reviews + "</ul>" : "") +
                    (data.labels && data.labels.length
                        ? "<div class='mt-3'>" + data.labels.map(function (l) { return "<span class='badge badge-glass'>" + esc(l) + "</span> "; }).join("") + "</div>"
                        : "");
            })
            .catch(function () {
                modalBody.innerHTML = renderError("Network error while loading pull request details.");
            });
    }

    // ---------- Issues: filter + detail modal ----------
    var issueSearch = document.getElementById("issueSearch");
    var issueState = document.getElementById("issueState");
    var issueAuthor = document.getElementById("issueAuthor");
    var issuesBody = document.getElementById("issuesBody");
    var issueCount = document.getElementById("issueCount");

    function applyIssueFilters() {
        if (!issuesBody) return;
        var query = (issueSearch && issueSearch.value.trim().toLowerCase()) || "";
        var state = (issueState && issueState.value) || "";
        var author = (issueAuthor && issueAuthor.value) || "";
        var visible = 0;
        issuesBody.querySelectorAll(".issue-row").forEach(function (row) {
            var show = true;
            if (state && row.dataset.state !== state) show = false;
            if (author && row.dataset.author !== author) show = false;
            if (show && query && row.dataset.search.indexOf(query) === -1) show = false;
            row.style.display = show ? "" : "none";
            if (show) visible += 1;
        });
        if (issueCount) issueCount.textContent = String(visible);
    }

    if (issueSearch) issueSearch.addEventListener("input", applyIssueFilters);
    if (issueState) issueState.addEventListener("change", applyIssueFilters);
    if (issueAuthor) issueAuthor.addEventListener("change", applyIssueFilters);

    if (issuesBody) {
        issuesBody.addEventListener("click", function (event) {
            if (event.target.closest("a, button")) return;
            var row = event.target.closest(".issue-row");
            if (!row) return;
            fetchIssueDetail(row.dataset.issue);
        });
    }

    function fetchIssueDetail(number) {
        openModal("Issue #" + number, "https://github.com/" + window.GITPULSE_REPO + "/issues/" + number);
        modalBody.innerHTML = '<p class="muted">Loading issue details…</p>';
        fetch("/api/issue/" + encodeURIComponent(number))
            .then(function (resp) { return resp.json(); })
            .then(function (data) {
                if (data.error) {
                    modalBody.innerHTML = renderError(data.error);
                    return;
                }
                var stateBadge = data.state === "open"
                    ? "<span class='badge badge-status st-open'>Open</span>"
                    : "<span class='badge badge-status st-closed'>Closed</span>";
                modalTitle.textContent = "Issue #" + number + " · " + data.title;
                var events = (data.timeline_events || []).map(function (e) {
                    return "<li><strong>" + esc(e.event) + "</strong> by @" + esc(e.actor) + " · " + esc((e.date || "").slice(0, 10)) + "</li>";
                }).join("");
                modalBody.innerHTML =
                    "<div class='gp-detail-meta'>" + stateBadge +
                    "<span><strong>Author</strong> @" + esc(data.author) + "</span>" +
                    "<span><strong>Created</strong> " + esc((data.created_at || "").slice(0, 10)) + "</span>" +
                    "<span><strong>Comments</strong> " + esc(data.comments_count) + "</span>" +
                    "</div>" +
                    (data.body ? "<p class='find-desc'>" + esc(data.body) + "</p>" : "") +
                    (data.labels && data.labels.length
                        ? "<div class='mt-3'>" + data.labels.map(function (l) { return "<span class='badge badge-glass'>" + esc(l) + "</span> "; }).join("") + "</div>"
                        : "") +
                    (events ? "<h3 class='h6 mt-3'>Timeline</h3><ul class='gp-list'>" + events + "</ul>" : "");
            })
            .catch(function () {
                modalBody.innerHTML = renderError("Network error while loading issue details.");
            });
    }

    // ---------- Repository health analysis ----------
    var analyzeRepoBtn = document.getElementById("analyzeRepoBtn");
    var repoAnalysisStatus = document.getElementById("repoAnalysisStatus");
    var repoAnalysisResult = document.getElementById("repoAnalysisResult");

    if (analyzeRepoBtn) {
        analyzeRepoBtn.addEventListener("click", function () {
            analyzeRepoBtn.disabled = true;
            analyzeRepoBtn.textContent = "Analyzing…";
            if (repoAnalysisStatus) repoAnalysisStatus.textContent = "Running rule-based analysis…";
            fetch("/api/ai/analyze-repo", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: "{}",
            })
                .then(function (resp) { return resp.json(); })
                .then(function (data) {
                    analyzeRepoBtn.disabled = false;
                    analyzeRepoBtn.textContent = "Analyze Repository";
                    if (data.error) {
                        if (repoAnalysisStatus) repoAnalysisStatus.textContent = "";
                        repoAnalysisResult.innerHTML = renderError(data.error);
                        return;
                    }
                    if (repoAnalysisStatus) repoAnalysisStatus.textContent = "";
                    var score = Number(data.health_score) || 0;
                    var scoreColor = score >= 70 ? "st-open" : (score >= 45 ? "st-merged" : "st-closed");
                    var findings = (data.findings || []).map(function (f) {
                        return "<div class='suggestion-card prio-" + esc(f.severity) + " mb-2'>" +
                            "<div class='sugg-top'>" +
                            "<span class='badge badge-prio'>" + esc(f.severity.toUpperCase()) + "</span>" +
                            "<span class='muted small'>" + esc(f.category) + (f.affected ? " · " + esc(f.affected) : "") + "</span>" +
                            "</div>" +
                            "<h3 class='h6 mb-1'>" + esc(f.title) + "</h3>" +
                            "<p class='sugg-detail muted mb-0'>" + esc(f.explanation) + "</p>" +
                            "<p class='sugg-detail muted mb-0'><strong>Recommendation:</strong> " + esc(f.recommendation) + "</p>" +
                            "</div>";
                    }).join("");
                    var narrative = data.ai_narrative
                        ? "<p class='muted'>" + esc(data.ai_narrative.narrative) + "</p>" +
                          (data.ai_narrative.priorities || []).map(function (p) { return "<li>" + esc(p) + "</li>"; }).join("")
                        : "";
                    var codeStats = data.code_stats
                        ? "<p class='muted small mb-1'><strong>Static code scan:</strong> " +
                          esc(data.code_stats.files_analyzed) + " file(s) analyzed · " +
                          esc(data.code_stats.findings.TOTAL) + " finding(s) (" +
                          esc(data.code_stats.findings.CRITICAL) + " critical, " +
                          esc(data.code_stats.findings.HIGH) + " high)</p>"
                        : "";
                    repoAnalysisResult.innerHTML =
                        "<div class='glass card-pulse p-3 mb-3'>" +
                        "<div class='d-flex align-items-center gap-3 flex-wrap'>" +
                        "<div class='repo-health-score badge-status " + scoreColor + "'>" + score + "</div>" +
                        "<div><strong>Health Score</strong>" +
                        (data.health_label ? " · <span class='badge badge-glass'>" + esc(data.health_label) + "</span>" : "") +
                        "<div class='muted'>" + esc(data.summary || "") + "</div></div>" +
                        "</div>" +
                        (narrative ? "<div class='mt-2'><ul class='gp-list'>" + narrative + "</ul></div>" : "") +
                        (codeStats ? "<div class='mt-2'>" + codeStats + "</div>" : "") +
                        "</div>" +
                        "<h3 class='h6'>Findings (" + esc((data.findings || []).length) + ")</h3>" +
                        (findings || '<p class="muted mb-0">No findings - repository looks healthy.</p>');
                    var lastAnalyzed = document.getElementById("lastAnalyzedBadge");
                    if (lastAnalyzed && data.last_analyzed) {
                        lastAnalyzed.textContent = "Last analysis " + String(data.last_analyzed).slice(0, 16).replace("T", " ") + " UTC";
                    }
                    // Update the static error stat cards on this tab.
                    if (data.code_stats) {
                        setStatValue("static_critical", data.code_stats.findings.CRITICAL || 0);
                        setStatValue("static_high", data.code_stats.findings.HIGH || 0);
                        setStatValue("static_medium", data.code_stats.findings.MEDIUM || 0);
                        setStatValue("static_low", data.code_stats.findings.LOW || 0);
                    }
                })
                .catch(function () {
                    analyzeRepoBtn.disabled = false;
                    analyzeRepoBtn.textContent = "Analyze Repository";
                    if (repoAnalysisStatus) repoAnalysisStatus.textContent = "";
                    repoAnalysisResult.innerHTML = renderError("Network error while analyzing repository.");
                });
        });
    }

    // ---------- Commit analysis ----------
    var analyzeCommitsBtn = document.getElementById("analyzeCommitsBtn");
    var commitAnalysisStatus = document.getElementById("commitAnalysisStatus");
    var commitAnalysisResult = document.getElementById("commitAnalysisResult");

    function renderCommitAnalyses(commitAnalyses) {
        if (!commitAnalysisResult) return;
        commitAnalyses = commitAnalyses || [];
        if (commitAnalyses.length === 0) {
            commitAnalysisResult.innerHTML = '<p class="muted mb-0">No commits to analyze.</p>';
            return;
        }
        var categoryBadge = {
            "Normal": "badge-glass",
            "Risky": "st-open",
            "Bug-prone": "st-closed",
            "Large": "st-merged",
            "Suspicious": "st-closed",
            "Needs review": "st-merged",
        };
        commitAnalysisResult.innerHTML = commitAnalyses.map(function (c) {
            var cls = categoryBadge[c.category] || "badge-glass";
            var reasons = (c.reasons || []).map(function (r) {
                return "<li>" + esc(r) + "</li>";
            }).join("");
            return "<div class='suggestion-card prio-" + (c.flagged ? "high" : "low") + " mb-2'>" +
                "<div class='sugg-top'>" +
                "<span class='badge " + cls + "'>" + esc(c.category) + "</span>" +
                "<span class='muted small'>" + esc(c.sha || "").slice(0, 7) + " · @" + esc(c.author) +
                (c.date ? " · " + esc((c.date || "").slice(0, 10)) : "") +
                " · +" + esc(c.additions) + "/-" + esc(c.deletions) + " · " + esc(c.files_changed) + " file(s)</span>" +
                "</div>" +
                "<h3 class='h6 mb-1'>" + esc(c.message) + "</h3>" +
                (reasons ? "<ul class='gp-list muted small mb-0'>" + reasons + "</ul>" : "") +
                "</div>";
        }).join("");
    }

    if (analyzeCommitsBtn) {
        analyzeCommitsBtn.addEventListener("click", function () {
            analyzeCommitsBtn.disabled = true;
            analyzeCommitsBtn.textContent = "Analyzing…";
            if (commitAnalysisStatus) commitAnalysisStatus.textContent = "Classifying commits…";
            fetch("/api/ai/analyze-repo", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: "{}",
            })
                .then(function (resp) { return resp.json(); })
                .then(function (data) {
                    analyzeCommitsBtn.disabled = false;
                    analyzeCommitsBtn.textContent = "Analyze Commits";
                    if (commitAnalysisStatus) commitAnalysisStatus.textContent = "";
                    if (data.error) {
                        commitAnalysisResult.innerHTML = renderError(data.error);
                        return;
                    }
                    renderCommitAnalyses(data.commit_analyses);
                })
                .catch(function () {
                    analyzeCommitsBtn.disabled = false;
                    analyzeCommitsBtn.textContent = "Analyze Commits";
                    if (commitAnalysisStatus) commitAnalysisStatus.textContent = "";
                    commitAnalysisResult.innerHTML = renderError("Network error while analyzing commits.");
                });
        });
    }

    // ---------- Member analysis ----------
    var analyzeMembersBtn = document.getElementById("analyzeMembersBtn");
    var memberAnalysisStatus = document.getElementById("memberAnalysisStatus");
    var memberAnalysisResult = document.getElementById("memberAnalysisResult");

    if (analyzeMembersBtn) {
        analyzeMembersBtn.addEventListener("click", function () {
            analyzeMembersBtn.disabled = true;
            analyzeMembersBtn.textContent = "Analyzing…";
            if (memberAnalysisStatus) memberAnalysisStatus.textContent = "Computing contribution share…";
            fetch("/api/ai/analyze-repo", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: "{}",
            })
                .then(function (resp) { return resp.json(); })
                .then(function (data) {
                    analyzeMembersBtn.disabled = false;
                    analyzeMembersBtn.textContent = "Analyze Members";
                    if (memberAnalysisStatus) memberAnalysisStatus.textContent = "";
                    if (data.error) {
                        memberAnalysisResult.innerHTML = renderError(data.error);
                        return;
                    }
                    var members = (data.member_analyses || []).map(function (m) {
                        var width = Math.max(1, Math.min(100, Number(m.contribution_pct) || 0));
                        return "<div class='mb-2'>" +
                            "<div class='d-flex justify-content-between small'>" +
                            "<strong>@" + esc(m.username) + "</strong>" +
                            "<span class='muted'>" + esc(m.activity_level) + " · " + esc(m.contribution_pct) + "% · " + esc(m.commits) + " commit(s)</span>" +
                            "</div>" +
                            "<div class='score-bar'><div class='score-fill' style='width:" + width + "%'></div></div>" +
                            "</div>";
                    }).join("");
                    memberAnalysisResult.innerHTML = members ||
                        '<p class="muted mb-0">No members to analyze.</p>';
                })
                .catch(function () {
                    analyzeMembersBtn.disabled = false;
                    analyzeMembersBtn.textContent = "Analyze Members";
                    if (memberAnalysisStatus) memberAnalysisStatus.textContent = "";
                    memberAnalysisResult.innerHTML = renderError("Network error while analyzing members.");
                });
        });
    }

    // ---------- Error analysis (static + scan + stored) ----------
    var analyzeErrorsBtn = document.getElementById("analyzeErrorsBtn");
    var errorAnalysisStatus = document.getElementById("errorAnalysisStatus");
    var errorAnalysisResult = document.getElementById("errorAnalysisResult");

    function renderErrors(items) {
        if (!errorAnalysisResult) return;
        items = items || [];
        if (items.length === 0) {
            errorAnalysisResult.innerHTML = '<p class="muted mb-0">No errors found - code looks clean.</p>';
            return;
        }
        errorAnalysisResult.innerHTML = items.map(function (item) {
            var f = item.finding || item.result || {};
            var detail = item.detail || {};
            var source = detail.kind || f.engine || "analysis";
            var target = detail.target || f.file || "";
            var line = f.line || f.line_number || 0;
            return "<div class='suggestion-card prio-" + esc(f.severity || "low") + " mb-2'>" +
                "<div class='sugg-top'>" +
                "<span class='badge badge-prio'>" + esc((f.severity || "low").toUpperCase()) + "</span>" +
                "<span class='muted small'>" + esc(source) + (target ? " · " + esc(target) : "") +
                (line ? " · line " + esc(line) : "") +
                (detail.created_at ? " · " + esc(String(detail.created_at).slice(0, 16).replace("T", " ")) : "") +
                "</span></div>" +
                "<h3 class='h6 mb-1'>" + esc(f.problem || f.description || "") + "</h3>" +
                (f.explanation ? "<p class='sugg-detail muted mb-0'>" + esc(f.explanation) + "</p>" : "") +
                (f.suggested_fix ? "<p class='sugg-detail muted mb-0'><strong>Fix:</strong> " + esc(f.suggested_fix) + "</p>" : "") +
                (f.error_type || f.rule_id ? "<small class='muted'>" + esc(f.error_type || f.rule_id) + "</small>" : "") +
                "</div>";
        }).join("");
    }

    if (analyzeErrorsBtn) {
        analyzeErrorsBtn.addEventListener("click", function () {
            analyzeErrorsBtn.disabled = true;
            analyzeErrorsBtn.textContent = "Analyzing…";
            if (errorAnalysisStatus) errorAnalysisStatus.textContent = "Merging static, scan and stored findings…";
            fetch("/api/ai/errors")
                .then(function (resp) { return resp.json(); })
                .then(function (data) {
                    analyzeErrorsBtn.disabled = false;
                    analyzeErrorsBtn.textContent = "Run Error Analysis";
                    if (errorAnalysisStatus) errorAnalysisStatus.textContent = "";
                    if (data.error) {
                        errorAnalysisResult.innerHTML = renderError(data.error);
                        return;
                    }
                    renderErrors(data.errors);
                    if (data.total !== undefined) {
                        setStatValue("ai_errors_count", data.total);
                    }
                })
                .catch(function () {
                    analyzeErrorsBtn.disabled = false;
                    analyzeErrorsBtn.textContent = "Run Error Analysis";
                    if (errorAnalysisStatus) errorAnalysisStatus.textContent = "";
                    errorAnalysisResult.innerHTML = renderError("Network error while running error analysis.");
                });
        });
    }

    // ---------- AI Fix preview (read-only diff) ----------
    var aiPreviewForm = document.getElementById("aiPreviewForm");
    var aiPreviewResult = document.getElementById("aiPreviewResult");

    if (aiPreviewForm) {
        aiPreviewForm.addEventListener("submit", function (event) {
            event.preventDefault();
            var pathInput = aiPreviewForm.querySelector('[name="path"]');
            var path = (pathInput && pathInput.value.trim()) || "";
            if (!path) return;
            var btn = document.getElementById("aiPreviewBtn");
            if (btn) {
                btn.disabled = true;
                btn.textContent = "Generating…";
            }
            aiPreviewResult.innerHTML = '<p class="muted mb-0">Generating fix preview (read-only)…</p>';
            fetch("/api/ai/fix/preview", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ path: path }),
            })
                .then(function (resp) { return resp.json(); })
                .then(function (data) {
                    if (btn) {
                        btn.disabled = false;
                        btn.textContent = "Generate Preview";
                    }
                    if (data.error) {
                        aiPreviewResult.innerHTML = renderError(data.error);
                        return;
                    }
                    var analysis = data.analysis || {};
                    var diffHtml = data.diff
                        ? "<h3 class='h6 mt-2'>Proposed diff</h3>" +
                          "<pre class='gp-diff'>" + esc(data.diff) + "</pre>"
                        : "<p class='muted mb-0'>The analyzer did not propose code changes for this file.</p>";
                    aiPreviewResult.innerHTML =
                        "<div class='suggestion-card prio-" + esc(analysis.severity || "low") + " mb-2'>" +
                        "<div class='sugg-top'>" +
                        "<span class='badge badge-prio'>" + esc((analysis.severity || "low").toUpperCase()) + "</span>" +
                        "<span class='muted small'>" + esc(data.path) + " · engine " + esc(analysis.engine || "") + "</span>" +
                        "</div>" +
                        "<h3 class='h6 mb-1'>" + esc(analysis.problem || "No issue detected") + "</h3>" +
                        (analysis.explanation ? "<p class='sugg-detail muted mb-0'>" + esc(analysis.explanation) + "</p>" : "") +
                        (analysis.suggested_fix ? "<p class='sugg-detail muted mb-0'><strong>Fix:</strong> " + esc(analysis.suggested_fix) + "</p>" : "") +
                        "</div>" + diffHtml +
                        "<p class='muted small mt-2 mb-0'>" + esc(data.note || "") + "</p>";
                })
                .catch(function () {
                    if (btn) {
                        btn.disabled = false;
                        btn.textContent = "Generate Preview";
                    }
                    aiPreviewResult.innerHTML = renderError("Network error while generating the fix preview.");
                });
        });
    }

    // ---------- AI Fix rollback ----------
    document.addEventListener("click", function (event) {
        var btn = event.target.closest(".rollback-btn");
        if (!btn) return;
        var branch = btn.dataset.branch;
        if (!branch) return;
        var message = "Delete branch " + branch + "?\n\n" +
            "This rolls back the AI fix: the branch is deleted and its pull request closes automatically. " +
            "This only works before the PR is merged - merged changes must be reverted normally.";
        if (!window.confirm(message)) return;
        btn.disabled = true;
        btn.textContent = "Deleting…";
        fetch("/api/ai/fix/rollback", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ branch: branch }),
        })
            .then(function (resp) { return resp.json(); })
            .then(function (data) {
                if (data.error) {
                    btn.disabled = false;
                    btn.textContent = "⟲ Rollback";
                    alert("Rollback failed: " + data.error);
                    return;
                }
                btn.textContent = "✓ Deleted";
                btn.classList.remove("btn-outline-danger");
                btn.classList.add("btn-outline-success");
                var row = btn.closest("tr");
                if (row) {
                    var statusCell = row.querySelector(".badge-status");
                    if (statusCell) {
                        statusCell.textContent = "rolled back";
                        statusCell.className = "badge badge-status st-closed";
                    }
                }
            })
            .catch(function () {
                btn.disabled = false;
                btn.textContent = "⟲ Rollback";
                alert("Network error while rolling back the branch.");
            });
    });

    // ---------- AI Fix form confirmation ----------
    var aiFixForm = document.getElementById("aiFixForm");
    if (aiFixForm) {
        aiFixForm.addEventListener("submit", function (event) {
            var path = aiFixForm.querySelector('[name="path"]');
            var label = aiFixForm.querySelector('[name="issue_label"]');
            var message = "This will commit an AI-generated fix to a new ai-fix/ branch and open a pull request on GitHub. It will NOT merge or touch the default branch.\n\nContinue?";
            if (!window.confirm(message)) {
                event.preventDefault();
                return;
            }
            var btn = document.getElementById("aiFixBtn");
            if (btn) {
                btn.disabled = true;
                btn.textContent = "Creating…";
            }
            void path; void label;
        });
    }
})();
