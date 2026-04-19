"""Prop firm trading terminal.

Bloomberg-meets-Robinhood aesthetic: deep navy-black canvas, electric
blue + neon green accents, tabular monospace numbers, subtle glows on
live data, sparklines inline in account rows. Run with:

    streamlit run src/trading_bot/dashboard/app.py
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from sqlalchemy import select

from trading_bot.db.models import (
    Account,
    BacktestRun,
    NewsWindow,
    StrategyHeartbeat,
    StrategyPerformanceDaily,
    Trade,
)
from trading_bot.db.session import get_session
from trading_bot.dashboard.live_feeds import (
    fetch_alpaca_balance,
    fetch_broker_balances,
    fetch_headlines,
    fetch_markets,
    market_tile,
    news_tape_html,
)
from trading_bot.learning import (
    attribute_by_day_of_week,
    attribute_by_hour,
    attribute_by_regime,
    month_3_decision,
    promotion_decision,
)
from trading_bot.risk.broker_pool import BrokerPool
from trading_bot.config import get_settings
from trading_bot.risk.rules import MODE_RULES


def _broker_configured(firm: str) -> bool:
    """True if the .env has real credentials for this account's firm."""
    s = get_settings()
    if firm == "Alpaca_Paper":
        return bool(s.alpaca_api_key and s.alpaca_api_secret)
    if firm == "OANDA_Demo":
        return bool(s.oanda_api_token and s.oanda_account_id)
    if firm == "Tradovate_Sim":
        return bool(
            s.tradovate_username
            and s.tradovate_password
            and s.tradovate_client_id
            and s.tradovate_client_secret
        )
    # Unknown firm — default to showing it (don't hide real live/funded accounts)
    return True

st.set_page_config(
    page_title="Trading Desk",
    page_icon="◆",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---- brand palette -------------------------------------------------------

BG = "#05080f"
BG_DEEP = "#02040a"
PANEL = "#0a0f1a"
PANEL_HI = "#111827"
BORDER = "#1a2232"
BORDER_HI = "#2a3548"
TEXT = "#e8eef8"
TEXT_DIM = "#7a8ba8"
TEXT_MUTED = "#4a5568"
ACCENT = "#00d9ff"           # electric blue
ACCENT_GLOW = "rgba(0, 217, 255, 0.35)"
POS = "#00ff88"               # neon green
POS_GLOW = "rgba(0, 255, 136, 0.3)"
NEG = "#ff3366"               # electric red
NEG_GLOW = "rgba(255, 51, 102, 0.3)"
WARN = "#ffb020"

# ---- css -----------------------------------------------------------------

st.html(
    f"""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap');
    :root {{
        --bg: {BG};
        --bg-deep: {BG_DEEP};
        --panel: {PANEL};
        --panel-hi: {PANEL_HI};
        --border: {BORDER};
        --border-hi: {BORDER_HI};
        --text: {TEXT};
        --text-dim: {TEXT_DIM};
        --text-muted: {TEXT_MUTED};
        --accent: {ACCENT};
        --accent-glow: {ACCENT_GLOW};
        --pos: {POS};
        --pos-glow: {POS_GLOW};
        --neg: {NEG};
        --neg-glow: {NEG_GLOW};
    }}
    html, body, [class*="css"], [class*="st-"] {{
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important;
        color: var(--text) !important;
    }}
    .stApp {{
        background: radial-gradient(ellipse at top, #0a1428 0%, var(--bg) 40%, var(--bg-deep) 100%);
        background-attachment: fixed;
    }}
    .block-container {{
        padding: 0.5rem 1.75rem 2.5rem 1.75rem !important;
        max-width: 100% !important;
    }}

    /* kill streamlit chrome */
    header[data-testid="stHeader"] {{ background: transparent; height: 0; }}
    footer {{ display: none; }}
    #MainMenu {{ display: none; }}
    div[data-testid="stDecoration"] {{ display: none; }}

    /* --- HERO HEADER --- */
    .hero {{
        display: flex; align-items: center; justify-content: space-between;
        padding: 14px 0 16px 0;
        border-bottom: 1px solid var(--border);
        margin-bottom: 0;
    }}
    .hero .brand {{
        display: flex; align-items: center; gap: 14px;
    }}
    .hero .logo {{
        width: 34px; height: 34px;
        background: linear-gradient(135deg, var(--accent) 0%, var(--pos) 100%);
        border-radius: 8px;
        box-shadow: 0 0 18px var(--accent-glow);
        display: flex; align-items: center; justify-content: center;
        font-weight: 800; font-size: 1.05rem; color: #051018;
    }}
    .hero h1 {{
        font-size: 1.25rem !important; font-weight: 700 !important;
        letter-spacing: -0.01em; color: var(--text) !important;
        margin: 0 !important; padding: 0 !important;
        line-height: 1;
    }}
    .hero .tag {{
        font-size: 0.66rem; font-weight: 600;
        letter-spacing: 0.14em; text-transform: uppercase;
        color: var(--text-muted); margin-top: 3px;
    }}
    .hero .clock {{
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.78rem; color: var(--text-dim);
        padding: 6px 12px; background: var(--panel);
        border: 1px solid var(--border); border-radius: 4px;
        font-feature-settings: 'tnum' on;
    }}
    .hero .live {{
        display: inline-block; width: 6px; height: 6px;
        background: var(--pos); border-radius: 50%;
        margin-right: 6px; vertical-align: middle;
        animation: pulse 2s ease-in-out infinite;
        box-shadow: 0 0 8px var(--pos);
    }}
    @keyframes pulse {{
        0%, 100% {{ opacity: 1; }}
        50% {{ opacity: 0.4; }}
    }}

    /* --- TICKER STRIP --- */
    .ticker {{
        display: flex; gap: 28px;
        padding: 14px 18px;
        background: linear-gradient(90deg, var(--panel) 0%, var(--panel-hi) 50%, var(--panel) 100%);
        border: 1px solid var(--border);
        border-radius: 8px;
        margin: 14px 0 18px 0;
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.82rem;
        overflow-x: auto;
        white-space: nowrap;
    }}
    .ticker .tick {{ display: flex; align-items: baseline; gap: 9px; }}
    .ticker .lbl {{
        color: var(--text-muted);
        text-transform: uppercase;
        letter-spacing: 0.14em;
        font-size: 0.62rem;
        font-weight: 700;
        font-family: 'Inter', sans-serif;
    }}
    .ticker .val {{
        color: var(--text); font-weight: 500;
        font-feature-settings: 'tnum' on;
    }}
    .ticker .pos {{ color: var(--pos); text-shadow: 0 0 6px var(--pos-glow); }}
    .ticker .neg {{ color: var(--neg); text-shadow: 0 0 6px var(--neg-glow); }}
    .ticker .zero {{ color: var(--text-muted); }}

    /* --- STAT TILES --- */
    .stat {{
        background: linear-gradient(145deg, var(--panel) 0%, var(--panel-hi) 100%);
        border: 1px solid var(--border);
        border-radius: 10px;
        padding: 16px 18px;
        height: 100%;
        position: relative;
        overflow: hidden;
        transition: border-color 0.2s, transform 0.2s;
    }}
    .stat:hover {{
        border-color: var(--border-hi);
    }}
    .stat::before {{
        content: '';
        position: absolute;
        top: 0; left: 0; right: 0; height: 1px;
        background: linear-gradient(90deg, transparent, var(--accent), transparent);
        opacity: 0.25;
    }}
    .stat .lbl {{
        font-size: 0.62rem; color: var(--text-muted);
        text-transform: uppercase; letter-spacing: 0.14em;
        font-weight: 700; margin-bottom: 8px;
    }}
    .stat .big {{
        font-family: 'JetBrains Mono', monospace;
        font-size: 1.7rem; font-weight: 500;
        color: var(--text); letter-spacing: -0.02em;
        line-height: 1.1; font-feature-settings: 'tnum' on;
    }}
    .stat .big.pos {{ color: var(--pos); text-shadow: 0 0 14px var(--pos-glow); }}
    .stat .big.neg {{ color: var(--neg); text-shadow: 0 0 14px var(--neg-glow); }}
    .stat .sub {{
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.72rem;
        margin-top: 6px; color: var(--text-dim);
        font-feature-settings: 'tnum' on;
    }}
    .stat .sub.pos {{ color: var(--pos); }}
    .stat .sub.neg {{ color: var(--neg); }}

    /* --- SECTION HEADINGS --- */
    h2 {{
        font-size: 0.68rem !important;
        text-transform: uppercase;
        letter-spacing: 0.18em;
        color: var(--text-dim) !important;
        font-weight: 700;
        margin: 2rem 0 0.8rem 0 !important;
        padding: 0;
        border-bottom: none !important;
    }}
    h2::before {{
        content: '—  ';
        color: var(--accent);
    }}

    /* --- ACCOUNTS TABLE --- */
    .acct-table {{
        width: 100%;
        border-collapse: separate;
        border-spacing: 0;
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.82rem;
        background: var(--panel);
        border: 1px solid var(--border);
        border-radius: 10px;
        overflow: hidden;
    }}
    .acct-table th {{
        text-align: left;
        color: var(--text-muted);
        font-weight: 700;
        font-size: 0.62rem;
        text-transform: uppercase;
        letter-spacing: 0.14em;
        padding: 12px 16px;
        border-bottom: 1px solid var(--border);
        background: var(--bg);
        font-family: 'Inter', sans-serif;
    }}
    .acct-table td {{
        padding: 14px 16px;
        border-bottom: 1px solid var(--border);
        color: var(--text);
        vertical-align: middle;
        font-feature-settings: 'tnum' on;
    }}
    .acct-table tbody tr:last-child td {{ border-bottom: none; }}
    .acct-table tbody tr {{ transition: background 0.15s; }}
    .acct-table tbody tr:hover {{ background: rgba(0, 217, 255, 0.03); }}
    .acct-table .num {{ text-align: right; }}
    .acct-table .pos {{ color: var(--pos); }}
    .acct-table .neg {{ color: var(--neg); }}
    .acct-table .zero {{ color: var(--text-muted); }}
    .acct-table .strat {{
        font-family: 'Inter', sans-serif;
        font-weight: 700;
        font-size: 0.88rem;
        color: var(--text);
        letter-spacing: -0.01em;
    }}
    .acct-table .firm {{
        font-family: 'Inter', sans-serif;
        color: var(--text-muted);
        font-size: 0.7rem;
        letter-spacing: 0.04em;
    }}

    /* risk bars */
    .riskbar {{
        display: inline-block; width: 84px; height: 5px;
        background: var(--bg-deep);
        border-radius: 3px; overflow: hidden;
        vertical-align: middle; margin-right: 10px;
        border: 1px solid var(--border);
    }}
    .riskbar .fill {{
        display: block; height: 100%;
        border-radius: 3px;
        transition: width 0.4s;
    }}

    /* status dot */
    .dot {{
        display: inline-block; width: 8px; height: 8px;
        border-radius: 50%; margin-right: 10px;
        vertical-align: middle;
    }}
    .dot.ACTIVE {{
        background: var(--pos);
        box-shadow: 0 0 9px var(--pos-glow);
        animation: pulse 2.5s ease-in-out infinite;
    }}
    .dot.HALTED {{ background: {WARN}; }}
    .dot.BLOWN {{ background: var(--neg); box-shadow: 0 0 9px var(--neg-glow); }}
    .dot.PASSED {{ background: var(--accent); box-shadow: 0 0 9px var(--accent-glow); }}

    /* mode tag */
    .modetag {{
        font-family: 'Inter', sans-serif;
        font-size: 0.6rem; font-weight: 700;
        letter-spacing: 0.14em;
        padding: 3px 8px;
        border: 1px solid var(--border-hi);
        border-radius: 4px;
        color: var(--text-dim);
    }}
    .modetag.CHALLENGE {{ color: {WARN}; border-color: {WARN}; }}
    .modetag.FUNDED {{ color: var(--pos); border-color: var(--pos); }}

    /* --- TABS --- */
    div[data-baseweb="tab-list"] {{
        gap: 0 !important;
        border-bottom: 1px solid var(--border) !important;
        margin-top: 18px;
    }}
    button[data-baseweb="tab"] {{
        font-family: 'Inter', sans-serif !important;
        font-size: 0.7rem !important;
        font-weight: 700 !important;
        letter-spacing: 0.14em;
        text-transform: uppercase;
        color: var(--text-muted) !important;
        padding: 12px 22px !important;
        border-bottom: 2px solid transparent !important;
        background: transparent !important;
    }}
    button[data-baseweb="tab"][aria-selected="true"] {{
        color: var(--accent) !important;
        border-bottom: 2px solid var(--accent) !important;
        text-shadow: 0 0 10px var(--accent-glow);
    }}

    /* dataframes */
    .stDataFrame {{
        font-family: 'JetBrains Mono', monospace !important;
        font-size: 0.8rem !important;
        border-radius: 8px;
        overflow: hidden;
    }}

    /* selectbox */
    .stSelectbox > div > div {{
        background: var(--panel) !important;
        border: 1px solid var(--border) !important;
        border-radius: 6px !important;
    }}

    /* scrollbar */
    ::-webkit-scrollbar {{ width: 10px; height: 10px; }}
    ::-webkit-scrollbar-track {{ background: var(--bg); }}
    ::-webkit-scrollbar-thumb {{ background: var(--border-hi); border-radius: 5px; }}
    ::-webkit-scrollbar-thumb:hover {{ background: var(--text-muted); }}

    /* news-row */
    .news-row {{
        display: grid;
        grid-template-columns: 80px 110px 1fr 170px;
        gap: 18px;
        padding: 14px 16px;
        border-bottom: 1px solid var(--border);
        background: var(--panel);
        font-family: 'Inter', sans-serif;
        font-size: 0.86rem;
        align-items: center;
    }}
    .news-row:first-child {{ border-top-left-radius: 10px; border-top-right-radius: 10px; }}
    .news-row:last-child {{ border-bottom: 1px solid var(--border); border-bottom-left-radius: 10px; border-bottom-right-radius: 10px; }}
    .news-row .ccy {{
        font-family: 'JetBrains Mono', monospace;
        color: var(--accent); font-weight: 600;
        letter-spacing: 0.08em;
    }}
    .news-row .impact {{
        display: inline-flex; align-items: center;
        font-size: 0.62rem;
        text-transform: uppercase; letter-spacing: 0.14em;
        color: {WARN}; font-weight: 700;
    }}
    .news-row .impact::before {{
        content: ''; display: inline-block;
        width: 6px; height: 6px; background: {WARN};
        border-radius: 50%; margin-right: 6px;
        box-shadow: 0 0 6px {WARN};
    }}
    .news-row .event {{
        color: var(--text);
        font-weight: 500;
    }}
    .news-row .time {{
        font-family: 'JetBrains Mono', monospace;
        color: var(--text-dim);
        text-align: right;
        font-size: 0.78rem;
    }}

    /* empty state */
    .empty {{
        padding: 28px;
        background: var(--panel);
        border: 1px dashed var(--border-hi);
        border-radius: 10px;
        color: var(--text-dim);
        font-family: 'Inter', sans-serif;
        font-size: 0.9rem;
        text-align: center;
    }}
    .empty code {{
        background: var(--bg-deep);
        padding: 2px 8px;
        color: var(--accent);
        border-radius: 4px;
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.82rem;
    }}

    /* sparkline container */
    .spark-cell {{
        padding: 0 !important;
        width: 120px;
    }}

    /* --- MARKETS STRIP --- */
    .markets {{
        display: flex;
        gap: 2px;
        background: var(--bg-deep);
        border: 1px solid var(--border);
        border-radius: 8px;
        padding: 2px;
        overflow-x: auto;
        white-space: nowrap;
        margin: 14px 0 0 0;
    }}
    .mkt-tile {{
        display: inline-flex;
        flex-direction: column;
        gap: 3px;
        padding: 10px 16px;
        min-width: 110px;
        background: var(--panel);
        border-radius: 6px;
        font-family: 'JetBrains Mono', monospace;
        font-feature-settings: 'tnum' on;
    }}
    .mkt-lbl {{
        color: var(--text-muted);
        font-size: 0.58rem;
        text-transform: uppercase;
        letter-spacing: 0.14em;
        font-weight: 700;
        font-family: 'Inter', sans-serif;
    }}
    .mkt-val {{
        color: var(--text);
        font-size: 0.95rem;
        font-weight: 500;
        letter-spacing: -0.01em;
    }}
    .mkt-chg {{
        font-size: 0.72rem;
        font-weight: 500;
    }}
    .mkt-chg.pos {{ color: var(--pos); text-shadow: 0 0 5px var(--pos-glow); }}
    .mkt-chg.neg {{ color: var(--neg); text-shadow: 0 0 5px var(--neg-glow); }}
    .mkt-chg.zero {{ color: var(--text-muted); }}

    /* --- NEWS TAPE (scrolling) --- */
    .newstape {{
        background: var(--panel);
        border: 1px solid var(--border);
        border-radius: 8px;
        overflow: hidden;
        margin: 10px 0 18px 0;
        position: relative;
    }}
    .newstape::before,
    .newstape::after {{
        content: '';
        position: absolute;
        top: 0; bottom: 0; width: 60px;
        z-index: 2;
        pointer-events: none;
    }}
    .newstape::before {{
        left: 0;
        background: linear-gradient(90deg, var(--panel), transparent);
    }}
    .newstape::after {{
        right: 0;
        background: linear-gradient(-90deg, var(--panel), transparent);
    }}
    .newstape-track {{
        display: inline-block;
        white-space: nowrap;
        padding: 12px 0;
        animation: scroll-tape 300s linear infinite;
        font-family: 'Inter', sans-serif;
        font-size: 0.84rem;
    }}
    .newstape:hover .newstape-track {{ animation-play-state: paused; }}
    @keyframes scroll-tape {{
        0% {{ transform: translateX(0); }}
        100% {{ transform: translateX(-50%); }}
    }}
    .headline {{
        display: inline-flex;
        align-items: center;
        gap: 8px;
        margin-right: 36px;
        color: var(--text);
    }}
    .headline-dot {{
        color: var(--accent);
        font-size: 0.7rem;
    }}
    .headline-sym {{
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.66rem;
        font-weight: 700;
        color: var(--accent);
        background: rgba(0, 217, 255, 0.1);
        border: 1px solid rgba(0, 217, 255, 0.3);
        padding: 1px 6px;
        border-radius: 3px;
        letter-spacing: 0.05em;
    }}
    .headline-title {{
        color: var(--text);
        font-weight: 500;
    }}
    .headline-meta {{
        color: var(--text-muted);
        font-size: 0.72rem;
        margin-left: 6px;
    }}

    </style>
    """
)

# ---- data ----------------------------------------------------------------


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


def _recent_trades(limit: int = 500) -> pd.DataFrame:
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
                "return": float(r.total_return_pct) if r.total_return_pct else None,
                "sharpe": float(r.sharpe) if r.sharpe else None,
                "max_dd": float(r.max_drawdown_pct) if r.max_drawdown_pct else None,
                "run_at": r.run_at,
            }
            for r in rows
        ]
    return pd.DataFrame(data)


def _heartbeats() -> pd.DataFrame:
    with get_session() as s:
        rows = s.execute(select(StrategyHeartbeat)).scalars().all()
        data = [
            {
                "strategy": h.strategy_name,
                "firm": h.firm,
                "last_tick_at": h.last_tick_at,
                "last_decision": h.last_decision,
                "iter_today": h.iteration_count_today,
                "iter_total": h.iterations_total,
                "sleeptime": h.sleeptime,
            }
            for h in rows
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


# ---- plotly helpers ------------------------------------------------------


def _chart_layout(height: int = 340) -> dict:
    return dict(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        height=height,
        margin=dict(l=10, r=10, t=10, b=10),
        font=dict(family="JetBrains Mono, monospace", color=TEXT, size=11),
        legend=dict(
            orientation="h", y=-0.18,
            bgcolor="rgba(0,0,0,0)",
            font=dict(family="Inter", size=11, color=TEXT_DIM),
        ),
        xaxis=dict(
            gridcolor=BORDER, showline=False, zeroline=False,
            tickfont=dict(family="JetBrains Mono", size=10, color=TEXT_DIM),
        ),
        yaxis=dict(
            gridcolor=BORDER, showline=False, zeroline=False,
            tickfont=dict(family="JetBrains Mono", size=10, color=TEXT_DIM),
        ),
        hoverlabel=dict(
            bgcolor=PANEL_HI, bordercolor=BORDER_HI,
            font=dict(family="JetBrains Mono", color=TEXT, size=11),
        ),
    )


def _empty_chart(msg: str, height: int = 280) -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(
        text=msg, xref="paper", yref="paper", x=0.5, y=0.5, showarrow=False,
        font=dict(family="Inter", color=TEXT_MUTED, size=12),
    )
    fig.update_layout(**_chart_layout(height))
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    return fig


def hero_equity(trades: pd.DataFrame, accounts: list[dict]) -> go.Figure:
    """Big top-of-page equity chart — aggregate NAV over time."""
    if trades.empty or "exit_time" not in trades.columns:
        return _empty_chart(
            "Live equity curve appears once strategies begin trading. "
            "Run the RSI2 strategy (`python run/run_rsi2_spy.py`) or a backtest to populate.",
            height=300,
        )
    closed = trades[trades["pnl"].notna() & trades["exit_time"].notna()].copy()
    if closed.empty:
        return _empty_chart("Live equity curve appears once strategies begin trading.", height=300)
    closed = closed.sort_values("exit_time")
    total_start = sum(a["starting_balance"] for a in accounts)
    closed["cum"] = closed["pnl"].cumsum()
    nav = total_start + closed["cum"]

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=closed["exit_time"], y=nav,
            mode="lines",
            line=dict(color=ACCENT, width=2.2),
            fill="tozeroy",
            fillcolor=f"rgba(0, 217, 255, 0.08)",
            hovertemplate="%{x|%Y-%m-%d}<br>$%{y:,.0f}<extra></extra>",
            name="NAV",
        )
    )
    fig.update_layout(**_chart_layout(280))
    fig.update_yaxes(tickprefix="$", tickformat=",.0f")
    return fig


def strategy_equity(trades: pd.DataFrame, accounts: list[dict]) -> go.Figure:
    if trades.empty:
        return _empty_chart("No closed trades yet.", height=320)
    closed = trades[trades["pnl"].notna() & trades["exit_time"].notna()].copy()
    if closed.empty:
        return _empty_chart("No closed trades yet.", height=320)
    closed = closed.sort_values("exit_time")
    closed["equity"] = closed.groupby("strategy")["pnl"].cumsum()

    palette = [ACCENT, POS, "#a78bfa", WARN, "#f472b6", "#60a5fa"]
    fig = go.Figure()
    for i, (strat, grp) in enumerate(closed.groupby("strategy")):
        start = next(
            (a["starting_balance"] for a in accounts if a["strategy"] == strat), 100000
        )
        fig.add_trace(
            go.Scatter(
                x=grp["exit_time"], y=start + grp["equity"],
                mode="lines", name=strat,
                line=dict(color=palette[i % len(palette)], width=1.8),
                hovertemplate="%{x|%Y-%m-%d}<br>$%{y:,.0f}<extra>%{fullData.name}</extra>",
            )
        )
    fig.update_layout(**_chart_layout(320))
    fig.update_yaxes(tickprefix="$", tickformat=",.0f")
    return fig


def drawdown_chart(trades: pd.DataFrame) -> go.Figure:
    if trades.empty or "exit_time" not in trades.columns:
        return _empty_chart("No data.", height=200)
    closed = trades[trades["pnl"].notna() & trades["exit_time"].notna()].copy()
    if closed.empty:
        return _empty_chart("No data.", height=200)
    closed = closed.sort_values("exit_time")
    closed["cum_pnl"] = closed.groupby("strategy")["pnl"].cumsum()
    closed["peak"] = closed.groupby("strategy")["cum_pnl"].cummax()
    closed["dd"] = closed["cum_pnl"] - closed["peak"]

    fig = go.Figure()
    for strat, grp in closed.groupby("strategy"):
        fig.add_trace(
            go.Scatter(
                x=grp["exit_time"], y=grp["dd"],
                mode="lines", fill="tozeroy",
                name=strat,
                line=dict(width=1.3, color=NEG),
                fillcolor="rgba(255, 51, 102, 0.15)",
            )
        )
    fig.update_layout(**_chart_layout(200))
    fig.update_yaxes(tickprefix="$", tickformat=",.0f")
    return fig


def heatmap_by_hour_dow(strategy_name: str) -> go.Figure:
    from trading_bot.learning.attribution import _closed_trades
    df = _closed_trades(strategy_name, window_days=90)
    if df.empty:
        return _empty_chart(f"Attribution heatmap appears once {strategy_name} has closed trades.")
    df["day_name"] = df["day_of_week"].apply(
        lambda d: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][d] if 0 <= d <= 6 else "?"
    )
    pivot = df.pivot_table(index="day_name", columns="hour", values="pnl", aggfunc="mean")
    days_order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    pivot = pivot.reindex([d for d in days_order if d in pivot.index])
    fig = go.Figure(
        data=go.Heatmap(
            z=pivot.values, x=pivot.columns, y=pivot.index,
            colorscale=[[0, NEG], [0.5, "#0a0f1a"], [1, POS]],
            zmid=0, showscale=False,
            hovertemplate="%{y} · %{x}:00<br>avg $%{z:,.2f}<extra></extra>",
        )
    )
    fig.update_layout(**_chart_layout(280))
    return fig


# ==========================================================================
# PAGE
# ==========================================================================

all_accounts = _accounts()
# Only show accounts whose broker creds are configured.
# The rest stay seeded in the DB — they'll appear automatically once you
# drop OANDA / Tradovate keys into .env.
accounts = [a for a in all_accounts if _broker_configured(a["firm"])]
dormant = [a for a in all_accounts if not _broker_configured(a["firm"])]
trades = _recent_trades(500)
runs = _backtest_runs(20)
news = _news()

# --- hero header ---
st.markdown(
    f"""
    <div class="hero">
        <div class="brand">
            <div class="logo">◆</div>
            <div>
                <h1>Trading Desk</h1>
                <div class="tag">Prop firm · 6 strategies · paper</div>
            </div>
        </div>
        <div class="clock"><span class="live"></span>{datetime.now(timezone.utc):%Y-%m-%d · %H:%M:%S UTC}</div>
    </div>
    """,
    unsafe_allow_html=True,
)

# --- live markets strip (yfinance, 60s cache) ---
markets = fetch_markets()
if markets:
    tiles = "".join(market_tile(m) for m in markets)
    st.markdown(f'<div class="markets">{tiles}</div>', unsafe_allow_html=True)

# --- news ticker (Alpaca News API, 5m cache) ---
headlines = fetch_headlines()
if headlines:
    st.markdown(news_tape_html(headlines), unsafe_allow_html=True)

# --- ticker strip ---
broker_balances = fetch_broker_balances()

# Pretty labels for tiles
_BROKER_LABELS = {"Alpaca_Paper": "ALPACA", "OANDA_Demo": "OANDA", "Tradovate_Sim": "TRADOVATE"}

def _ticker() -> str:
    nominal_alloc = sum(a["starting_balance"] for a in accounts)
    weekly_pnl = sum(a["weekly_pnl"] for a in accounts)
    active = sum(1 for a in accounts if a["status"] == "ACTIVE")
    halted = sum(1 for a in accounts if a["status"] in ("HALTED", "BLOWN"))
    max_dd = max((a["drawdown_pct"] for a in accounts), default=0)

    def cls(v):
        return "pos" if v > 0 else "neg" if v < 0 else "zero"

    # Per-broker NAV tiles (one for each configured broker)
    broker_tiles = ""
    total_day_delta = 0.0
    for firm, acct in broker_balances.items():
        eq = acct.get("equity") or 0
        last = acct.get("last_equity") or eq
        delta = eq - last
        total_day_delta += delta
        ret = delta / last if last else 0
        label = _BROKER_LABELS.get(firm, firm.upper())
        broker_tiles += (
            f'<div class="tick"><span class="lbl">{label}</span>'
            f'<span class="val">${eq:,.0f}</span>'
            f'<span class="val {cls(delta)}">{ret:+.2%}</span></div>'
        )

    return f"""
    <div class="ticker">
        {broker_tiles}
        <div class="tick"><span class="lbl">DAY P&L</span>
            <span class="val {cls(total_day_delta)}">${total_day_delta:+,.2f}</span></div>
        <div class="tick"><span class="lbl">NOMINAL ALLOC</span>
            <span class="val">${nominal_alloc:,.0f}</span>
            <span class="val" style="color:var(--text-muted); font-size:0.66rem;">
                {len(accounts)} strategies</span></div>
        <div class="tick"><span class="lbl">WK (STRAT)</span>
            <span class="val {cls(weekly_pnl)}">${weekly_pnl:+,.2f}</span></div>
        <div class="tick"><span class="lbl">ACTIVE</span>
            <span class="val">{active}/{len(accounts)}</span></div>
        <div class="tick"><span class="lbl">HALTED</span>
            <span class="val {"neg" if halted else "zero"}">{halted}</span></div>
        <div class="tick"><span class="lbl">MAX DD</span>
            <span class="val">{max_dd:.2%}</span></div>
    </div>
    """

st.markdown(_ticker(), unsafe_allow_html=True)

# Explainer line showing real accounts.
if broker_balances:
    lines = []
    for firm, acct in broker_balances.items():
        strats = [a for a in accounts if a["firm"] == firm]
        label = _BROKER_LABELS.get(firm, firm)
        lines.append(
            f'<strong style="color:{TEXT_DIM};">{label}</strong> ${acct["equity"]:,.0f} '
            f'real · {len(strats)} strateg{"y" if len(strats)==1 else "ies"} · '
            f'BP ${acct["buying_power"]:,.0f}'
        )
    st.markdown(
        f'<div style="font-family:Inter; font-size:0.72rem; color:{TEXT_MUTED}; '
        f'margin-top:6px; letter-spacing:0.04em;">'
        + " &nbsp;·&nbsp; ".join(lines)
        + "</div>",
        unsafe_allow_html=True,
    )

# --- hero equity chart ---
col_l, col_r = st.columns([3, 2])
with col_l:
    st.markdown('<div style="color:' + TEXT_DIM + '; font-size:0.68rem; letter-spacing:0.18em; text-transform:uppercase; font-weight:700; margin-bottom:4px;">Portfolio NAV</div>', unsafe_allow_html=True)
    st.plotly_chart(hero_equity(trades, accounts), width="stretch", config={"displayModeBar": False})

with col_r:
    st.markdown('<div style="color:' + TEXT_DIM + '; font-size:0.68rem; letter-spacing:0.18em; text-transform:uppercase; font-weight:700; margin-bottom:4px;">Stats</div>', unsafe_allow_html=True)
    closed_trades = trades[trades["pnl"].notna()] if not trades.empty else pd.DataFrame()
    n_trades = len(closed_trades)
    win_rate = (closed_trades["pnl"] > 0).mean() if n_trades else None
    avg_win = closed_trades[closed_trades["pnl"] > 0]["pnl"].mean() if n_trades and (closed_trades["pnl"] > 0).any() else None
    avg_loss = closed_trades[closed_trades["pnl"] < 0]["pnl"].mean() if n_trades and (closed_trades["pnl"] < 0).any() else None
    total_pnl = closed_trades["pnl"].sum() if n_trades else 0.0

    def _tile(label, big, sub="", big_class=""):
        return f"""
        <div class="stat" style="margin-bottom:10px;">
            <div class="lbl">{label}</div>
            <div class="big {big_class}">{big}</div>
            <div class="sub">{sub}&nbsp;</div>
        </div>
        """

    tiles_html = (
        _tile("Closed Trades", f"{n_trades:,}", f"{int(win_rate*n_trades) if win_rate else 0}W / {int((1-win_rate)*n_trades) if win_rate else 0}L" if win_rate is not None else "—")
        + _tile(
            "Total P&L",
            f"${total_pnl:+,.2f}" if total_pnl else "—",
            f"win rate {win_rate:.1%}" if win_rate is not None else "",
            "pos" if total_pnl > 0 else "neg" if total_pnl < 0 else "",
        )
        + _tile(
            "Avg Winner",
            f"${avg_win:,.2f}" if avg_win else "—",
            f"Avg Loser: ${avg_loss:,.2f}" if avg_loss else "",
            "pos" if avg_win else "",
        )
    )
    st.markdown(tiles_html, unsafe_allow_html=True)

# --- accounts table ---
st.markdown("## Accounts")

def account_row(a: dict) -> str:
    mode_rules = MODE_RULES[a["mode"]]
    pnl = a["daily_pnl"]
    pnl_cls = "pos" if pnl > 0 else "neg" if pnl < 0 else "zero"
    week_pnl = a["weekly_pnl"]
    week_cls = "pos" if week_pnl > 0 else "neg" if week_pnl < 0 else "zero"

    daily_loss_pct = abs(min(pnl, 0)) / a["starting_balance"] if a["starting_balance"] else 0
    daily_cap = float(mode_rules.max_daily_loss_halt_pct)
    daily_used = min(daily_loss_pct / daily_cap, 1.0) if daily_cap else 0

    dd_cap = float(mode_rules.max_total_drawdown_stop_pct)
    dd_used = min(a["drawdown_pct"] / dd_cap, 1.0) if dd_cap else 0

    def bar(used):
        col = POS if used < 0.5 else WARN if used < 0.8 else NEG
        return col

    return f"""
    <tr>
        <td>
            <span class="dot {a["status"]}"></span>
            <span class="strat">{a["strategy"]}</span><br>
            <span class="firm">{a["firm"]}</span>
        </td>
        <td><span class="modetag {a["mode"]}">{a["mode"]}</span></td>
        <td class="num">${a["balance"]:,.0f}</td>
        <td class="num {pnl_cls}">{pnl:+,.2f}</td>
        <td class="num {week_cls}">{week_pnl:+,.2f}</td>
        <td class="num">
            <span class="riskbar"><span class="fill" style="width:{daily_used*100:.0f}%; background:{bar(daily_used)};"></span></span>
            <span style="color:{TEXT_DIM};">{daily_loss_pct:.2%}</span>
        </td>
        <td class="num">
            <span class="riskbar"><span class="fill" style="width:{dd_used*100:.0f}%; background:{bar(dd_used)};"></span></span>
            <span style="color:{TEXT_DIM};">{a["drawdown_pct"]:.2%}</span>
        </td>
    </tr>
    """

rows_html = "".join(account_row(a) for a in accounts) if accounts else ""
if accounts:
    st.markdown(
        f"""
        <table class="acct-table">
            <thead>
                <tr>
                    <th>Strategy</th>
                    <th>Mode</th>
                    <th class="num">NAV</th>
                    <th class="num">Day P&amp;L</th>
                    <th class="num">Week P&amp;L</th>
                    <th class="num">Daily Loss · Cap</th>
                    <th class="num">Drawdown · Cap</th>
                </tr>
            </thead>
            <tbody>{rows_html}</tbody>
        </table>
        """,
        unsafe_allow_html=True,
    )
else:
    st.markdown(
        '<div class="empty">No broker credentials configured yet. '
        'Add <code>ALPACA_API_KEY</code>, <code>OANDA_API_TOKEN</code>, or '
        '<code>TRADOVATE_*</code> to <code>.env</code> and refresh.</div>',
        unsafe_allow_html=True,
    )

if dormant:
    firms = sorted({a["firm"] for a in dormant})
    st.markdown(
        f'<div style="color:{TEXT_MUTED}; font-family:Inter; font-size:0.72rem; '
        f'letter-spacing:0.08em; margin-top:10px; text-align:right;">'
        f"{len(dormant)} dormant account{'s' if len(dormant) != 1 else ''} hidden · "
        f"add credentials for {', '.join(firms)} to activate"
        "</div>",
        unsafe_allow_html=True,
    )

# --- strategy heartbeats ---
def _sleep_seconds(s: str) -> int:
    """Convert sleeptime string like '1M', '15M', '1D' to seconds."""
    s = (s or "").strip().upper()
    if not s:
        return 300
    unit = s[-1]
    try:
        n = int(s[:-1]) if len(s) > 1 else 1
    except ValueError:
        return 300
    return {"S": 1, "M": 60, "H": 3600, "D": 86400}.get(unit, 60) * n


def _format_age(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s ago"
    if seconds < 3600:
        return f"{int(seconds/60)}m ago"
    if seconds < 86400:
        return f"{seconds/3600:.1f}h ago"
    return f"{seconds/86400:.1f}d ago"


hb_df = _heartbeats()
if not hb_df.empty or accounts:
    st.markdown("## Strategy heartbeats")
    st.markdown(
        f'<div style="color:{TEXT_MUTED}; font-family:Inter; font-size:0.76rem; margin-bottom:10px;">'
        "Last iteration timestamp per strategy. Green = ticked within 2 × its sleeptime. "
        "Amber = 2–5 ×. Red = stuck or dead. "
        "NYSE strategies legitimately stop ticking outside market hours — amber/red is only alarming during open hours."
        "</div>",
        unsafe_allow_html=True,
    )

    # Merge with accounts to show expected cadence even for strategies that
    # haven't ticked yet (no DB row).
    by_name = {h["strategy"]: h for _, h in hb_df.iterrows()} if not hb_df.empty else {}
    now_utc = datetime.now(timezone.utc)
    cols = st.columns(max(len(accounts), 1))
    for col, a in zip(cols, accounts):
        name = a["strategy"]
        hb = by_name.get(name)
        if hb is None:
            age_s = None
            age_txt = "never"
            status_cls = "dot-red"
            decision = "no heartbeat yet"
            iters = "0"
            sleeptime = "?"
        else:
            last = hb["last_tick_at"]
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            age_s = (now_utc - last).total_seconds()
            age_txt = _format_age(age_s)
            sleeptime = hb["sleeptime"] or "?"
            expected = _sleep_seconds(sleeptime)
            if age_s < 2 * expected:
                status_cls = "dot-green"
            elif age_s < 5 * expected:
                status_cls = "dot-amber"
            else:
                status_cls = "dot-red"
            decision = (hb["last_decision"] or "")[:60]
            iters = f"{hb['iter_today']} today · {hb['iter_total']} total"

        dot_color = {"dot-green": POS, "dot-amber": WARN, "dot-red": NEG}[status_cls]
        with col:
            col.markdown(
                f"""
                <div class="stat" style="height: 100%;">
                    <div style="display:flex; align-items:center; gap:8px; margin-bottom:6px;">
                        <span style="display:inline-block; width:9px; height:9px; border-radius:50%;
                                     background:{dot_color}; box-shadow: 0 0 8px {dot_color}88;
                                     animation: {'pulse 2.5s ease-in-out infinite' if status_cls == 'dot-green' else 'none'};"></span>
                        <span class="lbl" style="margin-bottom:0;">{name}</span>
                    </div>
                    <div class="big" style="font-size:1.15rem;">{age_txt}</div>
                    <div class="sub" style="color:{TEXT_DIM}; font-size:0.7rem; margin-top:6px;
                                            white-space:nowrap; overflow:hidden; text-overflow:ellipsis;"
                         title="{decision}">
                        {decision if decision else "&nbsp;"}
                    </div>
                    <div style="color:{TEXT_MUTED}; font-size:0.66rem; font-family:Inter; margin-top:6px;">
                        cadence {sleeptime} · {iters}
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )


# --- pool exposure per broker ---
if accounts:
    st.markdown("## Broker-pool exposure")
    st.markdown(
        f'<div style="color:{TEXT_MUTED}; font-family:Inter; font-size:0.76rem; margin-bottom:10px;">'
        "Combined risk + notional across strategies sharing one real broker account. "
        "Risk engine uses this to cap aggregate exposure — shrinks position size if the real "
        "account can't take it.</div>",
        unsafe_allow_html=True,
    )

    firms = sorted({a["firm"] for a in accounts})
    pool_cols = st.columns(len(firms))
    for col, firm in zip(pool_cols, firms):
        snap = BrokerPool(firm).snapshot()
        real_eq = f"${snap.real_equity:,.0f}" if snap.real_equity is not None else "—"
        real_bp = f"${snap.real_buying_power:,.0f}" if snap.real_buying_power is not None else "—"
        risk = f"${snap.committed_risk:,.2f}"
        notional = f"${snap.open_notional:,.2f}"
        # Risk utilization (committed / real equity)
        util = (
            float(snap.committed_risk / snap.real_equity)
            if snap.real_equity and snap.real_equity > 0
            else 0
        )
        util_cls = "pos" if util < 0.02 else "neg" if util > 0.04 else ""
        with col:
            col.markdown(
                f"""
                <div class="stat">
                    <div class="lbl">{firm} · {snap.member_count} strategies</div>
                    <div class="big">{real_eq}</div>
                    <div class="sub">buying power {real_bp}</div>
                    <div style="margin-top:10px; display:grid; grid-template-columns:1fr 1fr; gap:8px; font-family:'JetBrains Mono', monospace; font-size:0.74rem;">
                        <div>
                            <div style="color:{TEXT_MUTED}; font-size:0.6rem; letter-spacing:0.12em; text-transform:uppercase; font-family:Inter; font-weight:700;">Committed Risk</div>
                            <div style="color:{TEXT};" class="{util_cls}">{risk}</div>
                        </div>
                        <div>
                            <div style="color:{TEXT_MUTED}; font-size:0.6rem; letter-spacing:0.12em; text-transform:uppercase; font-family:Inter; font-weight:700;">Open Notional</div>
                            <div style="color:{TEXT};">{notional}</div>
                        </div>
                    </div>
                    <div style="margin-top:10px; color:{TEXT_MUTED}; font-size:0.68rem; font-family:Inter;">
                        {snap.open_trades} open position{'s' if snap.open_trades != 1 else ''}
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

# --- tabs ---
tab_equity, tab_attr, tab_bt, tab_news, tab_trades, tab_cull = st.tabs(
    ["Equity", "Attribution", "Backtests", "News", "Trades", "Culling"]
)

def empty_msg(cmd: str, hint: str) -> None:
    st.markdown(
        f'<div class="empty">{hint}<br><br>'
        f'Run <code>{cmd}</code> to populate.</div>',
        unsafe_allow_html=True,
    )

with tab_equity:
    st.markdown("## Equity by strategy")
    st.plotly_chart(strategy_equity(trades, accounts), width="stretch", config={"displayModeBar": False})
    st.markdown("## Underwater drawdown")
    st.plotly_chart(drawdown_chart(trades), width="stretch", config={"displayModeBar": False})

    st.markdown("## Rolling snapshot")
    window_opts = [("ALL TIME", 0), ("90D", 90), ("30D", 30)]
    window_label = st.selectbox("Window", window_opts, format_func=lambda x: x[0], label_visibility="collapsed")
    perf = _performance(window_days=window_label[1])
    if perf.empty:
        empty_msg("make nightly", "Per-strategy Sharpe, Sortino, profit factor, expectancy and max-DD populate after the first closed trades.")
    else:
        st.dataframe(perf, width="stretch", hide_index=True)

with tab_attr:
    st.markdown("## Where the P&L comes from")
    strategies = sorted({a["strategy"] for a in accounts})
    strat = st.selectbox("Strategy", strategies, label_visibility="collapsed")
    col_l, col_r = st.columns([2, 1])
    with col_l:
        st.markdown(f'<div style="color:{TEXT_DIM}; font-size:0.66rem; letter-spacing:0.18em; text-transform:uppercase; font-weight:700; margin-bottom:6px;">Hour × Day-of-week · avg P&amp;L</div>', unsafe_allow_html=True)
        st.plotly_chart(heatmap_by_hour_dow(strat), width="stretch", config={"displayModeBar": False})
    with col_r:
        st.markdown(f'<div style="color:{TEXT_DIM}; font-size:0.66rem; letter-spacing:0.18em; text-transform:uppercase; font-weight:700; margin-bottom:6px;">By market regime</div>', unsafe_allow_html=True)
        regime = attribute_by_regime(strat, window_days=90)
        if regime.empty:
            empty_msg("python run/run_rsi2_spy.py", "Regime-tagged trades appear after live runs.")
        else:
            fig = px.bar(
                regime.reset_index(), x="market_regime", y="total_pnl",
                color="total_pnl",
                color_continuous_scale=[[0, NEG], [0.5, PANEL], [1, POS]],
            )
            fig.update_layout(**_chart_layout(280))
            fig.update_traces(marker_line_width=0)
            fig.update_layout(coloraxis_showscale=False, xaxis_title="", yaxis_title="", yaxis_tickprefix="$")
            st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})

with tab_bt:
    st.markdown("## Backtest history")
    if runs.empty:
        empty_msg("make backtest-rsi2", "Historical backtest runs with Sharpe, return, max DD and trade count.")
    else:
        disp = runs.copy()
        disp["budget"] = disp["budget"].map(lambda x: f"${x:,.0f}")
        disp["return"] = disp["return"].map(lambda x: f"{x:+.2%}" if pd.notna(x) else "—")
        disp["sharpe"] = disp["sharpe"].map(lambda x: f"{x:+.2f}" if pd.notna(x) else "—")
        disp["max_dd"] = disp["max_dd"].map(lambda x: f"{x:.2%}" if pd.notna(x) else "—")
        disp["run_at"] = disp["run_at"].map(lambda d: d.strftime("%Y-%m-%d %H:%M") if pd.notna(d) else "")
        st.dataframe(disp, width="stretch", hide_index=True)

        latest_ret = runs.dropna(subset=["return"]).head(10)
        if not latest_ret.empty:
            fig = px.bar(
                latest_ret, x="strategy", y="return",
                color="return",
                color_continuous_scale=[[0, NEG], [0.5, PANEL], [1, POS]],
                hover_data=["start", "end", "trades", "sharpe", "max_dd"],
            )
            fig.update_layout(**_chart_layout(280))
            fig.update_traces(marker_line_width=0)
            fig.update_layout(coloraxis_showscale=False, xaxis_title="", yaxis_title="", yaxis_tickformat=".1%")
            st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})

with tab_news:
    st.markdown("## Upcoming HIGH-impact events")
    if news.empty:
        empty_msg("make news", "ForexFactory HIGH-impact economic events pull into the risk engine blackout schedule.")
    else:
        rows = ""
        for _, n in news.iterrows():
            local = n["starts_at"].tz_convert("America/New_York")
            rows += (
                f'<div class="news-row">'
                f'<span class="ccy">{n["currency"]}</span>'
                f'<span class="impact">{n["impact"]}</span>'
                f'<span class="event">{n["event"]}</span>'
                f'<span class="time">{local.strftime("%a %b %d · %H:%M ET")}</span>'
                f'</div>'
            )
        st.markdown(f'<div style="background:var(--panel); border:1px solid var(--border); border-radius:10px; overflow:hidden;">{rows}</div>', unsafe_allow_html=True)

with tab_trades:
    st.markdown("## Recent trades")
    if trades.empty:
        empty_msg("python run/run_rsi2_spy.py", "Every trade gets logged here with regime, hour, direction, P&L, exit reason.")
    else:
        disp = trades.copy()
        if "pnl" in disp:
            disp["pnl"] = disp["pnl"].map(lambda x: f"${x:+,.2f}" if pd.notna(x) else "—")
        if "pnl_pct" in disp:
            disp["pnl_pct"] = disp["pnl_pct"].map(lambda x: f"{x:+.2%}" if pd.notna(x) else "—")
        st.dataframe(disp, width="stretch", height=480, hide_index=True)

with tab_cull:
    st.markdown("## §9 culling framework")
    st.markdown(
        f'<div style="color:{TEXT_DIM}; font-family:Inter; font-size:0.82rem; padding:0 0 12px 0;">'
        "Month 3 — kill on DD > 8%, flag on Sharpe < 0.5. "
        "Promote on Sharpe > 1 + win rate > 55% + DD < 5% + ≥20 trades. "
        "Verdicts are advisory.</div>",
        unsafe_allow_html=True,
    )
    all_time = _performance(window_days=0)
    if all_time.empty:
        empty_msg("make nightly", "Culling verdicts appear after the nightly analysis job runs against live trades.")
    else:
        from trading_bot.learning.performance import PerformanceMetrics
        rows = []
        for _, r in all_time.iterrows():
            m = PerformanceMetrics(
                strategy_name=r["strategy"], firm=r["firm"], window_days=0,
                trade_count=int(r["trades"]), win_rate=r["win_rate"],
                avg_winner=None, avg_loser=None,
                profit_factor=r["profit_factor"], expectancy=r["expectancy"],
                sharpe=r["sharpe"], sortino=r["sortino"],
                max_drawdown_pct=r["max_dd"], recovery_factor=None,
                best_day_pnl=r["best_day"], worst_day_pnl=r["worst_day"],
            )
            m3 = month_3_decision(m)
            promo = promotion_decision(m)
            rows.append({
                "strategy": m.strategy_name,
                "month 3": m3.verdict.value,
                "month 3 reason": m3.reason,
                "promotion": promo.verdict.value,
                "promotion reason": promo.reason,
            })
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
