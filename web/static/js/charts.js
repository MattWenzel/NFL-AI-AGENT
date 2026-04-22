// Chart rendering — shared by the CSV viewer builder and inline tool results
// -----------------------------------------------------------------------

import { state } from "./state.js";

const CHART_PALETTE = [
  "#4F46E5", "#059669", "#B45309", "#BE185D", "#0891B2",
  "#7C3AED", "#DC2626", "#CA8A04", "#2563EB", "#65A30D",
];

function asNumber(v) {
  if (v == null || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

export function destroyAllCharts() {
  for (const chart of state.chartInstances.values()) {
    try { chart.destroy(); } catch (err) { console.warn("chart.destroy failed:", err); }
  }
  state.chartInstances.clear();
}

// Walk the DOM for every <canvas data-chart-id="…">, look up its queued
// spec+rows in state.pendingCharts, and bind a Chart.js instance. Called
// after any innerHTML write that would orphan previously-tracked charts.
export function mountPendingCharts(root) {
  const canvases = (root || document).querySelectorAll("canvas[data-chart-id]");
  for (const canvas of canvases) {
    const id = canvas.dataset.chartId;
    const payload = state.pendingCharts.get(id);
    if (!payload) continue;
    const chart = renderChart(canvas, payload.spec, payload.rows);
    if (chart) state.chartInstances.set(id, chart);
  }
}

function renderChart(canvas, spec, rows) {
  if (!window.Chart) {
    console.warn("Chart.js not loaded");
    return null;
  }
  if (!spec || !rows || !rows.length) return null;
  const type = spec.type || "bar";
  const xKey = spec.x;
  const yKey = spec.y;
  const groupKey = spec.group_by || null;

  let data;
  if (type === "scatter") {
    data = {
      datasets: [{
        label: spec.title || yKey,
        data: rows.map(r => ({ x: asNumber(r[xKey]), y: asNumber(r[yKey]) }))
                  .filter(p => p.x != null && p.y != null),
        backgroundColor: CHART_PALETTE[0],
        borderColor: CHART_PALETTE[0],
      }],
    };
  } else if (type === "pie") {
    data = {
      labels: rows.map(r => String(r[xKey] ?? "")),
      datasets: [{
        label: yKey,
        data: rows.map(r => asNumber(r[yKey]) ?? 0),
        backgroundColor: rows.map((_, i) => CHART_PALETTE[i % CHART_PALETTE.length]),
      }],
    };
  } else {
    // bar / line — share the same label+dataset shape; group_by splits into series
    const labels = [...new Set(rows.map(r => String(r[xKey] ?? "")))];
    let datasets;
    if (groupKey) {
      const groups = [...new Set(rows.map(r => String(r[groupKey] ?? "")))];
      datasets = groups.map((g, i) => {
        const color = CHART_PALETTE[i % CHART_PALETTE.length];
        const byLabel = new Map(labels.map(l => [l, null]));
        for (const row of rows) {
          if (String(row[groupKey] ?? "") !== g) continue;
          byLabel.set(String(row[xKey] ?? ""), asNumber(row[yKey]));
        }
        return {
          label: g,
          data: labels.map(l => byLabel.get(l)),
          backgroundColor: color,
          borderColor: color,
          tension: type === "line" ? 0.3 : undefined,
          fill: false,
        };
      });
    } else {
      datasets = [{
        label: spec.title || yKey,
        data: rows.map(r => asNumber(r[yKey])),
        backgroundColor: rows.map((_, i) => CHART_PALETTE[i % CHART_PALETTE.length]),
        borderColor: CHART_PALETTE[0],
        tension: type === "line" ? 0.3 : undefined,
        fill: false,
      }];
    }
    data = { labels, datasets };
  }

  const config = {
    type,
    data,
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: type === "pie" || !!groupKey },
        title: spec.title ? { display: true, text: spec.title } : undefined,
      },
      scales: (type === "pie") ? undefined : {
        x: { ticks: { autoSkip: true, maxRotation: 40 } },
        y: { beginAtZero: true },
      },
    },
  };
  try {
    return new Chart(canvas, config);
  } catch (err) {
    console.error("Chart init failed:", err);
    return null;
  }
}
