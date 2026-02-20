"""Session data export in CSV and JSON formats.

Functions accept a database session and a time range, then return the
serialised data as a string (CSV) or a list of dictionaries (JSON).
"""

import csv
import io
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from pingwatcher.db.models import Sample


def _query_samples(
    db: Session,
    target_id: str,
    start: datetime,
    end: datetime,
) -> list[Sample]:
    """Fetch ordered sample rows within a time range.

    Args:
        db: Active database session.
        target_id: UUID-style target identifier.
        start: Lower bound on ``sampled_at``.
        end: Upper bound on ``sampled_at``.

    Returns:
        Ordered list of :class:`Sample` rows.
    """
    return (
        db.query(Sample)
        .filter(
            Sample.target_id == target_id,
            Sample.sampled_at >= start,
            Sample.sampled_at <= end,
        )
        .order_by(Sample.sampled_at, Sample.hop_number)
        .all()
    )


def export_session_csv(
    db: Session,
    target_id: str,
    start: datetime,
    end: datetime,
) -> str:
    """Export sample data as a CSV string.

    Columns: ``sampled_at``, ``hop_number``, ``ip``, ``dns``,
    ``rtt_ms``, ``is_timeout``.

    Args:
        db: Active database session.
        target_id: UUID-style target identifier.
        start: Start of the export window.
        end: End of the export window.

    Returns:
        A UTF-8 CSV string including the header row.
    """
    rows = _query_samples(db, target_id, start, end)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["sampled_at", "hop_number", "ip", "dns", "rtt_ms", "is_timeout"])

    for r in rows:
        writer.writerow(
            [
                r.sampled_at.isoformat() if r.sampled_at else "",
                r.hop_number,
                r.ip or "",
                r.dns or "",
                r.rtt_ms if r.rtt_ms is not None else "",
                r.is_timeout,
            ]
        )

    return buf.getvalue()


def export_session_json(
    db: Session,
    target_id: str,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    """Export sample data as a list of JSON-serialisable dictionaries.

    Args:
        db: Active database session.
        target_id: UUID-style target identifier.
        start: Start of the export window.
        end: End of the export window.

    Returns:
        List of dictionaries, one per sample row.
    """
    rows = _query_samples(db, target_id, start, end)

    return [
        {
            "sampled_at": r.sampled_at.isoformat() if r.sampled_at else None,
            "hop_number": r.hop_number,
            "ip": r.ip,
            "dns": r.dns,
            "rtt_ms": r.rtt_ms,
            "is_timeout": r.is_timeout,
        }
        for r in rows
    ]
