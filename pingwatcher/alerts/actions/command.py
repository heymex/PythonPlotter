"""Alert action: execute a shell command.

Expected ``action_config`` keys::

    {
        "command": "/usr/local/bin/notify-ops --message '{message}'"
    }

The literal ``{message}`` placeholder in the command string is replaced
with the alert text.
"""

import logging
import subprocess

logger = logging.getLogger(__name__)


def run_command(config: dict, message: str) -> None:
    """Run a shell command, injecting the alert message.

    Args:
        config: Must contain ``command``.
        message: The alert body text.
    """
    cmd_template = config.get("command")
    if not cmd_template:
        logger.error("Command alert skipped — no 'command' configured")
        return

    cmd = cmd_template.replace("{message}", message)

    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.warning(
                "Alert command exited %d: %s", result.returncode, result.stderr.strip()
            )
        else:
            logger.info("Alert command executed successfully")
    except subprocess.TimeoutExpired:
        logger.error("Alert command timed out: %s", cmd)
    except Exception:
        logger.exception("Alert command failed: %s", cmd)
