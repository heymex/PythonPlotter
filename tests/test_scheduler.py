"""Tests for :mod:`pingwatcher.engine.scheduler`."""

import socket
from unittest.mock import MagicMock, patch

from pingwatcher.engine.scheduler import (
    _process_dns_enrichment,
    _run_maintenance,
    _select_probe_engine,
    _notify_subscribers,
    latest_results,
    start_monitoring,
    stop_monitoring,
    ws_subscribers,
    ws_summary_subscribers,
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

    def test_summary_subscribers_receive_delta(self):
        """Summary subscribers receive summary_update payloads."""
        queue = MagicMock()
        ws_summary_subscribers.add(queue)
        _notify_subscribers(
            "t1",
            [{"hop": 1}],
            summary_row={"target_id": "t1", "host": "8.8.8.8"},
        )
        queue.put_nowait.assert_called_once()
        ws_summary_subscribers.discard(queue)


class TestCollectSampleDnsFailure:
    """Verify repeated DNS failures stop monitoring."""

    @patch("pingwatcher.engine.scheduler.stop_monitoring")
    @patch("pingwatcher.engine.scheduler._deactivate_target")
    @patch("pingwatcher.engine.scheduler._select_probe_engine")
    @patch("pingwatcher.engine.scheduler.get_settings")
    def test_stops_after_consecutive_dns_failures(
        self,
        mock_settings,
        mock_select_engine,
        mock_deactivate,
        mock_stop,
    ):
        """After N DNS errors, target is disabled and job is stopped."""
        from pingwatcher.engine import scheduler as scheduler_mod

        scheduler_mod._dns_failures_by_target.clear()
        mock_settings.return_value = MagicMock(default_inter_packet_delay=0.0)
        mock_select_engine.side_effect = socket.gaierror("host lookup failed")

        scheduler_mod._collect_sample("target-1", "no-such-host", 30, 1.0)
        scheduler_mod._collect_sample("target-1", "no-such-host", 30, 1.0)
        scheduler_mod._collect_sample("target-1", "no-such-host", 30, 1.0)

        assert mock_select_engine.call_count == 3
        mock_deactivate.assert_called_once_with("target-1")
        mock_stop.assert_called_once_with("target-1")


class TestCollectSampleFallback:
    """Verify fallback to system traceroute when ICMP data is unusable."""

    @patch("pingwatcher.engine.scheduler._notify_subscribers")
    @patch("pingwatcher.engine.scheduler.evaluate_alerts")
    @patch("pingwatcher.engine.scheduler.get_all_hop_stats", return_value=[])
    @patch("pingwatcher.engine.scheduler.get_target_summary", return_value=None)
    @patch("pingwatcher.engine.scheduler.store_sample")
    @patch("pingwatcher.engine.scheduler.get_last_known_route", return_value=None)
    @patch("pingwatcher.engine.scheduler.SessionLocal")
    @patch(
        "pingwatcher.engine.scheduler.system_traceroute",
        return_value=[
            {"hop": 1, "ip": "10.0.0.1", "dns": "gw.local", "rtt_ms": 1.2, "is_timeout": False}
        ],
    )
    @patch(
        "pingwatcher.engine.scheduler.icmp_traceroute",
        return_value=[
            {"hop": 1, "ip": None, "dns": None, "rtt_ms": None, "is_timeout": True},
            {"hop": 2, "ip": None, "dns": None, "rtt_ms": None, "is_timeout": True},
        ],
    )
    @patch("pingwatcher.engine.scheduler.get_settings")
    def test_falls_back_to_system_traceroute_when_all_icmp_rtt_none(
        self,
        mock_settings,
        _mock_icmp,
        mock_system_traceroute,
        mock_session_local,
        _mock_get_last_route,
        mock_store_sample,
        _mock_target_summary,
        _mock_get_all_hop_stats,
        _mock_evaluate_alerts,
        _mock_notify,
    ):
        """When ICMP returns only ERR rows, fallback traceroute should be used."""
        from pingwatcher.engine import scheduler as scheduler_mod

        mock_settings.return_value = MagicMock(default_inter_packet_delay=0.0, default_focus=10)

        db = MagicMock()
        mock_session_local.return_value = db

        scheduler_mod._collect_sample("target-2", "8.8.8.8", 30, 1.0)

        mock_system_traceroute.assert_called_once_with("8.8.8.8", max_hops=30, timeout=1.0)
        assert mock_store_sample.call_count == 1

    @patch("pingwatcher.engine.scheduler._notify_subscribers")
    @patch("pingwatcher.engine.scheduler.evaluate_alerts")
    @patch("pingwatcher.engine.scheduler.get_all_hop_stats", return_value=[])
    @patch("pingwatcher.engine.scheduler.get_target_summary", return_value=None)
    @patch("pingwatcher.engine.scheduler.store_sample")
    @patch("pingwatcher.engine.scheduler.get_last_known_route", return_value=None)
    @patch("pingwatcher.engine.scheduler.SessionLocal")
    @patch("pingwatcher.engine.scheduler.system_traceroute", return_value=[])
    @patch(
        "pingwatcher.engine.scheduler.icmp_traceroute",
        return_value=[
            {"hop": 1, "ip": "10.0.0.1", "dns": "gw.local", "rtt_ms": 1.2, "is_timeout": False}
        ],
    )
    @patch("pingwatcher.engine.scheduler.get_settings")
    def test_uses_icmp_when_system_traceroute_returns_empty(
        self,
        mock_settings,
        mock_icmp,
        mock_system_traceroute,
        mock_session_local,
        _mock_get_last_route,
        mock_store_sample,
        _mock_target_summary,
        _mock_get_all_hop_stats,
        _mock_evaluate_alerts,
        _mock_notify,
    ):
        """When system traceroute is unavailable, ICMP probe path is used."""
        from pingwatcher.engine import scheduler as scheduler_mod

        mock_settings.return_value = MagicMock(default_inter_packet_delay=0.0, default_focus=10)
        db = MagicMock()
        mock_session_local.return_value = db

        scheduler_mod._collect_sample("target-3", "8.8.8.8", 30, 1.0)

        mock_system_traceroute.assert_called_once_with("8.8.8.8", max_hops=30, timeout=1.0)
        mock_icmp.assert_called_once()
        assert mock_store_sample.call_count == 1


class TestProbeEngineSelection:
    """Verify probe-engine mode selection and fallback order."""

    @patch("pingwatcher.engine.scheduler.icmp_traceroute", return_value=[{"hop": 1}])
    @patch("pingwatcher.engine.scheduler.system_traceroute", return_value=[])
    @patch("pingwatcher.engine.scheduler.scapy_icmp_traceroute", return_value=[{"hop": 1}])
    @patch("pingwatcher.engine.scheduler.get_settings")
    def test_prefers_scapy_in_auto(
        self,
        mock_settings,
        mock_scapy,
        _mock_system,
        _mock_icmp,
    ):
        mock_settings.return_value = MagicMock(probe_engine="auto", scapy_enabled=True, default_inter_packet_delay=0.0)
        hops = _select_probe_engine("8.8.8.8", 30, 1.0)
        assert hops == [{"hop": 1}]
        mock_scapy.assert_called_once()


class TestBackgroundJobs:
    """Verify maintenance and DNS enrichment helpers."""

    @patch("pingwatcher.engine.scheduler.backfill_dns_for_ip")
    @patch("pingwatcher.engine.scheduler.SessionLocal")
    @patch("pingwatcher.engine.scheduler.reverse_dns", return_value="router.local")
    @patch("pingwatcher.engine.scheduler.get_settings")
    def test_dns_enrichment_backfills_rows(
        self,
        mock_settings,
        _mock_reverse,
        mock_session_local,
        mock_backfill,
    ):
        from pingwatcher.engine import scheduler as scheduler_mod

        scheduler_mod._dns_pending_ips.clear()
        scheduler_mod._dns_pending_ips.add("10.0.0.1")
        mock_settings.return_value = MagicMock(
            enable_dns_enrichment_worker=True,
            dns_enrichment_batch_size=100,
        )
        db = MagicMock()
        mock_session_local.return_value = db
        _process_dns_enrichment()
        mock_backfill.assert_called_once()

    @patch("pingwatcher.engine.scheduler.delete_raw_samples_older_than")
    @patch("pingwatcher.engine.scheduler.aggregate_hourly_rollups")
    @patch("pingwatcher.engine.scheduler.SessionLocal")
    @patch("pingwatcher.engine.scheduler.get_settings")
    def test_maintenance_runs_rollup_and_retention(
        self,
        mock_settings,
        mock_session_local,
        mock_rollups,
        mock_delete,
    ):
        mock_settings.return_value = MagicMock(
            enable_rollups=True,
            rollup_after_hours=24,
            raw_retention_days=14,
        )
        db = MagicMock()
        mock_session_local.return_value = db
        _run_maintenance()
        mock_rollups.assert_called_once_with(db, older_than_hours=24)
        mock_delete.assert_called_once_with(db, days=14)
