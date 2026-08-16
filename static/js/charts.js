/* ============================================================
   GitPulse - Chart.js renderers
   Reads data from data-* attributes on the canvas elements so the
   templates stay clean and free of inline script data.

   Charts are registered in window.GitPulseCharts so the dashboard
   can update them in place after an AJAX refresh (no reload).
   ============================================================ */
(function () {
    "use strict";

    const chartDefaults = {
        color: "#9aa7bd",
        font: { family: "Inter", size: 12 },
        grid: { color: "rgba(255,255,255,0.06)" },
    };

    Chart.defaults.color = chartDefaults.color;
    Chart.defaults.font.family = chartDefaults.font.family;

    const PALETTE = [
        "#7c5cff", "#00d4ff", "#2ecc71", "#f39c12", "#ff5252",
        "#4aa8ff", "#9b6cff", "#2ecc9c", "#e67e22", "#f1c40f",
    ];

    // ---------- Chart registry (for AJAX refresh) ----------
    const registry = {};
    window.GitPulseCharts = {
        charts: registry,

        // Update an existing chart's data in place and re-render it.
        update: function (name, data) {
            const chart = registry[name];
            if (!chart) return false;
            if (data.labels !== undefined) {
                chart.data.labels = data.labels || [];
            }
            if (data.values !== undefined && chart.data.datasets && chart.data.datasets.length) {
                chart.data.datasets[0].data = data.values || [];
            }
            if (data.active !== undefined && data.inactive !== undefined && chart.data.datasets && chart.data.datasets.length) {
                chart.data.datasets[0].data = [data.active || 0, data.inactive || 0];
            }
            chart.update();
            return true;
        },

        // Show the "empty" placeholder for a chart container.
        showEmpty: function (name) {
            const chart = registry[name];
            if (!chart) return;
            const canvas = chart.canvas;
            if (canvas) {
                const parent = canvas.parentNode;
                if (parent) {
                    const empty = parent.querySelector(".chart-empty");
                    if (empty) empty.classList.add("show");
                }
            }
        },

        // Hide the "empty" placeholder for a chart container.
        hideEmpty: function (name) {
            const chart = registry[name];
            if (!chart) return;
            const canvas = chart.canvas;
            if (canvas) {
                const parent = canvas.parentNode;
                if (parent) {
                    const empty = parent.querySelector(".chart-empty");
                    if (empty) empty.classList.remove("show");
                }
            }
        },
    };

    function register(name, canvas, chart) {
        if (!canvas || !chart) return;
        registry[name] = chart;
        // Keep the canvas element discoverable by name for tests.
        canvas.setAttribute("data-chart-name", name);
    }

    function parseJson(attr, fallback) {
        try {
            const value = JSON.parse(attr || "null");
            return Array.isArray(value) ? value : fallback;
        } catch (err) {
            console.error("GitPulse: bad chart data", err);
            return fallback;
        }
    }

    // ---------- Languages doughnut ----------
    const langCanvas = document.getElementById("languagesChart");
    if (langCanvas) {
        const labels = parseJson(langCanvas.dataset.labels, []);
        const values = parseJson(langCanvas.dataset.values, []);

        if (values.length === 0) {
            const empty = document.getElementById("langEmpty");
            if (empty) empty.classList.add("show");
        } else {
            register("languages", langCanvas, new Chart(langCanvas, {
                type: "doughnut",
                data: {
                    labels: labels,
                    datasets: [{
                        data: values,
                        backgroundColor: labels.map(function (_, i) {
                            return PALETTE[i % PALETTE.length];
                        }),
                        borderWidth: 0,
                        hoverOffset: 8,
                    }],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    cutout: "62%",
                    plugins: {
                        legend: {
                            position: "bottom",
                            labels: { padding: 14, usePointStyle: true, pointStyle: "circle" },
                        },
                    },
                },
            }));
        }
    }

    // ---------- Commits per member bar ----------
    const commitsCanvas = document.getElementById("commitsChart");
    if (commitsCanvas) {
        const labels = parseJson(commitsCanvas.dataset.labels, []);
        const values = parseJson(commitsCanvas.dataset.values, []);

        if (values.length > 0) {
            register("commits", commitsCanvas, new Chart(commitsCanvas, {
                type: "bar",
                data: {
                    labels: labels,
                    datasets: [{
                        label: "Commits",
                        data: values,
                        backgroundColor: "rgba(124, 92, 255, 0.55)",
                        borderColor: "#7c5cff",
                        borderWidth: 1,
                        borderRadius: 8,
                    }],
                },
                options: {
                    indexAxis: "y",
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {
                        x: { beginAtZero: true, grid: chartDefaults.grid, ticks: { precision: 0 } },
                        y: { grid: { display: false } },
                    },
                    plugins: {
                        legend: { display: false },
                    },
                },
            }));
        }
    }

    // ---------- Activity score per member bar ----------
    const scoreCanvas = document.getElementById("scoreChart");
    if (scoreCanvas) {
        const labels = parseJson(scoreCanvas.dataset.labels, []);
        const values = parseJson(scoreCanvas.dataset.values, []);

        if (values.length > 0) {
            register("score", scoreCanvas, new Chart(scoreCanvas, {
                type: "bar",
                data: {
                    labels: labels,
                    datasets: [{
                        label: "Activity score",
                        data: values,
                        backgroundColor: "rgba(0, 212, 255, 0.55)",
                        borderColor: "#00d4ff",
                        borderWidth: 1,
                        borderRadius: 8,
                    }],
                },
                options: {
                    indexAxis: "y",
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {
                        x: { min: 0, max: 100, grid: chartDefaults.grid, ticks: { precision: 0 } },
                        y: { grid: { display: false } },
                    },
                    plugins: {
                        legend: { display: false },
                    },
                },
            }));
        }
    }

    // ---------- Member status doughnut (Active / Inactive) ----------
    const statusCanvas = document.getElementById("statusChart");
    if (statusCanvas) {
        const active = parseInt(statusCanvas.dataset.active || "0", 10);
        const inactive = parseInt(statusCanvas.dataset.inactive || "0", 10);
        const labels = ["Active", "Inactive"];
        const values = [active, inactive];
        const empty = document.getElementById("statusEmpty");

        if (active + inactive === 0) {
            if (empty) empty.classList.add("show");
        } else {
            register("status", statusCanvas, new Chart(statusCanvas, {
                type: "doughnut",
                data: {
                    labels: labels,
                    datasets: [{
                        data: values,
                        backgroundColor: ["#2ecc71", "#ff5252"],
                        borderWidth: 0,
                        hoverOffset: 8,
                    }],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    cutout: "60%",
                    plugins: {
                        legend: {
                            position: "bottom",
                            labels: { padding: 12, usePointStyle: true, pointStyle: "circle" },
                        },
                    },
                },
            }));
        }
    }

    // ---------- Team Reports: commits per member bar ----------
    const reportsCommitsCanvas = document.getElementById("reportsCommitsChart");
    if (reportsCommitsCanvas) {
        const labels = parseJson(reportsCommitsCanvas.dataset.labels, []);
        const values = parseJson(reportsCommitsCanvas.dataset.values, []);

        if (values.length > 0) {
            register("reports-commits", reportsCommitsCanvas, new Chart(reportsCommitsCanvas, {
                type: "bar",
                data: {
                    labels: labels,
                    datasets: [{
                        label: "Commits",
                        data: values,
                        backgroundColor: "rgba(124, 92, 255, 0.55)",
                        borderColor: "#7c5cff",
                        borderWidth: 1,
                        borderRadius: 8,
                    }],
                },
                options: {
                    indexAxis: "y",
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {
                        x: { beginAtZero: true, grid: chartDefaults.grid, ticks: { precision: 0 } },
                        y: { grid: { display: false } },
                    },
                    plugins: { legend: { display: false } },
                },
            }));
        } else {
            const empty = document.getElementById("reportsCommitsEmpty");
            if (empty) empty.classList.add("show");
        }
    }

    // ---------- Team Reports: activity score per member bar ----------
    const reportsScoreCanvas = document.getElementById("reportsScoreChart");
    if (reportsScoreCanvas) {
        const labels = parseJson(reportsScoreCanvas.dataset.labels, []);
        const values = parseJson(reportsScoreCanvas.dataset.values, []);

        if (values.length > 0) {
            register("reports-score", reportsScoreCanvas, new Chart(reportsScoreCanvas, {
                type: "bar",
                data: {
                    labels: labels,
                    datasets: [{
                        label: "Activity score",
                        data: values,
                        backgroundColor: "rgba(0, 212, 255, 0.55)",
                        borderColor: "#00d4ff",
                        borderWidth: 1,
                        borderRadius: 8,
                    }],
                },
                options: {
                    indexAxis: "y",
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {
                        x: { min: 0, max: 100, grid: chartDefaults.grid, ticks: { precision: 0 } },
                        y: { grid: { display: false } },
                    },
                    plugins: { legend: { display: false } },
                },
            }));
        } else {
            const empty = document.getElementById("reportsScoreEmpty");
            if (empty) empty.classList.add("show");
        }
    }

    // ---------- Team Reports: weekly activity line ----------
    const reportsWeeklyCanvas = document.getElementById("reportsWeeklyChart");
    if (reportsWeeklyCanvas) {
        const labels = parseJson(reportsWeeklyCanvas.dataset.labels, []);
        const values = parseJson(reportsWeeklyCanvas.dataset.values, []);

        if (values.length > 0) {
            register("reports-weekly", reportsWeeklyCanvas, new Chart(reportsWeeklyCanvas, {
                type: "line",
                data: {
                    labels: labels,
                    datasets: [{
                        label: "Events",
                        data: values,
                        borderColor: "#2ecc71",
                        backgroundColor: "rgba(46, 204, 113, 0.15)",
                        fill: true,
                        tension: 0.3,
                        pointBackgroundColor: "#2ecc71",
                        borderWidth: 2,
                    }],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {
                        x: { grid: { display: false } },
                        y: { beginAtZero: true, grid: chartDefaults.grid, ticks: { precision: 0 } },
                    },
                    plugins: { legend: { display: false } },
                },
            }));
        } else {
            const empty = document.getElementById("reportsWeeklyEmpty");
            if (empty) empty.classList.add("show");
        }
    }

    // ---------- Member page language doughnut ----------
    const langMemberCanvas = document.getElementById("langMemberChart");
    if (langMemberCanvas) {
        const labels = parseJson(langMemberCanvas.dataset.labels, []);
        const values = parseJson(langMemberCanvas.dataset.values, []);

        if (values.length === 0) {
            const empty = document.getElementById("langMemberEmpty");
            if (empty) empty.classList.add("show");
        } else {
            register("lang-member", langMemberCanvas, new Chart(langMemberCanvas, {
                type: "doughnut",
                data: {
                    labels: labels,
                    datasets: [{
                        data: values,
                        backgroundColor: labels.map(function (_, i) {
                            return PALETTE[i % PALETTE.length];
                        }),
                        borderWidth: 0,
                        hoverOffset: 8,
                    }],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    cutout: "62%",
                    plugins: {
                        legend: {
                            position: "bottom",
                            labels: { padding: 14, usePointStyle: true, pointStyle: "circle" },
                        },
                    },
                },
            }));
        }
    }
})();
