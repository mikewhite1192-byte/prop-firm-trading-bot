"""Shared technical-indicator primitives used by multiple strategies.

Kept framework-free (pure pandas + numpy) so they can be unit-tested
against synthetic data and swapped out for a TA-Lib or pandas-ta
implementation later without touching strategies.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def rsi(close: pd.Series, period: int) -> pd.Series:
    """Wilder-style RSI. Uses EWM with alpha=1/period (matches TA-Lib/MT5).

    Edge handling:
      * All-up (loss=0, gain>0) → 100, not NaN.
      * All-flat (gain=loss=0)  → NaN (no information).
    """
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = gain / loss
        result = 100 - (100 / (1 + rs))
    # loss==0, gain>0 -> rs=inf -> result=100 already via limit, but nan/inf edge
    # sometimes renders as NaN under older numpy; force explicitly.
    all_up = (loss == 0) & (gain > 0)
    result = result.mask(all_up, 100.0)
    return result


def atr(df: pd.DataFrame, period: int) -> pd.Series:
    """Average True Range using the simple rolling mean form (Connors-style)."""
    high, low, close = df["high"], df["low"], df["close"]
    prev = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev).abs(), (low - prev).abs()], axis=1
    ).max(axis=1)
    return tr.rolling(period).mean()


def adx(df: pd.DataFrame, period: int) -> pd.Series:
    """Wilder's ADX on a DataFrame with high/low/close columns."""
    high, low, close = df["high"], df["low"], df["close"]
    up = high.diff()
    dn = -low.diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat(
        [(high - low), (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1
    ).max(axis=1)
    atr_ = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di = (
        100
        * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean()
        / atr_
    )
    minus_di = (
        100
        * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean()
        / atr_
    )
    dx = ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)) * 100
    return dx.ewm(alpha=1 / period, adjust=False).mean()


def bollinger(close: pd.Series, period: int, stddev: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series]:
    mid = close.rolling(period).mean()
    std = close.rolling(period).std()
    return mid - stddev * std, mid, mid + stddev * std


def bollinger_zscore(close: pd.Series, period: int) -> pd.Series:
    mid = close.rolling(period).mean()
    std = close.rolling(period).std()
    return (close - mid) / std.replace(0, np.nan)


def session_vwap_sigma(
    df: pd.DataFrame, sigma: float
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Intraday VWAP that resets at the start of df, with ±sigma bands
    derived from the volume-weighted residual of the typical price.
    """
    tp = (df["high"] + df["low"] + df["close"]) / 3
    vol = df.get("volume", pd.Series(1.0, index=df.index))
    cumvol = vol.cumsum()
    vwap = (tp * vol).cumsum() / cumvol.replace(0, np.nan)
    residual_sq = (tp - vwap).pow(2)
    std = ((residual_sq * vol).cumsum() / cumvol.replace(0, np.nan)).pow(0.5)
    return vwap, vwap + sigma * std, vwap - sigma * std


def is_stall_candle(df: pd.DataFrame, lookback: int = 3) -> bool:
    """True if the last bar's range is < 50% of the prior ``lookback`` avg range."""
    if len(df) < lookback + 1:
        return False
    cur = df["high"].iloc[-1] - df["low"].iloc[-1]
    avg = (df["high"] - df["low"]).iloc[-(lookback + 1) : -1].mean()
    return bool(cur < 0.5 * avg)
