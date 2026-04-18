"""Strategy 3 — Bollinger Band z-score mean reversion on EUR/USD (M15).

References:
  * vsebastien3, "Simple EA using Bollinger, RSI and MA" —
    https://www.mql5.com/en/code/32695 (MT5 EA, EUR/USD H1)
  * barabashkakvn, "RSI Bollinger Bands EA" —
    https://www.mql5.com/en/code/20705

The MQL5 references handle the BB+RSI core. We layer on top:
  * explicit z-score normalisation (close − BB_mid) / stddev
  * H4 ADX(14) < 20 range-regime filter
  * session filter (Asian + late NY low-vol only)

Broker: OANDA demo via our custom Lumibot broker (``trading_bot.brokers.oanda_lumibot``).
When promoted to FTMO it re-deploys as an MQL5 EA on the same logic.
"""

from __future__ import annotations

from datetime import time as dtime, timezone
from decimal import Decimal

import numpy as np
import pandas as pd
from lumibot.entities import Asset

from trading_bot.brokers.base_types import OrderSide
from trading_bot.strategies.base import RiskGatedStrategy


class BBZScoreEURUSD(RiskGatedStrategy):
    firm = "OANDA_Demo"
    strategy_name = "BBZ_EURUSD"

    parameters = {
        "symbol": "EUR_USD",
        "bb_period": 20,
        "bb_stddev": 2.0,
        "rsi_period": 14,
        "rsi_long_threshold": 30,
        "rsi_short_threshold": 70,
        "adx_period": 14,
        "adx_range_max": 20,
        "z_entry": 2.0,
        "z_exit": 0.5,
        "atr_stop_multiple": 1.5,
        "risk_per_trade_pct": 0.0075,
    }

    def initialize(self, parameters: dict | None = None) -> None:
        super().initialize(parameters)
        self.sleeptime = "15M"
        self._asset = Asset(symbol=self.parameters["symbol"], asset_type=Asset.AssetType.FOREX)

    def on_trading_iteration(self) -> None:
        if self.get_position(self._asset):
            self._maybe_exit()
            return

        if not self._in_allowed_session():
            return

        m15 = self.get_historical_prices(self._asset, length=self.parameters["bb_period"] + 50, timestep="minute")
        h4 = self.get_historical_prices(self._asset, length=self.parameters["adx_period"] + 50, timestep="240 minutes")
        if not self._have_bars(m15) or not self._have_bars(h4):
            return

        close = m15.df["close"]
        mid = close.rolling(self.parameters["bb_period"]).mean()
        std = close.rolling(self.parameters["bb_period"]).std()
        z = (close - mid) / std.replace(0, np.nan)
        rsi = _rsi(close, self.parameters["rsi_period"])
        adx_h4 = _adx(h4.df, self.parameters["adx_period"]).iloc[-1]

        if np.isnan(z.iloc[-1]) or np.isnan(rsi.iloc[-1]) or np.isnan(adx_h4):
            return
        if adx_h4 >= self.parameters["adx_range_max"]:
            return  # trending regime — skip mean reversion

        side: OrderSide | None = None
        if z.iloc[-1] <= -self.parameters["z_entry"] and rsi.iloc[-1] < self.parameters["rsi_long_threshold"]:
            side = OrderSide.BUY
        elif z.iloc[-1] >= self.parameters["z_entry"] and rsi.iloc[-1] > self.parameters["rsi_short_threshold"]:
            side = OrderSide.SELL
        if side is None:
            return

        atr = _atr(m15.df, 14).iloc[-1]
        last = close.iloc[-1]
        entry = Decimal(str(last))
        stop_dist = Decimal(str(atr * self.parameters["atr_stop_multiple"]))
        stop = entry - stop_dist if side == OrderSide.BUY else entry + stop_dist
        # Mid-band target converts to dynamic exit in _maybe_exit; no fixed TP.
        qty = self._position_size(entry, stop)
        if qty <= 0:
            return

        self.propose_entry(
            asset=self._asset,
            side=side,
            quantity=qty,
            entry_price=entry,
            stop_loss=stop,
            reason=f"z={z.iloc[-1]:.2f} rsi={rsi.iloc[-1]:.1f} adx_h4={adx_h4:.1f}",
        )

    def _maybe_exit(self) -> None:
        m15 = self.get_historical_prices(self._asset, length=self.parameters["bb_period"] + 5, timestep="minute")
        if not self._have_bars(m15):
            return
        close = m15.df["close"]
        mid = close.rolling(self.parameters["bb_period"]).mean()
        std = close.rolling(self.parameters["bb_period"]).std()
        z = (close.iloc[-1] - mid.iloc[-1]) / std.iloc[-1] if std.iloc[-1] else float("nan")
        if abs(z) <= self.parameters["z_exit"]:
            self.sell_all(cancel_open_orders=True)
            self.log_message(f"EXIT BBZ_EURUSD — z back to {z:.2f}")

    def _in_allowed_session(self) -> bool:
        """Asian (23:00-07:00 GMT) or late NY (19:00-22:00 GMT) low-vol windows."""
        now_utc = self.get_datetime().astimezone(timezone.utc).time()
        asian = now_utc >= dtime(23, 0) or now_utc < dtime(7, 0)
        late_ny = dtime(19, 0) <= now_utc < dtime(22, 0)
        return asian or late_ny

    @staticmethod
    def _have_bars(bars) -> bool:
        return bars is not None and bars.df is not None and len(bars.df) > 20

    def _position_size(self, entry: Decimal, stop: Decimal) -> Decimal:
        risk_dollar = Decimal(str(self.portfolio_value)) * Decimal(
            str(self.parameters["risk_per_trade_pct"])
        )
        distance = abs(entry - stop)
        if distance == 0:
            return Decimal("0")
        # OANDA units: 1 unit = 1 base currency (EUR). Return whole units.
        return (risk_dollar / distance).quantize(Decimal("1"))


def _rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev = close.shift(1)
    tr = pd.concat([(high - low), (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def _adx(df: pd.DataFrame, period: int) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    up = high.diff()
    dn = -low.diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat(
        [(high - low), (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1
    ).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / atr
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / atr
    dx = ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)) * 100
    return dx.ewm(alpha=1 / period, adjust=False).mean()
