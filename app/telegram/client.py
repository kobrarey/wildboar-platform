from __future__ import annotations

import logging

import requests

from app.config import settings

log = logging.getLogger("app.telegram")


def send_telegram_message(text: str) -> None:
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        log.info("Telegram not configured. Skip message: %s", text)
        return

    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"

    try:
        requests.post(
            url,
            json={"chat_id": settings.TELEGRAM_CHAT_ID, "text": text},
            timeout=10,
        )
    except Exception as exc:
        log.warning("Telegram message failed: %s", exc)
