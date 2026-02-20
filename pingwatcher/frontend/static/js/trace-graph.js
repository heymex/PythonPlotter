/**
 * @fileoverview Trace Graph view — renders the hop-by-hop statistics
 * table with colour-coded latency bars.
 */

const $tbody = document.getElementById("trace-tbody");

/**
 * Determine a CSS colour class based on latency and packet loss.
 *
 * @param {number|null} rtt - RTT in milliseconds.
 * @param {number} loss - Packet loss percentage (0-100).
 * @returns {string} CSS class name.
 */
function statusClass(rtt, loss) {
  if (loss >= 25) return "text-danger";
  if (loss >= 5) return "text-warning";
  if (rtt === null) return "text-muted";
  if (rtt > 200) return "text-danger";
  if (rtt > 80) return "text-warning";
  return "text-success";
}

/**
 * Return a bar colour hex based on latency and loss.
 *
 * @param {number|null} rtt - RTT in milliseconds.
 * @param {number} loss - Packet loss percentage.
 * @returns {string} Hex colour.
 */
function barColor(rtt, loss) {
  if (loss >= 25) return "#e74c3c";
  if (loss >= 5) return "#f39c12";
  if (rtt === null) return "#556677";
  if (rtt > 200) return "#e74c3c";
  if (rtt > 80) return "#f39c12";
  return "#2ecc71";
}

/**
 * Format a millisecond value for display.
 *
 * @param {number|null} ms - Value to format.
 * @returns {string} Formatted string.
 */
function fmtMs(ms) {
  if (ms === null || ms === undefined) return "—";
  return `${ms.toFixed(1)} ms`;
}

/**
 * Render the trace graph table from an array of hop stats.
 *
 * @param {Array<Object>} hops - Hop stat dictionaries from the API.
 */
export function renderTraceGraph(hops) {
  if (!hops || hops.length === 0) {
    $tbody.innerHTML = `<tr><td colspan="8" class="empty-state">
      No data yet. Add a target above to begin monitoring.</td></tr>`;
    return;
  }

  const maxRtt = Math.max(...hops.map((h) => h.avg_ms || 0), 1);

  $tbody.innerHTML = hops
    .map((h) => {
      const cls = statusClass(h.cur_ms, h.packet_loss_pct);
      const barW = h.avg_ms ? Math.max(2, (h.avg_ms / maxRtt) * 100) : 0;
      const color = barColor(h.avg_ms, h.packet_loss_pct);

      return `<tr>
        <td>${h.hop}</td>
        <td>${h.ip || "*"}</td>
        <td>${h.dns || "—"}</td>
        <td class="${cls}">${fmtMs(h.avg_ms)}</td>
        <td class="${cls}">${fmtMs(h.min_ms)}</td>
        <td class="${cls}">${h.cur_ms !== null && h.cur_ms !== undefined ? fmtMs(h.cur_ms) : '<span class="text-danger">ERR</span>'}</td>
        <td class="${cls}">${h.packet_loss_pct.toFixed(1)}%</td>
        <td class="col-graph">
          <div class="latency-bar" style="width:${barW}%;background:${color}"></div>
        </td>
      </tr>`;
    })
    .join("");
}

/**
 * Clear the trace graph table.
 */
export function clearTraceGraph() {
  $tbody.innerHTML = `<tr><td colspan="8" class="empty-state">
    Select a target to view trace data.</td></tr>`;
}
