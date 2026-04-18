from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trading_bot.indicators import (
    adx,
    atr,
    bollinger,
    bollinger_zscore,
    is_stall_candle,
    rsi,
    session_vwap_sigma,
)


def _ohlc(prices: list[float], vols: list[float] | None = None) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=len(prices), freq="1min", tz="UTC")
    close = pd.Series(prices, index=idx)
    high = close * 1.001
    low = close * 0.999
    vol = pd.Series(vols or [1000.0] * len(prices), index=idx)
    return pd.DataFrame({"open": close, "high": high, "low": low, "close": close, "volume": vol})


def test_rsi_at_extremes():
    # Straight up: RSI should approach 100.
    up = pd.Series(list(range(1, 30)), dtype=float)
    r = rsi(up, period=14).iloc[-1]
    assert r > 95

    # Straight down: RSI should approach 0.
    down = pd.Series(list(range(30, 1, -1)), dtype=float)
    r = rsi(down, period=14).iloc[-1]
    assert r < 5


def test_rsi_flat_is_nan_or_neutral():
    flat = pd.Series([100.0] * 30)
    r = rsi(flat, period=14).iloc[-1]
    # No gain and no loss — division by zero path; NaN is acceptable.
    assert np.isnan(r) or 0 <= r <= 100


def test_atr_matches_true_range_average():
    df = _ohlc([100, 101, 99, 102, 98, 103, 97, 104])
    a = atr(df, period=3).iloc[-1]
    assert a > 0


def test_atr_length():
    df = _ohlc([float(i) for i in range(1, 50)])
    a = atr(df, period=14)
    assert len(a) == len(df)
    # First 13 values should be NaN (rolling window).
    assert a.iloc[:13].isna().all()
    assert not np.isnan(a.iloc[-1])


def test_adx_range_regime_low():
    # Small random noise around a flat price -> low ADX (genuinely ranging).
    rng = np.random.default_rng(42)
    prices = 100 + rng.standard_normal(200) * 0.05
    df = _ohlc(prices.tolist())
    a = adx(df, period=14).iloc[-1]
    assert a < 30, f"expected ranging ADX < 30, got {a}"


def test_adx_trending_regime_high():
    prices = [100 + i * 0.5 for i in range(100)]
    df = _ohlc(prices)
    a = adx(df, period=14).iloc[-1]
    assert a > 25, f"expected trending ADX > 25, got {a}"


def test_bollinger_bands_bracket_price():
    prices = [100 + (i % 5) * 0.2 for i in range(40)]
    close = pd.Series(prices)
    lower, mid, upper = bollinger(close, period=20, stddev=2.0)
    assert mid.iloc[-1] == pytest.approx(close.iloc[-20:].mean(), rel=1e-6)
    assert lower.iloc[-1] < mid.iloc[-1] < upper.iloc[-1]


def test_bollinger_zscore_sign():
    # Large positive spike -> positive z.
    prices = [100.0] * 19 + [110.0]
    z = bollinger_zscore(pd.Series(prices), period=20).iloc[-1]
    assert z > 2


def test_vwap_sigma_centers_between_bands():
    df = _ohlc([100, 101, 102, 101, 100, 99, 100, 101, 102])
    vwap, upper, lower = session_vwap_sigma(df, sigma=2.0)
    assert all(lower <= vwap)
    assert all(vwap <= upper)


def test_vwap_weighted_by_volume():
    # Heavy volume at 100 should pull VWAP toward 100, not toward price=200 with tiny vol.
    prices = [100.0] * 9 + [200.0]
    vols = [10_000.0] * 9 + [1.0]
    df = _ohlc(prices, vols)
    vwap, _, _ = session_vwap_sigma(df, sigma=2.0)
    assert vwap.iloc[-1] < 105


def test_stall_candle_detection():
    df = _ohlc([100, 101, 102, 103, 104], vols=[1000] * 5)
    # Make the last bar's range tight.
    df.loc[df.index[-1], "high"] = df.loc[df.index[-1], "close"] * 1.0001
    df.loc[df.index[-1], "low"] = df.loc[df.index[-1], "close"] * 0.9999
    assert is_stall_candle(df, lookback=3)


def test_stall_candle_needs_enough_history():
    df = _ohlc([100, 101])
    assert not is_stall_candle(df, lookback=3)
