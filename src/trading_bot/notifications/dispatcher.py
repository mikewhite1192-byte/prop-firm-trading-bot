from __future__ import annotations

import enum
import logging

from trading_bot.notifications.email import EmailNotifier
from trading_bot.notifications.telegram import TelegramNotifier

log = logging.getLogger(__name__)


class Severity(str, enum.Enum):
    INFO = "INFO"
    WARN = "WARN"
    CRITICAL = "CRITICAL"


class NotificationDispatcher:
    """Fan out notifications to configured channels.

    Both channels are optional — if credentials are missing the notifier
    silently no-ops. At least one channel should be wired up for live
    trading so risk-halt events are visible.
    """

    def __init__(self) -> None:
        self._telegram = TelegramNotifier()
        self._email = EmailNotifier()

    async def send(self, severity: Severity, title: str, body: str) -> None:
        prefix = f"[{severity.value}] {title}"
        log.info("notify %s: %s", prefix, body)
        await self._telegram.send(prefix, body)
        await self._email.send(prefix, body)
