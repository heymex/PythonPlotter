# CLAUDE.md — Building a PingPlotter-Like Python Web Application

## Overview

This document describes how to replicate the core functionality of PingPlotter (v5) in a Python-based web application. PingPlotter is a continuous network monitoring and troubleshooting tool that combines traceroute with repeated ICMP/UDP pings to build a time-series picture of every hop along a route to a target host.

---

## Core Concepts

| PingPlotter Concept | What It Means |
|---|---|
| **Target** | A hostname or IP address being monitored |
| **Hop** | Each router/device along the route to the target |
| **Sample** | One full traceroute + ping round-trip measurement |
| **Sample Set / Focus** | The N most recent samples used for computing stats (Avg, PL%) |
| **Trace Interval** | How often a new sample is collected (e.g. every 2.5 seconds) |
| **Packet Loss %** | Percentage of packets not returned within the timeout window |
| **Timeline Graph** | A scrollable time-series view of latency + packet loss over time |
| **Summary View** | Aggregated view of multiple targets side-by-side |

---

## Technology Stack (Recommended)

```
Backend:   Python 3.11+, FastAPI or Flask
           SQLite or PostgreSQL (time-series data storage)
           APScheduler or Celery (periodic trace jobs)
           scapy or subprocess (packet crafting / traceroute)
Frontend:  React or HTMX + Plotly.js / Chart.js (real-time graphs)
           WebSockets (push live data to browser)
Auth:      HTTP Basic Auth or JWT (for multi-user / alert delivery)
```

---

## Module Breakdown

### 1. Packet Engine (`engine/`)

The core of PingPlotter is continuous traceroute with latency measurement.

#### 1a. Traceroute Methods to Implement

Support at least two packet types (configurable per target):

- **ICMP (default)** — Uses `ICMP Echo Request` with incrementing TTL. This is the `tracert`/`ping` method. Best compatibility, no root required on Linux with `ping` subprocess.
- **UDP (Unix-style)** — Sends UDP datagrams to ports 33434–33500 with incrementing TTL. Mirrors Unix `traceroute`. Requires root/raw socket access.
- **TCP (advanced)** — Sends TCP SYN packets. Useful for targets that block ICMP. Requires raw sockets (use `scapy` or `npcap` equivalent).

```python
# engine/tracer.py
import subprocess, socket, time

def icmp_traceroute(target: str, max_hops: int = 30, timeout: float = 3.0) -> list[dict]:
    """
    Run a single traceroute to target. Returns list of hops.
    Each hop: {hop: int, ip: str, dns: str, rtt_ms: float | None}
    """
    hops = []
    for ttl in range(1, max_hops + 1):
        result = _send_probe(target, ttl, timeout)
        hops.append(result)
        if result["ip"] == socket.gethostbyname(target):
            break
    return hops

def _send_probe(target: str, ttl: int, timeout: float) -> dict:
    # Use subprocess ping with TTL on Linux/macOS,
    # or scapy for raw packet construction.
    ...
```

**Key engine options to expose (matching PingPlotter):**

| Option | Default | Notes |
|---|---|---|
| `packet_type` | ICMP | ICMP, UDP, TCP |
| `packet_size` | 56 bytes | Smaller = lower overhead. TCP may need 40 bytes. |
| `timeout` | 3.0 seconds | Lost packets recorded as 9999ms / ERR |
| `max_hops` | 30 | Maximum TTL to send |
| `inter_packet_delay` | 25ms | Pause between each TTL probe in a single trace |
| `trace_interval` | 2.5 seconds | How often to run a full trace |
| `final_hop_only` | False | Only ping the final destination (not intermediates) |

#### 1b. Continuous Sampling Loop

```python
# engine/scheduler.py
from apscheduler.schedulers.background import BackgroundScheduler

scheduler = BackgroundScheduler()

def start_monitoring(target_id: str, target_host: str, interval: float):
    scheduler.add_job(
        func=collect_sample,
        trigger='interval',
        seconds=interval,
        args=[target_id, target_host],
        id=target_id,
        replace_existing=True
    )

def collect_sample(target_id: str, host: str):
    hops = icmp_traceroute(host)
    store_sample(target_id, hops, timestamp=time.time())
```

---

### 2. Data Storage (`db/`)

Store every sample for every hop. This is time-series data.

#### Schema (SQLite / PostgreSQL)

```sql
-- Targets being monitored
CREATE TABLE targets (
    id TEXT PRIMARY KEY,
    host TEXT NOT NULL,
    label TEXT,
    trace_interval REAL DEFAULT 2.5,
    packet_type TEXT DEFAULT 'icmp',
    packet_size INTEGER DEFAULT 56,
    max_hops INTEGER DEFAULT 30,
    timeout REAL DEFAULT 3.0,
    active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- One row per hop per sample
CREATE TABLE samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_id TEXT REFERENCES targets(id),
    sampled_at TIMESTAMP NOT NULL,
    hop_number INTEGER NOT NULL,
    ip TEXT,
    dns TEXT,
    rtt_ms REAL,           -- NULL = timeout (packet lost)
    is_timeout BOOLEAN DEFAULT FALSE
);

-- Index for fast time-range queries
CREATE INDEX idx_samples_target_time ON samples(target_id, sampled_at);
```

#### Focus / Sample Set Query

PingPlotter's "Focus" = the last N samples window. Compute stats over it:

```python
def get_hop_stats(target_id: str, hop_number: int, focus_n: int = 10) -> dict:
    """Returns avg, min, cur, packet_loss% for the last focus_n samples."""
    rows = db.query("""
        SELECT rtt_ms, is_timeout FROM samples
        WHERE target_id = ? AND hop_number = ?
        ORDER BY sampled_at DESC LIMIT ?
    """, [target_id, hop_number, focus_n])

    valid = [r.rtt_ms for r in rows if not r.is_timeout]
    total = len(rows)
    lost = sum(1 for r in rows if r.is_timeout)

    return {
        "avg_ms": sum(valid) / len(valid) if valid else None,
        "min_ms": min(valid) if valid else None,
        "cur_ms": valid[0] if valid else None,  # most recent
        "packet_loss_pct": (lost / total * 100) if total else 0,
    }
```

---

### 3. REST API (`api/`)

Build a JSON API that the frontend consumes.

```
GET  /api/targets                          # List all targets
POST /api/targets                          # Create target (start monitoring)
DELETE /api/targets/{id}                   # Stop and remove target

GET  /api/targets/{id}/hops               # Current hop stats (trace graph data)
GET  /api/targets/{id}/timeline           # Time-series data for timeline graph
     ?hop=last&start=<ts>&end=<ts>&resolution=60s

GET  /api/targets/{id}/sessions           # List saved sessions
POST /api/targets/{id}/sessions/export    # Export session as CSV or JSON

GET  /api/summary                         # All targets' final-hop stats (summary view)

GET  /api/targets/{id}/route_changes      # Detect when routes changed
```

#### WebSocket (Live Updates)

```python
# FastAPI WebSocket endpoint
@app.websocket("/ws/targets/{target_id}")
async def live_feed(websocket: WebSocket, target_id: str):
    await websocket.accept()
    while True:
        data = get_latest_sample(target_id)
        await websocket.send_json(data)
        await asyncio.sleep(0.5)
```

---

### 4. Trace Graph View (Frontend)

Replicate PingPlotter's main "Trace Graph" panel — a table of hops with statistics.

**Columns to display per hop:**

| # | Column | Description |
|---|---|---|
| 1 | Hop | TTL / hop number |
| 2 | IP | IP address of hop |
| 3 | DNS | Reverse-DNS name (or `----------` if unavailable) |
| 4 | Avg | Average RTT over focus period (exclude timeouts) |
| 5 | Min | Minimum RTT over focus period |
| 6 | Cur | Most recent RTT (show `ERR` for timeout) |
| 7 | PL% | Packet loss percentage over focus period |
| 8 | Graph bar | Visual mini bar showing latency relative to max in view |

**Focus control:** Allow user to set N (number of recent samples for stats, e.g. 10, 25, 50, ALL).

**Color coding:**
- Green = low latency, no loss
- Yellow = moderate latency or minor loss
- Red = high loss or extreme latency

---

### 5. Timeline Graph View (Frontend)

A horizontal scrollable time-series chart per hop. This is PingPlotter's most powerful view.

- **X-axis:** Time (scrollable, zoomable — support 10 min, 1 hr, 6 hr, 24 hr, 7 day views)
- **Y-axis:** RTT in milliseconds
- **Color coding:** Draw bars in green (normal), yellow (elevated), red (packet loss / timeout)
- **Interactions:**
  - Click + drag to pan through history
  - Double-click on a time point → "Focus" the Trace Graph to that moment
  - Zoom in/out on time axis
- **Default:** Show timeline for the final hop. Allow enabling/disabling per-hop timelines.

```javascript
// Example using Plotly.js
Plotly.newPlot('timeline', [{
  x: timestamps,
  y: rtt_values,
  type: 'bar',
  marker: { color: colors },  // red where timeout
}], { xaxis: { rangeslider: {} } });
```

---

### 6. Summary View (Frontend)

Displays multiple targets in one table. Mirrors PingPlotter's "Summary Graph."

- Each row = one monitored target (final hop stats)
- Columns: Target, Avg, Min, Cur RTT, PL%, mini timeline sparkline
- Clicking a target opens its full Trace Graph
- Auto-refresh every trace interval
- Support sorting by any column

---

### 7. Alerts System (`alerts/`)

PingPlotter supports configurable threshold-based alerts with multiple action types.

#### Alert Conditions

```python
class AlertCondition:
    metric: str          # "packet_loss_pct" | "avg_rtt_ms" | "cur_rtt_ms"
    operator: str        # ">" | "<" | ">=" | "<="
    threshold: float     # e.g. 10.0 (for 10% loss)
    duration_samples: int  # Must trigger for N consecutive samples
    hop: str             # "any" | "final" | specific IP
```

#### Alert Actions (implement as plugins)

| Action | Implementation |
|---|---|
| **Send Email** | `smtplib` or `SendGrid` API |
| **Log to File** | Write timestamped entry to configurable log path |
| **REST/Webhook** | `httpx.post(url, json=payload)` |
| **Execute Command** | `subprocess.run(command)` |
| **Save Image** | Render graph server-side (Matplotlib/Plotly) and save PNG |
| **Add Comment** | Insert annotation into the samples DB at that timestamp |

#### Alert Lifecycle

- **Trigger condition:** Alert fires when condition is met for `duration_samples` consecutive samples.
- **Recovery condition:** Alert clears when condition is no longer met (send recovery notification).
- **Repeat interval:** Don't spam — configure minimum time between repeated alerts.

```python
# alerts/engine.py
def evaluate_alerts(target_id: str, latest_sample: dict):
    alerts = get_active_alerts(target_id)
    for alert in alerts:
        is_triggered = check_condition(alert, latest_sample)
        handle_state_change(alert, is_triggered)
```

---

### 8. Session Management (`sessions/`)

PingPlotter saves trace data to disk for historical review.

- **Auto-save:** Continuously persist samples to DB (already handled by storing all rows)
- **Session concept:** A named time range of data for a target — can be exported or replayed
- **Export formats:** CSV, JSON
- **Session browser:** API endpoint + UI to list, reopen, delete, export past sessions

```python
# sessions/export.py
def export_session_csv(target_id: str, start_ts: float, end_ts: float) -> str:
    rows = db.query("""
        SELECT sampled_at, hop_number, ip, dns, rtt_ms, is_timeout
        FROM samples
        WHERE target_id = ? AND sampled_at BETWEEN ? AND ?
        ORDER BY sampled_at, hop_number
    """, [target_id, start_ts, end_ts])
    return to_csv(rows)
```

---

### 9. Multiple Targets / Named Configurations

- Support monitoring many targets simultaneously (each with its own scheduler job)
- Allow per-target configuration overrides (packet type, interval, etc.)
- "Named Configurations" = saved config presets that can be applied to any target
- Store configurations in DB or a YAML/JSON config file

---

### 10. Route Change Detection

PingPlotter detects when the route to a target changes (hops appear/disappear/reorder).

```python
def detect_route_change(target_id: str, new_hops: list[dict]) -> bool:
    last_hops = get_last_known_route(target_id)
    if last_hops is None:
        return False
    last_ips = [h["ip"] for h in last_hops]
    new_ips = [h["ip"] for h in new_hops]
    return last_ips != new_ips
```

- Log route changes with timestamps
- Optionally trigger an alert on route change
- Display route change events as annotations on the timeline graph

---

### 11. DNS Resolution

- Perform reverse DNS lookup for each hop IP
- Cache DNS results (DNS names rarely change) — use TTL-aware cache
- Display `----------` when no PTR record exists
- Track Dynamic DNS changes (re-resolve periodically, log when IP changes for a hostname)

```python
import socket, functools

@functools.lru_cache(maxsize=512)
def reverse_dns(ip: str) -> str:
    try:
        return socket.gethostbyaddr(ip)[0]
    except socket.herror:
        return "----------"
```

---

### 12. Web UI Features Checklist

- [ ] Target input bar (hostname or IP, trace interval selector, start/stop button)
- [ ] Trace graph table (hops × columns, color-coded, focus selector)
- [ ] Timeline graph (scrollable, per-hop toggle, zoom controls)
- [ ] Summary dashboard (all targets, sortable table + sparklines)
- [ ] Alert management UI (create/edit/delete alerts, view alert history)
- [ ] Session browser (list, filter by date, export CSV/JSON)
- [ ] Route change log
- [ ] Live updates via WebSocket
- [ ] Dark/light mode (optional but recommended)

---

### 13. Privilege Considerations

- **ICMP raw sockets** require `CAP_NET_RAW` on Linux or root on macOS.
- **Workaround:** Use `subprocess` to call system `ping` with `-t <ttl>` flag — works without root.
- **UDP/TCP probes** require raw socket access — run the backend as root or use `setcap cap_net_raw+ep` on the Python binary.
- On Linux: `sudo setcap cap_net_raw+ep $(which python3)`

---

### 14. Running as a Service

```ini
# /etc/systemd/system/pingwatcher.service
[Unit]
Description=PingWatcher Network Monitor
After=network.target

[Service]
ExecStart=/usr/bin/python3 /opt/pingwatcher/main.py
Restart=always
User=pingwatcher

[Install]
WantedBy=multi-user.target
```

---

## Project Structure

```
pingwatcher/
├── main.py                  # FastAPI app entry point
├── engine/
│   ├── tracer.py            # Traceroute implementations (ICMP, UDP, TCP)
│   └── scheduler.py         # APScheduler jobs, continuous sampling
├── db/
│   ├── models.py            # SQLAlchemy models
│   └── queries.py           # Data access layer
├── api/
│   ├── targets.py           # Target CRUD + start/stop
│   ├── data.py              # Trace graph, timeline, summary endpoints
│   └── sessions.py          # Session browser + export
├── alerts/
│   ├── conditions.py        # Alert condition evaluator
│   └── actions/
│       ├── email.py
│       ├── webhook.py
│       ├── log_file.py
│       └── command.py
├── sessions/
│   └── export.py            # CSV/JSON export
├── frontend/                # React or HTMX frontend
│   ├── TraceGraph.jsx
│   ├── TimelineGraph.jsx
│   └── SummaryView.jsx
├── requirements.txt
└── CLAUDE.md                # This file
```

---

## Key Python Libraries

```
fastapi          # Web framework + WebSocket support
uvicorn          # ASGI server
apscheduler      # Periodic trace job scheduling
scapy            # Raw packet crafting (ICMP/UDP/TCP probes)
sqlalchemy       # ORM for SQLite/PostgreSQL
httpx            # Async HTTP (for webhook alert actions)
smtplib          # Email alerts (stdlib)
plotly           # Server-side graph image rendering (for alert snapshots)
pandas           # Time-series aggregation for export
```

---

## Implementation Notes & Gotchas

1. **Intermediate hop packet loss is often misleading.** Many routers deprioritize ICMP replies to TTL-exceeded packets. A hop showing 10% loss is not necessarily a problem if downstream hops show 0% loss. Document this in the UI.

2. **Asymmetric routes.** Traceroute only shows the outbound path. Return traffic may take a completely different route. Display a note when this is likely (clean hops with high final-hop latency).

3. **Timeout display.** Show `ERR` (not 0) for timed-out packets in the "Cur" column. Store as `NULL` in DB, not `0`.

4. **Focus period.** Avg and PL% must be calculated over the configured focus window (last N samples), not all-time. Make this configurable and clearly labeled.

5. **Avoid storing redundant data for stable hops.** For hops that never change IP, you only need to store RTT per sample, not IP/DNS repeatedly. Normalize accordingly.

6. **Rate limiting on probes.** Use a small inter-packet delay (default 25ms) between probes in a single trace to avoid saturating narrow links.

7. **Packet size.** Default 56 bytes for ICMP. Reduce to 40 bytes if testing TCP/port-80 paths, as some firewalls drop larger TCP probe packets.

8. **Route changes invalidate per-hop history.** When a route changes, mark the old route's timeline data as belonging to the previous route to avoid confusing latency jumps.
