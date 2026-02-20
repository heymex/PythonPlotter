"""Tests for :mod:`pingwatcher.db.models` — ORM table creation and basic CRUD."""

from datetime import datetime

from pingwatcher.db.models import (
    Alert,
    AlertHistory,
    RouteChange,
    Sample,
    Session,
    Target,
)


class TestTargetModel:
    """Verify Target ORM round-trips."""

    def test_create_target(self, db_session):
        """A Target row persists and reads back correctly."""
        t = Target(id="t1", host="8.8.8.8", label="Google DNS")
        db_session.add(t)
        db_session.commit()

        fetched = db_session.query(Target).get("t1")
        assert fetched is not None
        assert fetched.host == "8.8.8.8"
        assert fetched.label == "Google DNS"
        assert fetched.active is True

    def test_target_defaults(self, db_session):
        """Default column values should match the spec."""
        t = Target(id="t2", host="1.1.1.1")
        db_session.add(t)
        db_session.commit()

        fetched = db_session.query(Target).get("t2")
        assert fetched.trace_interval == 2.5
        assert fetched.packet_type == "icmp"
        assert fetched.packet_size == 56
        assert fetched.max_hops == 30
        assert fetched.timeout == 3.0

    def test_cascade_delete_removes_samples(self, db_session):
        """Deleting a Target should cascade-delete its Samples."""
        t = Target(id="t3", host="10.0.0.1")
        db_session.add(t)
        db_session.commit()

        s = Sample(target_id="t3", sampled_at=datetime.utcnow(), hop_number=1, ip="10.0.0.1")
        db_session.add(s)
        db_session.commit()

        db_session.delete(t)
        db_session.commit()

        assert db_session.query(Sample).filter_by(target_id="t3").count() == 0


class TestSampleModel:
    """Verify Sample ORM round-trips."""

    def test_create_sample(self, db_session):
        """A Sample row persists with all fields."""
        t = Target(id="ts1", host="example.com")
        db_session.add(t)
        db_session.commit()

        now = datetime.utcnow()
        s = Sample(
            target_id="ts1",
            sampled_at=now,
            hop_number=3,
            ip="192.168.1.1",
            dns="router.local",
            rtt_ms=12.5,
            is_timeout=False,
        )
        db_session.add(s)
        db_session.commit()

        fetched = db_session.query(Sample).filter_by(target_id="ts1").first()
        assert fetched.hop_number == 3
        assert fetched.rtt_ms == 12.5
        assert fetched.is_timeout is False

    def test_timeout_sample(self, db_session):
        """Timeout samples should have rtt_ms=None and is_timeout=True."""
        t = Target(id="ts2", host="example.com")
        db_session.add(t)
        db_session.commit()

        s = Sample(
            target_id="ts2",
            sampled_at=datetime.utcnow(),
            hop_number=5,
            ip=None,
            rtt_ms=None,
            is_timeout=True,
        )
        db_session.add(s)
        db_session.commit()

        fetched = db_session.query(Sample).filter_by(target_id="ts2").first()
        assert fetched.rtt_ms is None
        assert fetched.is_timeout is True


class TestRouteChangeModel:
    """Verify RouteChange ORM round-trips."""

    def test_create_route_change(self, db_session):
        """RouteChange rows store old/new routes as comma-separated IPs."""
        t = Target(id="tr1", host="1.2.3.4")
        db_session.add(t)
        db_session.commit()

        rc = RouteChange(
            target_id="tr1",
            detected_at=datetime.utcnow(),
            old_route="10.0.0.1,10.0.0.2",
            new_route="10.0.0.1,10.0.0.3",
        )
        db_session.add(rc)
        db_session.commit()

        fetched = db_session.query(RouteChange).filter_by(target_id="tr1").first()
        assert "10.0.0.2" in fetched.old_route
        assert "10.0.0.3" in fetched.new_route


class TestAlertModel:
    """Verify Alert and AlertHistory ORM round-trips."""

    def test_create_alert(self, db_session):
        """An Alert row persists with condition fields."""
        t = Target(id="ta1", host="4.3.2.1")
        db_session.add(t)
        db_session.commit()

        a = Alert(
            id="a1",
            target_id="ta1",
            metric="packet_loss_pct",
            operator=">",
            threshold=10.0,
            duration_samples=3,
            hop="final",
            action_type="log",
        )
        db_session.add(a)
        db_session.commit()

        fetched = db_session.query(Alert).get("a1")
        assert fetched.metric == "packet_loss_pct"
        assert fetched.threshold == 10.0
        assert fetched.duration_samples == 3

    def test_alert_history(self, db_session):
        """AlertHistory rows link back to an Alert."""
        t = Target(id="ta2", host="5.5.5.5")
        a = Alert(
            id="a2", target_id="ta2", metric="cur_rtt_ms",
            operator=">", threshold=100.0, action_type="webhook",
        )
        db_session.add_all([t, a])
        db_session.commit()

        h = AlertHistory(
            alert_id="a2", target_id="ta2",
            triggered_at=datetime.utcnow(), metric_value=150.0,
            message="test",
        )
        db_session.add(h)
        db_session.commit()

        fetched = db_session.query(AlertHistory).filter_by(alert_id="a2").first()
        assert fetched.metric_value == 150.0


class TestSessionModel:
    """Verify Session ORM round-trips."""

    def test_create_session(self, db_session):
        """A Session bookmark persists and reads back."""
        t = Target(id="tse1", host="example.org")
        db_session.add(t)
        db_session.commit()

        sess = Session(
            id="s1",
            target_id="tse1",
            name="Morning check",
            start_time=datetime(2025, 6, 1, 8, 0),
            end_time=datetime(2025, 6, 1, 9, 0),
        )
        db_session.add(sess)
        db_session.commit()

        fetched = db_session.query(Session).get("s1")
        assert fetched.name == "Morning check"
