"""API router for trace-graph data, timeline, summary, and route changes.

Endpoints
---------
* ``GET /api/targets/{id}/hops``           — per-hop stats (trace graph).
* ``GET /api/targets/{id}/timeline``       — time-series for timeline graph.
* ``GET /api/targets/{id}/route_changes``  — detected route changes.
* ``GET /api/summary``                     — final-hop summary for all targets.
"""

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from pingwatcher.config import get_settings
from pingwatcher.db.models import get_db
from pingwatcher.db.queries import (
    get_all_hop_stats,
    get_route_changes,
    get_summary,
    get_target,
    get_timeline_data,
)

router = APIRouter(tags=["data"])


@router.get("/api/targets/{target_id}/hops")
def api_hop_stats(
    target_id: str,
    focus: int = Query(default=None, ge=1, description="Focus window size (last N samples)"),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """Return per-hop statistics for the trace graph view.

    Args:
        target_id: UUID-style target identifier.
        focus: Number of recent samples to include.  Falls back to the
            global default when omitted.
        db: Injected database session.

    Returns:
        Ordered list of hop stat dictionaries.

    Raises:
        HTTPException: 404 if the target does not exist.
    """
    if get_target(db, target_id) is None:
        raise HTTPException(status_code=404, detail="Target not found")
    cfg = get_settings()
    n = focus if focus is not None else cfg.default_focus
    return get_all_hop_stats(db, target_id, focus_n=n)


@router.get("/api/targets/{target_id}/timeline")
def api_timeline(
    target_id: str,
    hop: str = Query(default="last", description="Hop number or 'last'"),
    start: Optional[str] = Query(default=None, description="ISO start timestamp"),
    end: Optional[str] = Query(default=None, description="ISO end timestamp"),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """Return time-series latency data for the timeline graph.

    Args:
        target_id: UUID-style target identifier.
        hop: ``"last"`` for the final hop, or a specific hop number.
        start: ISO 8601 lower-bound timestamp filter.
        end: ISO 8601 upper-bound timestamp filter.
        db: Injected database session.

    Returns:
        List of ``{timestamp, rtt_ms, is_timeout}`` dictionaries.

    Raises:
        HTTPException: 404 if the target does not exist.
    """
    if get_target(db, target_id) is None:
        raise HTTPException(status_code=404, detail="Target not found")

    start_dt = datetime.fromisoformat(start) if start else None
    end_dt = datetime.fromisoformat(end) if end else None
    return get_timeline_data(db, target_id, hop=hop, start=start_dt, end=end_dt)


@router.get("/api/targets/{target_id}/route_changes")
def api_route_changes(
    target_id: str,
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """List detected route changes for a target.

    Args:
        target_id: UUID-style target identifier.
        db: Injected database session.

    Returns:
        List of route-change event dictionaries.

    Raises:
        HTTPException: 404 if the target does not exist.
    """
    if get_target(db, target_id) is None:
        raise HTTPException(status_code=404, detail="Target not found")
    return get_route_changes(db, target_id)


@router.get("/api/summary")
def api_summary(
    focus: int = Query(default=None, ge=1, description="Focus window size"),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """Return final-hop stats for every active target (summary dashboard).

    Args:
        focus: Number of recent samples per target.
        db: Injected database session.

    Returns:
        List of summary dictionaries, one per active target.
    """
    cfg = get_settings()
    n = focus if focus is not None else cfg.default_focus
    return get_summary(db, focus_n=n)
