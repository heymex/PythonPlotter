"""APScheduler integration for continuous traceroute sampling.

Each monitored :class:`~pingwatcher.db.models.Target` gets its own
interval-triggered job.  Collected samples are persisted through the
query helpers and broadcast to connected WebSocket clients.
"""

import json
import logging
import socket
import threading
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler

from pingwatcher.config import get_settings
from pingwatcher.db.models import Sample, SessionLocal, Target
from pingwatcher.db.queries import (
    get_last_known_route,
    record_route_change,
    store_sample,
)
from pingwatcher.engine.tracer import icmp_traceroute

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler(daemon=True)

# In-memory dict of latest samples per target for WebSocket push.
# Keyed by target_id; value is the serialised hop list.
latest_results: dict[str, list[dict]] = {}

# WebSocket subscribers (managed by the main app module).
# Maps target_id → set of asyncio.Queue instances.
ws_subscribers: dict[str, set] = {}

# Number of consecutive DNS resolution failures before disabling
# monitoring for a target.
_MAX_DNS_FAILURES = 3
_dns_failures_by_target: dict[str, int] = {}
_target_run_locks: dict[str, threading.Lock] = {}


def _deactivate_target(target_id: str) -> None:
    """Mark a target inactive in the database."""
    db = SessionLocal()
    try:
        target = db.query(Target).filter(Target.id == target_id).first()
        if target and target.active:
            target.active = False
            db.commit()
    finally:
        db.close()


def _collect_sample(target_id: str, host: str, max_hops: int, timeout: float) -> None:
    """Run a single traceroute and persist results.

    This function is called by APScheduler inside a worker thread.

    Args:
        target_id: UUID-style target identifier.
        host: Hostname or IP to trace.
        max_hops: Maximum TTL.
        timeout: Per-probe timeout in seconds.
    """
    lock = _target_run_locks.setdefault(target_id, threading.Lock())
    if not lock.acquire(blocking=False):
        logger.debug(
            "Skipping sample for %s (%s): previous run still in progress",
            target_id,
            host,
        )
        return

    try:
        cfg = get_settings()
        try:
            hops = icmp_traceroute(
                host,
                max_hops=max_hops,
                timeout=timeout,
                inter_packet_delay=cfg.default_inter_packet_delay,
            )
        except socket.gaierror as exc:
            failures = _dns_failures_by_target.get(target_id, 0) + 1
            _dns_failures_by_target[target_id] = failures
            if failures >= _MAX_DNS_FAILURES:
                logger.error(
                    "Stopping monitoring %s (%s) after %d consecutive DNS failures: %s",
                    target_id,
                    host,
                    failures,
                    exc,
                )
                _deactivate_target(target_id)
                stop_monitoring(target_id)
            else:
                logger.warning(
                    "DNS resolution failed for target %s (%s) (%d/%d): %s",
                    target_id,
                    host,
                    failures,
                    _MAX_DNS_FAILURES,
                    exc,
                )
            return
        except Exception:
            logger.exception("Traceroute failed for target %s (%s)", target_id, host)
            return

        _dns_failures_by_target.pop(target_id, None)

        now = datetime.utcnow()
        db = SessionLocal()
        try:
            # Detect route changes before storing the new sample.
            old_route = get_last_known_route(db, target_id)
            new_ips = [h["ip"] for h in hops]

            if old_route is not None and old_route != new_ips:
                record_route_change(db, target_id, old_route, new_ips)
                logger.info(
                    "Route change detected for %s: %s → %s",
                    target_id,
                    old_route,
                    new_ips,
                )

            samples = [
                Sample(
                    target_id=target_id,
                    sampled_at=now,
                    hop_number=h["hop"],
                    ip=h["ip"],
                    dns=h["dns"],
                    rtt_ms=h["rtt_ms"],
                    is_timeout=h["is_timeout"],
                )
                for h in hops
            ]
            store_sample(db, samples)
        finally:
            db.close()

        # Cache latest result for WebSocket consumers.
        latest_results[target_id] = hops

        # Push to any connected WebSocket subscribers.
        _notify_subscribers(target_id, hops)
    finally:
        lock.release()


def _notify_subscribers(target_id: str, hops: list[dict]) -> None:
    """Enqueue the latest hop data for all WebSocket subscribers.

    Args:
        target_id: UUID-style target identifier.
        hops: List of hop dictionaries from the most recent trace.
    """
    queues = ws_subscribers.get(target_id, set())
    payload = json.dumps({"target_id": target_id, "hops": hops})
    dead: list = []
    for queue in queues:
        try:
            queue.put_nowait(payload)
        except Exception:
            dead.append(queue)
    for q in dead:
        queues.discard(q)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def start_monitoring(
    target_id: str,
    host: str,
    interval: float,
    max_hops: int = 30,
    timeout: float = 3.0,
) -> None:
    """Register an interval job to continuously trace *host*.

    If a job with the same *target_id* already exists it is replaced.

    Args:
        target_id: Unique identifier used as the APScheduler job ID.
        host: Hostname or IP to trace.
        interval: Seconds between successive traces.
        max_hops: Maximum TTL per trace.
        timeout: Per-probe timeout in seconds.
    """
    scheduler.add_job(
        func=_collect_sample,
        trigger="interval",
        seconds=interval,
        args=[target_id, host, max_hops, timeout],
        id=target_id,
        coalesce=True,
        max_instances=32,
        misfire_grace_time=1,
        replace_existing=True,
    )
    logger.info("Started monitoring %s (%s) every %.1fs", target_id, host, interval)


def stop_monitoring(target_id: str) -> None:
    """Remove the scheduled trace job for *target_id*.

    No-op if the job does not exist.

    Args:
        target_id: The APScheduler job ID (same as the target ID).
    """
    try:
        scheduler.remove_job(target_id)
        logger.info("Stopped monitoring %s", target_id)
    except Exception:
        logger.debug("No active job for %s", target_id)

    latest_results.pop(target_id, None)
    _dns_failures_by_target.pop(target_id, None)
    _target_run_locks.pop(target_id, None)


def start_scheduler() -> None:
    """Start the APScheduler background thread.

    Safe to call multiple times — will not restart an already-running
    scheduler.
    """
    if not scheduler.running:
        scheduler.start()
        logger.info("Scheduler started")


def shutdown_scheduler() -> None:
    """Gracefully shut down the APScheduler background thread."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
