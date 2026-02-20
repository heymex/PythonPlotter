"""Tests for :mod:`pingwatcher.db.queries`."""

from datetime import datetime, timedelta

from pingwatcher.db.models import Alert, Sample, Target
from pingwatcher.db.queries import (
    create_target,
    delete_target,
    get_active_alerts,
    get_all_hop_stats,
    get_hop_stats,
    get_last_known_route,
    get_route_changes,
    get_summary,
    get_target,
    get_timeline_data,
    list_targets,
    record_alert_event,
    record_route_change,
    store_sample,
)


def _make_target(db, tid="t1", host="8.8.8.8"):
    """Helper to insert a target and return it."""
    t = Target(id=tid, host=host, active=True)
    return create_target(db, t)


def _add_samples(db, target_id, hop_count=3, count=5, base_rtt=10.0):
    """Insert sample rows for *hop_count* hops across *count* traces."""
    now = datetime.utcnow()
    for i in range(count):
        ts = now - timedelta(seconds=(count - i) * 3)
        rows = []
        for h in range(1, hop_count + 1):
            rows.append(
                Sample(
                    target_id=target_id,
                    sampled_at=ts,
                    hop_number=h,
                    ip=f"10.0.0.{h}",
                    dns=f"hop{h}.local",
                    rtt_ms=base_rtt + h + i * 0.5,
                    is_timeout=False,
                )
            )
        store_sample(db, rows)


class TestTargetCRUD:
    """list / get / create / delete helpers."""

    def test_create_and_get(self, db_session):
        """Created target is retrievable by ID."""
        t = _make_target(db_session)
        assert get_target(db_session, "t1") is not None
        assert get_target(db_session, "t1").host == "8.8.8.8"

    def test_list_targets(self, db_session):
        """list_targets returns all targets."""
        _make_target(db_session, "a")
        _make_target(db_session, "b")
        assert len(list_targets(db_session)) == 2

    def test_delete_target(self, db_session):
        """Deleting a target removes it from the database."""
        _make_target(db_session)
        assert delete_target(db_session, "t1") is True
        assert get_target(db_session, "t1") is None

    def test_delete_nonexistent(self, db_session):
        """Deleting a missing target returns False."""
        assert delete_target(db_session, "nope") is False


class TestHopStats:
    """get_hop_stats / get_all_hop_stats."""

    def test_hop_stats_basic(self, db_session):
        """Stats for a single hop with known data."""
        _make_target(db_session)
        _add_samples(db_session, "t1", hop_count=2, count=4, base_rtt=10.0)

        stats = get_hop_stats(db_session, "t1", 1, focus_n=4)
        assert stats["hop"] == 1
        assert stats["ip"] == "10.0.0.1"
        assert stats["avg_ms"] is not None
        assert stats["packet_loss_pct"] == 0.0

    def test_hop_stats_empty(self, db_session):
        """Stats for a hop with no data should return null values."""
        _make_target(db_session)
        stats = get_hop_stats(db_session, "t1", 99, focus_n=10)
        assert stats["avg_ms"] is None
        assert stats["packet_loss_pct"] == 0.0

    def test_all_hop_stats(self, db_session):
        """get_all_hop_stats should return one dict per hop."""
        _make_target(db_session)
        _add_samples(db_session, "t1", hop_count=3, count=5)

        all_stats = get_all_hop_stats(db_session, "t1", focus_n=5)
        assert len(all_stats) == 3
        assert all_stats[0]["hop"] == 1
        assert all_stats[2]["hop"] == 3

    def test_packet_loss_calculation(self, db_session):
        """Timeout samples should count towards packet loss."""
        _make_target(db_session)
        now = datetime.utcnow()
        samples = [
            Sample(target_id="t1", sampled_at=now, hop_number=1, rtt_ms=10.0, is_timeout=False),
            Sample(
                target_id="t1",
                sampled_at=now - timedelta(seconds=3),
                hop_number=1,
                rtt_ms=None,
                is_timeout=True,
            ),
        ]
        store_sample(db_session, samples)

        stats = get_hop_stats(db_session, "t1", 1, focus_n=10)
        assert stats["packet_loss_pct"] == 50.0


class TestTimeline:
    """get_timeline_data."""

    def test_timeline_last_hop(self, db_session):
        """Timeline for 'last' hop returns final-hop data."""
        _make_target(db_session)
        _add_samples(db_session, "t1", hop_count=3, count=4)

        data = get_timeline_data(db_session, "t1", hop="last")
        assert len(data) == 4
        assert "timestamp" in data[0]
        assert "rtt_ms" in data[0]

    def test_timeline_specific_hop(self, db_session):
        """Timeline for a specific hop returns only that hop's data."""
        _make_target(db_session)
        _add_samples(db_session, "t1", hop_count=3, count=4)

        data = get_timeline_data(db_session, "t1", hop="1")
        assert len(data) == 4

    def test_timeline_empty(self, db_session):
        """Timeline returns empty list when no data exists."""
        _make_target(db_session)
        data = get_timeline_data(db_session, "t1", hop="last")
        assert data == []


class TestSummary:
    """get_summary."""

    def test_summary_with_data(self, db_session):
        """Summary includes stats for active targets."""
        _make_target(db_session, "s1", "1.1.1.1")
        _add_samples(db_session, "s1", hop_count=2, count=3)

        summaries = get_summary(db_session, focus_n=3)
        assert len(summaries) == 1
        assert summaries[0]["host"] == "1.1.1.1"
        assert summaries[0]["avg_ms"] is not None

    def test_summary_no_targets(self, db_session):
        """Summary returns empty list when there are no active targets."""
        assert get_summary(db_session) == []


class TestRouteChanges:
    """Route detection helpers."""

    def test_get_last_known_route(self, db_session):
        """Returns the IP list from the most recent sample set."""
        _make_target(db_session)
        _add_samples(db_session, "t1", hop_count=3, count=1)

        route = get_last_known_route(db_session, "t1")
        assert route == ["10.0.0.1", "10.0.0.2", "10.0.0.3"]

    def test_get_last_known_route_empty(self, db_session):
        """Returns None when no samples exist."""
        _make_target(db_session)
        assert get_last_known_route(db_session, "t1") is None

    def test_record_route_change(self, db_session):
        """record_route_change persists and is retrievable."""
        _make_target(db_session)
        record_route_change(
            db_session, "t1",
            ["10.0.0.1", "10.0.0.2"],
            ["10.0.0.1", "10.0.0.3"],
        )
        changes = get_route_changes(db_session, "t1")
        assert len(changes) == 1
        assert "10.0.0.3" in changes[0]["new_route"]


class TestAlertHelpers:
    """get_active_alerts / record_alert_event."""

    def test_get_active_alerts(self, db_session):
        """Returns only enabled alerts for the target."""
        _make_target(db_session)
        a1 = Alert(
            id="a1", target_id="t1", metric="cur_rtt_ms",
            operator=">", threshold=50.0, action_type="log", enabled=True,
        )
        a2 = Alert(
            id="a2", target_id="t1", metric="cur_rtt_ms",
            operator=">", threshold=50.0, action_type="log", enabled=False,
        )
        db_session.add_all([a1, a2])
        db_session.commit()

        active = get_active_alerts(db_session, "t1")
        assert len(active) == 1
        assert active[0].id == "a1"

    def test_record_alert_event(self, db_session):
        """record_alert_event creates an AlertHistory row."""
        _make_target(db_session)
        a = Alert(
            id="ae1", target_id="t1", metric="packet_loss_pct",
            operator=">", threshold=5.0, action_type="log",
        )
        db_session.add(a)
        db_session.commit()

        event = record_alert_event(db_session, a, 12.0, "test event")
        assert event.metric_value == 12.0
        assert event.alert_id == "ae1"
