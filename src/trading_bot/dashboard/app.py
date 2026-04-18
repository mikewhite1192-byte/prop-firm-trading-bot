from __future__ import annotations

from decimal import Decimal

import pandas as pd
import streamlit as st
from sqlalchemy import select

from trading_bot.db.models import Account, Trade
from trading_bot.db.session import get_session

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
                "exit_reason": t.exit_reason.value if t.exit_reason else None,
            }
            for t in rows
        ]
    )


st.set_page_config(page_title="Trading Bot", layout="wide")
st.title("Prop Firm Trading — live monitor")

st.subheader("Accounts")
st.dataframe(_accounts_df(), width="stretch")

st.subheader("Recent trades")
st.dataframe(_recent_trades_df(), width="stretch")
