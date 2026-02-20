"""Reverse-DNS resolution with an LRU cache.

Hop IPs are looked up via PTR records.  Results are cached with
:func:`functools.lru_cache` to avoid redundant queries for stable
hops.  When no PTR record exists, the sentinel ``"----------"`` is
returned (matching PingPlotter's display convention).
"""

import functools
import logging
import socket

logger = logging.getLogger(__name__)

#: Sentinel displayed when a PTR record is absent.
NO_PTR = "----------"


@functools.lru_cache(maxsize=512)
def reverse_dns(ip: str) -> str:
    """Look up the PTR record for *ip* and return the hostname.

    Args:
        ip: Dotted-quad IPv4 address.

    Returns:
        Reverse-DNS hostname, or :data:`NO_PTR` if the lookup fails.
    """
    if not ip:
        return NO_PTR
    try:
        hostname, _aliases, _addrs = socket.gethostbyaddr(ip)
        return hostname
    except (socket.herror, socket.gaierror, OSError):
        logger.debug("PTR lookup failed for %s", ip)
        return NO_PTR


def clear_cache() -> None:
    """Flush the reverse-DNS LRU cache.

    Useful for testing or when the administrator wants to force
    re-resolution of all hop IPs.
    """
    reverse_dns.cache_clear()


def cache_info():
    """Return cache-hit statistics from the reverse-DNS LRU cache.

    Returns:
        A :class:`functools._CacheInfo` named tuple with ``hits``,
        ``misses``, ``maxsize``, and ``currsize``.
    """
    return reverse_dns.cache_info()
