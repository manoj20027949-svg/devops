/* ============================================================
   GitPulse - Chart.js renderers
   Reads data from data-* attributes on the canvas elements so the
   templates stay clean and free of inline script data.
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

    // ---------- Languages doughnut ----------
    const langCanvas = document.getElementById("languagesChart");
    if (langCanvas) {
        let labels = [];
        let values = [];
        try {
            labels = JSON.parse(langCanvas.dataset.labels || "[]");
            values = JSON.parse(langCanvas.dataset.values || "[]");
        } catch (err) {
            console.error("GitPulse: bad language chart data", err);
        }

        if (values.length === 0) {
            const empty = document.getElementById("langEmpty");
            if (empty) empty.classList.add("show");
        } else {
            new Chart(langCanvas, {
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
            });
        }
    }

    // ---------- Commits per member bar ----------
    const commitsCanvas = document.getElementById("commitsChart");
    if (commitsCanvas) {
        let labels = [];
        let values = [];
        try {
            labels = JSON.parse(commitsCanvas.dataset.labels || "[]");
            values = JSON.parse(commitsCanvas.dataset.values || "[]");
        } catch (err) {
            console.error("GitPulse: bad commits chart data", err);
        }

        if (values.length > 0) {
            new Chart(commitsCanvas, {
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
            });
        }
    }

    // ---------- Activity score per member bar ----------
    const scoreCanvas = document.getElementById("scoreChart");
    if (scoreCanvas) {
        let labels = [];
        let values = [];
        try {
            labels = JSON.parse(scoreCanvas.dataset.labels || "[]");
            values = JSON.parse(scoreCanvas.dataset.values || "[]");
        } catch (err) {
            console.error("GitPulse: bad score chart data", err);
        }

        if (values.length > 0) {
            new Chart(scoreCanvas, {
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
            });
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
            new Chart(statusCanvas, {
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
            });
        }
    }

    // ---------- Member page language doughnut ----------
    const langMemberCanvas = document.getElementById("langMemberChart");
    if (langMemberCanvas) {
        let labels = [];
        let values = [];
        try {
            labels = JSON.parse(langMemberCanvas.dataset.labels || "[]");
            values = JSON.parse(langMemberCanvas.dataset.values || "[]");
        } catch (err) {
            console.error("GitPulse: bad member language chart data", err);
        }

        if (values.length === 0) {
            const empty = document.getElementById("langMemberEmpty");
            if (empty) empty.classList.add("show");
        } else {
            new Chart(langMemberCanvas, {
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
            });
        }
    }
})();
