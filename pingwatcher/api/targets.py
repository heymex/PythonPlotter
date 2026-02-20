"""API router for target CRUD and monitoring lifecycle.

Endpoints
---------
* ``GET  /api/targets``       — list all targets.
* ``POST /api/targets``       — create a target and start monitoring.
* ``GET  /api/targets/{id}``  — fetch a single target.
* ``DELETE /api/targets/{id}`` — stop monitoring and remove a target.
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from pingwatcher.config import get_settings
from pingwatcher.db.models import Target, get_db
from pingwatcher.db.queries import (
    create_target,
    delete_target,
    get_target,
    list_targets,
)
from pingwatcher.engine.scheduler import start_monitoring, stop_monitoring

router = APIRouter(prefix="/api/targets", tags=["targets"])


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class TargetCreate(BaseModel):
    """Schema for creating a new monitoring target.

    Attributes:
        host: Hostname or IP address to monitor.
        label: Optional human-friendly label.
        trace_interval: Seconds between samples.
        packet_type: Probe protocol (``icmp``, ``udp``, ``tcp``).
        packet_size: Payload size in bytes.
        max_hops: Maximum TTL.
        timeout: Per-probe timeout in seconds.
    """

    host: str
    label: Optional[str] = None
    trace_interval: Optional[float] = None
    packet_type: Optional[str] = None
    packet_size: Optional[int] = None
    max_hops: Optional[int] = None
    timeout: Optional[float] = None


class TargetResponse(BaseModel):
    """Serialised target returned by the API.

    Attributes:
        id: Unique target identifier.
        host: Monitored hostname or IP.
        label: Display label.
        trace_interval: Sampling interval in seconds.
        packet_type: Probe protocol.
        packet_size: Payload size.
        max_hops: Maximum TTL.
        timeout: Probe timeout.
        active: Whether monitoring is running.
        created_at: ISO-formatted creation timestamp.
    """

    id: str
    host: str
    label: Optional[str] = None
    trace_interval: float
    packet_type: str
    packet_size: int
    max_hops: int
    timeout: float
    active: bool
    created_at: Optional[str] = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=list[TargetResponse])
def api_list_targets(db: Session = Depends(get_db)):
    """Return all monitored targets, newest first."""
    targets = list_targets(db)
    return [_serialize_target(t) for t in targets]


@router.post("", response_model=TargetResponse, status_code=201)
def api_create_target(body: TargetCreate, db: Session = Depends(get_db)):
    """Create a new target and immediately begin monitoring it."""
    cfg = get_settings()
    target = Target(
        id=str(uuid.uuid4()),
        host=body.host,
        label=body.label,
        trace_interval=body.trace_interval or cfg.default_trace_interval,
        packet_type=body.packet_type or cfg.default_packet_type,
        packet_size=body.packet_size or cfg.default_packet_size,
        max_hops=body.max_hops or cfg.default_max_hops,
        timeout=body.timeout or cfg.default_timeout,
        active=True,
    )
    target = create_target(db, target)

    start_monitoring(
        target_id=target.id,
        host=target.host,
        interval=target.trace_interval,
        max_hops=target.max_hops,
        timeout=target.timeout,
    )

    return _serialize_target(target)


@router.get("/{target_id}", response_model=TargetResponse)
def api_get_target(target_id: str, db: Session = Depends(get_db)):
    """Fetch a single target by its ID."""
    target = get_target(db, target_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Target not found")
    return _serialize_target(target)


@router.delete("/{target_id}", status_code=204)
def api_delete_target(target_id: str, db: Session = Depends(get_db)):
    """Stop monitoring and remove a target (cascades to samples, alerts, etc.)."""
    stop_monitoring(target_id)
    if not delete_target(db, target_id):
        raise HTTPException(status_code=404, detail="Target not found")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _serialize_target(target: Target) -> TargetResponse:
    """Convert an ORM :class:`Target` into a response model.

    Args:
        target: The ORM instance.

    Returns:
        A :class:`TargetResponse` Pydantic model.
    """
    return TargetResponse(
        id=target.id,
        host=target.host,
        label=target.label,
        trace_interval=target.trace_interval,
        packet_type=target.packet_type,
        packet_size=target.packet_size,
        max_hops=target.max_hops,
        timeout=target.timeout,
        active=target.active,
        created_at=target.created_at.isoformat() if target.created_at else None,
    )
