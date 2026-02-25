"""Tests for :mod:`pingwatcher.engine.tracer`.

All subprocess calls are mocked so these tests run offline and without
root privileges.
"""

import subprocess
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from pingwatcher.engine.tracer import (
    _build_ping_cmd,
    _parse_ping_output,
    _parse_traceroute_output,
    icmp_traceroute,
    resolve_target,
    scapy_icmp_traceroute,
    system_traceroute,
)


class TestBuildPingCmd:
    """Verify platform-specific ping command construction."""

    @patch("pingwatcher.engine.tracer._PLATFORM", "darwin")
    def test_macos_command(self):
        """macOS uses -m for TTL and -t for timeout."""
        cmd = _build_ping_cmd("8.8.8.8", ttl=5, timeout=3.0)
        assert cmd == ["ping", "-c", "1", "-m", "5", "-t", "3", "8.8.8.8"]

    @patch("pingwatcher.engine.tracer._PLATFORM", "linux")
    def test_linux_command(self):
        """Linux uses -t for TTL and -W for timeout."""
        cmd = _build_ping_cmd("8.8.8.8", ttl=5, timeout=3.0)
        assert cmd == ["ping", "-c", "1", "-t", "5", "-W", "3", "8.8.8.8"]


class TestParsePingOutput:
    """Verify parsing of ping stdout/stderr across platforms."""

    def test_ttl_exceeded_macos(self):
        """macOS TTL Exceeded reply is parsed correctly."""
        output = "92 bytes from 192.168.1.1: Time to live exceeded\n"
        result = _parse_ping_output(output, "8.8.8.8")
        assert result["ip"] == "192.168.1.1"
        assert result["is_timeout"] is False

    def test_ttl_exceeded_linux(self):
        """Linux TTL Exceeded reply is parsed correctly."""
        output = "From 10.0.0.1 icmp_seq=1 Time to live exceeded\n"
        result = _parse_ping_output(output, "8.8.8.8")
        assert result["ip"] == "10.0.0.1"
        assert result["is_timeout"] is False

    def test_echo_reply(self):
        """Successful echo reply extracts IP and RTT."""
        output = "64 bytes from 8.8.8.8: icmp_seq=1 ttl=118 time=12.3 ms\n"
        result = _parse_ping_output(output, "8.8.8.8")
        assert result["ip"] == "8.8.8.8"
        assert result["rtt_ms"] == 12.3
        assert result["is_timeout"] is False

    def test_timeout(self):
        """No response yields a timeout result."""
        result = _parse_ping_output("", "8.8.8.8")
        assert result["ip"] is None
        assert result["is_timeout"] is True


class TestIcmpTraceroute:
    """Verify the ping-per-hop traceroute strategy."""

    @patch("pingwatcher.engine.tracer.resolve_target", return_value="8.8.8.8")
    @patch("pingwatcher.engine.tracer._send_probe")
    def test_stops_at_target(self, mock_probe, mock_resolve):
        """Trace stops when the final hop IP matches the target."""
        mock_probe.side_effect = [
            {"hop": 1, "ip": "10.0.0.1", "dns": None, "rtt_ms": 1.0, "is_timeout": False},
            {"hop": 2, "ip": "8.8.8.8", "dns": None, "rtt_ms": 12.0, "is_timeout": False},
        ]
        hops = icmp_traceroute("8.8.8.8", max_hops=30, timeout=1.0, inter_packet_delay=0)
        assert len(hops) == 2
        assert hops[-1]["ip"] == "8.8.8.8"

    @patch("pingwatcher.engine.tracer.resolve_target", return_value="8.8.8.8")
    @patch("pingwatcher.engine.tracer._send_probe")
    def test_respects_max_hops(self, mock_probe, mock_resolve):
        """Trace does not exceed max_hops even when target is unreachable."""
        mock_probe.return_value = {
            "hop": 1, "ip": None, "dns": None, "rtt_ms": None, "is_timeout": True,
        }
        hops = icmp_traceroute("8.8.8.8", max_hops=3, timeout=1.0, inter_packet_delay=0)
        assert len(hops) == 3

    @patch("pingwatcher.engine.tracer.resolve_target", return_value="8.8.8.8")
    @patch("pingwatcher.engine.tracer._send_probe")
    def test_stops_after_consecutive_timeouts(self, mock_probe, mock_resolve):
        """Trace stops early once timeout streak threshold is reached."""
        mock_probe.return_value = {
            "hop": 1, "ip": None, "dns": None, "rtt_ms": None, "is_timeout": True,
        }
        hops = icmp_traceroute(
            "8.8.8.8",
            max_hops=30,
            timeout=1.0,
            inter_packet_delay=0,
            max_consecutive_timeouts=4,
        )
        assert len(hops) == 4


class TestParseTracerouteOutput:
    """Verify parsing of system traceroute text."""

    def test_normal_output(self):
        """Typical traceroute output is parsed into hop dicts."""
        output = (
            "traceroute to 8.8.8.8 (8.8.8.8), 30 hops max\n"
            " 1  192.168.1.1  1.234 ms\n"
            " 2  10.0.0.1  5.678 ms\n"
            " 3  *\n"
            " 4  8.8.8.8  12.345 ms\n"
        )
        hops = _parse_traceroute_output(output)
        assert len(hops) >= 3
        assert hops[0]["hop"] == 1
        assert hops[0]["ip"] == "192.168.1.1"

    def test_all_timeouts(self):
        """Lines with only * are treated as timeouts."""
        output = (
            "traceroute to 8.8.8.8 (8.8.8.8), 5 hops max\n"
            " 1  *\n"
            " 2  *\n"
        )
        hops = _parse_traceroute_output(output)
        for h in hops:
            assert h["is_timeout"] is True


class TestSystemTraceroute:
    """Verify the system-traceroute fallback."""

    @patch("subprocess.run")
    def test_parses_output(self, mock_run):
        """Successful run returns parsed hops."""
        mock_run.return_value = MagicMock(
            stdout=(
                "traceroute to 1.1.1.1, 30 hops max\n"
                " 1  10.0.0.1  1.5 ms\n"
                " 2  1.1.1.1  8.0 ms\n"
            )
        )
        hops = system_traceroute("1.1.1.1", max_hops=5, timeout=1.0)
        assert len(hops) >= 1

    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_missing_binary(self, mock_run):
        """Returns empty list when traceroute binary is not found."""
        hops = system_traceroute("1.1.1.1")
        assert hops == []

    @patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="traceroute", timeout=5))
    def test_timeout(self, mock_run):
        """Returns empty list when the subprocess times out."""
        hops = system_traceroute("1.1.1.1", timeout=1.0)
        assert hops == []


class TestResolveTarget:
    """Verify hostname resolution wrapper."""

    @patch("socket.gethostbyname", return_value="93.184.216.34")
    def test_resolves_hostname(self, mock_dns):
        """Hostnames are resolved to IPv4 addresses."""
        assert resolve_target("example.com") == "93.184.216.34"

    @patch("socket.gethostbyname", return_value="8.8.8.8")
    def test_passthrough_ip(self, mock_dns):
        """IP addresses pass through unchanged."""
        assert resolve_target("8.8.8.8") == "8.8.8.8"


class TestScapyTraceroute:
    """Verify the Scapy batch traceroute path."""

    @patch("pingwatcher.engine.tracer.resolve_target", return_value="8.8.8.8")
    def test_maps_answers_by_ttl(self, _mock_resolve):
        """Answered packets are converted to ordered hop rows."""

        class _Pkt:
            def __init__(self, ttl, src=None, sent_time=0.0, recv_time=0.0):
                self.ttl = ttl
                self.src = src
                self.sent_time = sent_time
                self.time = recv_time

        class _FakeIP:
            def __init__(self, dst, ttl):
                self.dst = dst
                self.ttl = ttl

            def __truediv__(self, _other):
                return self

        fake_scapy_all = types.SimpleNamespace(
            IP=lambda dst, ttl: _FakeIP(dst, ttl),
            ICMP=lambda: object(),
            sr=lambda _pkts, timeout, retry, verbose: (
                [
                    (_Pkt(1, sent_time=1.0), _Pkt(1, src="10.0.0.1", recv_time=1.002)),
                    (_Pkt(2, sent_time=1.0), _Pkt(2, src="8.8.8.8", recv_time=1.010)),
                ],
                [],
            ),
        )
        fake_scapy = types.SimpleNamespace(all=fake_scapy_all)

        with patch.dict(sys.modules, {"scapy": fake_scapy, "scapy.all": fake_scapy_all}):
            hops = scapy_icmp_traceroute("8.8.8.8", max_hops=5, timeout=1.0)

        assert len(hops) == 2
        assert hops[0]["ip"] == "10.0.0.1"
        assert hops[1]["ip"] == "8.8.8.8"
