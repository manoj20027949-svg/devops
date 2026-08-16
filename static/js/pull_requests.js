/* ============================================================
   GitPulse - live pull requests tab
   Fetches /api/pull-requests (backed by the GitHub REST API),
   renders the PR table, wires the filters and a 60-second
   auto-refresh with a single timer per page load.
   ============================================================ */
(function () {
    "use strict";

    // Single-init guard: never bind a second fetch loop/timer.
    if (window.GitPulsePullRequests) return;
    window.GitPulsePullRequests = {};

    var prsBody = document.getElementById("prsBody");
    if (!prsBody) return;

    var REFRESH_INTERVAL_MS = 60000;
    var timer = null;
    var loading = false;
    var lastFetch = 0;
    var prs = [];

    function esc(value) {
        return String(value == null ? "" : value)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    function readableDate(iso) {
        if (!iso) return "";
        var d = new Date(iso);
        if (isNaN(d.getTime())) return String(iso).slice(0, 10);
        return d.toLocaleDateString(undefined, {
            year: "numeric",
            month: "short",
            day: "numeric",
        });
    }

    function reviewLabel(status) {
        return ({
            approved: "Approved",
            changes_requested: "Changes Requested",
            commented: "Commented",
            pending: "Pending",
            not_reviewed: "Not Reviewed",
        })[status] || "Not Reviewed";
    }

    function setCount(count) {
        var el = document.getElementById("prCount");
        if (el) el.textContent = String(count);
    }

    function setLoading() {
        prsBody.innerHTML =
            '<tr><td colspan="8" class="muted text-center py-4">Loading pull requests…</td></tr>';
    }

    function renderError(msg) {
        prsBody.innerHTML =
            '<tr><td colspan="8"><p class="muted p-3 mb-0">' + esc(msg) + "</p></td></tr>";
    }

    function renderEmpty() {
        prsBody.innerHTML =
            '<tr><td colspan="8" class="muted text-center py-4">No pull requests found.</td></tr>';
    }

    function rowFor(pr) {
        var status = pr.merged ? "merged" : (pr.state === "closed" ? "closed" : "open");
        var statusLabel = pr.merged ? "Merged"
            : (pr.state === "closed" ? "Closed" : (pr.draft ? "Draft" : "Open"));
        var statusClass = pr.merged ? "st-merged" : (pr.state === "closed" ? "st-closed" : "st-open");
        return '<tr class="hover-row pr-row" data-pr="' + esc(pr.number) +
            '" data-author="' + esc(pr.author) +
            '" data-state="' + status +
            '" data-search="' + esc((pr.title + " #" + pr.number).toLowerCase()) + '">' +
            '<td><a href="' + esc(pr.html_url) + '" target="_blank" rel="noopener" title="Open on GitHub">#' +
            esc(pr.number) + "</a></td>" +
            '<td class="find-desc">' + esc(pr.title) + "</td>" +
            '<td><strong>@' + esc(pr.author) + "</strong></td>" +
            '<td><span class="badge badge-status ' + statusClass + '">' + statusLabel + "</span></td>" +
            '<td><span class="badge badge-glass">' + reviewLabel(pr.review_status) + "</span></td>" +
            '<td class="text-center"><span class="text-success">+' + (pr.additions || 0) +
            '</span> <span class="text-danger">−' + (pr.deletions || 0) + "</span>" +
            '<small class="muted d-block">' + (pr.changed_files || 0) + " files</small></td>" +
            '<td class="muted">' + esc(readableDate(pr.updated_at)) + "</td>" +
            '<td class="text-center">' +
            '<button type="button" class="btn btn-pulse btn-sm ai-pr-btn" data-pr="' +
            esc(pr.number) + '">Analyze</button></td></tr>';
    }

    function render() {
        if (prs.length === 0) {
            renderEmpty();
            return;
        }
        prsBody.innerHTML = prs.map(rowFor).join("");
        if (window.applyPrFilters) window.applyPrFilters();
    }

    function rebuildAuthors() {
        var sel = document.getElementById("prAuthor");
        if (!sel) return;
        var current = sel.value;
        var seen = {};
        var options = ['<option value="">All authors</option>'];
        prs.forEach(function (pr) {
            if (pr.author && !seen[pr.author]) {
                seen[pr.author] = true;
                options.push('<option value="' + esc(pr.author) + '">@' + esc(pr.author) + "</option>");
            }
        });
        sel.innerHTML = options.join("");
        if (current) sel.value = current;
    }

    function fetchAndRender(opts) {
        opts = opts || {};
        if (loading) return;
        loading = true;
        if (!opts.silent) setLoading();
        // A cache-busting query param on manual refreshes bypasses the
        // server's 60-second HTTP cache so the click always sees fresh data.
        var url = opts.forceFresh ? "/api/pull-requests?_=" + Date.now() : "/api/pull-requests";
        fetch(url, { headers: { "Accept": "application/json" } })
            .then(function (resp) {
                return resp.json().then(function (payload) {
                    return { ok: resp.ok, payload: payload };
                });
            })
            .then(function (res) {
                var payload = res.payload || {};
                if (!res.ok || !payload.success) {
                    throw new Error(payload.error || "Could not load pull requests.");
                }
                prs = payload.pull_requests || [];
                setCount(payload.count != null ? payload.count : prs.length);
                rebuildAuthors();
                render();
            })
            .catch(function (err) {
                renderError(err.message);
            })
            .then(function () {
                loading = false;
                lastFetch = Date.now();
            });
    }

    // Public entry points used by dashboard.js.
    window.refreshPullRequests = function (forceFresh) {
        fetchAndRender(forceFresh ? { forceFresh: true } : { silent: true });
    };

    // Manual refresh button in the PR tab header.
    var prRefreshBtn = document.getElementById("prRefreshBtn");
    if (prRefreshBtn) {
        prRefreshBtn.addEventListener("click", function () {
            window.refreshPullRequests(true);
        });
    }

    // Initial load, then a single 60s auto-refresh timer.
    fetchAndRender({});
    if (!timer) {
        timer = setInterval(function () {
            if (document.hidden) return; // pause while the tab is hidden
            fetchAndRender({ silent: true });
        }, REFRESH_INTERVAL_MS);
        document.addEventListener("visibilitychange", function () {
            if (!document.hidden && Date.now() - lastFetch >= REFRESH_INTERVAL_MS) {
                fetchAndRender({ silent: true });
            }
        });
    }
})();
