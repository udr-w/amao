from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)

MAX_MESSAGE_CHARS = 3500  # keep well under Slack/Discord payload limits


class Notifier:
    def __init__(self, webhook_url: str = "") -> None:
        if webhook_url and not webhook_url.startswith("https://"):
            raise ValueError("webhook_url must use https://")
        self.webhook_url = webhook_url

    def notify(self, title: str, message: str, requires_human: bool = False) -> None:
        message = message[:MAX_MESSAGE_CHARS]
        log_msg = f"--- ALERT [{title}] ---\n{message}\n"
        if requires_human:
            log_msg += "ACTION REQUIRED: Human intervention needed.\n"
        logger.info(log_msg)

        if not self.webhook_url:
            return

        payload = {
            "text": f"Agent Pipeline Alert: {title}\n{message}"
            + ("\nSTUCK: Human support requested." if requires_human else "")
        }
        try:
            requests.post(self.webhook_url, json=payload, timeout=5)
        except requests.RequestException as e:
            logger.error("Failed to dispatch webhook notification: %s", e)
