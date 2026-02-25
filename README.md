# PingWatcher

A PingPlotter-inspired network monitoring web application built with Python and FastAPI. PingWatcher continuously traces routes to target hosts, records per-hop latency and packet loss, and presents the data through an interactive browser UI with real-time WebSocket updates.

![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
![License: Unlicense](https://img.shields.io/badge/license-Unlicense-green)
![Tests: 104](https://img.shields.io/badge/tests-104%20passing-brightgreen)

---

## Features

- **Continuous traceroute** — ICMP ping-per-hop probes at configurable intervals (default 2.5 s), with a system `traceroute` fallback.
- **Trace Graph** — per-hop table with Avg, Min, Current RTT, packet-loss %, and colour-coded latency bars.
- **Timeline Graph** — scrollable Plotly.js time-series chart of latency and loss for any hop.
- **Summary Dashboard** — all-targets overview with final-hop stats at a glance.
- **Route Change Detection** — automatic logging when the hop path to a target changes.
- **Alert System** — threshold-based rules with consecutive-sample confirmation. Pluggable actions: email (SMTP), webhook, log file, shell command.
- **Session Management** — bookmark named time ranges and export data as CSV or JSON.
- **Live Updates** — WebSocket push to the browser on every new sample.
- **Dark / Light Theme** — toggle in the header; preference is remembered.
- **Cross-Platform** — runs on macOS and Linux without root (uses system `ping` with per-hop TTL).

---

## Quick Start

```bash
git clone <repo-url> && cd PythonPlotter

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python -m pingwatcher.main
```

Open **http://localhost:8000** in a browser, enter a hostname (e.g. `8.8.8.8`), and click **Start Monitoring**.

---

## Project Structure

```
PythonPlotter/
├── pingwatcher/                   # Main application package
│   ├── __init__.py                # Package metadata (version)
│   ├── config.py                  # Pydantic Settings (env + .env file)
│   ├── main.py                    # FastAPI app, lifespan, WebSocket, CLI
│   │
│   ├── engine/                    # Packet engine
│   │   ├── tracer.py              # ICMP traceroute (ping-per-hop + system fallback)
│   │   ├── dns.py                 # Reverse-DNS with LRU cache
│   │   └── scheduler.py           # APScheduler continuous sampling + WS notify
│   │
│   ├── db/                        # Database layer
│   │   ├── models.py              # 6 SQLAlchemy ORM tables + engine/session factory
│   │   └── queries.py             # Data access: hop stats, timeline, summary, etc.
│   │
│   ├── api/                       # REST API routers
│   │   ├── targets.py             # CRUD + start/stop monitoring
│   │   ├── data.py                # Hops, timeline, summary, route changes
│   │   └── sessions.py            # Session CRUD + CSV/JSON export
│   │
│   ├── alerts/                    # Alert system
│   │   ├── conditions.py          # Condition evaluator + state machine
│   │   └── actions/               # Pluggable action dispatch
│   │       ├── __init__.py        # dispatch_action() router
│   │       ├── email_action.py    # SMTP email
│   │       ├── webhook.py         # HTTP POST webhook
│   │       ├── log_file.py        # File-based alert log
│   │       └── command.py         # Shell command execution
│   │
│   ├── sessions/
│   │   └── export.py              # CSV + JSON export functions
│   │
│   └── frontend/                  # Single-page web UI
│       ├── index.html             # App shell
│       └── static/
│           ├── css/style.css      # Dark/light theme CSS
│           └── js/
│               ├── app.js         # Bootstrap, routing, WebSocket
│               ├── trace-graph.js # Hop stats table
│               ├── timeline-graph.js  # Plotly.js timeline chart
│               └── summary-view.js    # Multi-target summary
│
├── tests/                         # 104 unit tests (pytest)
│   ├── conftest.py                # In-memory SQLite fixtures (StaticPool)
│   ├── test_config.py
│   ├── test_models.py
│   ├── test_queries.py
│   ├── test_tracer.py
│   ├── test_dns.py
│   ├── test_scheduler.py
│   ├── test_api_targets.py
│   ├── test_api_data.py
│   ├── test_sessions.py
│   └── test_alerts.py
│
├── requirements.txt               # Production dependencies
├── requirements-dev.txt           # Dev/test dependencies
├── CLAUDE.md                      # AI development specification
├── LICENSE                        # Unlicense (public domain)
└── .gitignore
```

---

## Configuration

All settings are driven by environment variables (prefixed `PINGWATCHER_`) or a `.env` file. Defaults are sensible for local use.

| Variable | Default | Description |
|---|---|---|
| `PINGWATCHER_DATABASE_URL` | `sqlite:///pingwatcher.db` | SQLAlchemy connection string |
| `PINGWATCHER_DEFAULT_TRACE_INTERVAL` | `2.5` | Seconds between samples for new targets |
| `PINGWATCHER_DEFAULT_PACKET_TYPE` | `icmp` | Probe protocol (`icmp`, `udp`, `tcp`) |
| `PINGWATCHER_DEFAULT_PACKET_SIZE` | `56` | ICMP payload size in bytes |
| `PINGWATCHER_DEFAULT_MAX_HOPS` | `30` | Maximum TTL |
| `PINGWATCHER_DEFAULT_TIMEOUT` | `3.0` | Per-probe timeout in seconds |
| `PINGWATCHER_DEFAULT_INTER_PACKET_DELAY` | `0.025` | Delay between TTL probes (seconds) |
| `PINGWATCHER_DEFAULT_FOCUS` | `10` | Recent-sample window for stats |
| `PINGWATCHER_LOG_LEVEL` | `INFO` | Python logging level |
| `PINGWATCHER_HOST` | `0.0.0.0` | Uvicorn bind address |
| `PINGWATCHER_PORT` | `8000` | Uvicorn bind port |

Example `.env` file:

```bash
PINGWATCHER_DATABASE_URL=sqlite:///pingwatcher.db
PINGWATCHER_DEFAULT_TRACE_INTERVAL=5.0
PINGWATCHER_LOG_LEVEL=DEBUG
PINGWATCHER_PORT=9000
```

---

## API Reference

Interactive Swagger UI is available at **http://localhost:8000/docs** when the server is running.

### Targets

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/targets` | List all targets |
| `POST` | `/api/targets` | Create a target and start monitoring |
| `GET` | `/api/targets/{id}` | Get a single target |
| `DELETE` | `/api/targets/{id}` | Stop monitoring and delete target |

### Data

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/targets/{id}/hops?focus=N` | Per-hop stats for the trace graph |
| `GET` | `/api/targets/{id}/timeline?hop=last&start=...&end=...` | Time-series data for the timeline |
| `GET` | `/api/targets/{id}/route_changes` | Route change event log |
| `GET` | `/api/summary?focus=N` | Final-hop stats for all active targets |

### Sessions

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/targets/{id}/sessions` | List saved sessions |
| `POST` | `/api/targets/{id}/sessions` | Create a named session bookmark |
| `POST` | `/api/targets/{id}/sessions/export` | Export data as CSV or JSON |

### WebSocket

| Endpoint | Description |
|---|---|
| `ws://host:port/ws/targets/{id}` | Live hop data pushed on each sample |

---

## Frontend Views

### Trace Graph

Per-hop statistics table, refreshed every sample interval. Columns: hop number, IP, reverse DNS, average RTT, minimum RTT, current RTT, packet loss %, and a colour-coded latency bar.

A configurable **Focus** selector controls how many recent samples are used to compute statistics (10, 25, 50, or 100).

> **Note:** Intermediate hop packet loss is often misleading — many routers deprioritise ICMP Time Exceeded replies. Always check the final hop for the true picture.

### Timeline

Scrollable Plotly.js bar chart showing per-hop latency over time. Bars are colour-coded green (normal), yellow (elevated), red (packet loss / timeout). Supports pan, zoom, and per-hop selection.

### Summary

Multi-target dashboard showing final-hop stats for every active target in one table. Auto-refreshes with each sample interval.

---

## Alert System

Alerts fire when a metric breaches a threshold for a configurable number of consecutive samples.

**Supported metrics:**
- `packet_loss_pct` — packet loss percentage
- `avg_rtt_ms` — average round-trip time
- `cur_rtt_ms` — most recent RTT

**Operators:** `>`, `<`, `>=`, `<=`

**Hop selectors:** `any`, `final`, or a specific IP address.

**Action plugins:**

| Action | Description |
|---|---|
| `email` | Send via SMTP (config: `smtp_host`, `smtp_port`, `from`, `to`, `subject`) |
| `webhook` | HTTP POST to a URL (config: `url`, optional `headers`) |
| `log` | Append to a log file (config: `path`) |
| `command` | Execute a shell command (config: `cmd`) |

Alerts automatically track consecutive triggers and log recovery when conditions clear.

---

## Development

### Install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

### Run the server (with hot reload)

```bash
uvicorn pingwatcher.main:app --reload --host 0.0.0.0 --port 8000
```

### Run tests

```bash
python -m pytest tests/ -v
```

Tests use an **in-memory SQLite** database with `StaticPool` so they run fast and in isolation.

### Format and lint

```bash
black pingwatcher/ tests/
mypy pingwatcher/
```

---

## Data Model

Six SQLAlchemy ORM tables:

| Table | Purpose |
|---|---|
| `targets` | Monitored hosts with per-target probe settings |
| `samples` | One row per hop per traceroute run (time-series) |
| `route_changes` | Detected changes in the hop sequence to a target |
| `alerts` | User-defined threshold alert rules |
| `alert_history` | Audit trail of fired / resolved alert events |
| `sessions` | Named time-range bookmarks for export and replay |

The `samples` table is indexed on `(target_id, sampled_at)` and `(target_id, hop_number)` for efficient time-range and per-hop queries. Cascading deletes ensure that removing a target cleans up all associated data.

---

## Technology Stack

| Layer | Technology |
|---|---|
| Web framework | FastAPI |
| ASGI server | Uvicorn |
| ORM | SQLAlchemy 2.0 |
| Database | SQLite (default), PostgreSQL-ready |
| Scheduler | APScheduler |
| Config | Pydantic Settings |
| Charts | Plotly.js |
| Live updates | WebSocket (native browser + FastAPI) |
| Testing | pytest, pytest-cov, pytest-asyncio |
| Formatting | Black |
| Type checking | mypy |

---

## Privilege Notes

- **ICMP probes** use the system `ping` binary via `subprocess` — no root required on macOS or most Linux distributions.
- On macOS, TTL is set with `ping -m <ttl>`; on Linux, with `ping -t <ttl>`.
- **UDP/TCP probes** would require raw socket access (`CAP_NET_RAW` on Linux). These are noted in the spec but not yet implemented.
- A `system_traceroute` fallback shells out to the `traceroute` binary for platforms where per-hop ping parsing is unreliable.

---

## Running as a Service

```ini
# /etc/systemd/system/pingwatcher.service
[Unit]
Description=PingWatcher Network Monitor
After=network.target

[Service]
ExecStart=/opt/pingwatcher/.venv/bin/python -m pingwatcher.main
WorkingDirectory=/opt/pingwatcher
Restart=always
User=pingwatcher
Environment=PINGWATCHER_DATABASE_URL=sqlite:////opt/pingwatcher/data/pingwatcher.db

[Install]
WantedBy=multi-user.target
```

```bash
sudo cp pingwatcher.service /etc/systemd/system/
sudo systemctl enable --now pingwatcher
```

---

## License

This project is released under the [Unlicense](LICENSE) — public domain.
