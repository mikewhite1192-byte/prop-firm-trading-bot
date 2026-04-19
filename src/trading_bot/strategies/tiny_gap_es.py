"""Strategy 5 — Tiny gap fill on ES / MES (overnight hold).

No direct OSS reference — built as a variant of GapFillSPY with ATR-normalised
gap classification per the FMZ Quant writeup
https://medium.com/@FMZQuant/dynamic-gap-fill-mean-reversion-strategy-trend-volume-filters-2f41a9b3bcc1

Entry: gap < 0.3x 14-day ATR; higher conviction if 9:30 open is inside
       prior RTH range.
Exit:  prior RTH close.
Stop:  $400-500 hard stop per contract.
Broker: Tradovate sim. Challenge target: MyFundedFutures Core (only firm
        permitting overnight holds with EOD DD).
"""

from __future__ import annotations

from datetime import time as dtime
from decimal import Decimal

import numpy as np
from lumibot.entities import Asset

from trading_bot.brokers.base_types import OrderSide
from trading_bot.indicators import atr
from trading_bot.learning import classify_regime
from trading_bot.strategies.base import RiskGatedStrategy

ES_TICK_SIZE = Decimal("0.25")
ES_TICK_VALUE = Decimal("12.50")


class TinyGapES(RiskGatedStrategy):
    firm = "Tradovate_Sim"
    strategy_name = "TINYGAP_ES"

    parameters = {
        "symbol": "ES",
        "atr_period": 14,
        "gap_atr_max_mult": 0.3,
        "gap_atr_skip_mult": 0.7,
        "stop_dollars": Decimal("450"),
        "risk_per_trade_pct": 0.0075,
    }

    def initialize(self, parameters: dict | None = None) -> None:
        super().initialize(parameters)
        self.sleeptime = "1M"
        self._asset = Asset(
            symbol=self.parameters["symbol"], asset_type=Asset.AssetType.FUTURE
        )
        self._entered_today = False

    def before_market_opens(self) -> None:
        self._entered_today = False

    def on_trading_iteration(self) -> None:
        self._heartbeat("tick")
        now_et = self.get_datetime()
        if now_et.time() < dtime(9, 30) or now_et.time() > dtime(9, 35):
            return  # only evaluate in the first ~5 minutes of RTH
        if self._entered_today or self.get_position(self._asset):
            return

        daily = self.get_historical_prices(
            self._asset, length=self.parameters["atr_period"] + 5, timestep="day"
        )
        minute = self.get_historical_prices(self._asset, length=10, timestep="minute")
        if daily is None or daily.df is None or minute is None or minute.df is None:
            return
        if len(daily.df) < self.parameters["atr_period"] + 1:
            return

        atr_val = atr(daily.df, self.parameters["atr_period"]).iloc[-1]
        prior_rth_close = daily.df["close"].iloc[-1]
        prior_high = daily.df["high"].iloc[-1]
        prior_low = daily.df["low"].iloc[-1]
        today_open = minute.df["open"].iloc[0]
        last = minute.df["close"].iloc[-1]
        if np.isnan(atr_val):
            return

        gap = today_open - prior_rth_close
        atr_norm = abs(gap) / atr_val if atr_val else float("inf")
        if atr_norm > self.parameters["gap_atr_skip_mult"]:
            return
        if atr_norm > self.parameters["gap_atr_max_mult"]:
            return  # in the skip band

        # Higher conviction if today's open sits inside prior RTH range.
        inside_prior = prior_low <= today_open <= prior_high
        if not inside_prior:
            return

        side = OrderSide.BUY if gap < 0 else OrderSide.SELL
        entry = Decimal(str(last))
        stop_dollars = self.parameters["stop_dollars"]
        stop_ticks = (stop_dollars / ES_TICK_VALUE).quantize(Decimal("1"))
        stop_distance = ES_TICK_SIZE * stop_ticks
        stop = entry - stop_distance if side == OrderSide.BUY else entry + stop_distance
        take_profit = Decimal(str(prior_rth_close))
        qty = self._position_size(entry, stop)
        if qty <= 0:
            return

        self.propose_entry(
            asset=self._asset,
            side=side,
            quantity=qty,
            entry_price=entry,
            stop_loss=stop,
            take_profit=take_profit,
            reason=f"gap={gap:.2f} ({atr_norm:.2f}xATR), inside prior range",
            market_regime=classify_regime(daily.df),
        )
        self._entered_today = True

    def _position_size(self, entry: Decimal, stop: Decimal) -> Decimal:
        risk_dollar = Decimal(str(self.portfolio_value)) * Decimal(
            str(self.parameters["risk_per_trade_pct"])
        )
        ticks = (abs(entry - stop) / ES_TICK_SIZE).quantize(Decimal("1"))
        if ticks == 0:
            return Decimal("0")
        return (risk_dollar / (ticks * ES_TICK_VALUE)).quantize(Decimal("1"))


