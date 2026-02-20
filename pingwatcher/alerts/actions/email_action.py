"""Alert action: send an email notification via SMTP.

Expected ``action_config`` keys::

    {
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "smtp_user": "alerts@example.com",
        "smtp_password": "s3cret",
        "from_addr": "alerts@example.com",
        "to_addr": "ops@example.com",
        "subject_prefix": "[PingWatcher]"
    }
"""

import logging
import smtplib
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)


def send_email_alert(config: dict, message: str) -> None:
    """Compose and send a plain-text alert email.

    Args:
        config: SMTP connection and addressing parameters.
        message: The alert body text.
    """
    host = config.get("smtp_host", "localhost")
    port = int(config.get("smtp_port", 587))
    user = config.get("smtp_user")
    password = config.get("smtp_password")
    from_addr = config.get("from_addr", user or "pingwatcher@localhost")
    to_addr = config.get("to_addr")
    prefix = config.get("subject_prefix", "[PingWatcher]")

    if not to_addr:
        logger.error("Email alert skipped — no 'to_addr' configured")
        return

    msg = MIMEText(message)
    msg["Subject"] = f"{prefix} Alert Triggered"
    msg["From"] = from_addr
    msg["To"] = to_addr

    try:
        with smtplib.SMTP(host, port, timeout=10) as smtp:
            smtp.ehlo()
            if port != 25:
                smtp.starttls()
                smtp.ehlo()
            if user and password:
                smtp.login(user, password)
            smtp.sendmail(from_addr, [to_addr], msg.as_string())
        logger.info("Email alert sent to %s", to_addr)
    except Exception:
        logger.exception("Failed to send email alert to %s", to_addr)
