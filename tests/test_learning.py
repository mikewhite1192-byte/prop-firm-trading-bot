from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from trading_bot.db.models import MarketRegime
from trading_bot.learning import (
    classify_regime,
    month_3_decision,
    month_6_rank,
    promotion_decision,
)
from trading_bot.learning.culling import CullVerdict
from trading_bot.learning.performance import metrics_from_trades


# ---------- regime classifier ----------


def _ohlc(close: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=len(close), freq="1min", tz="UTC")
    s = pd.Series(close, index=idx)
    return pd.DataFrame({"open": s, "high": s * 1.001, "low": s * 0.999, "close": s, "volume": 1000.0})


def test_regime_trending():
    prices = [100 + i * 0.5 for i in range(100)]
    assert classify_regime(_ohlc(prices)) == MarketRegime.TRENDING


def test_regime_ranging():
    rng = np.random.default_rng(42)
    prices = 100 + rng.standard_normal(200) * 0.05
    assert classify_regime(_ohlc(prices.tolist())) == MarketRegime.RANGING


def test_regime_volatile_spike():
    # Stable prices then one giant-range bar.
    prices = [100.0] * 50 + [105.0]
    df = _ohlc(prices)
    df.loc[df.index[-1], "high"] = 120.0
    df.loc[df.index[-1], "low"] = 90.0
    assert classify_regime(df) == MarketRegime.VOLATILE


def test_regime_unknown_on_short_df():
    assert classify_regime(_ohlc([100, 101, 102])) == MarketRegime.UNKNOWN


# ---------- performance metrics ----------


def _trade_df(pnls: list[float], start: datetime | None = None) -> pd.DataFrame:
    start = start or datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    for i, pnl in enumerate(pnls):
        rows.append(
            {
                "firm": "Alpaca_Paper",
                "asset": "SPY",
                "direction": "LONG",
                "exit_time": start + timedelta(days=i),
                "pnl": pnl,
                "pnl_pct": pnl / 10000,
                "hour": 10,
                "day_of_week": (start + timedelta(days=i)).weekday(),
                "market_regime": "TRENDING",
                "vix_at_entry": 14.0,
                "exit_reason": "SIGNAL",
            }
        )
    return pd.DataFrame(rows)


def test_metrics_winning_strategy():
    df = _trade_df([100, -40, 150, -30, 80, -20, 120])
    m = metrics_from_trades(df, strategy_name="TEST")
    assert m.trade_count == 7
    assert m.win_rate == pytest.approx(4 / 7)
    assert m.profit_factor is not None and m.profit_factor > 1.0
    assert m.expectancy is not None and m.expectancy > 0


def test_metrics_losing_strategy():
    df = _trade_df([-100, -50, 30, -80])
    m = metrics_from_trades(df, strategy_name="LOSER")
    assert m.win_rate == 0.25
    assert m.profit_factor is not None and m.profit_factor < 1.0
    assert m.expectancy is not None and m.expectancy < 0


def test_metrics_max_drawdown_is_positive_pct():
    df = _trade_df([100, 100, 100, -200, -100, 50])
    m = metrics_from_trades(df, strategy_name="DD")
    assert m.max_drawdown_pct is not None and m.max_drawdown_pct > 0


def test_metrics_empty_df():
    m = metrics_from_trades(pd.DataFrame(), strategy_name="EMPTY")
    assert m.trade_count == 0
    assert m.win_rate is None


# ---------- culling ----------


def _metric(
    *,
    trades=50,
    sharpe=1.2,
    win_rate=0.6,
    max_dd=0.03,
    name="TEST",
):
    from trading_bot.learning.performance import PerformanceMetrics

    return PerformanceMetrics(
        strategy_name=name,
        firm="Alpaca_Paper",
        window_days=0,
        trade_count=trades,
        win_rate=win_rate,
        avg_winner=100.0,
        avg_loser=-50.0,
        profit_factor=2.0,
        expectancy=50.0,
        sharpe=sharpe,
        sortino=sharpe * 1.2,
        max_drawdown_pct=max_dd,
        recovery_factor=3.0,
        best_day_pnl=200.0,
        worst_day_pnl=-100.0,
    )


def test_month3_kills_on_big_drawdown():
    m = _metric(max_dd=0.10)
    v = month_3_decision(m)
    assert v.verdict == CullVerdict.KILL
    assert "8%" in v.reason


def test_month3_flags_on_low_sharpe():
    m = _metric(sharpe=0.3, max_dd=0.02)
    v = month_3_decision(m)
    assert v.verdict == CullVerdict.FLAG


def test_month3_keeps_healthy():
    v = month_3_decision(_metric())
    assert v.verdict == CullVerdict.KEEP


def test_month6_ranks_by_sharpe():
    ranked = month_6_rank(
        [
            _metric(name="A", sharpe=0.5),
            _metric(name="B", sharpe=2.1),
            _metric(name="C", sharpe=1.4),
        ]
    )
    assert [v.strategy_name for v in ranked] == ["B", "C", "A"]
    assert ranked[0].rank == 1
    assert ranked[0].verdict == CullVerdict.KEEP


def test_promotion_requires_all_three_criteria():
    assert promotion_decision(_metric()).verdict == CullVerdict.PROMOTE
    assert promotion_decision(_metric(sharpe=0.9)).verdict == CullVerdict.KEEP
    assert promotion_decision(_metric(win_rate=0.45)).verdict == CullVerdict.KEEP
    assert promotion_decision(_metric(max_dd=0.08)).verdict == CullVerdict.KEEP


def test_promotion_flags_low_sample_size():
    v = promotion_decision(_metric(trades=5))
    assert v.verdict == CullVerdict.FLAG
    assert "≥20" in v.reason
