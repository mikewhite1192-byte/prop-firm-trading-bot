from __future__ import annotations

from datetime import datetime

from trading_bot.strategies.base import Strategy, StrategySignal


class BBZScoreEURUSDStrategy(Strategy):
    """Strategy 3 — Bollinger Band z-score mean reversion on EUR/USD.

    Entry: z-score beyond +/-2.0 on 20-period BB (M15/H1),
           RSI(14) < 30 long or > 70 short, H4 ADX < 20 (range regime).
    Exit:  return to z-score +/-0.5 (mid-band).
    Session filter: Asian (23:00-07:00 GMT) or late NY low-vol only.
    Cadence: every 15 minutes during allowed sessions.
    Broker: OANDA demo. Challenge target: FTMO $10k.
    """

    name = "BBZ_EURUSD"
    asset = "EUR_USD"
    timeframe = "M15"

    async def check(self, now: datetime) -> StrategySignal | None:
        # TODO Phase 2: pull 20-period BB on M15 via OANDA candles endpoint,
        # compute z-score, check RSI(14) and H4 ADX(14), apply session filter.
        return None
