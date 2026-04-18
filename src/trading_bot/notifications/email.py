from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from trading_bot.config import get_settings

log = logging.getLogger(__name__)


class EmailNotifier:
    def __init__(self) -> None:
        s = get_settings()
        self._host = s.smtp_host
        self._port = s.smtp_port
        self._user = s.smtp_username
        self._password = s.smtp_password
        self._from = s.smtp_from
        self._to = s.smtp_to
        self._enabled = all([self._host, self._user, self._password, self._from, self._to])

    def send(self, title: str, body: str) -> None:
        if not self._enabled:
            return
        msg = EmailMessage()
        msg["From"] = self._from
        msg["To"] = self._to
        msg["Subject"] = title
        msg.set_content(body)
        try:
            with smtplib.SMTP(self._host, self._port, timeout=15) as server:
                server.starttls()
                server.login(self._user, self._password)
                server.send_message(msg)
        except Exception as e:
            log.warning("email send failed: %s", e)
