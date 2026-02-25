/**
 * @fileoverview Timeline Graph view — renders a scrollable Plotly.js
 * bar chart of latency over time for a selected hop.
 */

/* global Plotly */

const $chart = document.getElementById("timeline-chart");

/** @type {boolean} Whether the chart has been initialised. */
let chartInitialised = false;
/** @type {Array<{timestamp: string, rtt_ms: number|null, is_timeout: boolean}>} */
let timelinePoints = [];

/**
 * Map a data point to a colour hex for the bar chart.
 *
 * @param {number|null} rtt - RTT in milliseconds.
 * @param {boolean} isTimeout - Whether the probe timed out.
 * @returns {string} Hex colour.
 */
function pointColor(rtt, isTimeout) {
  if (isTimeout) return "#e74c3c";
  if (rtt === null || rtt === undefined) return "#556677";
  if (rtt > 200) return "#e74c3c";
  if (rtt > 80) return "#f39c12";
  return "#2ecc71";
}

/**
 * Determine the current theme background colours for Plotly layout.
 *
 * @returns {{paper: string, plot: string, font: string, grid: string}}
 */
function themeColors() {
  const isDark = document.documentElement.getAttribute("data-theme") !== "light";
  return {
    paper: isDark ? "#1a2736" : "#ffffff",
    plot: isDark ? "#1a2736" : "#ffffff",
    font: isDark ? "#e0e8f0" : "#1a2736",
    grid: isDark ? "#2a3d50" : "#ccd5de",
  };
}

/**
 * Render the timeline bar chart from API data.
 *
 * @param {Array<{timestamp: string, rtt_ms: number|null, is_timeout: boolean}>} data
 */
export function renderTimeline(data) {
  timelinePoints = Array.isArray(data) ? data.slice() : [];
  if (timelinePoints.length === 0) {
    $chart.innerHTML = `<div class="empty-state" style="padding-top:6rem">
      No timeline data available yet.</div>`;
    chartInitialised = false;
    return;
  }

  const timestamps = timelinePoints.map((d) => d.timestamp);
  const rtts = timelinePoints.map((d) => (d.is_timeout ? null : d.rtt_ms));
  const colours = timelinePoints.map((d) => pointColor(d.rtt_ms, d.is_timeout));

  // For timeouts, show a fixed-height red bar so they're visible.
  const maxRtt = Math.max(...rtts.filter((r) => r !== null), 1);
  const displayRtts = timelinePoints.map((d) =>
    d.is_timeout ? maxRtt * 1.1 : d.rtt_ms
  );

  const hoverTexts = timelinePoints.map((d) =>
    d.is_timeout
      ? "TIMEOUT"
      : d.rtt_ms !== null
        ? `${d.rtt_ms.toFixed(1)} ms`
        : "—"
  );

  const theme = themeColors();

  const trace = {
    x: timestamps,
    y: displayRtts,
    type: "bar",
    marker: { color: colours },
    text: hoverTexts,
    hoverinfo: "x+text",
  };

  const layout = {
    paper_bgcolor: theme.paper,
    plot_bgcolor: theme.plot,
    font: { color: theme.font, family: "Inter, sans-serif", size: 12 },
    margin: { t: 30, r: 20, b: 50, l: 60 },
    xaxis: {
      title: "Time",
      gridcolor: theme.grid,
      rangeslider: { visible: true },
    },
    yaxis: {
      title: "RTT (ms)",
      gridcolor: theme.grid,
      rangemode: "tozero",
    },
    bargap: 0.1,
  };

  const config = { responsive: true, displayModeBar: true };

  if (chartInitialised) {
    Plotly.react($chart, [trace], layout, config);
  } else {
    Plotly.newPlot($chart, [trace], layout, config);
    chartInitialised = true;
  }
}

/**
 * Append one point and rerender with a bounded history window.
 *
 * @param {{timestamp: string, rtt_ms: number|null, is_timeout: boolean}} point
 * @param {number} maxPoints - Maximum points retained in memory and chart.
 */
export function appendTimelinePoint(point, maxPoints = 600) {
  if (!point) return;
  timelinePoints.push(point);
  if (timelinePoints.length > maxPoints) {
    timelinePoints = timelinePoints.slice(-maxPoints);
  }
  renderTimeline(timelinePoints);
}

/**
 * Remove the timeline chart.
 */
export function clearTimeline() {
  timelinePoints = [];
  if (chartInitialised) {
    Plotly.purge($chart);
    chartInitialised = false;
  }
  $chart.innerHTML = `<div class="empty-state" style="padding-top:6rem">
    Select a target to view the timeline.</div>`;
}
