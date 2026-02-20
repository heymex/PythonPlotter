"""Tests for :mod:`pingwatcher.engine.dns`."""

import socket
from unittest.mock import patch

from pingwatcher.engine.dns import NO_PTR, cache_info, clear_cache, reverse_dns


class TestReverseDns:
    """Verify PTR lookups with caching."""

    def setup_method(self):
        """Clear the LRU cache before each test."""
        clear_cache()

    @patch("socket.gethostbyaddr", return_value=("dns.google", [], []))
    def test_successful_lookup(self, mock_gethostbyaddr):
        """Known PTR record returns the hostname."""
        assert reverse_dns("8.8.8.8") == "dns.google"
        mock_gethostbyaddr.assert_called_once_with("8.8.8.8")

    @patch("socket.gethostbyaddr", side_effect=socket.herror)
    def test_failed_lookup(self, mock_gethostbyaddr):
        """Missing PTR record returns the sentinel string."""
        assert reverse_dns("192.0.2.1") == NO_PTR

    def test_empty_ip(self):
        """Empty string returns the sentinel without calling the resolver."""
        assert reverse_dns("") == NO_PTR

    @patch("socket.gethostbyaddr", return_value=("router.local", [], []))
    def test_caching(self, mock_gethostbyaddr):
        """Repeated calls with the same IP hit the LRU cache."""
        reverse_dns("10.0.0.1")
        reverse_dns("10.0.0.1")
        mock_gethostbyaddr.assert_called_once()

    def test_clear_cache(self):
        """clear_cache resets the LRU cache."""
        clear_cache()
        info = cache_info()
        assert info.currsize == 0

    @patch("socket.gethostbyaddr", side_effect=OSError)
    def test_os_error(self, mock_gethostbyaddr):
        """OSError during lookup returns sentinel."""
        assert reverse_dns("172.16.0.1") == NO_PTR
