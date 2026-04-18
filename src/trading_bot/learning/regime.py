"""Lightweight market-regime classifier.

Called at entry time by the strategies so every ``trades`` row carries the
regime the trade was taken in. The nightly analysis layer then slices P&L
by regime to flag "strategy X only works in ranges, bleeds in trends."

The heuristic is intentionally simple — ADX level + Bollinger width —
so it's cheap, explainable, and stable. Swap for an HMM or changepoint
detector in Phase 6 once we have enough labelled data to validate.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from trading_bot.db.models import MarketRegime
from trading_bot.indicators import adx as adx_indicator


def classify_regime(
    df: pd.DataFrame,
    *,
    adx_period: int = 14,
    trend_threshold: float = 25.0,
    range_threshold: float = 18.0,
    volatility_lookback: int = 20,
    volatility_threshold: float = 2.5,
) -> MarketRegime:
    """Return one of TRENDING / RANGING / VOLATILE / UNKNOWN.

    Rules (evaluated in this order):
      * VOLATILE if the most recent bar's range exceeds ``volatility_threshold``
        multiples of the recent average range.
      * TRENDING if ADX >= ``trend_threshold``.
      * RANGING  if ADX <= ``range_threshold``.
      * UNKNOWN  otherwise (middle zone — too noisy to classify).
    """
    if df is None or df.empty or len(df) < max(adx_period + 1, volatility_lookback + 1):
        return MarketRegime.UNKNOWN

    high, low = df["high"], df["low"]
    ranges = high - low
    recent_avg = ranges.iloc[-(volatility_lookback + 1) : -1].mean()
    if recent_avg and ranges.iloc[-1] / recent_avg >= volatility_threshold:
        return MarketRegime.VOLATILE

    adx_val = adx_indicator(df, adx_period).iloc[-1]
    if np.isnan(adx_val):
        return MarketRegime.UNKNOWN
    if adx_val >= trend_threshold:
        return MarketRegime.TRENDING
    if adx_val <= range_threshold:
        return MarketRegime.RANGING
    return MarketRegime.UNKNOWN
