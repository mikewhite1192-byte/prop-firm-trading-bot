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

    Synchronous on purpose — call sites are low-rate (a few alerts per day)
    and live inside Lumibot's thread-based strategy loops. Both channels
    silently no-op if their credentials are unset.
    """

    def __init__(self) -> None:
        self._telegram = TelegramNotifier()
        self._email = EmailNotifier()

    def send(self, severity: Severity, title: str, body: str) -> None:
        prefix = f"[{severity.value}] {title}"
        log.info("notify %s: %s", prefix, body)
        self._telegram.send(prefix, body)
        self._email.send(prefix, body)
