"""Prop Firm Trading — live monitor.

Run with:  streamlit run src/trading_bot/dashboard/app.py

Design goals:
  * Financial-terminal feel: dark, high-density, fast.
  * Every tile answers one question. No tables where a chart tells it better.
  * Empty states are first-class — new users see "why it's empty + what to run"
    instead of spinner soup.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from sqlalchemy import select

from trading_bot.db.models import (
    Account,
    AccountStatus,
    BacktestRun,
    NewsWindow,
    StrategyPerformanceDaily,
    Trade,
)
from trading_bot.db.session import get_session
from trading_bot.learning import (
    attribute_by_day_of_week,
    attribute_by_hour,
    attribute_by_regime,
    month_3_decision,
    promotion_decision,
)
from trading_bot.risk.rules import MODE_RULES

st.set_page_config(
    page_title="Prop Firm Trading",
    page_icon="◆",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---- global CSS ----------------------------------------------------------

st.markdown(
    """
    <style>
    /* tighter layout */
    .block-container {padding-top: 1.2rem; padding-bottom: 2rem; max-width: 1600px;}

    /* metric cards */
    div[data-testid="stMetric"] {
        background: linear-gradient(135deg, #151c28 0%, #1a2332 100%);
        border: 1px solid #1f2a3a;
        border-radius: 12px;
        padding: 18px 22px;
        box-shadow: 0 1px 0 rgba(255,255,255,0.03) inset;
    }
    div[data-testid="stMetricLabel"] {
        color: #7a8ba8 !important;
        font-size: 0.72rem !important;
        letter-spacing: 0.05em;
        text-transform: uppercase;
        font-weight: 600 !important;
    }
    div[data-testid="stMetricValue"] {
        font-size: 1.85rem !important;
        font-weight: 600 !important;
        letter-spacing: -0.02em;
        color: #f5f7fa !important;
    }

    /* section headers */
    h2 {
        font-size: 0.82rem !important;
        text-transform: uppercase;
        letter-spacing: 0.09em;
        color: #7a8ba8 !important;
        font-weight: 600;
        margin-top: 2rem !important;
        margin-bottom: 0.6rem !important;
        border-bottom: 1px solid #1f2a3a;
        padding-bottom: 0.4rem;
    }

    /* account cards */
    .acct-card {
        background: linear-gradient(135deg, #151c28 0%, #1a2332 100%);
        border: 1px solid #1f2a3a;
        border-radius: 14px;
        padding: 18px 20px;
        height: 100%;
    }
    .acct-card .title {font-weight: 600; font-size: 0.95rem; letter-spacing: -0.01em; color: #f5f7fa;}
    .acct-card .firm {font-size: 0.7rem; color: #7a8ba8; text-transform: uppercase; letter-spacing: 0.08em; margin-top: 2px;}
    .acct-card .balance {font-size: 1.6rem; font-weight: 600; margin-top: 12px; color: #e8eef8;}
    .acct-card .pnl-row {display: flex; gap: 16px; margin-top: 4px; font-size: 0.82rem;}
    .acct-card .pnl-row span {color: #7a8ba8;}
    .acct-card .pnl-row b.pos {color: #22d3ee;}
    .acct-card .pnl-row b.neg {color: #f87171;}
    .acct-card .pnl-row b.zero {color: #aab4c2;}
    .acct-card .meter {margin-top: 14px;}
    .acct-card .meter-label {display:flex; justify-content:space-between; font-size: 0.7rem; color: #7a8ba8; margin-bottom: 4px;}
    .acct-card .meter-bar {background: #0b0f17; border-radius: 8px; height: 6px; overflow: hidden;}
    .acct-card .meter-fill {height: 100%; border-radius: 8px;}
    .acct-card .badges {display: flex; gap: 6px; margin-top: 10px;}
    .badge {
        font-size: 0.65rem;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        padding: 3px 9px;
        border-radius: 6px;
        font-weight: 600;
    }
    .badge-paper {background: #1e3a5f; color: #93c5fd;}
    .badge-challenge {background: #5e4a1b; color: #fbbf24;}
    .badge-funded {background: #14532d; color: #4ade80;}
    .badge-active {background: #052e1a; color: #22d3ee;}
    .badge-halted {background: #5c1f24; color: #fca5a5;}
    .badge-blown {background: #5c1f24; color: #f87171;}
    .badge-passed {background: #14532d; color: #4ade80;}

    /* news pill */
    .news-pill {
        display: inline-block;
        background: #1a2332;
        border: 1px solid #2d3a4d;
        padding: 6px 12px;
        border-radius: 20px;
        margin: 4px 6px 4px 0;
        font-size: 0.78rem;
        color: #e8eef8;
    }
    .news-pill .ccy {color: #22d3ee; font-weight: 600; margin-right: 6px;}
    .news-pill .time {color: #7a8ba8; margin-left: 8px;}

    /* tabs */
    button[data-baseweb="tab"] {
        font-weight: 600 !important;
        letter-spacing: 0.02em;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---- data fetchers (all run inside the session context) -----------------


def _accounts() -> list[dict]:
    with get_session() as s:
        rows = s.execute(select(Account).order_by(Account.strategy_name)).scalars().all()
        return [
            {
                "id": a.id,
                "firm": a.firm,
                "strategy": a.strategy_name,
                "mode": a.mode.value,
                "status": a.status.value,
                "size": float(a.account_size),
                "starting_balance": float(a.starting_balance),
                "balance": float(a.current_balance),
                "peak": float(a.peak_balance),
                "daily_pnl": float(a.daily_pnl),
                "weekly_pnl": float(a.weekly_pnl),
                "drawdown_pct": float(a.current_drawdown_pct),
            }
            for a in rows
        ]


def _recent_trades(limit: int = 100) -> pd.DataFrame:
    with get_session() as s:
        rows = (
            s.execute(select(Trade).order_by(Trade.entry_time.desc()).limit(limit))
            .scalars()
            .all()
        )
        data = [
            {
                "id": t.id,
                "strategy": t.strategy_name,
                "asset": t.asset,
                "direction": t.direction.value,
                "entry_time": t.entry_time,
                "exit_time": t.exit_time,
                "entry_price": float(t.entry_price),
                "exit_price": float(t.exit_price) if t.exit_price else None,
                "quantity": float(t.quantity),
                "pnl": float(t.pnl) if t.pnl is not None else None,
                "pnl_pct": float(t.pnl_pct) if t.pnl_pct is not None else None,
                "regime": t.market_regime.value if t.market_regime else None,
                "exit_reason": t.exit_reason.value if t.exit_reason else None,
                "notes": (t.notes or "")[:200],
            }
            for t in rows
        ]
    return pd.DataFrame(data)


def _backtest_runs(limit: int = 20) -> pd.DataFrame:
    with get_session() as s:
        rows = (
            s.execute(select(BacktestRun).order_by(BacktestRun.run_at.desc()).limit(limit))
            .scalars()
            .all()
        )
        data = [
            {
                "strategy": r.strategy_name,
                "data": r.data_source,
                "start": r.start_date,
                "end": r.end_date,
                "budget": float(r.budget),
                "trades": r.trade_count,
                "return_pct": float(r.total_return_pct) if r.total_return_pct else None,
                "sharpe": float(r.sharpe) if r.sharpe else None,
                "max_dd_pct": float(r.max_drawdown_pct) if r.max_drawdown_pct else None,
                "run_at": r.run_at,
            }
            for r in rows
        ]
    return pd.DataFrame(data)


def _news(within: timedelta = timedelta(days=14)) -> pd.DataFrame:
    now = datetime.now(timezone.utc)
    until = now + within
    with get_session() as s:
        rows = (
            s.execute(
                select(NewsWindow)
                .where(NewsWindow.starts_at >= now - timedelta(hours=1))
                .where(NewsWindow.starts_at <= until)
                .order_by(NewsWindow.starts_at)
            )
            .scalars()
            .all()
        )
        data = [
            {
                "event": n.event,
                "currency": n.currency,
                "impact": n.impact,
                "starts_at": n.starts_at,
                "ends_at": n.ends_at,
            }
            for n in rows
        ]
    return pd.DataFrame(data)


def _performance(window_days: int = 0) -> pd.DataFrame:
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
        latest: dict = {}
        for r in rows:
            if r.strategy_name not in latest:
                latest[r.strategy_name] = r
        data = [
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
    return pd.DataFrame(data)


# ---- components ---------------------------------------------------------


def kpi_row(accounts: list[dict], trades: pd.DataFrame) -> None:
    total_balance = sum(a["balance"] for a in accounts)
    daily_pnl = sum(a["daily_pnl"] for a in accounts)
    total_start = sum(a["starting_balance"] for a in accounts)
    total_return = (total_balance - total_start) / total_start if total_start else 0
    active = sum(1 for a in accounts if a["status"] == "ACTIVE")
    halted = sum(1 for a in accounts if a["status"] in ("HALTED", "BLOWN"))

    if not trades.empty and "pnl" in trades.columns:
        closed = trades[trades["pnl"].notna()]
        win_rate = (closed["pnl"] > 0).mean() if len(closed) else None
        trade_count = len(closed)
    else:
        win_rate = None
        trade_count = 0

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Total Capital", f"${total_balance:,.0f}", f"{total_return:+.2%}" if total_start else None)
    c2.metric(
        "Today's P&L",
        f"${daily_pnl:,.2f}",
        delta=f"{daily_pnl:+.2f}" if daily_pnl else "—",
        delta_color="normal",
    )
    c3.metric("Active Strategies", f"{active}/{len(accounts)}", delta=f"-{halted} halted" if halted else None)
    c4.metric("Closed Trades", f"{trade_count:,}")
    c5.metric("Win Rate", f"{win_rate:.1%}" if win_rate is not None else "—")
    total_dd = max((a["drawdown_pct"] for a in accounts), default=0)
    c6.metric("Max Drawdown", f"{total_dd:.2%}" if total_dd else "—")


def account_card(a: dict) -> str:
    mode_badge_class = {
        "PAPER": "badge-paper",
        "CHALLENGE": "badge-challenge",
        "FUNDED": "badge-funded",
    }.get(a["mode"], "badge-paper")
    status_class = {
        "ACTIVE": "badge-active",
        "HALTED": "badge-halted",
        "BLOWN": "badge-blown",
        "PASSED": "badge-passed",
    }.get(a["status"], "badge-active")

    pnl = a["daily_pnl"]
    pnl_class = "pos" if pnl > 0 else "neg" if pnl < 0 else "zero"
    pnl_display = f"{pnl:+,.2f}" if pnl else "0.00"

    week_pnl = a["weekly_pnl"]
    week_class = "pos" if week_pnl > 0 else "neg" if week_pnl < 0 else "zero"

    # Risk meters: daily loss used vs mode's halt limit, DD vs stop limit.
    mode_rules = MODE_RULES[a["mode"]]
    daily_loss_pct = abs(min(pnl, 0)) / a["starting_balance"] if a["starting_balance"] else 0
    daily_cap = float(mode_rules.max_daily_loss_halt_pct)
    daily_used = min(daily_loss_pct / daily_cap, 1.0) if daily_cap else 0

    dd_cap = float(mode_rules.max_total_drawdown_stop_pct)
    dd_used = min(a["drawdown_pct"] / dd_cap, 1.0) if dd_cap else 0

    def bar_color(used: float) -> str:
        if used < 0.5:
            return "#22d3ee"
        if used < 0.8:
            return "#fbbf24"
        return "#f87171"

    return f"""
    <div class="acct-card">
        <div class="title">{a["strategy"]}</div>
        <div class="firm">{a["firm"]}</div>
        <div class="balance">${a["balance"]:,.0f}</div>
        <div class="pnl-row">
            <span>Day <b class="{pnl_class}">{pnl_display}</b></span>
            <span>Week <b class="{week_class}">{week_pnl:+,.2f}</b></span>
        </div>
        <div class="meter">
            <div class="meter-label"><span>Daily loss {daily_loss_pct:.2%}</span><span>cap {daily_cap:.1%}</span></div>
            <div class="meter-bar"><div class="meter-fill" style="width:{daily_used*100:.1f}%; background:{bar_color(daily_used)};"></div></div>
        </div>
        <div class="meter">
            <div class="meter-label"><span>Drawdown {a["drawdown_pct"]:.2%}</span><span>stop {dd_cap:.1%}</span></div>
            <div class="meter-bar"><div class="meter-fill" style="width:{dd_used*100:.1f}%; background:{bar_color(dd_used)};"></div></div>
        </div>
        <div class="badges">
            <span class="badge {mode_badge_class}">{a["mode"]}</span>
            <span class="badge {status_class}">{a["status"]}</span>
        </div>
    </div>
    """


def equity_curve(trades: pd.DataFrame, accounts: list[dict]) -> go.Figure:
    """Build cumulative P&L by strategy from the trade log."""
    if trades.empty or "exit_time" not in trades.columns:
        return _empty_chart(
            "No closed trades yet. Run a strategy or a backtest to populate the equity curve."
        )
    closed = trades[trades["pnl"].notna() & trades["exit_time"].notna()].copy()
    if closed.empty:
        return _empty_chart("No closed trades yet.")
    closed = closed.sort_values("exit_time")
    closed["equity"] = closed.groupby("strategy")["pnl"].cumsum()

    fig = go.Figure()
    for strat, grp in closed.groupby("strategy"):
        start_balance = next(
            (a["starting_balance"] for a in accounts if a["strategy"] == strat), 100000
        )
        fig.add_trace(
            go.Scatter(
                x=grp["exit_time"],
                y=start_balance + grp["equity"],
                mode="lines",
                name=strat,
                hovertemplate="%{x|%Y-%m-%d}<br>$%{y:,.0f}<extra>%{fullData.name}</extra>",
            )
        )
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#0b0f17",
        plot_bgcolor="#0b0f17",
        height=360,
        margin=dict(l=10, r=10, t=10, b=10),
        legend=dict(orientation="h", y=-0.18),
        xaxis=dict(gridcolor="#1f2a3a", showline=False),
        yaxis=dict(gridcolor="#1f2a3a", showline=False, tickprefix="$", tickformat=",.0f"),
        hoverlabel=dict(bgcolor="#141a24"),
    )
    return fig


def drawdown_chart(trades: pd.DataFrame) -> go.Figure:
    if trades.empty or "exit_time" not in trades.columns:
        return _empty_chart("No data.")
    closed = trades[trades["pnl"].notna() & trades["exit_time"].notna()].copy()
    if closed.empty:
        return _empty_chart("No data.")
    closed = closed.sort_values("exit_time")
    closed["cum_pnl"] = closed.groupby("strategy")["pnl"].cumsum()
    closed["peak"] = closed.groupby("strategy")["cum_pnl"].cummax()
    closed["dd"] = closed["cum_pnl"] - closed["peak"]

    fig = go.Figure()
    for strat, grp in closed.groupby("strategy"):
        fig.add_trace(
            go.Scatter(
                x=grp["exit_time"],
                y=grp["dd"],
                mode="lines",
                fill="tozeroy",
                name=strat,
                line=dict(width=1),
            )
        )
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#0b0f17",
        plot_bgcolor="#0b0f17",
        height=260,
        margin=dict(l=10, r=10, t=10, b=10),
        legend=dict(orientation="h", y=-0.25),
        xaxis=dict(gridcolor="#1f2a3a"),
        yaxis=dict(gridcolor="#1f2a3a", tickprefix="$", tickformat=",.0f"),
    )
    return fig


def heatmap_by_hour_dow(strategy_name: str) -> go.Figure:
    hours = attribute_by_hour(strategy_name, window_days=90)
    dows = attribute_by_day_of_week(strategy_name, window_days=90)
    if hours.empty or dows.empty:
        return _empty_chart(
            "No attribution data yet — attribution needs closed trades tagged with hour/day-of-week."
        )
    # Build a 7x24 matrix of avg_pnl from raw trades.
    from trading_bot.learning.attribution import _closed_trades

    df = _closed_trades(strategy_name, window_days=90)
    if df.empty:
        return _empty_chart("No closed trades yet for this strategy.")
    df["day_name"] = df["day_of_week"].apply(lambda d: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][d] if 0 <= d <= 6 else "?")
    pivot = df.pivot_table(index="day_name", columns="hour", values="pnl", aggfunc="mean")
    days_order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    pivot = pivot.reindex([d for d in days_order if d in pivot.index])
    fig = go.Figure(
        data=go.Heatmap(
            z=pivot.values,
            x=pivot.columns,
            y=pivot.index,
            colorscale=[[0, "#f87171"], [0.5, "#141a24"], [1, "#22d3ee"]],
            zmid=0,
            hovertemplate="%{y} %{x}:00<br>avg P&L: $%{z:,.2f}<extra></extra>",
        )
    )
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#0b0f17",
        plot_bgcolor="#0b0f17",
        height=280,
        margin=dict(l=10, r=10, t=10, b=10),
        xaxis=dict(title="Hour of entry", gridcolor="#1f2a3a"),
        yaxis=dict(title="", gridcolor="#1f2a3a"),
    )
    return fig


def _empty_chart(msg: str) -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(
        text=msg,
        xref="paper",
        yref="paper",
        x=0.5,
        y=0.5,
        showarrow=False,
        font=dict(color="#7a8ba8", size=13),
    )
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#0b0f17",
        plot_bgcolor="#0b0f17",
        height=260,
        margin=dict(l=10, r=10, t=10, b=10),
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
    )
    return fig


# ---- page ---------------------------------------------------------------


accounts = _accounts()
trades = _recent_trades(limit=500)
runs = _backtest_runs(limit=20)
news = _news()

# Header row
title_col, spacer, refresh_col = st.columns([4, 2, 1])
with title_col:
    st.markdown(
        "<div style='display:flex; align-items:baseline; gap:14px;'>"
        "<div style='font-size:1.45rem; font-weight:700; letter-spacing:-0.02em; color:#f5f7fa;'>"
        "◆ Prop Firm Trading</div>"
        "<div style='font-size:0.78rem; color:#7a8ba8;'>"
        f"last updated {datetime.now():%H:%M:%S}</div></div>",
        unsafe_allow_html=True,
    )
with refresh_col:
    auto = st.toggle("Auto-refresh 30s", value=False)
    if auto:
        import time

        time.sleep(30)
        st.rerun()

# KPI strip
kpi_row(accounts, trades)

# Account cards
st.markdown("## Accounts")
cols = st.columns(3)
for idx, a in enumerate(accounts):
    with cols[idx % 3]:
        st.markdown(account_card(a), unsafe_allow_html=True)

# Tabs
tab_perf, tab_attr, tab_cull, tab_bt, tab_news, tab_trades = st.tabs(
    ["Equity", "Attribution", "Culling", "Backtests", "News", "Trades"]
)

with tab_perf:
    st.markdown("## Equity by strategy")
    st.plotly_chart(equity_curve(trades, accounts), width="stretch", config={"displayModeBar": False})

    st.markdown("## Drawdown (underwater curve)")
    st.plotly_chart(drawdown_chart(trades), width="stretch", config={"displayModeBar": False})

    st.markdown("## Rolling metrics (nightly snapshot)")
    window_label = st.selectbox(
        "Window",
        [("All time", 0), ("Last 90 days", 90), ("Last 30 days", 30)],
        format_func=lambda x: x[0],
    )
    perf = _performance(window_days=window_label[1])
    if perf.empty:
        st.info("Run `make nightly` once there are closed trades to populate this view.")
    else:
        st.dataframe(perf, width="stretch")

with tab_attr:
    st.markdown("## Where does each strategy actually make money?")
    strategies = sorted({a["strategy"] for a in accounts})
    strat = st.selectbox("Strategy", strategies)
    col_l, col_r = st.columns(2)
    with col_l:
        st.markdown("**Heatmap: hour × day of week (avg P&L)**")
        st.plotly_chart(
            heatmap_by_hour_dow(strat),
            width="stretch",
            config={"displayModeBar": False},
        )
    with col_r:
        st.markdown("**By market regime**")
        regime = attribute_by_regime(strat, window_days=90)
        if regime.empty:
            st.info("No regime-tagged trades yet.")
        else:
            fig = px.bar(
                regime.reset_index(),
                x="market_regime",
                y="total_pnl",
                color="total_pnl",
                color_continuous_scale=[[0, "#f87171"], [0.5, "#141a24"], [1, "#22d3ee"]],
            )
            fig.update_layout(
                template="plotly_dark",
                paper_bgcolor="#0b0f17",
                plot_bgcolor="#0b0f17",
                height=260,
                margin=dict(l=10, r=10, t=10, b=10),
                xaxis=dict(gridcolor="#1f2a3a", title=""),
                yaxis=dict(gridcolor="#1f2a3a", title="", tickprefix="$"),
                coloraxis_showscale=False,
            )
            st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})

with tab_cull:
    st.markdown("## Culling verdicts — spec §9 decision framework")
    st.caption(
        "Month 3: kill if DD > 8%, flag if Sharpe < 0.5. "
        "Promote: Sharpe > 1, win_rate > 55%, DD < 5%, ≥20 trades. "
        "Verdicts surface for human review — never auto-applied to account status."
    )
    all_time = _performance(window_days=0)
    if all_time.empty:
        st.info("Run `make nightly` after the first paper trades close.")
    else:
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
                    "month 3": m3.verdict.value,
                    "month 3 reason": m3.reason,
                    "promotion": promo.verdict.value,
                    "promotion reason": promo.reason,
                }
            )
        st.dataframe(pd.DataFrame(rows), width="stretch")

with tab_bt:
    st.markdown("## Backtest history")
    if runs.empty:
        st.info("Run `make backtest-rsi2` (or `python scripts/backtest.py ...`).")
    else:
        st.dataframe(
            runs.style.format(
                {
                    "budget": "${:,.0f}",
                    "return_pct": "{:+.2%}",
                    "sharpe": "{:+.2f}",
                    "max_dd_pct": "{:.2%}",
                    "run_at": lambda d: d.strftime("%Y-%m-%d %H:%M") if pd.notna(d) else "",
                }
            ),
            width="stretch",
        )
        latest_ret = runs.dropna(subset=["return_pct"]).head(10)
        if not latest_ret.empty:
            fig = px.bar(
                latest_ret,
                x="strategy",
                y="return_pct",
                color="return_pct",
                color_continuous_scale=[[0, "#f87171"], [0.5, "#141a24"], [1, "#22d3ee"]],
                hover_data=["start", "end", "trades", "sharpe", "max_dd_pct"],
            )
            fig.update_layout(
                template="plotly_dark",
                paper_bgcolor="#0b0f17",
                plot_bgcolor="#0b0f17",
                height=300,
                margin=dict(l=10, r=10, t=10, b=10),
                xaxis=dict(gridcolor="#1f2a3a", title=""),
                yaxis=dict(gridcolor="#1f2a3a", title="Return", tickformat=".1%"),
                coloraxis_showscale=False,
            )
            st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})

with tab_news:
    st.markdown("## Upcoming HIGH-impact events (risk blackouts)")
    if news.empty:
        st.info("Run `make news` to pull the current week's ForexFactory calendar.")
    else:
        pills = ""
        for _, n in news.iterrows():
            local = n["starts_at"].tz_convert("America/New_York")
            pills += (
                f'<span class="news-pill">'
                f'<span class="ccy">{n["currency"]}</span>'
                f'{n["event"]}<span class="time">{local.strftime("%a %m-%d %H:%M ET")}</span>'
                f"</span>"
            )
        st.markdown(pills, unsafe_allow_html=True)

with tab_trades:
    st.markdown("## Recent trades")
    if trades.empty:
        st.info("No trades yet. Run a strategy to populate.")
    else:
        pnl_styled = trades.copy()
        pnl_styled["pnl"] = pnl_styled["pnl"].apply(
            lambda x: f"${x:+,.2f}" if pd.notna(x) else "—"
        )
        pnl_styled["pnl_pct"] = pnl_styled["pnl_pct"].apply(
            lambda x: f"{x:+.2%}" if pd.notna(x) else "—"
        )
        st.dataframe(pnl_styled, width="stretch", height=420)
