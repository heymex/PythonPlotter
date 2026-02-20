"""Tests for the alert condition evaluator and action dispatcher."""

from unittest.mock import MagicMock, patch

from pingwatcher.alerts.actions import dispatch_action
from pingwatcher.alerts.conditions import (
    _extract_metric,
    _find_matching_hops,
    check_condition,
)
from pingwatcher.db.models import Alert


def _make_stats(hops=3, base_rtt=10.0, loss=0.0):
    """Build a list of fake hop stats."""
    return [
        {
            "hop": i + 1,
            "ip": f"10.0.0.{i + 1}",
            "dns": f"hop{i + 1}.local",
            "avg_ms": base_rtt + i,
            "min_ms": base_rtt,
            "max_ms": base_rtt + i * 2,
            "cur_ms": base_rtt + i + 1,
            "packet_loss_pct": loss,
        }
        for i in range(hops)
    ]


def _make_alert(**overrides):
    """Create a minimal Alert-like object for testing."""
    defaults = {
        "id": "a1",
        "target_id": "t1",
        "metric": "packet_loss_pct",
        "operator": ">",
        "threshold": 5.0,
        "duration_samples": 1,
        "hop": "final",
        "action_type": "log",
        "action_config": None,
        "enabled": True,
        "consecutive_triggers": 0,
    }
    defaults.update(overrides)
    alert = MagicMock(spec=Alert)
    for k, v in defaults.items():
        setattr(alert, k, v)
    return alert


class TestExtractMetric:
    """Verify metric extraction from stat dicts."""

    def test_packet_loss(self):
        """packet_loss_pct maps to the correct key."""
        stats = {"packet_loss_pct": 12.5}
        assert _extract_metric(stats, "packet_loss_pct") == 12.5

    def test_avg_rtt(self):
        """avg_rtt_ms maps to avg_ms."""
        stats = {"avg_ms": 42.0}
        assert _extract_metric(stats, "avg_rtt_ms") == 42.0

    def test_cur_rtt(self):
        """cur_rtt_ms maps to cur_ms."""
        stats = {"cur_ms": 7.7}
        assert _extract_metric(stats, "cur_rtt_ms") == 7.7

    def test_unknown_metric(self):
        """Unknown metric names return None."""
        assert _extract_metric({}, "bogus") is None


class TestFindMatchingHops:
    """Verify hop selection logic."""

    def test_final(self):
        """'final' selects only the last hop."""
        stats = _make_stats(3)
        result = _find_matching_hops(stats, "final")
        assert len(result) == 1
        assert result[0]["hop"] == 3

    def test_any(self):
        """'any' returns all hops."""
        stats = _make_stats(3)
        result = _find_matching_hops(stats, "any")
        assert len(result) == 3

    def test_specific_ip(self):
        """A specific IP filters to matching hops."""
        stats = _make_stats(3)
        result = _find_matching_hops(stats, "10.0.0.2")
        assert len(result) == 1
        assert result[0]["hop"] == 2

    def test_empty_stats(self):
        """Empty stats list returns empty."""
        assert _find_matching_hops([], "final") == []


class TestCheckCondition:
    """Verify condition evaluation."""

    def test_triggered(self):
        """Condition fires when threshold is breached."""
        alert = _make_alert(metric="packet_loss_pct", operator=">", threshold=5.0, hop="final")
        stats = _make_stats(3, loss=10.0)
        triggered, value = check_condition(alert, stats)
        assert triggered is True
        assert value == 10.0

    def test_not_triggered(self):
        """Condition does not fire when below threshold."""
        alert = _make_alert(metric="packet_loss_pct", operator=">", threshold=20.0, hop="final")
        stats = _make_stats(3, loss=5.0)
        triggered, value = check_condition(alert, stats)
        assert triggered is False

    def test_any_hop_triggered(self):
        """'any' hop fires if any hop breaches the threshold."""
        alert = _make_alert(metric="cur_rtt_ms", operator=">", threshold=12.0, hop="any")
        stats = _make_stats(3, base_rtt=10.0)
        triggered, _ = check_condition(alert, stats)
        assert triggered is True

    def test_unknown_operator(self):
        """Unknown operator does not trigger."""
        alert = _make_alert(operator="!=")
        stats = _make_stats(1, loss=50.0)
        triggered, _ = check_condition(alert, stats)
        assert triggered is False


class TestDispatchAction:
    """Verify action routing."""

    @patch("pingwatcher.alerts.actions.log_file.log_alert")
    def test_dispatch_log(self, mock_log):
        """'log' action type calls log_alert."""
        dispatch_action("log", '{"path": "/tmp/test.log"}', "test msg")
        mock_log.assert_called_once()

    @patch("pingwatcher.alerts.actions.webhook.send_webhook")
    def test_dispatch_webhook(self, mock_webhook):
        """'webhook' action type calls send_webhook."""
        dispatch_action("webhook", '{"url": "http://example.com"}', "test msg")
        mock_webhook.assert_called_once()

    @patch("pingwatcher.alerts.actions.email_action.send_email_alert")
    def test_dispatch_email(self, mock_email):
        """'email' action type calls send_email_alert."""
        dispatch_action("email", '{"to_addr": "a@b.com"}', "test msg")
        mock_email.assert_called_once()

    @patch("pingwatcher.alerts.actions.command.run_command")
    def test_dispatch_command(self, mock_cmd):
        """'command' action type calls run_command."""
        dispatch_action("command", '{"command": "echo hi"}', "test msg")
        mock_cmd.assert_called_once()

    def test_dispatch_unknown(self):
        """Unknown action types do not raise."""
        dispatch_action("unknown", None, "test msg")  # Should not raise.
