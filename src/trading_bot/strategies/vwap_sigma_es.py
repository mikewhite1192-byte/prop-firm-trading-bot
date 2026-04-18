from __future__ import annotations

from datetime import datetime

from trading_bot.strategies.base import Strategy, StrategySignal


class VWAPSigmaESStrategy(Strategy):
    """Strategy 4 — VWAP +/-2 sigma reversion on ES/MES.

    Entry: fade touches of VWAP +/-2 sigma 30-120 min into RTH,
           requires stall candle or failed breakout confirmation.
    Exit:  target VWAP, runner to opposite +/-1 sigma.
    Stop:  6-8 ticks beyond the sigma extreme.
    Filter: skip if 9:30-10:00 bar never trades back through VWAP (trend day).
    Cadence: every minute between 10:00 and 11:30 ET.
    Broker: Tradovate sim. Challenge target: MyFundedFutures Core.
    """

    name = "VWAP_SIGMA_ES"
    asset = "ES"
    timeframe = "1m"

    async def check(self, now: datetime) -> StrategySignal | None:
        # TODO Phase 2: compute session VWAP + rolling sigma bands, check stall
        # candle pattern on sigma touch, trend-day filter on 9:30-10:00 bar.
        return None
