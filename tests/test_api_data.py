"""Tests for the data / summary / route-change API endpoints."""

from datetime import datetime, timedelta
from unittest.mock import patch

from pingwatcher.db.models import Sample, Target


def _seed(client):
    """Create a target and seed sample data, return the target ID."""
    with patch("pingwatcher.api.targets.start_monitoring"):
        resp = client.post("/api/targets", json={"host": "8.8.8.8"})
    return resp.json()["id"]


def _insert_samples(db_engine, target_id, hops=3, traces=5):
    """Insert sample rows directly into the database."""
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=db_engine)
    session = Session()
    now = datetime.utcnow()
    for i in range(traces):
        ts = now - timedelta(seconds=(traces - i) * 3)
        for h in range(1, hops + 1):
            session.add(
                Sample(
                    target_id=target_id,
                    sampled_at=ts,
                    hop_number=h,
                    ip=f"10.0.0.{h}",
                    dns=f"hop{h}.local",
                    rtt_ms=10.0 + h,
                    is_timeout=False,
                )
            )
    session.commit()
    session.close()


class TestHopsEndpoint:
    """GET /api/targets/{id}/hops."""

    def test_hops_no_data(self, client):
        """Returns empty list when target has no samples."""
        tid = _seed(client)
        resp = client.get(f"/api/targets/{tid}/hops")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_hops_with_data(self, client, db_engine):
        """Returns hop stats when samples exist."""
        tid = _seed(client)
        _insert_samples(db_engine, tid)

        resp = client.get(f"/api/targets/{tid}/hops?focus=5")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3

    def test_hops_nonexistent_target(self, client):
        """Returns 404 for a missing target."""
        resp = client.get("/api/targets/fake/hops")
        assert resp.status_code == 404


class TestTimelineEndpoint:
    """GET /api/targets/{id}/timeline."""

    def test_timeline_empty(self, client):
        """Returns empty list when target has no samples."""
        tid = _seed(client)
        resp = client.get(f"/api/targets/{tid}/timeline?hop=last")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_timeline_with_data(self, client, db_engine):
        """Returns time-series data for the last hop."""
        tid = _seed(client)
        _insert_samples(db_engine, tid, hops=2, traces=4)

        resp = client.get(f"/api/targets/{tid}/timeline?hop=last")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 4

    def test_timeline_limit(self, client, db_engine):
        """Supports limiting timeline points via query parameter."""
        tid = _seed(client)
        _insert_samples(db_engine, tid, hops=2, traces=10)

        resp = client.get(f"/api/targets/{tid}/timeline?hop=last&limit=3")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3

    def test_timeline_nonexistent_target(self, client):
        """Returns 404 for a missing target."""
        resp = client.get("/api/targets/fake/timeline")
        assert resp.status_code == 404


class TestSummaryEndpoint:
    """GET /api/summary."""

    def test_summary_empty(self, client):
        """Empty summary when no targets exist."""
        resp = client.get("/api/summary")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_summary_with_targets(self, client, db_engine):
        """Summary includes active targets with stats."""
        tid = _seed(client)
        _insert_samples(db_engine, tid, hops=2, traces=3)

        resp = client.get("/api/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["host"] == "8.8.8.8"


class TestRouteChangesEndpoint:
    """GET /api/targets/{id}/route_changes."""

    def test_route_changes_empty(self, client):
        """Returns empty list when no route changes occurred."""
        tid = _seed(client)
        resp = client.get(f"/api/targets/{tid}/route_changes")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_route_changes_nonexistent_target(self, client):
        """Returns 404 for a missing target."""
        resp = client.get("/api/targets/fake/route_changes")
        assert resp.status_code == 404
