"""Sync Lumibot broker state into the ``accounts`` table.

The risk engine reads Account fields (current_balance, daily_pnl,
current_drawdown_pct, peak_balance) every time it evaluates a trade. If
those fields sit at their seed values, the engine is toothless — it
approves every trade because "daily_pnl == 0" always looks safe.

This module pulls the authoritative numbers from Lumibot's broker
adapter on startup and after every fill, and writes them to the row the
engine reads. One call per fill keeps the risk gate honest.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select

from trading_bot.db.models import Account, Trade
from trading_bot.db.session import get_session

log = logging.getLogger(__name__)


class AccountSync:
    """Reads live broker balances + today's realised P&L into the accounts row."""

    def __init__(self, account_id: int, firm: str, strategy_name: str) -> None:
        self.account_id = account_id
        self.firm = firm
        self.strategy_name = strategy_name

    def refresh(self, *, portfolio_value: float, cash: float) -> Account:
        """Call on startup and on every filled order. Returns a detached snapshot."""
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        with get_session() as s:
            acct: Account | None = s.get(Account, self.account_id)
            if acct is None:
                raise RuntimeError(f"Account {self.account_id} not in DB")

            new_balance = Decimal(str(portfolio_value))
            acct.current_balance = new_balance
            if new_balance > acct.peak_balance:
                acct.peak_balance = new_balance

            if acct.peak_balance > 0:
                dd = (acct.peak_balance - new_balance) / acct.peak_balance
                acct.current_drawdown_pct = dd.quantize(Decimal("0.0001"))

            daily_pnl = s.execute(
                select(func.coalesce(func.sum(Trade.pnl), 0)).where(
                    Trade.account_id == self.account_id,
                    Trade.exit_time >= today_start,
                    Trade.pnl.is_not(None),
                )
            ).scalar_one()
            acct.daily_pnl = Decimal(str(daily_pnl))

            s.flush()
            s.refresh(acct)
            s.expunge(acct)
            return acct
