"""Strategy 2 — Gap fill fade on SPY (MNQ when promoted to futures).

No strong OSS reference exists for this exact spec (research flagged
`Andrew-Biggins/GapTrader` but it's C# and uses Fibonacci entries instead of
direction filters). Implementation is fresh, ~100 lines.

Entry: overnight gap 0.15-0.6%. Fade gap-downs when prior close > SMA(200),
       fade gap-ups when prior close < SMA(200).
Exit:  prior close target OR noon ET time stop.
Filter: skip gaps > 1%, skip Monday, skip FOMC / NFP days.
Paper broker: Alpaca.  Challenge target: Bulenox (MNQ).
"""

from __future__ import annotations

from datetime import time as dtime
from decimal import Decimal

import numpy as np
import pandas as pd
from lumibot.entities import Asset

from trading_bot.brokers.base_types import OrderSide
from trading_bot.learning import classify_regime
from trading_bot.strategies.base import RiskGatedStrategy


class GapFillSPY(RiskGatedStrategy):
    firm = "Alpaca_Paper"
    strategy_name = "GAPFILL_SPY"

    parameters = {
        "symbol": "SPY",
        "sma_period": 200,
        "gap_min_pct": 0.0015,   # 0.15%
        "gap_max_pct": 0.006,    # 0.6%
        "gap_skip_pct": 0.01,    # skip gaps > 1%
        "time_stop_hour": 12,    # noon ET flat-out
        "risk_per_trade_pct": 0.0075,
    }

    def initialize(self, parameters: dict | None = None) -> None:
        super().initialize(parameters)
        self.sleeptime = "1M"
        self.set_market("NYSE")
        self._asset = Asset(symbol=self.parameters["symbol"], asset_type=Asset.AssetType.STOCK)
        self._entered_today = False

    def before_market_opens(self) -> None:
        self._entered_today = False

    def on_trading_iteration(self) -> None:
        now_et = self.get_datetime()
        if now_et.weekday() == 0:  # skip Monday per spec
            return

        if now_et.time() >= dtime(self.parameters["time_stop_hour"], 0):
            if self.get_position(self._asset):
                self.sell_all(cancel_open_orders=True)
                self.log_message("EXIT GAPFILL_SPY — noon time stop")
            return

        if self._entered_today or self.get_position(self._asset):
            return

        # TODO Phase 2: consult news calendar to skip FOMC / NFP days.

        daily = self.get_historical_prices(self._asset, length=self.parameters["sma_period"] + 5, timestep="day")
        minute = self.get_historical_prices(self._asset, length=5, timestep="minute")
        if daily is None or daily.df is None or minute is None or minute.df is None:
            return
        if len(daily.df) < self.parameters["sma_period"]:
            return

        prior_close = daily.df["close"].iloc[-1]
        today_open = minute.df["open"].iloc[0]
        sma = daily.df["close"].rolling(self.parameters["sma_period"]).mean().iloc[-1]
        if np.isnan(sma):
            return

        gap_pct = (today_open - prior_close) / prior_close
        abs_gap = abs(gap_pct)
        if abs_gap < self.parameters["gap_min_pct"] or abs_gap > self.parameters["gap_skip_pct"]:
            return
        if abs_gap > self.parameters["gap_max_pct"]:
            return  # still inside skip; log noise not worth it

        fade_side: OrderSide | None = None
        if gap_pct < 0 and prior_close > sma:
            fade_side = OrderSide.BUY   # gap-down in uptrend -> buy the gap
        elif gap_pct > 0 and prior_close < sma:
            fade_side = OrderSide.SELL  # gap-up in downtrend -> sell the gap
        if fade_side is None:
            return

        last = minute.df["close"].iloc[-1]
        target = prior_close
        stop_distance = abs(last - target) * Decimal("1.5") if isinstance(last, Decimal) else (
            abs(Decimal(str(last)) - Decimal(str(target))) * Decimal("1.5")
        )
        entry = Decimal(str(last))
        stop = entry - stop_distance if fade_side == OrderSide.BUY else entry + stop_distance
        take_profit = Decimal(str(target))
        qty = self._position_size(entry, stop)
        if qty <= 0:
            return

        self.propose_entry(
            asset=self._asset,
            side=fade_side,
            quantity=qty,
            entry_price=entry,
            stop_loss=stop,
            take_profit=take_profit,
            reason=f"gap={gap_pct:.3%} vs SMA({self.parameters['sma_period']}) "
            f"{'>' if prior_close > sma else '<='}",
            market_regime=classify_regime(daily.df),
        )
        self._entered_today = True

    def _position_size(self, entry: Decimal, stop: Decimal) -> Decimal:
        risk_dollar = Decimal(str(self.portfolio_value)) * Decimal(
            str(self.parameters["risk_per_trade_pct"])
        )
        distance = abs(entry - stop)
        if distance == 0:
            return Decimal("0")
        return (risk_dollar / distance).quantize(Decimal("1"))
