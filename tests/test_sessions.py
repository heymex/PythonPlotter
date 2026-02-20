"""Tests for session export and the sessions API router."""

from datetime import datetime, timedelta

from pingwatcher.db.models import Sample, Target
from pingwatcher.db.queries import create_target, store_sample
from pingwatcher.sessions.export import export_session_csv, export_session_json


def _seed_session_data(db):
    """Insert a target with sample data and return (target_id, start, end)."""
    t = create_target(db, Target(id="se1", host="example.com"))
    now = datetime.utcnow()
    start = now - timedelta(minutes=5)
    end = now

    for i in range(10):
        ts = start + timedelta(seconds=i * 30)
        store_sample(
            db,
            [
                Sample(
                    target_id="se1",
                    sampled_at=ts,
                    hop_number=1,
                    ip="10.0.0.1",
                    rtt_ms=5.0 + i,
                    is_timeout=False,
                ),
                Sample(
                    target_id="se1",
                    sampled_at=ts,
                    hop_number=2,
                    ip="10.0.0.2",
                    rtt_ms=10.0 + i,
                    is_timeout=False,
                ),
            ],
        )
    return "se1", start, end


class TestExportCSV:
    """Verify CSV export."""

    def test_csv_header(self, db_session):
        """CSV output starts with the expected header row."""
        tid, start, end = _seed_session_data(db_session)
        csv = export_session_csv(db_session, tid, start, end)
        header = csv.splitlines()[0]
        assert "sampled_at" in header
        assert "hop_number" in header
        assert "rtt_ms" in header

    def test_csv_row_count(self, db_session):
        """CSV contains one header row plus one row per sample."""
        tid, start, end = _seed_session_data(db_session)
        csv = export_session_csv(db_session, tid, start, end)
        lines = [l for l in csv.strip().splitlines() if l]
        # 10 traces × 2 hops = 20 data rows + 1 header.
        assert len(lines) == 21

    def test_csv_empty(self, db_session):
        """Empty time range yields header-only CSV."""
        create_target(db_session, Target(id="empty", host="x.x.x.x"))
        csv = export_session_csv(
            db_session, "empty", datetime(2000, 1, 1), datetime(2000, 1, 2)
        )
        lines = [l for l in csv.strip().splitlines() if l]
        assert len(lines) == 1  # Header only.


class TestExportJSON:
    """Verify JSON export."""

    def test_json_structure(self, db_session):
        """JSON export returns a list of dictionaries."""
        tid, start, end = _seed_session_data(db_session)
        data = export_session_json(db_session, tid, start, end)
        assert isinstance(data, list)
        assert len(data) == 20

    def test_json_fields(self, db_session):
        """Each JSON row has the expected fields."""
        tid, start, end = _seed_session_data(db_session)
        data = export_session_json(db_session, tid, start, end)
        row = data[0]
        assert "sampled_at" in row
        assert "hop_number" in row
        assert "rtt_ms" in row
        assert "is_timeout" in row

    def test_json_empty(self, db_session):
        """Empty time range yields an empty list."""
        create_target(db_session, Target(id="empty2", host="y.y.y.y"))
        data = export_session_json(
            db_session, "empty2", datetime(2000, 1, 1), datetime(2000, 1, 2)
        )
        assert data == []
