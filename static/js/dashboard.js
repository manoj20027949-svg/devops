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

    // ---------- Refresh (AJAX) ----------
    var refreshBtn = document.getElementById("refreshBtn");
    if (refreshBtn) {
        refreshBtn.addEventListener("click", function () {
            refreshBtn.disabled = true;
            refreshBtn.textContent = "Refreshing…";
            fetch("/api/refresh", { method: "POST" })
                .then(function (resp) {
                    return resp.json().then(function (payload) {
                        return { ok: resp.ok, payload: payload };
                    });
                })
                .then(function (res) {
                    if (!res.ok) {
                        throw new Error((res.payload && res.payload.error) || "Refresh failed");
                    }
                    refreshBtn.textContent = "✓ Refreshed";
                    setTimeout(function () {
                        window.location.reload();
                    }, 400);
                })
                .catch(function (err) {
                    refreshBtn.disabled = false;
                    refreshBtn.textContent = "↻ Refresh";
                    alert("Could not refresh: " + err.message);
                });
        });
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
                    repoAnalysisResult.innerHTML =
                        "<div class='glass card-pulse p-3 mb-3'>" +
                        "<div class='d-flex align-items-center gap-3 flex-wrap'>" +
                        "<div class='repo-health-score badge-status " + scoreColor + "'>" + score + "</div>" +
                        "<div><strong>Health Score</strong><div class='muted'>" + esc(data.summary || "") + "</div></div>" +
                        "</div>" +
                        (narrative ? "<div class='mt-2'><ul class='gp-list'>" + narrative + "</ul></div>" : "") +
                        "</div>" +
                        "<h3 class='h6'>Findings (" + esc((data.findings || []).length) + ")</h3>" +
                        (findings || '<p class="muted mb-0">No findings - repository looks healthy.</p>');
                })
                .catch(function () {
                    analyzeRepoBtn.disabled = false;
                    analyzeRepoBtn.textContent = "Analyze Repository";
                    if (repoAnalysisStatus) repoAnalysisStatus.textContent = "";
                    repoAnalysisResult.innerHTML = renderError("Network error while analyzing repository.");
                });
        });
    }

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
