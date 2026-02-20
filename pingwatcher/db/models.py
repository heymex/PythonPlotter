"""SQLAlchemy ORM models and database bootstrapping.

Tables
------
* **targets** — monitored hosts with per-target probe settings.
* **samples** — one row per hop per traceroute sample (time-series).
* **route_changes** — logged whenever the hop sequence to a target changes.
* **alerts** — user-defined threshold conditions with action config.
* **alert_history** — audit trail of fired / resolved alert events.
* **sessions** — named time-range bookmarks for data export and replay.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    create_engine,
    func,
)
from sqlalchemy.orm import DeclarativeBase, relationship, sessionmaker

from pingwatcher.config import get_settings


class Base(DeclarativeBase):
    """Declarative base for all PingWatcher ORM models."""


class Target(Base):
    """A host being monitored by the packet engine.

    Attributes:
        id: Unique identifier (UUID-style string).
        host: Hostname or IP address to trace.
        label: Optional human-friendly display name.
        trace_interval: Seconds between successive samples.
        packet_type: Probe protocol — ``icmp``, ``udp``, or ``tcp``.
        packet_size: ICMP payload size in bytes.
        max_hops: Maximum TTL value to send.
        timeout: Per-probe timeout in seconds.
        active: Whether the scheduler is currently tracing this target.
        created_at: Row creation timestamp.
    """

    __tablename__ = "targets"

    id = Column(String, primary_key=True)
    host = Column(String, nullable=False)
    label = Column(String, nullable=True)
    trace_interval = Column(Float, default=2.5)
    packet_type = Column(String, default="icmp")
    packet_size = Column(Integer, default=56)
    max_hops = Column(Integer, default=30)
    timeout = Column(Float, default=3.0)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=func.now())

    samples = relationship("Sample", back_populates="target", cascade="all, delete-orphan")
    route_changes = relationship(
        "RouteChange", back_populates="target", cascade="all, delete-orphan"
    )
    alerts = relationship("Alert", back_populates="target", cascade="all, delete-orphan")
    sessions = relationship("Session", back_populates="target", cascade="all, delete-orphan")


class Sample(Base):
    """A single hop measurement from one traceroute sample.

    Every traceroute run produces one :class:`Sample` row for each hop
    discovered.  Timed-out hops have ``rtt_ms`` set to ``NULL`` and
    ``is_timeout`` set to ``True``.

    Attributes:
        id: Auto-incrementing primary key.
        target_id: FK to the monitored :class:`Target`.
        sampled_at: Timestamp of the traceroute run this hop belongs to.
        hop_number: TTL / hop position (1-based).
        ip: IP address of the responding router (``None`` on timeout).
        dns: Reverse-DNS hostname (``None`` if lookup failed).
        rtt_ms: Round-trip time in milliseconds (``None`` on timeout).
        is_timeout: ``True`` when the probe timed out (packet lost).
    """

    __tablename__ = "samples"

    id = Column(Integer, primary_key=True, autoincrement=True)
    target_id = Column(String, ForeignKey("targets.id", ondelete="CASCADE"), nullable=False)
    sampled_at = Column(DateTime, nullable=False)
    hop_number = Column(Integer, nullable=False)
    ip = Column(String, nullable=True)
    dns = Column(String, nullable=True)
    rtt_ms = Column(Float, nullable=True)
    is_timeout = Column(Boolean, default=False)

    target = relationship("Target", back_populates="samples")

    __table_args__ = (
        Index("idx_samples_target_time", "target_id", "sampled_at"),
        Index("idx_samples_target_hop", "target_id", "hop_number"),
    )


class RouteChange(Base):
    """Record of a detected route change for a target.

    Attributes:
        id: Auto-incrementing primary key.
        target_id: FK to the affected :class:`Target`.
        detected_at: Timestamp when the route change was noticed.
        old_route: Comma-separated list of old hop IPs.
        new_route: Comma-separated list of new hop IPs.
    """

    __tablename__ = "route_changes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    target_id = Column(String, ForeignKey("targets.id", ondelete="CASCADE"), nullable=False)
    detected_at = Column(DateTime, nullable=False)
    old_route = Column(Text, nullable=True)
    new_route = Column(Text, nullable=True)

    target = relationship("Target", back_populates="route_changes")


class Alert(Base):
    """A user-defined alert rule attached to a target.

    Attributes:
        id: Unique identifier (UUID-style string).
        target_id: FK to the :class:`Target` being watched.
        metric: The statistic to evaluate (``packet_loss_pct``,
            ``avg_rtt_ms``, ``cur_rtt_ms``).
        operator: Comparison operator (``>``, ``<``, ``>=``, ``<=``).
        threshold: Numeric threshold value.
        duration_samples: How many consecutive samples must breach the
            threshold before the alert fires.
        hop: Which hop to evaluate — ``any``, ``final``, or a
            specific IP address.
        action_type: Dispatch method — ``email``, ``webhook``,
            ``log``, ``command``.
        action_config: JSON-encoded action-specific parameters.
        enabled: Whether the alert rule is active.
        last_triggered_at: Most recent trigger timestamp.
        consecutive_triggers: Running count of consecutive breaches.
        created_at: Row creation timestamp.
    """

    __tablename__ = "alerts"

    id = Column(String, primary_key=True)
    target_id = Column(String, ForeignKey("targets.id", ondelete="CASCADE"), nullable=False)
    metric = Column(String, nullable=False)
    operator = Column(String, nullable=False)
    threshold = Column(Float, nullable=False)
    duration_samples = Column(Integer, default=1)
    hop = Column(String, default="final")
    action_type = Column(String, nullable=False)
    action_config = Column(Text, nullable=True)
    enabled = Column(Boolean, default=True)
    last_triggered_at = Column(DateTime, nullable=True)
    consecutive_triggers = Column(Integer, default=0)
    created_at = Column(DateTime, default=func.now())

    target = relationship("Target", back_populates="alerts")
    history = relationship("AlertHistory", back_populates="alert", cascade="all, delete-orphan")


class AlertHistory(Base):
    """Audit row created each time an :class:`Alert` fires or resolves.

    Attributes:
        id: Auto-incrementing primary key.
        alert_id: FK to the originating :class:`Alert`.
        target_id: FK to the affected :class:`Target`.
        triggered_at: When the alert condition was met.
        resolved_at: When the condition cleared (``None`` while active).
        metric_value: The metric reading that caused the trigger.
        message: Human-readable description of the event.
    """

    __tablename__ = "alert_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    alert_id = Column(String, ForeignKey("alerts.id", ondelete="CASCADE"), nullable=False)
    target_id = Column(String, ForeignKey("targets.id", ondelete="CASCADE"), nullable=False)
    triggered_at = Column(DateTime, nullable=False)
    resolved_at = Column(DateTime, nullable=True)
    metric_value = Column(Float, nullable=True)
    message = Column(String, nullable=True)

    alert = relationship("Alert", back_populates="history")


class Session(Base):
    """A named time-range bookmark for export / replay.

    Attributes:
        id: Unique identifier (UUID-style string).
        target_id: FK to the :class:`Target`.
        name: User-given session label.
        start_time: Beginning of the time window.
        end_time: End of the time window (``None`` while recording).
        created_at: Row creation timestamp.
    """

    __tablename__ = "sessions"

    id = Column(String, primary_key=True)
    target_id = Column(String, ForeignKey("targets.id", ondelete="CASCADE"), nullable=False)
    name = Column(String, nullable=True)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=func.now())

    target = relationship("Target", back_populates="sessions")


# ---------------------------------------------------------------------------
# Engine / session factory
# ---------------------------------------------------------------------------

_cfg = get_settings()
engine = create_engine(
    _cfg.database_url,
    connect_args={"check_same_thread": False} if "sqlite" in _cfg.database_url else {},
    echo=False,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    """Create all tables that do not yet exist."""
    Base.metadata.create_all(bind=engine)


def get_db():
    """FastAPI dependency that yields a database session.

    Yields:
        A :class:`sqlalchemy.orm.Session` instance that is closed
        after the request finishes.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
