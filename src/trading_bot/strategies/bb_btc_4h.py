from __future__ import annotations

from datetime import datetime

from trading_bot.strategies.base import Strategy, StrategySignal


class BBBTCStrategy(Strategy):
    """Strategy 6 — BTC Bollinger Band mean reversion on 4H.

    Entry: 4H close below lower 20-period BB, RSI(14) < 30, daily still
           above 200-MA, ADX < 25 (range regime).
    Exit:  scale at mid-band, scale again at upper band.
    Stop:  1.5x ATR below entry.
    Cadence: every 4H bar close.
    Broker: Alpaca crypto. Challenge target: FTMO crypto CFD.
    """

    name = "BB_BTC_4H"
    asset = "BTC/USD"
    timeframe = "4H"

    async def check(self, now: datetime) -> StrategySignal | None:
        # TODO Phase 2: pull 4H candles from Alpaca crypto, compute BB(20)/RSI(14)/ADX(14),
        # check daily 200-MA regime, emit signal on lower band break.
        return None
