/* ============================================================
   GitPulse - main UI behaviour
   Sidebar toggle, tab navigation, scan button loading state.
   ============================================================ */
(function () {
    "use strict";

    // ---------- Sidebar toggle (mobile) ----------
    const sidebar = document.getElementById("sidebar");
    const sidebarToggle = document.getElementById("sidebarToggle");

    if (sidebarToggle && sidebar) {
        sidebarToggle.addEventListener("click", function () {
            sidebar.classList.toggle("open");
        });
        document.addEventListener("click", function (event) {
            if (
                window.innerWidth <= 992 &&
                sidebar.classList.contains("open") &&
                !sidebar.contains(event.target) &&
                event.target !== sidebarToggle
            ) {
                sidebar.classList.remove("open");
            }
        });
    }

    // ---------- Tab navigation ----------
    const navLinks = document.querySelectorAll(".sidebar-nav .nav-link");
    const panes = document.querySelectorAll(".tab-pane");

    function activateTab(targetId) {
        panes.forEach(function (pane) {
            pane.classList.toggle("active", pane.id === targetId);
        });
        navLinks.forEach(function (link) {
            link.classList.toggle("active", link.dataset.tab === targetId);
        });
    }

    navLinks.forEach(function (link) {
        link.addEventListener("click", function (event) {
            event.preventDefault();
            activateTab(link.dataset.tab);
            if (sidebar && window.innerWidth <= 992) sidebar.classList.remove("open");
        });
    });

    // ---------- Scan button loading state ----------
    const scanBtn = document.getElementById("scanBtn");
    if (scanBtn) {
        scanBtn.addEventListener("click", function () {
            scanBtn.disabled = true;
            scanBtn.textContent = "Scanning…";
            // Give the UI a moment to paint before the (slow) network scan.
            setTimeout(function () {
                scanBtn.closest("form").submit();
            }, 50);
        });
    }

    // ---------- Auto-dismiss alerts ----------
    window.setTimeout(function () {
        document.querySelectorAll(".alert").forEach(function (alert) {
            // Bootstrap's own dismiss method if present.
            var instance = window.bootstrap && window.bootstrap.Alert
                ? window.bootstrap.Alert.getOrCreateInstance(alert)
                : null;
            if (instance) instance.close();
        });
    }, 6000);

    // ---------- AI helpers ----------
    function postJSON(url, data) {
        return fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(data),
        }).then(function (resp) {
            return resp.json().then(function (payload) {
                return { ok: resp.ok, payload: payload };
            });
        });
    }

    function showResult(title, payload) {
        var problem = payload.problem || payload.summary || payload.error || "No result.";
        var detail = payload.explanation || payload.root_cause || "";
        var suggestions = (payload.suggestions || []).map(function (s) { return "• " + s; }).join("\n");
        alert(title + "\n\n" + problem + (detail ? "\n\n" + detail : "") + (suggestions ? "\n\n" + suggestions : ""));
    }

    // Analyze a pull request
    document.querySelectorAll(".ai-pr-btn").forEach(function (btn) {
        btn.addEventListener("click", function () {
            var number = btn.dataset.pr;
            btn.disabled = true;
            btn.textContent = "Analyzing…";
            postJSON("/api/ai/analyze-pr", { number: number }).then(function (res) {
                btn.disabled = false;
                btn.textContent = "Analyze";
                if (res.ok) {
                    showResult("AI Analysis · PR #" + number, res.payload);
                } else {
                    alert("Analysis failed: " + (res.payload.error || "unknown error"));
                }
            }).catch(function () {
                btn.disabled = false;
                btn.textContent = "Analyze";
                alert("Network error while analyzing PR.");
            });
        });
    });

    // Analyze an issue
    document.querySelectorAll(".ai-issue-btn").forEach(function (btn) {
        btn.addEventListener("click", function () {
            var number = btn.dataset.issue;
            btn.disabled = true;
            btn.textContent = "Analyzing…";
            postJSON("/api/ai/analyze-issue", { number: number }).then(function (res) {
                btn.disabled = false;
                btn.textContent = "Analyze";
                if (res.ok) {
                    showResult("AI Analysis · Issue #" + number, res.payload);
                } else {
                    alert("Analysis failed: " + (res.payload.error || "unknown error"));
                }
            }).catch(function () {
                btn.disabled = false;
                btn.textContent = "Analyze";
                alert("Network error while analyzing issue.");
            });
        });
    });
})();
