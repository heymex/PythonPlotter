"""Alert action: POST a JSON payload to a webhook URL.

Expected ``action_config`` keys::

    {
        "url": "https://hooks.example.com/alert",
        "headers": {"Authorization": "Bearer abc"}
    }
"""

import logging

import httpx

logger = logging.getLogger(__name__)


def send_webhook(config: dict, message: str) -> None:
    """Fire an HTTP POST with the alert payload.

    Args:
        config: Must contain ``url``; may contain custom ``headers``.
        message: The alert body text.
    """
    url = config.get("url")
    if not url:
        logger.error("Webhook alert skipped — no 'url' configured")
        return

    headers = config.get("headers", {})
    payload = {"message": message}

    try:
        resp = httpx.post(url, json=payload, headers=headers, timeout=10)
        resp.raise_for_status()
        logger.info("Webhook alert delivered to %s (status %d)", url, resp.status_code)
    except Exception:
        logger.exception("Failed to deliver webhook alert to %s", url)
