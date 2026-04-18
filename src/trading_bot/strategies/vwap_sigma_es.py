"""Strategy 4 — VWAP +/-2 sigma reversion on ES / MES futures.

VWAP primitive pattern adapted from
https://github.com/eslazarev/vwap-backtrader (Backtrader, MIT). We compute
intraday VWAP that resets at RTH open + rolling sigma bands from the typical
price residual. Stall-candle confirmation is a custom overlay.

Entry: fade touches of VWAP +/-2 sigma 30-120 min into RTH, require stall
       candle (current bar range < 0.5 * prior 3-bar avg range)
Exit:  target = VWAP; optional runner to opposite +/-1 sigma.
Stop:  6-8 ticks beyond sigma extreme (ES tick = 0.25 = $12.50).
Filter: skip day if 9:30-10:00 range never traded through VWAP.
Broker: Tradovate sim (Lumibot native).
"""

from __future__ import annotations

from datetime import time as dtime
from decimal import Decimal

import pandas as pd
from lumibot.entities import Asset

from trading_bot.brokers.base_types import OrderSide
from trading_bot.indicators import is_stall_candle, session_vwap_sigma
from trading_bot.learning import classify_regime
from trading_bot.strategies.base import RiskGatedStrategy

ES_TICK_SIZE = Decimal("0.25")
ES_TICK_VALUE = Decimal("12.50")  # $ per tick per contract


class VWAPSigmaES(RiskGatedStrategy):
    firm = "Tradovate_Sim"
    strategy_name = "VWAP_SIGMA_ES"

    parameters = {
        "symbol": "ES",
        "sigma_entry": 2.0,
        "stop_ticks_beyond": 7,   # 6-8 tick range
        "min_minutes_into_rth": 30,
        "max_minutes_into_rth": 120,
        "trend_day_filter_end_minute": 30,  # 9:30-10:00
        "risk_per_trade_pct": 0.0075,
    }

    def initialize(self, parameters: dict | None = None) -> None:
        super().initialize(parameters)
        self.sleeptime = "1M"
        self._asset = Asset(
            symbol=self.parameters["symbol"], asset_type=Asset.AssetType.FUTURE
        )
        self._trend_day = False
        self._evaluated_trend_day_for: str | None = None

    def on_trading_iteration(self) -> None:
        now_et = self.get_datetime()
        rth_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
        elapsed_min = (now_et - rth_open).total_seconds() / 60

        if elapsed_min < self.parameters["min_minutes_into_rth"]:
            return
        if elapsed_min > self.parameters["max_minutes_into_rth"]:
            if self.get_position(self._asset):
                self._maybe_exit_vwap()
            return

        bars = self.get_historical_prices(self._asset, length=240, timestep="minute")
        if bars is None or bars.df is None or len(bars.df) < 35:
            return
        today = _today_session(bars.df, now_et)
        if len(today) < 30:
            return

        date_key = now_et.strftime("%Y-%m-%d")
        if self._evaluated_trend_day_for != date_key:
            self._trend_day = _is_trend_day(today, self.parameters["trend_day_filter_end_minute"])
            self._evaluated_trend_day_for = date_key
        if self._trend_day:
            return

        vwap, upper, lower = session_vwap_sigma(today, self.parameters["sigma_entry"])
        last_bar = today.iloc[-1]
        last = last_bar["close"]
        stall = is_stall_candle(today, lookback=3)

        if self.get_position(self._asset):
            self._maybe_exit_vwap(vwap_series=vwap)
            return
        if not stall:
            return

        side: OrderSide | None = None
        entry = Decimal(str(last))
        stop: Decimal | None = None
        if last <= lower.iloc[-1]:
            side = OrderSide.BUY
            stop = Decimal(str(today["low"].iloc[-1])) - ES_TICK_SIZE * self.parameters["stop_ticks_beyond"]
        elif last >= upper.iloc[-1]:
            side = OrderSide.SELL
            stop = Decimal(str(today["high"].iloc[-1])) + ES_TICK_SIZE * self.parameters["stop_ticks_beyond"]
        if side is None or stop is None:
            return

        take_profit = Decimal(str(vwap.iloc[-1]))
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
            reason=f"VWAP={vwap.iloc[-1]:.2f} upper={upper.iloc[-1]:.2f} lower={lower.iloc[-1]:.2f}",
            market_regime=classify_regime(bars.df),
        )

    def _maybe_exit_vwap(self, vwap_series: pd.Series | None = None) -> None:
        # Simple exit: reach VWAP. Phase 2: add runner to opposite ±1σ.
        if vwap_series is None:
            bars = self.get_historical_prices(self._asset, length=240, timestep="minute")
            if bars is None or bars.df is None:
                return
            today = _today_session(bars.df, self.get_datetime())
            if today.empty:
                return
            vwap_series, _, _ = session_vwap_sigma(today, self.parameters["sigma_entry"])
        pos = self.get_position(self._asset)
        if pos is None:
            return
        last = self.get_last_price(self._asset)
        if last is None:
            return
        vwap = vwap_series.iloc[-1]
        if (pos.quantity > 0 and last >= vwap) or (pos.quantity < 0 and last <= vwap):
            self.sell_all(cancel_open_orders=True)
            self.log_message(f"EXIT VWAP_SIGMA_ES — reached VWAP {vwap:.2f}")

    def _position_size(self, entry: Decimal, stop: Decimal) -> Decimal:
        risk_dollar = Decimal(str(self.portfolio_value)) * Decimal(
            str(self.parameters["risk_per_trade_pct"])
        )
        ticks = (abs(entry - stop) / ES_TICK_SIZE).quantize(Decimal("1"))
        if ticks == 0:
            return Decimal("0")
        contracts = (risk_dollar / (ticks * ES_TICK_VALUE)).quantize(Decimal("1"))
        return max(contracts, Decimal("0"))


def _today_session(df: pd.DataFrame, now) -> pd.DataFrame:
    rth_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    return df[df.index >= rth_open]


def _is_trend_day(today: pd.DataFrame, minutes_window: int) -> bool:
    """Trend-day filter: true if first N minutes never traded through VWAP."""
    window = today.iloc[:minutes_window]
    if window.empty:
        return False
    vwap, _, _ = session_vwap_sigma(window, sigma=1.0)
    crossed = ((window["high"] >= vwap) & (window["low"] <= vwap)).any()
    return not crossed
