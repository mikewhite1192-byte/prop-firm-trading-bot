from __future__ import annotations

import logging

import httpx

from trading_bot.config import get_settings

log = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self) -> None:
        s = get_settings()
        self._token = s.telegram_bot_token
        self._chat_id = s.telegram_chat_id
        self._enabled = bool(self._token and self._chat_id)

    def send(self, title: str, body: str) -> None:
        if not self._enabled:
            return
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        text = f"*{title}*\n{body}"
        try:
            with httpx.Client(timeout=10.0) as client:
                client.post(
                    url,
                    json={"chat_id": self._chat_id, "text": text, "parse_mode": "Markdown"},
                )
        except Exception as e:
            log.warning("telegram send failed: %s", e)
