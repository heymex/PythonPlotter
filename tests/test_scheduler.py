"""Tests for :mod:`pingwatcher.engine.scheduler`."""

import socket
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
        assert call_kwargs.kwargs["coalesce"] is True
        assert call_kwargs.kwargs["max_instances"] == 32

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


class TestCollectSampleDnsFailure:
    """Verify repeated DNS failures stop monitoring."""

    @patch("pingwatcher.engine.scheduler.stop_monitoring")
    @patch("pingwatcher.engine.scheduler._deactivate_target")
    @patch(
        "pingwatcher.engine.scheduler.icmp_traceroute",
        side_effect=socket.gaierror("host lookup failed"),
    )
    @patch("pingwatcher.engine.scheduler.get_settings")
    def test_stops_after_consecutive_dns_failures(
        self,
        mock_settings,
        mock_traceroute,
        mock_deactivate,
        mock_stop,
    ):
        """After N DNS errors, target is disabled and job is stopped."""
        from pingwatcher.engine import scheduler as scheduler_mod

        scheduler_mod._dns_failures_by_target.clear()
        mock_settings.return_value = MagicMock(default_inter_packet_delay=0.0)

        scheduler_mod._collect_sample("target-1", "no-such-host", 30, 1.0)
        scheduler_mod._collect_sample("target-1", "no-such-host", 30, 1.0)
        scheduler_mod._collect_sample("target-1", "no-such-host", 30, 1.0)

        assert mock_traceroute.call_count == 3
        mock_deactivate.assert_called_once_with("target-1")
        mock_stop.assert_called_once_with("target-1")
