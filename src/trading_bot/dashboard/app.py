from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy import select

from trading_bot.db.models import Account, StrategyPerformanceDaily, Trade
from trading_bot.db.session import get_session
from trading_bot.learning import (
    attribute_by_day_of_week,
    attribute_by_hour,
    attribute_by_regime,
    attribute_by_vix_bucket,
    month_3_decision,
    promotion_decision,
)

# Run with: streamlit run src/trading_bot/dashboard/app.py


def _accounts_df() -> pd.DataFrame:
    with get_session() as s:
        rows = s.execute(select(Account)).scalars().all()
    return pd.DataFrame(
        [
            {
                "id": a.id,
                "firm": a.firm,
                "strategy": a.strategy_name,
                "mode": a.mode.value,
                "status": a.status.value,
                "size": float(a.account_size),
                "balance": float(a.current_balance),
                "daily_pnl": float(a.daily_pnl),
                "weekly_pnl": float(a.weekly_pnl),
                "drawdown_pct": float(a.current_drawdown_pct),
            }
            for a in rows
        ]
    )


def _recent_trades_df(limit: int = 50) -> pd.DataFrame:
    with get_session() as s:
        rows = (
            s.execute(select(Trade).order_by(Trade.entry_time.desc()).limit(limit))
            .scalars()
            .all()
        )
    return pd.DataFrame(
        [
            {
                "id": t.id,
                "account_id": t.account_id,
                "strategy": t.strategy_name,
                "asset": t.asset,
                "dir": t.direction.value,
                "entry_time": t.entry_time,
                "exit_time": t.exit_time,
                "entry_price": float(t.entry_price),
                "exit_price": float(t.exit_price) if t.exit_price else None,
                "pnl": float(t.pnl) if t.pnl is not None else None,
                "regime": t.market_regime.value if t.market_regime else None,
                "exit_reason": t.exit_reason.value if t.exit_reason else None,
            }
            for t in rows
        ]
    )


def _performance_df(window_days: int = 0) -> pd.DataFrame:
    """Read the nightly snapshot, not the live aggregate — fast dashboard
    reads. Fall back to 'no data' if the nightly job hasn't run yet."""
    with get_session() as s:
        rows = (
            s.execute(
                select(StrategyPerformanceDaily)
                .where(StrategyPerformanceDaily.window_days == window_days)
                .order_by(StrategyPerformanceDaily.as_of_date.desc())
            )
            .scalars()
            .all()
        )
    if not rows:
        return pd.DataFrame()
    # Keep only the latest as_of_date per strategy.
    latest = {}
    for r in rows:
        if r.strategy_name not in latest:
            latest[r.strategy_name] = r
    return pd.DataFrame(
        [
            {
                "strategy": r.strategy_name,
                "firm": r.firm,
                "trades": r.trade_count,
                "win_rate": float(r.win_rate) if r.win_rate else None,
                "sharpe": float(r.sharpe) if r.sharpe else None,
                "sortino": float(r.sortino) if r.sortino else None,
                "profit_factor": float(r.profit_factor) if r.profit_factor else None,
                "expectancy": float(r.expectancy) if r.expectancy else None,
                "max_dd": float(r.max_drawdown_pct) if r.max_drawdown_pct else None,
                "best_day": float(r.best_day_pnl) if r.best_day_pnl else None,
                "worst_day": float(r.worst_day_pnl) if r.worst_day_pnl else None,
                "as_of": r.as_of_date,
            }
            for r in latest.values()
        ]
    )


# ------------------------------- UI -------------------------------

st.set_page_config(page_title="Trading Bot", layout="wide")
st.title("Prop Firm Trading — live monitor")

tab_overview, tab_performance, tab_attribution, tab_culling = st.tabs(
    ["Overview", "Performance", "Attribution", "Culling"]
)

with tab_overview:
    st.subheader("Accounts")
    st.dataframe(_accounts_df(), width="stretch")

    st.subheader("Recent trades")
    st.dataframe(_recent_trades_df(), width="stretch")

with tab_performance:
    st.subheader("Per-strategy metrics (nightly snapshot)")
    window = st.selectbox("Window", [("All time", 0), ("30 days", 30), ("90 days", 90)], format_func=lambda x: x[0])
    perf = _performance_df(window_days=window[1])
    if perf.empty:
        st.info(
            "No snapshots yet. Run `python scripts/nightly_analysis.py` "
            "once there are closed trades to compute metrics over."
        )
    else:
        st.dataframe(perf, width="stretch")

with tab_attribution:
    st.subheader("Per-strategy attribution — where the P&L comes from")
    strategies = ["RSI2_SPY", "GAPFILL_SPY", "BBZ_EURUSD", "VWAP_SIGMA_ES", "TINYGAP_ES", "BB_BTC_4H"]
    strat = st.selectbox("Strategy", strategies)
    lookback = st.slider("Look-back (days)", min_value=30, max_value=365, value=90, step=30)
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**By market regime**")
        st.dataframe(attribute_by_regime(strat, window_days=lookback), width="stretch")
        st.markdown("**By day of week**")
        st.dataframe(attribute_by_day_of_week(strat, window_days=lookback), width="stretch")
    with col2:
        st.markdown("**By hour of entry**")
        st.dataframe(attribute_by_hour(strat, window_days=lookback), width="stretch")
        st.markdown("**By VIX bucket**")
        st.dataframe(attribute_by_vix_bucket(strat, window_days=lookback), width="stretch")

with tab_culling:
    st.subheader("Culling — spec §9 decision framework")
    st.caption(
        "Month 3: kill if DD > 8%, flag if Sharpe < 0.5. "
        "Promote: Sharpe > 1, win_rate > 55%, DD < 5%, ≥20 trades. "
        "Human review required — the dashboard never flips account status."
    )
    all_time = _performance_df(window_days=0)
    if all_time.empty:
        st.info("No performance data yet. Run the nightly analysis job.")
    else:
        # Re-derive verdicts from the latest in-memory metrics.
        from trading_bot.learning.performance import PerformanceMetrics

        rows = []
        for _, r in all_time.iterrows():
            m = PerformanceMetrics(
                strategy_name=r["strategy"],
                firm=r["firm"],
                window_days=0,
                trade_count=int(r["trades"]),
                win_rate=r["win_rate"],
                avg_winner=None,
                avg_loser=None,
                profit_factor=r["profit_factor"],
                expectancy=r["expectancy"],
                sharpe=r["sharpe"],
                sortino=r["sortino"],
                max_drawdown_pct=r["max_dd"],
                recovery_factor=None,
                best_day_pnl=r["best_day"],
                worst_day_pnl=r["worst_day"],
            )
            m3 = month_3_decision(m)
            promo = promotion_decision(m)
            rows.append(
                {
                    "strategy": m.strategy_name,
                    "month_3": m3.verdict.value,
                    "month_3_reason": m3.reason,
                    "promotion": promo.verdict.value,
                    "promotion_reason": promo.reason,
                }
            )
        st.dataframe(pd.DataFrame(rows), width="stretch")
