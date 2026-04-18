from __future__ import annotations

from datetime import datetime

from trading_bot.strategies.base import Strategy, StrategySignal


class RSI2SPYStrategy(Strategy):
    """Strategy 1 — RSI(2) mean reversion on SPY/MES.

    Entry: price > 200-day SMA AND RSI(2) < 10.
    Exit:  close > 5-day SMA.
    Stop:  2x ATR from entry.
    Cadence: once per day near the close.
    Paper broker: Alpaca. Challenge target: MyFundedFutures (MES).
    """

    name = "RSI2_SPY"
    asset = "SPY"
    timeframe = "1D"

    async def check(self, now: datetime) -> StrategySignal | None:
        # TODO Phase 2: pull OHLC from Alpaca data API, compute RSI(2) + SMA(200)/SMA(5),
        # emit StrategySignal when entry conditions met.
        return None
