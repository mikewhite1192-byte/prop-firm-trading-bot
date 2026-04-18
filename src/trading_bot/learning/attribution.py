"""Slice strategy performance to answer "what conditions does this
strategy actually work in?"

Every function returns a DataFrame so the dashboard can render directly
or dump to CSV for offline review.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import select

from trading_bot.db.models import Trade
from trading_bot.db.session import get_session


_DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _closed_trades(strategy_name: str, window_days: int | None = None) -> pd.DataFrame:
    since = (
        datetime.now(timezone.utc) - timedelta(days=window_days)
        if window_days is not None
        else None
    )
    with get_session() as s:
        stmt = select(Trade).where(
            Trade.strategy_name == strategy_name,
            Trade.pnl.is_not(None),
        )
        if since is not None:
            stmt = stmt.where(Trade.exit_time >= since)
        rows = s.execute(stmt).scalars().all()
        return pd.DataFrame(
            [
                {
                    "hour": t.hour_of_entry,
                    "day_of_week": t.day_of_week,
                    "market_regime": t.market_regime.value if t.market_regime else "UNKNOWN",
                    "vix_at_entry": float(t.vix_at_entry) if t.vix_at_entry else None,
                    "pnl": float(t.pnl),
                    "pnl_pct": float(t.pnl_pct) if t.pnl_pct else 0.0,
                    "direction": t.direction.value,
                }
                for t in rows
            ]
        )


def _summarise(df: pd.DataFrame, group_col: str) -> pd.DataFrame:
    if df.empty or group_col not in df.columns:
        return pd.DataFrame(
            columns=["n", "win_rate", "avg_pnl", "total_pnl"]
        )
    grouped = df.groupby(group_col)
    out = pd.DataFrame(
        {
            "n": grouped.size(),
            "win_rate": grouped["pnl"].apply(lambda s: (s > 0).sum() / len(s)),
            "avg_pnl": grouped["pnl"].mean(),
            "total_pnl": grouped["pnl"].sum(),
        }
    )
    return out.sort_values("total_pnl", ascending=False)


def attribute_by_regime(strategy_name: str, window_days: int | None = None) -> pd.DataFrame:
    df = _closed_trades(strategy_name, window_days)
    return _summarise(df, "market_regime")


def attribute_by_hour(strategy_name: str, window_days: int | None = None) -> pd.DataFrame:
    df = _closed_trades(strategy_name, window_days)
    out = _summarise(df, "hour")
    out.index.name = "hour_of_day"
    return out


def attribute_by_day_of_week(
    strategy_name: str, window_days: int | None = None
) -> pd.DataFrame:
    df = _closed_trades(strategy_name, window_days)
    out = _summarise(df, "day_of_week")
    out.index = [_DAY_NAMES[i] if 0 <= i <= 6 else str(i) for i in out.index]
    out.index.name = "day_of_week"
    return out


def attribute_by_vix_bucket(
    strategy_name: str, window_days: int | None = None
) -> pd.DataFrame:
    df = _closed_trades(strategy_name, window_days)
    if df.empty or df["vix_at_entry"].isna().all():
        return pd.DataFrame(columns=["n", "win_rate", "avg_pnl", "total_pnl"])
    bins = [0, 15, 20, 25, 30, 100]
    labels = ["<15", "15-20", "20-25", "25-30", "30+"]
    df = df.copy()
    df["vix_bucket"] = pd.cut(df["vix_at_entry"], bins=bins, labels=labels)
    return _summarise(df, "vix_bucket")
