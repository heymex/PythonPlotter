"""API router for session management and data export.

Endpoints
---------
* ``GET  /api/targets/{id}/sessions``        — list sessions.
* ``POST /api/targets/{id}/sessions``        — create a named session.
* ``POST /api/targets/{id}/sessions/export`` — export data as CSV or JSON.
"""

import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession

from pingwatcher.db.models import Session as SessionModel, get_db
from pingwatcher.db.queries import get_target
from pingwatcher.sessions.export import export_session_csv, export_session_json

router = APIRouter(prefix="/api/targets/{target_id}/sessions", tags=["sessions"])


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class SessionCreate(BaseModel):
    """Schema for creating a new session bookmark.

    Attributes:
        name: Human-friendly session label.
        start_time: ISO 8601 start of the time window.
        end_time: Optional ISO 8601 end (defaults to *now*).
    """

    name: str
    start_time: str
    end_time: Optional[str] = None


class SessionResponse(BaseModel):
    """Serialised session returned by the API.

    Attributes:
        id: Unique session identifier.
        target_id: The owning target.
        name: Session label.
        start_time: ISO 8601 start timestamp.
        end_time: ISO 8601 end timestamp (may be ``None``).
        created_at: ISO 8601 creation timestamp.
    """

    id: str
    target_id: str
    name: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    created_at: Optional[str] = None

    model_config = {"from_attributes": True}


class ExportRequest(BaseModel):
    """Schema for requesting a data export.

    Attributes:
        format: ``"csv"`` or ``"json"``.
        start_time: ISO 8601 start of the time window.
        end_time: Optional ISO 8601 end (defaults to *now*).
    """

    format: str = "csv"
    start_time: str
    end_time: Optional[str] = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=list[SessionResponse])
def api_list_sessions(target_id: str, db: DbSession = Depends(get_db)):
    """Return all saved sessions for a target, newest first.

    Args:
        target_id: UUID-style target identifier.
        db: Injected database session.

    Returns:
        List of :class:`SessionResponse` models.

    Raises:
        HTTPException: 404 if the target does not exist.
    """
    if get_target(db, target_id) is None:
        raise HTTPException(status_code=404, detail="Target not found")

    rows = (
        db.query(SessionModel)
        .filter(SessionModel.target_id == target_id)
        .order_by(SessionModel.created_at.desc())
        .all()
    )
    return [_serialize_session(s) for s in rows]


@router.post("", response_model=SessionResponse, status_code=201)
def api_create_session(
    target_id: str,
    body: SessionCreate,
    db: DbSession = Depends(get_db),
):
    """Create a named session bookmark for a target.

    Args:
        target_id: UUID-style target identifier.
        body: Session creation payload.
        db: Injected database session.

    Returns:
        The newly created :class:`SessionResponse`.

    Raises:
        HTTPException: 404 if the target does not exist.
    """
    if get_target(db, target_id) is None:
        raise HTTPException(status_code=404, detail="Target not found")

    session = SessionModel(
        id=str(uuid.uuid4()),
        target_id=target_id,
        name=body.name,
        start_time=datetime.fromisoformat(body.start_time),
        end_time=datetime.fromisoformat(body.end_time) if body.end_time else None,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return _serialize_session(session)


@router.post("/export")
def api_export_session(
    target_id: str,
    body: ExportRequest,
    db: DbSession = Depends(get_db),
):
    """Export sample data for a target within a time range.

    Supports ``csv`` and ``json`` formats.

    Args:
        target_id: UUID-style target identifier.
        body: Export parameters (format, start, end).
        db: Injected database session.

    Returns:
        ``text/csv`` or ``application/json`` response body.

    Raises:
        HTTPException: 404 if the target does not exist.  400 if the
            requested format is unsupported.
    """
    if get_target(db, target_id) is None:
        raise HTTPException(status_code=404, detail="Target not found")

    start_dt = datetime.fromisoformat(body.start_time)
    end_dt = datetime.fromisoformat(body.end_time) if body.end_time else datetime.utcnow()

    if body.format == "csv":
        content = export_session_csv(db, target_id, start_dt, end_dt)
        return PlainTextResponse(content, media_type="text/csv")
    elif body.format == "json":
        data = export_session_json(db, target_id, start_dt, end_dt)
        return JSONResponse(content=data)
    else:
        raise HTTPException(status_code=400, detail="Unsupported format. Use 'csv' or 'json'.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _serialize_session(session: SessionModel) -> SessionResponse:
    """Convert a :class:`SessionModel` ORM row into a response model.

    Args:
        session: The ORM instance.

    Returns:
        A :class:`SessionResponse` Pydantic model.
    """
    return SessionResponse(
        id=session.id,
        target_id=session.target_id,
        name=session.name,
        start_time=session.start_time.isoformat() if session.start_time else None,
        end_time=session.end_time.isoformat() if session.end_time else None,
        created_at=session.created_at.isoformat() if session.created_at else None,
    )
