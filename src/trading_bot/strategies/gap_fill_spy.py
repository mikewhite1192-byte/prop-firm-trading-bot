from __future__ import annotations

from datetime import datetime

from trading_bot.strategies.base import Strategy, StrategySignal


class GapFillSPYStrategy(Strategy):
    """Strategy 2 — Gap fill fade on SPY/QQQ (MNQ for futures).

    Entry: overnight gap 0.15-0.6% — fade gap-down above 200-SMA, fade
           gap-up below 200-SMA.
    Exit:  prior close OR noon ET time stop.
    Filters: skip gaps > 1%, skip Mondays, skip FOMC days.
    Cadence: 9:30 ET session open.
    Paper broker: Alpaca. Challenge target: Bulenox (MNQ).
    """

    name = "GAPFILL_SPY"
    asset = "SPY"
    timeframe = "1m"

    async def check(self, now: datetime) -> StrategySignal | None:
        # TODO Phase 2: compute overnight gap from prior close to open, apply
        # 200-SMA direction filter, emit signal only in 0.15-0.6% band.
        return None
