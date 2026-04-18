"""Per-strategy performance metrics — the numbers that tell us whether
a strategy is earning its keep.

Source of truth: the ``trades`` table. Reads are lazy; the nightly
analysis job writes a denormalised snapshot to ``strategy_performance_daily``
for fast dashboard reads.

Metrics implemented:
  * win_rate               — closed trades won / closed trades
  * profit_factor          — gross profit / gross loss
  * expectancy             — expected $ per trade = win_rate * avg_win
                             + (1-win_rate) * avg_loss
  * sharpe  (daily)        — mean(daily P&L) / std(daily P&L) * sqrt(252)
  * sortino (daily)        — mean / std(downside) * sqrt(252)
  * max_drawdown_pct       — peak-to-trough on cumulative P&L / peak
  * recovery_factor        — total P&L / max drawdown $
  * best_day / worst_day   — single-day P&L extremes
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import numpy as np
import pandas as pd
from sqlalchemy import select

from trading_bot.db.models import StrategyPerformanceDaily, Trade
from trading_bot.db.session import get_session

log = logging.getLogger(__name__)

TRADING_DAYS_PER_YEAR = 252


@dataclass(slots=True)
class PerformanceMetrics:
    strategy_name: str
    firm: str
    window_days: int
    trade_count: int
    win_rate: float | None
    avg_winner: float | None
    avg_loser: float | None
    profit_factor: float | None
    expectancy: float | None
    sharpe: float | None
    sortino: float | None
    max_drawdown_pct: float | None
    recovery_factor: float | None
    best_day_pnl: float | None
    worst_day_pnl: float | None


def _fetch_closed_trades(
    strategy_name: str, since: datetime | None = None
) -> pd.DataFrame:
    with get_session() as s:
        stmt = select(Trade).where(
            Trade.strategy_name == strategy_name,
            Trade.pnl.is_not(None),
            Trade.exit_time.is_not(None),
        )
        if since is not None:
            stmt = stmt.where(Trade.exit_time >= since)
        rows = s.execute(stmt.order_by(Trade.exit_time)).scalars().all()
        df = pd.DataFrame(
            [
                {
                    "id": t.id,
                    "firm": t.account.firm if t.account else None,
                    "asset": t.asset,
                    "direction": t.direction.value,
                    "exit_time": t.exit_time,
                    "pnl": float(t.pnl) if t.pnl else 0.0,
                    "pnl_pct": float(t.pnl_pct) if t.pnl_pct else 0.0,
                    "hour": t.hour_of_entry,
                    "day_of_week": t.day_of_week,
                    "market_regime": t.market_regime.value if t.market_regime else None,
                    "vix_at_entry": float(t.vix_at_entry) if t.vix_at_entry else None,
                    "exit_reason": t.exit_reason.value if t.exit_reason else None,
                }
                for t in rows
            ]
        )
    return df


def compute_metrics(
    strategy_name: str,
    firm: str = "",
    window_days: int | None = None,
) -> PerformanceMetrics:
    since = (
        datetime.now(timezone.utc) - timedelta(days=window_days)
        if window_days is not None
        else None
    )
    df = _fetch_closed_trades(strategy_name, since=since)
    return metrics_from_trades(df, strategy_name=strategy_name, firm=firm, window_days=window_days)


def metrics_from_trades(
    df: pd.DataFrame,
    *,
    strategy_name: str,
    firm: str = "",
    window_days: int | None = None,
) -> PerformanceMetrics:
    """Compute metrics from a pre-fetched trades DataFrame. Used by tests
    and any caller that already has the data in memory."""
    if df is None or df.empty:
        return PerformanceMetrics(
            strategy_name=strategy_name,
            firm=firm,
            window_days=window_days or 0,
            trade_count=0,
            win_rate=None,
            avg_winner=None,
            avg_loser=None,
            profit_factor=None,
            expectancy=None,
            sharpe=None,
            sortino=None,
            max_drawdown_pct=None,
            recovery_factor=None,
            best_day_pnl=None,
            worst_day_pnl=None,
        )

    if not firm and "firm" in df.columns and df["firm"].notna().any():
        firm = df["firm"].dropna().iloc[0]

    winners = df[df["pnl"] > 0]
    losers = df[df["pnl"] < 0]
    trade_count = len(df)
    win_rate = len(winners) / trade_count if trade_count else None
    avg_winner = float(winners["pnl"].mean()) if not winners.empty else None
    avg_loser = float(losers["pnl"].mean()) if not losers.empty else None

    gross_profit = float(winners["pnl"].sum()) if not winners.empty else 0.0
    gross_loss = float(-losers["pnl"].sum()) if not losers.empty else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else None

    expectancy = (
        (win_rate or 0) * (avg_winner or 0) + (1 - (win_rate or 0)) * (avg_loser or 0)
        if avg_winner is not None or avg_loser is not None
        else None
    )

    daily = df.set_index("exit_time")["pnl"].resample("1D").sum()
    daily = daily[daily != 0]  # ignore days with no fills
    if len(daily) >= 2:
        mean = daily.mean()
        std = daily.std()
        sharpe = (mean / std) * np.sqrt(TRADING_DAYS_PER_YEAR) if std > 0 else None
        downside = daily[daily < 0]
        d_std = downside.std() if len(downside) > 1 else None
        sortino = (mean / d_std) * np.sqrt(TRADING_DAYS_PER_YEAR) if d_std and d_std > 0 else None
        best_day = float(daily.max())
        worst_day = float(daily.min())
    else:
        sharpe = sortino = best_day = worst_day = None

    equity = df["pnl"].cumsum()
    if len(equity) >= 2:
        running_peak = equity.cummax()
        drawdown_dollar = equity - running_peak
        max_dd_dollar = float(abs(drawdown_dollar.min())) if drawdown_dollar.min() < 0 else 0.0
        peak_val = float(running_peak.max()) if running_peak.max() > 0 else 0.0
        max_dd_pct = max_dd_dollar / peak_val if peak_val > 0 else 0.0
        total_pnl = float(equity.iloc[-1])
        recovery_factor = total_pnl / max_dd_dollar if max_dd_dollar > 0 else None
    else:
        max_dd_pct = 0.0
        recovery_factor = None

    return PerformanceMetrics(
        strategy_name=strategy_name,
        firm=firm,
        window_days=window_days or 0,
        trade_count=trade_count,
        win_rate=win_rate,
        avg_winner=avg_winner,
        avg_loser=avg_loser,
        profit_factor=profit_factor,
        expectancy=expectancy,
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown_pct=max_dd_pct,
        recovery_factor=recovery_factor,
        best_day_pnl=best_day,
        worst_day_pnl=worst_day,
    )


def snapshot_all(
    strategy_names: list[str],
    firms: dict[str, str] | None = None,
    windows: tuple[int, ...] = (30, 90, 0),
    as_of: date | None = None,
) -> list[PerformanceMetrics]:
    """Compute + persist metrics for each (strategy, window) pair.

    ``window_days=0`` means "all time" in the schema.
    """
    firms = firms or {}
    as_of = as_of or datetime.now(timezone.utc).date()
    results: list[PerformanceMetrics] = []
    with get_session() as s:
        for name in strategy_names:
            firm = firms.get(name, "")
            for window in windows:
                m = compute_metrics(name, firm, window_days=window or None)
                results.append(m)
                row = StrategyPerformanceDaily(
                    strategy_name=name,
                    firm=m.firm or firm,
                    as_of_date=as_of,
                    window_days=window,
                    trade_count=m.trade_count,
                    win_rate=_dec(m.win_rate, scale="0.0001"),
                    avg_winner=_dec(m.avg_winner),
                    avg_loser=_dec(m.avg_loser),
                    profit_factor=_dec(m.profit_factor, scale="0.0001"),
                    expectancy=_dec(m.expectancy, scale="0.0001"),
                    sharpe=_dec(m.sharpe, scale="0.0001"),
                    sortino=_dec(m.sortino, scale="0.0001"),
                    max_drawdown_pct=_dec(m.max_drawdown_pct, scale="0.0001"),
                    recovery_factor=_dec(m.recovery_factor, scale="0.0001"),
                    best_day_pnl=_dec(m.best_day_pnl),
                    worst_day_pnl=_dec(m.worst_day_pnl),
                )
                existing = (
                    s.execute(
                        select(StrategyPerformanceDaily).where(
                            StrategyPerformanceDaily.strategy_name == name,
                            StrategyPerformanceDaily.as_of_date == as_of,
                            StrategyPerformanceDaily.window_days == window,
                        )
                    )
                    .scalars()
                    .first()
                )
                if existing is not None:
                    # Update in place to keep the unique constraint happy.
                    for k, v in asdict(m).items():
                        if hasattr(existing, k):
                            setattr(existing, k, _dec(v))
                else:
                    s.add(row)
    return results


def _dec(value, scale: str = "0.01") -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value)).quantize(Decimal(scale))
    except Exception:
        return None
