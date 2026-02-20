"""Alert action: append a timestamped entry to a log file.

Expected ``action_config`` keys::

    {
        "path": "/var/log/pingwatcher/alerts.log"
    }
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_DEFAULT_LOG_PATH = "pingwatcher_alerts.log"


def log_alert(config: dict, message: str) -> None:
    """Write a timestamped alert line to the configured log file.

    Args:
        config: Must contain ``path`` (defaults to
            ``pingwatcher_alerts.log`` in the working directory).
        message: The alert body text.
    """
    path = config.get("path", _DEFAULT_LOG_PATH)
    timestamp = datetime.now(timezone.utc).isoformat()
    line = f"[{timestamp}] {message}\n"

    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line)
        logger.info("Alert logged to %s", path)
    except Exception:
        logger.exception("Failed to write alert log to %s", path)
