from __future__ import annotations

from datetime import datetime

from trading_bot.strategies.base import Strategy, StrategySignal


class TinyGapESStrategy(Strategy):
    """Strategy 5 — Tiny gap fill on ES/MES (overnight hold).

    Entry: gap < 0.3x 14-day ATR, bonus conviction if 9:30 open is inside
           prior RTH range.
    Exit:  prior RTH close.
    Stop:  $400-500 hard stop.
    Filter: skip gaps > 0.7x ATR, skip news-driven gaps.
    Cadence: 9:30 ET.
    Broker: Tradovate sim. Challenge target: MyFundedFutures Core
            (only firm permitting overnight holds with EOD DD).
    """

    name = "TINYGAP_ES"
    asset = "ES"
    timeframe = "1m"

    async def check(self, now: datetime) -> StrategySignal | None:
        # TODO Phase 2: compute 14-day ATR, normalize overnight gap, require
        # news-calendar clear window, emit entry signal when gap under 0.3 ATR.
        return None
