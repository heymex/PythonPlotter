/**
 * @fileoverview Summary view — displays a table of all active targets
 * with their final-hop statistics.
 */

const $summaryTbody = document.getElementById("summary-tbody");

/**
 * Determine a CSS colour class based on loss and latency.
 *
 * @param {number|null} rtt - Current RTT in milliseconds.
 * @param {number} loss - Packet loss percentage.
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
 * Return a human-friendly status label.
 *
 * @param {number|null} rtt - Current RTT.
 * @param {number} loss - Packet loss percentage.
 * @returns {string} Status text.
 */
function statusLabel(rtt, loss) {
  if (loss >= 50) return "Critical";
  if (loss >= 25) return "Degraded";
  if (loss >= 5) return "Warning";
  if (rtt !== null && rtt > 200) return "High latency";
  if (rtt !== null && rtt > 80) return "Elevated";
  if (rtt !== null) return "Healthy";
  return "No data";
}

/**
 * Format a millisecond value.
 *
 * @param {number|null} ms - Value to format.
 * @returns {string} Formatted string.
 */
function fmtMs(ms) {
  if (ms === null || ms === undefined) return "—";
  return `${ms.toFixed(1)} ms`;
}

/**
 * Render the summary table from an array of target summaries.
 *
 * @param {Array<Object>} summaries - Summary dictionaries from the API.
 */
export function renderSummary(summaries) {
  if (!summaries || summaries.length === 0) {
    $summaryTbody.innerHTML = `<tr><td colspan="7" class="empty-state">
      No active targets. Add one above to begin monitoring.</td></tr>`;
    return;
  }

  $summaryTbody.innerHTML = summaries
    .map((s) => {
      const cls = statusClass(s.cur_ms, s.packet_loss_pct);
      const label = statusLabel(s.cur_ms, s.packet_loss_pct);

      return `<tr>
        <td>${s.label || "—"}</td>
        <td>${s.host}</td>
        <td class="${cls}">${fmtMs(s.avg_ms)}</td>
        <td class="${cls}">${fmtMs(s.min_ms)}</td>
        <td class="${cls}">${fmtMs(s.cur_ms)}</td>
        <td class="${cls}">${s.packet_loss_pct.toFixed(1)}%</td>
        <td class="${cls}">${label}</td>
      </tr>`;
    })
    .join("");
}
