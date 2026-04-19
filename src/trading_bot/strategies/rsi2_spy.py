"""Strategy 1 — RSI(2) mean reversion on SPY (Connors).

Reference: Zhuo Kai Chen, "Day Trading Larry Connors RSI2 Mean-Reversion Strategies",
https://www.mql5.com/en/articles/17636 — MQL5 source ported to Python/Lumibot.

Entry: close > SMA(200) AND RSI(2) < 10
Exit:  close > SMA(5)
Stop:  2x ATR(14) from entry
Paper broker: Alpaca.  Challenge target: MyFundedFutures (MES conversion).
"""

from __future__ import annotations

from decimal import Decimal

import numpy as np
from lumibot.entities import Asset

from trading_bot.brokers.base_types import OrderSide
from trading_bot.indicators import atr, rsi
from trading_bot.learning import classify_regime
from trading_bot.strategies.base import RiskGatedStrategy


class RSI2SPY(RiskGatedStrategy):
    firm = "Alpaca_Paper"
    strategy_name = "RSI2_SPY"

    parameters = {
        "symbol": "SPY",
        "sma_long": 200,
        "sma_exit": 5,
        "rsi_period": 2,
        "rsi_entry_threshold": 10.0,
        "atr_period": 14,
        "atr_stop_multiple": 2.0,
        "risk_per_trade_pct": 0.0075,  # 0.75% — overridden by risk engine mode rules
    }

    def initialize(self, parameters: dict | None = None) -> None:
        super().initialize(parameters)
        self.sleeptime = "1D"
        self.set_market("NYSE")
        self._asset = Asset(symbol=self.parameters["symbol"], asset_type=Asset.AssetType.STOCK)

    def on_trading_iteration(self) -> None:
        self._heartbeat("tick")
        if self.get_position(self._asset):
            self._maybe_exit()
            return

        bars = self.get_historical_prices(
            self._asset, length=max(self.parameters["sma_long"] + 5, 250), timestep="day"
        )
        if bars is None or bars.df is None or len(bars.df) < self.parameters["sma_long"]:
            return

        close = bars.df["close"]
        sma_long = close.rolling(self.parameters["sma_long"]).mean().iloc[-1]
        rsi_val = rsi(close, self.parameters["rsi_period"]).iloc[-1]
        atr_val = atr(bars.df, self.parameters["atr_period"]).iloc[-1]
        last = close.iloc[-1]

        if np.isnan(sma_long) or np.isnan(rsi_val) or np.isnan(atr_val):
            return

        if last > sma_long and rsi_val < self.parameters["rsi_entry_threshold"]:
            entry = Decimal(str(last))
            stop = entry - Decimal(str(atr_val * self.parameters["atr_stop_multiple"]))
            qty = self._position_size(entry, stop)
            if qty > 0:
                self.propose_entry(
                    asset=self._asset,
                    side=OrderSide.BUY,
                    quantity=qty,
                    entry_price=entry,
                    stop_loss=stop,
                    reason=f"RSI(2)={rsi_val:.2f} < 10, close > SMA(200)={sma_long:.2f}",
                    market_regime=classify_regime(bars.df),
                )

    def _maybe_exit(self) -> None:
        bars = self.get_historical_prices(self._asset, length=10, timestep="day")
        if bars is None or bars.df is None or len(bars.df) < self.parameters["sma_exit"]:
            return
        close = bars.df["close"]
        sma_exit = close.rolling(self.parameters["sma_exit"]).mean().iloc[-1]
        if close.iloc[-1] > sma_exit:
            self.sell_all(cancel_open_orders=True)
            self.log_message(f"EXIT RSI2_SPY — close > SMA({self.parameters['sma_exit']})")

    def _position_size(self, entry: Decimal, stop: Decimal) -> Decimal:
        risk_dollar = Decimal(str(self.portfolio_value)) * Decimal(
            str(self.parameters["risk_per_trade_pct"])
        )
        distance = abs(entry - stop)
        if distance == 0:
            return Decimal("0")
        return (risk_dollar / distance).quantize(Decimal("1"))
