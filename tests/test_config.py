"""Tests for :mod:`pingwatcher.config`."""

from pingwatcher.config import Settings, get_settings


class TestSettings:
    """Verify default configuration values."""

    def test_default_trace_interval(self):
        """Default trace interval should be 2.5 seconds."""
        s = Settings()
        assert s.default_trace_interval == 2.5

    def test_default_packet_type(self):
        """Default packet type should be ICMP."""
        s = Settings()
        assert s.default_packet_type == "icmp"

    def test_default_max_hops(self):
        """Default max hops should be 30."""
        s = Settings()
        assert s.default_max_hops == 30

    def test_default_timeout(self):
        """Default timeout should be 3.0 seconds."""
        s = Settings()
        assert s.default_timeout == 3.0

    def test_default_focus(self):
        """Default focus window should be 10 samples."""
        s = Settings()
        assert s.default_focus == 10

    def test_default_timeline_points(self):
        """Default timeline window should be 600 points."""
        s = Settings()
        assert s.default_timeline_points == 600

    def test_default_port(self):
        """Default server port should be 8000."""
        s = Settings()
        assert s.port == 8000

    def test_default_host(self):
        """Default bind host should be 0.0.0.0."""
        s = Settings()
        assert s.host == "0.0.0.0"

    def test_get_settings_returns_instance(self):
        """get_settings() should return a Settings object."""
        cfg = get_settings()
        assert isinstance(cfg, Settings)

    def test_get_settings_is_cached(self):
        """get_settings() should return the same object on repeated calls."""
        a = get_settings()
        b = get_settings()
        assert a is b
