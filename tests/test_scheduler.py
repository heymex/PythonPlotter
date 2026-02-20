"""Tests for :mod:`pingwatcher.engine.scheduler`."""

from unittest.mock import MagicMock, patch

from pingwatcher.engine.scheduler import (
    _notify_subscribers,
    latest_results,
    start_monitoring,
    stop_monitoring,
    ws_subscribers,
)


class TestStartStopMonitoring:
    """Verify scheduler job management."""

    @patch("pingwatcher.engine.scheduler.scheduler")
    def test_start_monitoring_adds_job(self, mock_scheduler):
        """start_monitoring registers an interval job."""
        start_monitoring("t1", "8.8.8.8", interval=5.0)
        mock_scheduler.add_job.assert_called_once()
        call_kwargs = mock_scheduler.add_job.call_args
        assert call_kwargs.kwargs["id"] == "t1"
        assert call_kwargs.kwargs["seconds"] == 5.0

    @patch("pingwatcher.engine.scheduler.scheduler")
    def test_stop_monitoring_removes_job(self, mock_scheduler):
        """stop_monitoring removes the scheduled job."""
        latest_results["t1"] = [{"hop": 1}]
        stop_monitoring("t1")
        mock_scheduler.remove_job.assert_called_once_with("t1")
        assert "t1" not in latest_results

    @patch("pingwatcher.engine.scheduler.scheduler")
    def test_stop_monitoring_no_job(self, mock_scheduler):
        """stop_monitoring does not raise when no job exists."""
        mock_scheduler.remove_job.side_effect = Exception("not found")
        stop_monitoring("missing")  # Should not raise.


class TestNotifySubscribers:
    """Verify WebSocket notification dispatch."""

    def test_enqueues_payload(self):
        """Subscribers receive the payload via their queues."""
        queue = MagicMock()
        ws_subscribers["t1"] = {queue}

        _notify_subscribers("t1", [{"hop": 1, "ip": "10.0.0.1"}])
        queue.put_nowait.assert_called_once()

        # Cleanup.
        ws_subscribers.pop("t1", None)

    def test_dead_queues_removed(self):
        """Queues that raise on put_nowait are discarded."""
        dead_queue = MagicMock()
        dead_queue.put_nowait.side_effect = RuntimeError("closed")
        ws_subscribers["t2"] = {dead_queue}

        _notify_subscribers("t2", [{"hop": 1}])
        assert dead_queue not in ws_subscribers.get("t2", set())

        # Cleanup.
        ws_subscribers.pop("t2", None)

    def test_no_subscribers(self):
        """No error when there are no subscribers for a target."""
        _notify_subscribers("t_none", [{"hop": 1}])  # Should not raise.
