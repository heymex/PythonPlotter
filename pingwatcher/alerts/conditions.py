"""Alert condition evaluator and state machine.

Each :class:`~pingwatcher.db.models.Alert` has a *metric*, *operator*,
*threshold*, and *duration_samples* that together define when the alert
should fire.  This module provides the logic for checking conditions,
tracking consecutive breaches, and dispatching configured actions.
"""

import logging
import operator as op
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from pingwatcher.db.models import Alert
from pingwatcher.db.queries import (
    get_active_alerts,
    get_all_hop_stats,
    record_alert_event,
)

logger = logging.getLogger(__name__)

#: Map of string operators to callables.
OPERATORS: dict[str, Any] = {
    ">": op.gt,
    "<": op.lt,
    ">=": op.ge,
    "<=": op.le,
}


def _extract_metric(stats: dict[str, Any], metric: str) -> float | None:
    """Pull the named metric out of a hop-stats dictionary.

    Args:
        stats: Dictionary returned by
            :func:`~pingwatcher.db.queries.get_hop_stats`.
        metric: One of ``packet_loss_pct``, ``avg_rtt_ms``, or
            ``cur_rtt_ms``.

    Returns:
        Numeric value, or ``None`` when unavailable.
    """
    mapping = {
        "packet_loss_pct": "packet_loss_pct",
        "avg_rtt_ms": "avg_ms",
        "cur_rtt_ms": "cur_ms",
    }
    key = mapping.get(metric)
    if key is None:
        return None
    return stats.get(key)


def _find_matching_hops(
    all_stats: list[dict[str, Any]],
    hop_selector: str,
) -> list[dict[str, Any]]:
    """Filter hop-stats to the hops relevant for an alert rule.

    Args:
        all_stats: Full list of per-hop stat dictionaries.
        hop_selector: ``"any"``, ``"final"``, or a specific IP string.

    Returns:
        Filtered list of hop-stats dictionaries.
    """
    if not all_stats:
        return []
    if hop_selector == "final":
        return [all_stats[-1]]
    if hop_selector == "any":
        return all_stats
    # Specific IP.
    return [s for s in all_stats if s.get("ip") == hop_selector]


def check_condition(alert: Alert, all_stats: list[dict[str, Any]]) -> tuple[bool, float | None]:
    """Evaluate whether an alert's condition is currently breached.

    Args:
        alert: The :class:`Alert` rule to check.
        all_stats: Per-hop statistics for the current focus window.

    Returns:
        Tuple of ``(is_triggered, metric_value)``.  *metric_value* is
        the reading that caused (or did not cause) the trigger.
    """
    cmp_fn = OPERATORS.get(alert.operator)
    if cmp_fn is None:
        logger.warning("Unknown operator %r on alert %s", alert.operator, alert.id)
        return False, None

    hops = _find_matching_hops(all_stats, alert.hop)
    for hop_stats in hops:
        value = _extract_metric(hop_stats, alert.metric)
        if value is not None and cmp_fn(value, alert.threshold):
            return True, value

    # Return the metric value from the first matching hop for logging.
    if hops:
        value = _extract_metric(hops[0], alert.metric)
        return False, value
    return False, None


def evaluate_alerts(
    db: Session,
    target_id: str,
    focus_n: int = 10,
    all_stats: list[dict[str, Any]] | None = None,
) -> None:
    """Run all enabled alert rules for a target against current data.

    This is called after every sample collection.  It updates the
    ``consecutive_triggers`` counter, fires actions when
    ``duration_samples`` is reached, and logs recovery when conditions
    clear.

    Args:
        db: Active database session.
        target_id: UUID-style target identifier.
        focus_n: Focus window size for stat computation.
        all_stats: Pre-computed hop statistics from the current sample
            cycle.  When supplied the DB query inside this function is
            skipped entirely, avoiding redundant round-trips.
    """
    alerts = get_active_alerts(db, target_id)
    if not alerts:
        return

    if all_stats is None:
        all_stats = get_all_hop_stats(db, target_id, focus_n=focus_n)

    for alert in alerts:
        triggered, value = check_condition(alert, all_stats)
        _handle_state_change(db, alert, triggered, value)


def _handle_state_change(
    db: Session,
    alert: Alert,
    triggered: bool,
    metric_value: float | None,
) -> None:
    """Update alert state counters and dispatch actions when appropriate.

    Args:
        db: Active database session.
        alert: The :class:`Alert` being evaluated.
        triggered: Whether the condition is currently met.
        metric_value: The metric reading that was evaluated.
    """
    if triggered:
        alert.consecutive_triggers += 1
        if alert.consecutive_triggers >= alert.duration_samples:
            _fire_alert(db, alert, metric_value)
    else:
        if alert.consecutive_triggers >= alert.duration_samples:
            logger.info("Alert %s recovered (was active for %d samples)", alert.id, alert.consecutive_triggers)
        alert.consecutive_triggers = 0

    db.commit()


def _fire_alert(db: Session, alert: Alert, metric_value: float | None) -> None:
    """Dispatch the configured action and record the event.

    Args:
        db: Active database session.
        alert: The firing :class:`Alert`.
        metric_value: The offending metric reading.
    """
    message = (
        f"Alert {alert.id}: {alert.metric} {alert.operator} {alert.threshold} "
        f"(value={metric_value}) on target {alert.target_id}, hop={alert.hop}"
    )
    logger.warning(message)

    alert.last_triggered_at = datetime.utcnow()
    record_alert_event(db, alert, metric_value or 0.0, message)

    # Dispatch via the appropriate action plugin.
    from pingwatcher.alerts.actions import dispatch_action

    dispatch_action(alert.action_type, alert.action_config, message)
