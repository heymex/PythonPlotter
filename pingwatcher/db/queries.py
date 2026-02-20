"""Data-access helpers for targets, samples, and computed statistics.

All functions accept an explicit :class:`sqlalchemy.orm.Session` so the
caller controls transaction scope (typically injected via FastAPI's
``Depends(get_db)``).
"""

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import desc, distinct, func
from sqlalchemy.orm import Session

from pingwatcher.db.models import (
    Alert,
    AlertHistory,
    RouteChange,
    Sample,
    Target,
)


# ---------------------------------------------------------------------------
# Target helpers
# ---------------------------------------------------------------------------


def list_targets(db: Session) -> list[Target]:
    """Return every :class:`Target` ordered by creation date.

    Args:
        db: Active database session.

    Returns:
        List of :class:`Target` rows, newest first.
    """
    return db.query(Target).order_by(desc(Target.created_at)).all()


def get_target(db: Session, target_id: str) -> Optional[Target]:
    """Fetch a single :class:`Target` by primary key.

    Args:
        db: Active database session.
        target_id: UUID-style target identifier.

    Returns:
        The :class:`Target` row, or ``None`` if not found.
    """
    return db.query(Target).filter(Target.id == target_id).first()


def create_target(db: Session, target: Target) -> Target:
    """Persist a new :class:`Target` and flush to obtain defaults.

    Args:
        db: Active database session.
        target: Populated :class:`Target` instance (not yet added).

    Returns:
        The same instance after being flushed to the database.
    """
    db.add(target)
    db.commit()
    db.refresh(target)
    return target


def delete_target(db: Session, target_id: str) -> bool:
    """Remove a :class:`Target` and all cascade-linked rows.

    Args:
        db: Active database session.
        target_id: UUID-style target identifier.

    Returns:
        ``True`` if the target existed and was deleted, ``False``
        otherwise.
    """
    target = get_target(db, target_id)
    if target is None:
        return False
    db.delete(target)
    db.commit()
    return True


# ---------------------------------------------------------------------------
# Sample helpers
# ---------------------------------------------------------------------------


def store_sample(db: Session, samples: list[Sample]) -> None:
    """Bulk-insert a list of :class:`Sample` rows for one trace run.

    Args:
        db: Active database session.
        samples: Pre-populated sample rows (one per hop).
    """
    db.add_all(samples)
    db.commit()


def get_hop_stats(
    db: Session,
    target_id: str,
    hop_number: int,
    focus_n: int = 10,
) -> dict[str, Any]:
    """Compute aggregated statistics for the last *focus_n* samples of a hop.

    This replicates PingPlotter's *Focus* window.

    Args:
        db: Active database session.
        target_id: UUID-style target identifier.
        hop_number: 1-based hop / TTL position.
        focus_n: Number of recent samples to include.

    Returns:
        Dictionary with keys ``avg_ms``, ``min_ms``, ``max_ms``,
        ``cur_ms``, ``packet_loss_pct``, ``ip``, and ``dns``.
    """
    rows = (
        db.query(Sample)
        .filter(Sample.target_id == target_id, Sample.hop_number == hop_number)
        .order_by(desc(Sample.sampled_at))
        .limit(focus_n)
        .all()
    )

    if not rows:
        return {
            "hop": hop_number,
            "ip": None,
            "dns": None,
            "avg_ms": None,
            "min_ms": None,
            "max_ms": None,
            "cur_ms": None,
            "packet_loss_pct": 0.0,
        }

    valid_rtts = [r.rtt_ms for r in rows if not r.is_timeout and r.rtt_ms is not None]
    total = len(rows)
    lost = sum(1 for r in rows if r.is_timeout)

    # Use the most recent row for IP / DNS display.
    latest = rows[0]

    return {
        "hop": hop_number,
        "ip": latest.ip,
        "dns": latest.dns,
        "avg_ms": round(sum(valid_rtts) / len(valid_rtts), 2) if valid_rtts else None,
        "min_ms": round(min(valid_rtts), 2) if valid_rtts else None,
        "max_ms": round(max(valid_rtts), 2) if valid_rtts else None,
        "cur_ms": round(valid_rtts[0], 2) if valid_rtts else None,
        "packet_loss_pct": round(lost / total * 100, 1) if total else 0.0,
    }


def get_all_hop_stats(
    db: Session,
    target_id: str,
    focus_n: int = 10,
) -> list[dict[str, Any]]:
    """Return :func:`get_hop_stats` for every hop seen on *target_id*.

    Args:
        db: Active database session.
        target_id: UUID-style target identifier.
        focus_n: Number of recent samples per hop.

    Returns:
        List of per-hop stat dictionaries sorted by hop number.
    """
    hop_numbers = (
        db.query(distinct(Sample.hop_number))
        .filter(Sample.target_id == target_id)
        .order_by(Sample.hop_number)
        .all()
    )
    return [get_hop_stats(db, target_id, h[0], focus_n) for h in hop_numbers]


def get_timeline_data(
    db: Session,
    target_id: str,
    hop: str = "last",
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    """Retrieve time-series latency data for the timeline graph.

    Args:
        db: Active database session.
        target_id: UUID-style target identifier.
        hop: ``"last"`` for the final hop, or a stringified hop number.
        start: Optional lower bound on ``sampled_at``.
        end: Optional upper bound on ``sampled_at``.

    Returns:
        List of dictionaries with ``timestamp``, ``rtt_ms``, and
        ``is_timeout`` suitable for Plotly consumption.
    """
    if hop == "last":
        max_hop_row = (
            db.query(func.max(Sample.hop_number))
            .filter(Sample.target_id == target_id)
            .scalar()
        )
        if max_hop_row is None:
            return []
        hop_number = max_hop_row
    else:
        hop_number = int(hop)

    query = db.query(Sample).filter(
        Sample.target_id == target_id,
        Sample.hop_number == hop_number,
    )
    if start:
        query = query.filter(Sample.sampled_at >= start)
    if end:
        query = query.filter(Sample.sampled_at <= end)

    rows = query.order_by(Sample.sampled_at).all()

    return [
        {
            "timestamp": r.sampled_at.isoformat() if r.sampled_at else None,
            "rtt_ms": r.rtt_ms,
            "is_timeout": r.is_timeout,
        }
        for r in rows
    ]


def get_summary(db: Session, focus_n: int = 10) -> list[dict[str, Any]]:
    """Build a summary row for every active target (final-hop stats).

    Args:
        db: Active database session.
        focus_n: Number of recent samples used for stats.

    Returns:
        List of dictionaries with target metadata plus final-hop
        statistics.
    """
    targets = db.query(Target).filter(Target.active.is_(True)).all()
    summaries: list[dict[str, Any]] = []

    for t in targets:
        max_hop = (
            db.query(func.max(Sample.hop_number))
            .filter(Sample.target_id == t.id)
            .scalar()
        )
        if max_hop is None:
            stats: dict[str, Any] = {
                "avg_ms": None,
                "min_ms": None,
                "max_ms": None,
                "cur_ms": None,
                "packet_loss_pct": 0.0,
            }
        else:
            stats = get_hop_stats(db, t.id, max_hop, focus_n)

        summaries.append(
            {
                "target_id": t.id,
                "host": t.host,
                "label": t.label,
                "active": t.active,
                **{k: v for k, v in stats.items() if k != "hop"},
            }
        )

    return summaries


# ---------------------------------------------------------------------------
# Route-change helpers
# ---------------------------------------------------------------------------


def get_last_known_route(db: Session, target_id: str) -> Optional[list[str]]:
    """Return the IP list of the most recently stored route for *target_id*.

    Args:
        db: Active database session.
        target_id: UUID-style target identifier.

    Returns:
        Ordered list of hop IPs, or ``None`` if no samples exist yet.
    """
    latest_time = (
        db.query(func.max(Sample.sampled_at))
        .filter(Sample.target_id == target_id)
        .scalar()
    )
    if latest_time is None:
        return None

    rows = (
        db.query(Sample)
        .filter(Sample.target_id == target_id, Sample.sampled_at == latest_time)
        .order_by(Sample.hop_number)
        .all()
    )
    return [r.ip for r in rows]


def record_route_change(
    db: Session,
    target_id: str,
    old_route: list[str],
    new_route: list[str],
) -> RouteChange:
    """Persist a :class:`RouteChange` event.

    Args:
        db: Active database session.
        target_id: UUID-style target identifier.
        old_route: Previous ordered list of hop IPs.
        new_route: Current ordered list of hop IPs.

    Returns:
        The newly created :class:`RouteChange` row.
    """
    change = RouteChange(
        target_id=target_id,
        detected_at=datetime.utcnow(),
        old_route=",".join(str(ip) for ip in old_route),
        new_route=",".join(str(ip) for ip in new_route),
    )
    db.add(change)
    db.commit()
    db.refresh(change)
    return change


def get_route_changes(db: Session, target_id: str) -> list[dict[str, Any]]:
    """Fetch all route-change events for a target, newest first.

    Args:
        db: Active database session.
        target_id: UUID-style target identifier.

    Returns:
        List of serialisable route-change dictionaries.
    """
    rows = (
        db.query(RouteChange)
        .filter(RouteChange.target_id == target_id)
        .order_by(desc(RouteChange.detected_at))
        .all()
    )
    return [
        {
            "id": r.id,
            "detected_at": r.detected_at.isoformat() if r.detected_at else None,
            "old_route": r.old_route.split(",") if r.old_route else [],
            "new_route": r.new_route.split(",") if r.new_route else [],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Alert helpers
# ---------------------------------------------------------------------------


def get_active_alerts(db: Session, target_id: str) -> list[Alert]:
    """Return enabled :class:`Alert` rules for the given target.

    Args:
        db: Active database session.
        target_id: UUID-style target identifier.

    Returns:
        List of enabled :class:`Alert` rows.
    """
    return (
        db.query(Alert)
        .filter(Alert.target_id == target_id, Alert.enabled.is_(True))
        .all()
    )


def record_alert_event(
    db: Session,
    alert: Alert,
    metric_value: float,
    message: str,
) -> AlertHistory:
    """Write an :class:`AlertHistory` row when an alert fires.

    Args:
        db: Active database session.
        alert: The :class:`Alert` that fired.
        metric_value: The reading that breached the threshold.
        message: Human-readable event description.

    Returns:
        The newly created :class:`AlertHistory` row.
    """
    event = AlertHistory(
        alert_id=alert.id,
        target_id=alert.target_id,
        triggered_at=datetime.utcnow(),
        metric_value=metric_value,
        message=message,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event
