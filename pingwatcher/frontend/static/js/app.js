/**
 * @fileoverview Main application module — bootstraps views, manages target
 * state, and coordinates WebSocket connections.
 */

import { renderTraceGraph, clearTraceGraph } from "./trace-graph.js";
import { appendTimelinePoint, renderTimeline, clearTimeline } from "./timeline-graph.js";
import { renderSummary } from "./summary-view.js";

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

/** @type {Array<{id: string, host: string, label: string|null}>} */
let targets = [];

/** @type {string|null} Currently selected target ID. */
let activeTargetId = null;

/** @type {WebSocket|null} Active WebSocket connection. */
let ws = null;
/** @type {WebSocket|null} Summary view WebSocket. */
let summaryWs = null;
/** @type {Map<string, Object>} Latest summary rows keyed by target ID. */
let summaryRows = new Map();

const DEFAULT_TIMELINE_POINTS = 600;

// ---------------------------------------------------------------------------
// DOM references
// ---------------------------------------------------------------------------

const $host = document.getElementById("target-host");
const $label = document.getElementById("target-label");
const $interval = document.getElementById("target-interval");
const $addBtn = document.getElementById("btn-add-target");
const $pills = document.getElementById("target-pills");
const $focus = document.getElementById("focus-select");
const $themeToggle = document.getElementById("theme-toggle");
const $tabs = document.querySelectorAll(".tab-btn");

// ---------------------------------------------------------------------------
// Theme
// ---------------------------------------------------------------------------

/**
 * Apply the persisted theme or fall back to dark.
 */
function initTheme() {
  const saved = localStorage.getItem("pw-theme") || "dark";
  document.documentElement.setAttribute("data-theme", saved);
  $themeToggle.textContent = saved === "dark" ? "🌙" : "☀️";
}

$themeToggle.addEventListener("click", () => {
  const current = document.documentElement.getAttribute("data-theme");
  const next = current === "dark" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  localStorage.setItem("pw-theme", next);
  $themeToggle.textContent = next === "dark" ? "🌙" : "☀️";
});

// ---------------------------------------------------------------------------
// Tab navigation
// ---------------------------------------------------------------------------

$tabs.forEach((btn) => {
  btn.addEventListener("click", () => {
    $tabs.forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    document.querySelectorAll(".view-panel").forEach((p) => p.classList.remove("active"));
    const view = btn.dataset.view;
    document.getElementById(`view-${view}`).classList.add("active");

    if (view === "summary") {
      refreshSummary();
      connectSummaryWebSocket();
    } else {
      closeSummaryWebSocket();
      if (activeTargetId) {
        if (view === "trace") refreshTrace();
        if (view === "timeline") refreshTimeline();
      }
    }
  });
});

// ---------------------------------------------------------------------------
// Target management
// ---------------------------------------------------------------------------

/**
 * Fetch the target list from the API and update local state + pills.
 */
async function loadTargets() {
  try {
    const res = await fetch("/api/targets");
    targets = await res.json();
    renderPills();
    if (targets.length && !activeTargetId) {
      selectTarget(targets[0].id);
    }
  } catch (err) {
    console.error("Failed to load targets:", err);
  }
}

/**
 * Render the target pill bar.
 */
function renderPills() {
  $pills.innerHTML = "";
  for (const t of targets) {
    const pill = document.createElement("div");
    pill.className = `pill${t.id === activeTargetId ? " active" : ""}`;
    pill.dataset.id = t.id;

    const label = document.createElement("span");
    label.textContent = t.label || t.host;
    label.addEventListener("click", () => selectTarget(t.id));

    const remove = document.createElement("button");
    remove.className = "pill__remove";
    remove.textContent = "×";
    remove.addEventListener("click", (e) => {
      e.stopPropagation();
      deleteTarget(t.id);
    });

    pill.appendChild(label);
    pill.appendChild(remove);
    $pills.appendChild(pill);
  }
}

/**
 * Add a new monitoring target via the API.
 */
$addBtn.addEventListener("click", async () => {
  const host = $host.value.trim();
  if (!host) return;

  $addBtn.disabled = true;
  try {
    const res = await fetch("/api/targets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        host,
        label: $label.value.trim() || null,
        trace_interval: parseFloat($interval.value),
      }),
    });
    if (!res.ok) throw new Error(await res.text());
    const created = await res.json();
    targets.unshift(created);
    $host.value = "";
    $label.value = "";
    selectTarget(created.id);
    renderPills();
  } catch (err) {
    console.error("Failed to create target:", err);
    alert("Failed to start monitoring. Check the console for details.");
  } finally {
    $addBtn.disabled = false;
  }
});

/**
 * Delete a target via the API and clean up.
 *
 * @param {string} id - Target ID to remove.
 */
async function deleteTarget(id) {
  try {
    await fetch(`/api/targets/${id}`, { method: "DELETE" });
    targets = targets.filter((t) => t.id !== id);
    if (activeTargetId === id) {
      activeTargetId = null;
      closeWebSocket();
      clearTraceGraph();
      clearTimeline();
      if (targets.length) selectTarget(targets[0].id);
    }
    renderPills();
  } catch (err) {
    console.error("Failed to delete target:", err);
  }
}

/**
 * Select a target and begin streaming live data.
 *
 * @param {string} id - Target ID to activate.
 */
function selectTarget(id) {
  activeTargetId = id;
  renderPills();
  refreshTrace();
  refreshTimeline();
  connectWebSocket(id);
}

// ---------------------------------------------------------------------------
// Data fetching
// ---------------------------------------------------------------------------

/**
 * Fetch and render the trace graph for the active target.
 */
async function refreshTrace() {
  if (!activeTargetId) return;
  try {
    const focus = $focus.value;
    const res = await fetch(`/api/targets/${activeTargetId}/hops?focus=${focus}`);
    const hops = await res.json();
    renderTraceGraph(hops);
  } catch (err) {
    console.error("Failed to fetch hops:", err);
  }
}

/**
 * Fetch and render the timeline for the active target.
 */
async function refreshTimeline() {
  if (!activeTargetId) return;
  try {
    const res = await fetch(
      `/api/targets/${activeTargetId}/timeline?hop=last&limit=${DEFAULT_TIMELINE_POINTS}`
    );
    const data = await res.json();
    renderTimeline(data);
  } catch (err) {
    console.error("Failed to fetch timeline:", err);
  }
}

/**
 * Fetch and render the summary view.
 */
async function refreshSummary() {
  try {
    const res = await fetch("/api/summary");
    const data = await res.json();
    renderSummary(data);
  } catch (err) {
    console.error("Failed to fetch summary:", err);
  }
}

$focus.addEventListener("change", () => {
  refreshTrace();
});

// ---------------------------------------------------------------------------
// WebSocket
// ---------------------------------------------------------------------------

/**
 * Open a WebSocket to stream live data for a target.
 *
 * @param {string} targetId - Target ID.
 */
function connectWebSocket(targetId) {
  closeWebSocket();
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${protocol}//${location.host}/ws/targets/${targetId}`);

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      if (data.target_id !== activeTargetId) return;
      const sampledAt = data.sampled_at || new Date().toISOString();
      const activeView = document.querySelector(".tab-btn.active")?.dataset.view;
      if (activeView === "trace") {
        if (Array.isArray(data.hop_stats)) {
          renderTraceGraph(data.hop_stats);
        } else {
          refreshTrace();
        }
      }
      if (activeView === "timeline") {
        const finalHop = getFinalHop(data.hops || []);
        appendTimelinePoint(
          {
            timestamp: sampledAt,
            rtt_ms: finalHop?.rtt_ms ?? null,
            is_timeout: finalHop ? !!finalHop.is_timeout : true,
          },
          DEFAULT_TIMELINE_POINTS
        );
      }
    } catch (err) {
      console.error("WebSocket parse error:", err);
    }
  };

  ws.onclose = () => {
    // Reconnect after a short delay.
    setTimeout(() => {
      if (activeTargetId === targetId) connectWebSocket(targetId);
    }, 3000);
  };
}

/**
 * Return the highest-hop row from a traceroute sample.
 *
 * @param {Array<{hop:number}>} hops
 * @returns {Object|null}
 */
function getFinalHop(hops) {
  if (!Array.isArray(hops) || hops.length === 0) return null;
  return hops.reduce((acc, row) => (row.hop > acc.hop ? row : acc), hops[0]);
}

/** Close the current WebSocket connection. */
function closeWebSocket() {
  if (ws) {
    ws.onclose = null;
    ws.close();
    ws = null;
  }
}

/** Open/maintain summary updates WebSocket. */
function connectSummaryWebSocket() {
  closeSummaryWebSocket();
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  summaryWs = new WebSocket(`${protocol}//${location.host}/ws/summary`);
  summaryWs.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      if (data.type === "summary_snapshot" && Array.isArray(data.rows)) {
        summaryRows = new Map(data.rows.map((row) => [row.target_id, row]));
      } else if (data.type === "summary_update" && data.summary_row) {
        summaryRows.set(data.target_id, data.summary_row);
      } else {
        return;
      }
      renderSummary(Array.from(summaryRows.values()));
    } catch (err) {
      console.error("Summary WS parse error:", err);
    }
  };
  summaryWs.onclose = () => {
    setTimeout(() => {
      const activeView = document.querySelector(".tab-btn.active")?.dataset.view;
      if (activeView === "summary") connectSummaryWebSocket();
    }, 3000);
  };
}

/** Close summary WebSocket and reset in-memory rows. */
function closeSummaryWebSocket() {
  if (summaryWs) {
    summaryWs.onclose = null;
    summaryWs.close();
    summaryWs = null;
  }
}

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------

initTheme();
loadTargets();
