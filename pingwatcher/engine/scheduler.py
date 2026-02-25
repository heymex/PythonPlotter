"""APScheduler integration for continuous traceroute sampling."""

import json
import logging
import socket
import threading
from datetime import datetime
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from pingwatcher.alerts.conditions import evaluate_alerts
from pingwatcher.config import get_settings
from pingwatcher.db.models import Sample, SessionLocal, Target
from pingwatcher.db.queries import (
    aggregate_hourly_rollups,
    backfill_dns_for_ip,
    delete_raw_samples_older_than,
    get_all_hop_stats,
    get_last_known_route,
    get_target_summary,
    record_route_change,
    store_sample,
)
from pingwatcher.engine.dns import NO_PTR, reverse_dns
from pingwatcher.engine.tracer import (
    icmp_traceroute,
    scapy_icmp_traceroute,
    system_traceroute,
)

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()

# In-memory dict of latest raw hop list per target for WebSocket push.
# Keyed by target_id; value is the hop list from the most recent trace.
latest_results: dict[str, list[dict]] = {}

# In-memory cache of computed hop statistics for the default focus window.
# Updated after every successful sample; used by the /hops API endpoint to
# avoid re-querying the database on every request.
latest_hop_stats: dict[str, list[dict]] = {}

# WebSocket subscribers (managed by the main app module).
# Maps target_id → set of asyncio.Queue instances.
ws_subscribers: dict[str, set] = {}
ws_summary_subscribers: set = set()

# Number of consecutive DNS resolution failures before disabling
# monitoring for a target.
_MAX_DNS_FAILURES = 3
_dns_failures_by_target: dict[str, int] = {}
_target_run_locks: dict[str, threading.Lock] = {}

# In-memory route cache: last known hop-IP list per target.
# Avoids two DB queries per sample for route-change detection.
# _route_cache_initialized tracks which targets have been seeded from DB
# (needed to handle server restarts without missing the first-sample check).
_last_known_routes: dict[str, list] = {}
_route_cache_initialized: set[str] = set()
_dns_pending_ips: set[str] = set()

_DNS_JOB_ID = "__dns_enrichment__"
_MAINTENANCE_JOB_ID = "__maintenance__"


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


def _queue_dns_enrichment(hops: list[dict]) -> None:
    """Queue unresolved hop IPs for async DNS backfill."""
    for hop in hops:
        ip = hop.get("ip")
        dns = hop.get("dns")
        if ip and not dns:
            _dns_pending_ips.add(ip)


def _select_probe_engine(host: str, max_hops: int, timeout: float) -> list[dict]:
    """Run traceroute using configured probe-engine strategy."""
    cfg = get_settings()
    mode = str(getattr(cfg, "probe_engine", "auto") or "auto").lower()

    if mode in {"auto", "scapy"} and cfg.scapy_enabled:
        try:
            return scapy_icmp_traceroute(
                host,
                max_hops=max_hops,
                timeout=timeout,
                resolve_dns_name=False,
            )
        except Exception as exc:
            if mode == "scapy":
                logger.error("Scapy traceroute failed for %s: %s", host, exc)
                return []
            logger.debug("Scapy unavailable for %s; falling back: %s", host, exc)

    # Fast one-shot subprocess fallback when Scapy is unavailable.
    hops = system_traceroute(
        host,
        max_hops=max_hops,
        timeout=timeout,
        resolve_dns_name=False,
    )
    if hops:
        return hops

    return icmp_traceroute(
        host,
        max_hops=max_hops,
        timeout=timeout,
        inter_packet_delay=cfg.default_inter_packet_delay,
        resolve_dns_name=False,
    )


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
            hops = _select_probe_engine(host, max_hops=max_hops, timeout=timeout)
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
        all_stats = None
        summary_row = None
        try:
            # --- Route-change detection (cached; DB only on first run) ---
            new_ips = [h["ip"] for h in hops]
            if target_id not in _route_cache_initialized:
                # First sample since startup — seed cache from DB so we
                # don't lose a real route change that occurred while down.
                old_route = get_last_known_route(db, target_id)
                _route_cache_initialized.add(target_id)
            else:
                old_route = _last_known_routes.get(target_id)

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
            _queue_dns_enrichment(hops)

            # Update route cache after the new sample is stored.
            _last_known_routes[target_id] = new_ips

            # Compute and cache hop statistics for the default focus window.
            # This lets the /hops API endpoint skip the DB entirely on the
            # common path (default focus, active target).
            try:
                all_stats = get_all_hop_stats(db, target_id, focus_n=cfg.default_focus)
                latest_hop_stats[target_id] = all_stats
            except Exception:
                logger.exception("Failed to cache hop stats for %s", target_id)

            # Evaluate threshold alerts, reusing the stats we just computed
            # so the alert engine does not issue its own DB queries.
            try:
                evaluate_alerts(
                    db,
                    target_id,
                    focus_n=cfg.default_focus,
                    all_stats=all_stats,
                )
            except Exception:
                logger.exception("Alert evaluation failed for %s", target_id)

            if cfg.enable_ws_summary_push:
                try:
                    summary_row = get_target_summary(db, target_id, focus_n=cfg.default_focus)
                except Exception:
                    logger.exception("Failed to build summary row for %s", target_id)
        finally:
            db.close()

        # Cache latest raw hops for WebSocket consumers.
        latest_results[target_id] = hops

        # Push to any connected WebSocket subscribers.
        _notify_subscribers(
            target_id,
            hops,
            sampled_at=now.isoformat(),
            hop_stats=all_stats,
            summary_row=summary_row,
        )
    finally:
        lock.release()


def _notify_subscribers(
    target_id: str,
    hops: list[dict],
    sampled_at: Optional[str] = None,
    hop_stats: Optional[list[dict]] = None,
    summary_row: Optional[dict] = None,
) -> None:
    """Enqueue the latest hop data for all WebSocket subscribers.

    Args:
        target_id: UUID-style target identifier.
        hops: List of hop dictionaries from the most recent trace.
    """
    queues = ws_subscribers.get(target_id, set())
    payload_data = {"type": "target_sample", "target_id": target_id, "hops": hops}
    if sampled_at is not None:
        payload_data["sampled_at"] = sampled_at
    if hop_stats is not None:
        payload_data["hop_stats"] = hop_stats
    if summary_row is not None:
        payload_data["summary_row"] = summary_row
    payload = json.dumps(payload_data)
    dead: list = []
    for queue in queues:
        try:
            queue.put_nowait(payload)
        except Exception:
            dead.append(queue)
    for q in dead:
        queues.discard(q)

    # Broadcast summary deltas to subscribers of the summary feed.
    if summary_row is not None:
        summary_payload = json.dumps(
            {
                "type": "summary_update",
                "target_id": target_id,
                "summary_row": summary_row,
                "sampled_at": sampled_at,
            }
        )
        dead_summary = []
        for queue in ws_summary_subscribers:
            try:
                queue.put_nowait(summary_payload)
            except Exception:
                dead_summary.append(queue)
        for q in dead_summary:
            ws_summary_subscribers.discard(q)


def _process_dns_enrichment() -> None:
    """Resolve queued IPs and backfill DNS names asynchronously."""
    cfg = get_settings()
    if not cfg.enable_dns_enrichment_worker or not _dns_pending_ips:
        return

    batch: list[str] = []
    for ip in list(_dns_pending_ips):
        batch.append(ip)
        _dns_pending_ips.discard(ip)
        if len(batch) >= cfg.dns_enrichment_batch_size:
            break
    if not batch:
        return

    resolved: dict[str, str] = {}
    for ip in batch:
        name = reverse_dns(ip)
        if name and name != NO_PTR:
            resolved[ip] = name

    if not resolved:
        return

    db = SessionLocal()
    try:
        for ip, dns_name in resolved.items():
            backfill_dns_for_ip(db, ip=ip, dns_name=dns_name, limit=5000)
        db.commit()
    finally:
        db.close()


def _run_maintenance() -> None:
    """Roll up and prune historical sample data."""
    cfg = get_settings()
    db = SessionLocal()
    try:
        if cfg.enable_rollups:
            aggregate_hourly_rollups(db, older_than_hours=cfg.rollup_after_hours)
        if cfg.raw_retention_days > 0:
            delete_raw_samples_older_than(db, days=cfg.raw_retention_days)
    finally:
        db.close()


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
    latest_hop_stats.pop(target_id, None)
    _dns_failures_by_target.pop(target_id, None)
    _target_run_locks.pop(target_id, None)
    _last_known_routes.pop(target_id, None)
    _route_cache_initialized.discard(target_id)


def start_scheduler() -> None:
    """Start the APScheduler background thread.

    Safe to call multiple times — will not restart an already-running
    scheduler.
    """
    if scheduler.running:
        return
    scheduler.start()
    cfg = get_settings()
    if cfg.enable_dns_enrichment_worker:
        scheduler.add_job(
            func=_process_dns_enrichment,
            trigger="interval",
            seconds=max(0.25, cfg.dns_enrichment_tick_seconds),
            id=_DNS_JOB_ID,
            coalesce=True,
            max_instances=1,
            replace_existing=True,
        )
    scheduler.add_job(
        func=_run_maintenance,
        trigger="interval",
        minutes=max(1, cfg.maintenance_interval_minutes),
        id=_MAINTENANCE_JOB_ID,
        coalesce=True,
        max_instances=1,
        replace_existing=True,
    )
    logger.info("Scheduler started")


def shutdown_scheduler() -> None:
    """Gracefully shut down the APScheduler background thread."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
