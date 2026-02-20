"""Pluggable alert actions: email, webhook, log-file, shell command.

The :func:`dispatch_action` function is the single entry point.  It
selects the correct plugin by *action_type* and delegates execution.
"""

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def dispatch_action(action_type: str, action_config: Optional[str], message: str) -> None:
    """Route an alert event to the appropriate action handler.

    Args:
        action_type: One of ``"email"``, ``"webhook"``, ``"log"``,
            ``"command"``.
        action_config: JSON-encoded string of handler-specific settings.
        message: Human-readable alert description.
    """
    config: dict = json.loads(action_config) if action_config else {}

    if action_type == "email":
        from pingwatcher.alerts.actions.email_action import send_email_alert

        send_email_alert(config, message)
    elif action_type == "webhook":
        from pingwatcher.alerts.actions.webhook import send_webhook

        send_webhook(config, message)
    elif action_type == "log":
        from pingwatcher.alerts.actions.log_file import log_alert

        log_alert(config, message)
    elif action_type == "command":
        from pingwatcher.alerts.actions.command import run_command

        run_command(config, message)
    else:
        logger.warning("Unknown alert action type: %s", action_type)
