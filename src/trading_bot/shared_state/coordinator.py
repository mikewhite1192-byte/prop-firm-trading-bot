"""Cross-process coordination for the 6 strategy processes.

Lumibot's one-strategy-per-process model means prop-firm-wide rules —
news blackouts, consistency tracking, cross-account hedging, the global
halt broadcast on a hard-stop — can't live in a single object. This module
is the shared layer, backed by Postgres (advisory locks + LISTEN/NOTIFY).

Two channels in use:
  * ``halts``        — emitted when any strategy triggers a hard-stop so
                       sibling strategies flatten immediately.
  * ``news_windows`` — emitted when the scraper inserts an upcoming event
                       so strategies can pre-cancel working orders.

Reads of the news_windows table are the hot path for the risk engine
itself — see ``RiskEngine._active_news_window``.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text

from trading_bot.db.models import NewsWindow
from trading_bot.db.session import engine, get_session

log = logging.getLogger(__name__)


class SharedStateCoordinator:
    """Thin wrapper over Postgres pub/sub for strategy processes."""

    HALT_CHANNEL = "trading_bot_halts"
    NEWS_CHANNEL = "trading_bot_news"

    def __init__(self, strategy_name: str) -> None:
        self._strategy_name = strategy_name

    def broadcast_halt(self, reason: str) -> None:
        payload = json.dumps({"strategy": self._strategy_name, "reason": reason})
        with engine.begin() as conn:
            conn.execute(text(f"NOTIFY {self.HALT_CHANNEL}, :payload"), {"payload": payload})
        log.warning("broadcast halt: %s — %s", self._strategy_name, reason)

    def upcoming_news_windows(
        self, within: timedelta = timedelta(hours=24), impact: str = "HIGH"
    ) -> list[NewsWindow]:
        now = datetime.now(timezone.utc)
        until = now + within
        with get_session() as s:
            rows = (
                s.execute(
                    select(NewsWindow)
                    .where(
                        NewsWindow.starts_at >= now,
                        NewsWindow.starts_at <= until,
                        NewsWindow.impact == impact,
                    )
                    .order_by(NewsWindow.starts_at)
                )
                .scalars()
                .all()
            )
            for r in rows:
                s.expunge(r)
        return rows


def broadcast_halt(strategy_name: str, reason: str) -> None:
    SharedStateCoordinator(strategy_name).broadcast_halt(reason)


def is_news_blackout(buffer_minutes: int = 30, impact: str = "HIGH") -> bool:
    """True if we're currently inside a news window within the buffer."""
    now = datetime.now(timezone.utc)
    buf = timedelta(minutes=buffer_minutes)
    with get_session() as s:
        window = (
            s.execute(
                select(NewsWindow).where(
                    NewsWindow.starts_at - buf <= now,
                    NewsWindow.ends_at + buf >= now,
                    NewsWindow.impact == impact,
                )
            )
            .scalars()
            .first()
        )
    if window is not None:
        log.info(
            "news blackout active: %s %s-%s (%s)",
            window.event,
            window.starts_at,
            window.ends_at,
            window.currency,
        )
    return window is not None


def register_strategy_trade(
    *,
    firm: str,
    strategy_name: str,
    pnl: float,
    trade_date: datetime,
    trade_count_delta: int = 1,
) -> None:
    """Roll the day's P&L into a central tally for consistency-rule checks.

    The funded-mode consistency rule (no day > 30% of total profit) needs
    a per-firm view across strategies sharing that firm.
    """
    from decimal import Decimal

    from trading_bot.db.models import StrategyDailyPnL

    delta = Decimal(str(pnl))
    day = trade_date.date()
    with get_session() as s:
        existing = s.execute(
            select(StrategyDailyPnL).where(
                StrategyDailyPnL.firm == firm,
                StrategyDailyPnL.strategy_name == strategy_name,
                StrategyDailyPnL.trade_date == day,
            )
        ).scalar_one_or_none()
        if existing is None:
            s.add(
                StrategyDailyPnL(
                    firm=firm,
                    strategy_name=strategy_name,
                    trade_date=day,
                    pnl=delta,
                    trade_count=trade_count_delta,
                )
            )
        else:
            existing.pnl = existing.pnl + delta
            existing.trade_count = existing.trade_count + trade_count_delta
