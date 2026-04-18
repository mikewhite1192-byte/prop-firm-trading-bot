"""Cross-process coordination for the 6 strategy processes.

Lumibot's one-strategy-per-process model means our prop-firm-wide rules —
news blackouts, consistency tracking, cross-account hedging, the global
halt broadcast on a hard-stop — can't live in a single object. This module
is the shared layer, backed by Postgres (advisory locks + LISTEN/NOTIFY).

Two channels in use:
  * ``halts``        — emitted when any strategy triggers a hard-stop so
                       sibling strategies flatten immediately.
  * ``news_windows`` — emitted when the news-calendar job posts an
                       upcoming FOMC/NFP window; strategies check before
                       opening a new position.

Consumers call :func:`is_news_blackout` and
:func:`register_strategy_trade` as pure reads/writes; subscribers to the
``halts`` channel are wired up in each strategy's run_*.py entrypoint
before ``Trader.run_all()`` is invoked.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from trading_bot.db.session import engine, get_session

log = logging.getLogger(__name__)


@dataclass(slots=True)
class NewsWindow:
    event: str           # "FOMC", "NFP", etc.
    start: datetime      # UTC
    end: datetime        # UTC


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

    def upcoming_news_windows(self, within: timedelta = timedelta(minutes=45)) -> list[NewsWindow]:
        # TODO Phase 2: query a news_calendar table populated by a daily job
        # (e.g. ForexFactory / ICE economic calendar scrape). For Phase 1 this
        # returns empty so strategies don't block; the risk engine's own
        # news buffer still applies at order time.
        return []


_GLOBAL_COORDINATOR: SharedStateCoordinator | None = None


def _coordinator(strategy_name: str = "") -> SharedStateCoordinator:
    global _GLOBAL_COORDINATOR
    if _GLOBAL_COORDINATOR is None:
        _GLOBAL_COORDINATOR = SharedStateCoordinator(strategy_name or "unknown")
    return _GLOBAL_COORDINATOR


def broadcast_halt(strategy_name: str, reason: str) -> None:
    SharedStateCoordinator(strategy_name).broadcast_halt(reason)


def is_news_blackout(asset_class: str, buffer_minutes: int = 30) -> bool:
    """True if we're currently inside a news window that affects this asset class."""
    now = datetime.now(timezone.utc)
    coord = _coordinator()
    for w in coord.upcoming_news_windows():
        pad = timedelta(minutes=buffer_minutes)
        if (w.start - pad) <= now <= (w.end + pad):
            log.info("news blackout active: %s %s-%s", w.event, w.start, w.end)
            return True
    return False


def register_strategy_trade(strategy_name: str, pnl: float, trade_date: datetime) -> None:
    """Roll the day's P&L into a central tally for consistency-rule checks.

    The funded-mode consistency rule (no day > 30% of total profit) needs
    a per-firm view across strategies sharing that firm. Phase 1 updates
    only the per-account ``daily_summary`` row; the per-firm aggregation
    query is what the risk engine will read when the rule is turned on
    for funded accounts. Implementing that aggregation as a SQL view
    rather than another table keeps the write path single-source.
    """
    # TODO Phase 2: wire to daily_summary via the trade logger once trades
    # are actually filling. Phase 1 scaffold — intentionally no-op so
    # strategies can import the symbol without dragging in unrun schema.
    log.debug("register_strategy_trade stub: %s %+.2f %s", strategy_name, pnl, trade_date.date())
