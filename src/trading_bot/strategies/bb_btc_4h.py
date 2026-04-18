"""Strategy 6 — BTC Bollinger Band mean reversion on 4H.

Logic adapted from lhandal/crypto-trading-bot
(https://github.com/lhandal/crypto-trading-bot, 310 stars, FreqTrade).
Ported to Lumibot + Alpaca crypto, timeframe pushed from 1H to 4H,
daily 200-MA and ADX(14) range filters added per spec.

Entry: 4H close below lower BB(20,2), RSI(14) < 30,
       daily close > 200-MA, ADX(14) < 25 (range regime)
Exit:  scale at mid-band, scale again at upper band
Stop:  1.5x ATR(14) below entry
Broker: Alpaca crypto.  Challenge target: FTMO crypto CFD.
"""

from __future__ import annotations

from decimal import Decimal

import numpy as np
from lumibot.entities import Asset

from trading_bot.brokers.base_types import OrderSide
from trading_bot.indicators import adx, atr, rsi
from trading_bot.learning import classify_regime
from trading_bot.strategies.base import RiskGatedStrategy


class BBBTC4H(RiskGatedStrategy):
    firm = "Alpaca_Paper"
    strategy_name = "BB_BTC_4H"

    parameters = {
        "base": "BTC",
        "quote": "USD",
        "bb_period": 20,
        "bb_stddev": 2.0,
        "rsi_period": 14,
        "rsi_long_threshold": 30,
        "daily_trend_period": 200,
        "adx_period": 14,
        "adx_range_max": 25,
        "atr_period": 14,
        "atr_stop_multiple": 1.5,
        "risk_per_trade_pct": 0.0075,
    }

    def initialize(self, parameters: dict | None = None) -> None:
        super().initialize(parameters)
        self.sleeptime = "60M"  # evaluate hourly; entry condition fires on 4H close
        self._asset = Asset(
            symbol=self.parameters["base"],
            asset_type=Asset.AssetType.CRYPTO,
        )
        self._quote = Asset(
            symbol=self.parameters["quote"],
            asset_type=Asset.AssetType.FOREX,  # Lumibot treats USD as forex quote
        )

    def on_trading_iteration(self) -> None:
        if self.get_position(self._asset):
            self._maybe_scale_exit()
            return

        h4 = self.get_historical_prices(
            self._asset,
            length=self.parameters["bb_period"] + 60,
            timestep="4h",
            quote=self._quote,
        )
        daily = self.get_historical_prices(
            self._asset,
            length=self.parameters["daily_trend_period"] + 10,
            timestep="day",
            quote=self._quote,
        )
        if not _have_bars(h4, self.parameters["bb_period"]) or not _have_bars(
            daily, self.parameters["daily_trend_period"]
        ):
            return

        close4 = h4.df["close"]
        mid = close4.rolling(self.parameters["bb_period"]).mean()
        std = close4.rolling(self.parameters["bb_period"]).std()
        lower = mid - self.parameters["bb_stddev"] * std
        rsi_val = rsi(close4, self.parameters["rsi_period"]).iloc[-1]
        adx_val = adx(h4.df, self.parameters["adx_period"]).iloc[-1]
        atr_val = atr(h4.df, self.parameters["atr_period"]).iloc[-1]
        sma_daily = daily.df["close"].rolling(self.parameters["daily_trend_period"]).mean().iloc[-1]

        last4 = close4.iloc[-1]
        last_daily = daily.df["close"].iloc[-1]
        if any(np.isnan(x) for x in (rsi_val, adx_val, atr_val, sma_daily, lower.iloc[-1])):
            return
        if last_daily <= sma_daily:
            return  # daily trend filter — long bias only
        if adx_val >= self.parameters["adx_range_max"]:
            return  # trending regime

        if last4 > lower.iloc[-1]:
            return
        if rsi_val >= self.parameters["rsi_long_threshold"]:
            return

        entry = Decimal(str(last4))
        stop = entry - Decimal(str(atr_val * self.parameters["atr_stop_multiple"]))
        qty = self._position_size(entry, stop)
        if qty <= 0:
            return

        self.propose_entry(
            asset=self._asset,
            side=OrderSide.BUY,
            quantity=qty,
            entry_price=entry,
            stop_loss=stop,
            reason=f"4H close {last4:.2f} < lower BB {lower.iloc[-1]:.2f}, "
            f"RSI={rsi_val:.1f}, ADX={adx_val:.1f}, daily>SMA200",
            market_regime=classify_regime(h4.df),
        )

    def _maybe_scale_exit(self) -> None:
        # Phase 2: implement two-leg scale at mid-band and upper band.
        h4 = self.get_historical_prices(
            self._asset,
            length=self.parameters["bb_period"] + 5,
            timestep="4h",
            quote=self._quote,
        )
        if not _have_bars(h4, self.parameters["bb_period"]):
            return
        close4 = h4.df["close"]
        mid = close4.rolling(self.parameters["bb_period"]).mean().iloc[-1]
        if np.isnan(mid):
            return
        if close4.iloc[-1] >= mid:
            self.sell_all(cancel_open_orders=True)
            self.log_message(f"EXIT BB_BTC_4H — close {close4.iloc[-1]:.2f} >= mid {mid:.2f}")

    def _position_size(self, entry: Decimal, stop: Decimal) -> Decimal:
        risk_dollar = Decimal(str(self.portfolio_value)) * Decimal(
            str(self.parameters["risk_per_trade_pct"])
        )
        distance = abs(entry - stop)
        if distance == 0:
            return Decimal("0")
        return (risk_dollar / distance).quantize(Decimal("0.0001"))


def _have_bars(bars, min_len: int) -> bool:
    return bars is not None and bars.df is not None and len(bars.df) >= min_len
