"""Application configuration via environment variables and defaults."""

from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Global configuration loaded from environment / ``.env`` file.

    Attributes:
        database_url: SQLAlchemy connection string. Defaults to a local
            SQLite file in the project root.
        default_trace_interval: Seconds between successive traceroute
            samples for new targets.
        default_packet_type: Probe type for new targets (``icmp``,
            ``udp``, or ``tcp``).
        default_packet_size: ICMP payload size in bytes.
        default_max_hops: Maximum TTL / hop count.
        default_timeout: Per-probe timeout in seconds.
        default_inter_packet_delay: Delay between TTL probes inside a
            single trace, in seconds.
        default_focus: Number of recent samples used to compute summary
            statistics.
        log_level: Python logging level name.
        host: Bind address for the Uvicorn server.
        port: Bind port for the Uvicorn server.
    """

    database_url: str = "sqlite:///pingwatcher.db"
    default_trace_interval: float = 2.5
    default_packet_type: str = "icmp"
    default_packet_size: int = 56
    default_max_hops: int = 30
    default_timeout: float = 3.0
    default_inter_packet_delay: float = 0.025
    default_focus: int = 10
    default_timeline_points: int = 600
    log_level: str = "INFO"
    host: str = "0.0.0.0"
    port: int = 8000

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "env_prefix": "PINGWATCHER_",
    }


def get_settings() -> Settings:
    """Return a cached :class:`Settings` instance.

    The instance is constructed once and reused for the lifetime of the
    process.
    """
    return _settings


_settings = Settings()
