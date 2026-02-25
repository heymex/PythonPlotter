"""Cross-platform traceroute engine using system ``ping`` and ``traceroute``.

Two strategies are provided:

* **ping-per-hop** — sends one ICMP echo with an incrementing TTL via
  the system ``ping`` binary.  Works without root on both macOS and
  Linux.
* **system traceroute** — shells out to ``traceroute`` / ``tracert``
  and parses the textual output.  Used as a fallback when ping-per-hop
  parsing fails.

All public functions return a list of *hop dictionaries*::

    {"hop": int, "ip": str | None, "rtt_ms": float | None, "is_timeout": bool}
"""

import logging
import platform
import re
import socket
import subprocess
import time
from typing import Optional

from pingwatcher.engine.dns import reverse_dns

logger = logging.getLogger(__name__)

# Pre-compiled patterns for parsing ping output across platforms.
_RE_TTL_EXCEEDED_MAC = re.compile(
    r"(\d+) bytes from ([\d.]+): Time to live exceeded"
)
_RE_TTL_EXCEEDED_LINUX = re.compile(
    r"From ([\d.]+) .*Time to live exceeded"
)
_RE_REPLY_RTT = re.compile(r"time[=<]([\d.]+)\s*ms")
_RE_REPLY_FROM = re.compile(r"from ([\d.]+)")

# Patterns for parsing system traceroute output.
_RE_TRACEROUTE_HOP = re.compile(
    r"^\s*(\d+)\s+"
    r"(?:(\d+\.\d+\.\d+\.\d+)\s+([\d.]+)\s*ms"
    r"|\*)"
)

_PLATFORM = platform.system().lower()


def resolve_target(host: str) -> str:
    """Resolve *host* to an IPv4 address string.

    Args:
        host: Hostname or dotted-quad IP.

    Returns:
        IPv4 address as a string.

    Raises:
        socket.gaierror: If DNS resolution fails.
    """
    return socket.gethostbyname(host)


# ---------------------------------------------------------------------------
# Ping-per-hop strategy
# ---------------------------------------------------------------------------


def _build_ping_cmd(target: str, ttl: int, timeout: float) -> list[str]:
    """Return the platform-appropriate ``ping`` command list.

    Args:
        target: Destination host or IP.
        ttl: IP Time-To-Live for this probe.
        timeout: Probe timeout in seconds.

    Returns:
        Command tokens suitable for :func:`subprocess.run`.
    """
    if _PLATFORM == "darwin":
        return [
            "ping",
            "-c", "1",
            "-m", str(ttl),
            "-t", str(max(1, int(timeout))),
            target,
        ]
    # Linux (and other POSIX).
    return [
        "ping",
        "-c", "1",
        "-t", str(ttl),
        "-W", str(max(1, int(timeout))),
        target,
    ]


def _parse_ping_output(output: str, target_ip: str) -> dict:
    """Extract hop IP and RTT from ``ping`` standard output/error.

    Args:
        output: Combined stdout + stderr text from the ping process.
        target_ip: Resolved IPv4 address of the final destination.

    Returns:
        Dictionary with ``ip``, ``rtt_ms``, and ``is_timeout``.
    """
    # Check for TTL-exceeded (intermediate hop).
    match = _RE_TTL_EXCEEDED_MAC.search(output) or _RE_TTL_EXCEEDED_LINUX.search(output)
    if match:
        ip = match.group(2) if match.lastindex and match.lastindex >= 2 else match.group(1)
        return {"ip": ip, "rtt_ms": None, "is_timeout": False}

    # Check for a successful reply (final hop or echo reply).
    rtt_match = _RE_REPLY_RTT.search(output)
    from_match = _RE_REPLY_FROM.search(output)
    if rtt_match and from_match:
        return {
            "ip": from_match.group(1),
            "rtt_ms": float(rtt_match.group(1)),
            "is_timeout": False,
        }

    # Timeout — no usable response.
    return {"ip": None, "rtt_ms": None, "is_timeout": True}


def _send_probe(
    target: str,
    target_ip: str,
    ttl: int,
    timeout: float,
) -> dict:
    """Send a single ping probe at the given TTL and parse the result.

    Args:
        target: Destination hostname or IP.
        target_ip: Pre-resolved IPv4 of *target*.
        ttl: TTL / hop count for this probe.
        timeout: Per-probe timeout in seconds.

    Returns:
        Hop dictionary with ``hop``, ``ip``, ``dns``, ``rtt_ms``, and
        ``is_timeout``.
    """
    cmd = _build_ping_cmd(target, ttl, timeout)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout + 2,
        )
        combined = proc.stdout + "\n" + proc.stderr
    except subprocess.TimeoutExpired:
        combined = ""

    parsed = _parse_ping_output(combined, target_ip)
    rtt = parsed["rtt_ms"]

    dns_name = reverse_dns(parsed["ip"]) if parsed["ip"] else None

    return {
        "hop": ttl,
        "ip": parsed["ip"],
        "dns": dns_name,
        "rtt_ms": round(rtt, 2) if rtt is not None else None,
        "is_timeout": parsed["is_timeout"],
    }


def icmp_traceroute(
    target: str,
    max_hops: int = 30,
    timeout: float = 3.0,
    inter_packet_delay: float = 0.025,
    max_consecutive_timeouts: int = 4,
) -> list[dict]:
    """Run a single ICMP traceroute to *target* using per-hop pings.

    Each TTL from 1 to *max_hops* is probed in sequence.  The trace
    stops early when a reply arrives from the target IP itself.

    Args:
        target: Hostname or IP to trace.
        max_hops: Maximum TTL to send.
        timeout: Per-probe timeout in seconds.
        inter_packet_delay: Pause between successive probes in seconds.
        max_consecutive_timeouts: End the trace early after this many
            consecutive hop timeouts.

    Returns:
        Ordered list of hop dictionaries.
    """
    target_ip = resolve_target(target)
    hops: list[dict] = []
    consecutive_timeouts = 0

    for ttl in range(1, max_hops + 1):
        result = _send_probe(target, target_ip, ttl, timeout)
        hops.append(result)
        logger.debug("hop %d → %s  rtt=%s", ttl, result["ip"], result["rtt_ms"])

        if result["ip"] == target_ip:
            break
        if result["is_timeout"]:
            consecutive_timeouts += 1
            if consecutive_timeouts >= max_consecutive_timeouts:
                break
        else:
            consecutive_timeouts = 0
        if inter_packet_delay > 0:
            time.sleep(inter_packet_delay)

    return hops


# ---------------------------------------------------------------------------
# System-traceroute fallback
# ---------------------------------------------------------------------------


def _parse_traceroute_output(output: str) -> list[dict]:
    """Parse the textual output of ``traceroute -n -q 1``.

    Args:
        output: Raw stdout from the traceroute process.

    Returns:
        List of hop dictionaries.
    """
    hops: list[dict] = []
    for line in output.splitlines():
        match = _RE_TRACEROUTE_HOP.match(line)
        if not match:
            continue
        hop_num = int(match.group(1))
        ip: Optional[str] = match.group(2)
        rtt_str = match.group(3)
        rtt: Optional[float] = float(rtt_str) if rtt_str else None
        is_timeout = ip is None

        dns_name = reverse_dns(ip) if ip else None
        hops.append(
            {
                "hop": hop_num,
                "ip": ip,
                "dns": dns_name,
                "rtt_ms": round(rtt, 2) if rtt is not None else None,
                "is_timeout": is_timeout,
            }
        )
    return hops


def system_traceroute(
    target: str,
    max_hops: int = 30,
    timeout: float = 3.0,
) -> list[dict]:
    """Run the system ``traceroute`` binary and parse its output.

    This is a fallback for platforms where per-hop ``ping`` parsing is
    unreliable.

    Args:
        target: Hostname or IP to trace.
        max_hops: Maximum TTL.
        timeout: Per-hop timeout in seconds.

    Returns:
        Ordered list of hop dictionaries.
    """
    cmd = [
        "traceroute",
        "-n",
        "-q", "1",
        "-w", str(max(1, int(timeout))),
        "-m", str(max_hops),
        target,
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout * max_hops + 10,
        )
        return _parse_traceroute_output(proc.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.error("system traceroute failed: %s", exc)
        return []
