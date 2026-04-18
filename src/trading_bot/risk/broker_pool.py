"""Broker-pool risk aggregation.

Motivation
----------
Multiple strategies can share one real broker account (Alpaca paper today —
three strategies on one $100k account). Each strategy's per-trade risk
engine checks its *own* nominal $100k book, but the broker only has one
real balance. Without a shared view, three strategies can each think they
have $750 of risk budget and collectively try to deploy $2,250 worth of
loss exposure against one real account.

``BrokerPool`` answers "what's the pool's current state?" — live equity,
live buying power, plus the sum of risk and notional committed by all
strategies that share this broker's credentials. The risk engine consults
it before approving a new entry and shrinks or rejects when the aggregate
would exceed the real broker's capacity.

Design
------
* A pool is identified by ``firm`` (the DB column on ``Account``).
* ``fetch_equity`` / ``fetch_buying_power`` hit the broker's real API via
  plug-in balance fetchers registered per-firm. In a backtest they return
  ``None`` — the pool then gracefully no-ops.
* Committed risk = sum over open trades on the pool of
  ``|entry_price - stop_loss| * quantity``. Open trades are rows where
  ``pnl IS NULL`` and ``exit_time IS NULL``.
* Open notional = sum over open trades of ``|quantity * entry_price|``.

In production each strategy has its own real prop-firm account, so a pool
usually has one member — the check degenerates to the per-strategy check
and behaviour is unchanged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable

from sqlalchemy import and_, select

from trading_bot.db.models import Account, Trade
from trading_bot.db.session import get_session

log = logging.getLogger(__name__)


BalanceFetcher = Callable[[], dict | None]

# Per-firm live-balance sources. Each fetcher returns a dict with at minimum
# {"equity": float, "buying_power": float} or None if unavailable.
_balance_fetchers: dict[str, BalanceFetcher] = {}


def register_balance_fetcher(firm: str, fetcher: BalanceFetcher) -> None:
    """Wire a firm to its live-balance API. Called once at startup per broker."""
    _balance_fetchers[firm] = fetcher


def get_balance_fetcher(firm: str) -> BalanceFetcher | None:
    return _balance_fetchers.get(firm)


@dataclass(slots=True)
class PoolSnapshot:
    firm: str
    member_count: int
    nominal_budget: Decimal  # sum of member starting_balance — sizing convention
    real_equity: Decimal | None  # live from broker API
    real_buying_power: Decimal | None
    committed_risk: Decimal  # sum (entry-stop) * qty over open trades
    open_notional: Decimal   # sum |qty * entry| over open trades
    open_trades: int

    @property
    def available_risk_budget(self) -> Decimal | None:
        """How much new per-trade risk the pool can still take, given the
        real equity. ``None`` when real balance is unknown (backtest / fetcher
        offline) — the pool check degrades to a no-op in that case."""
        return self.real_equity - self.committed_risk if self.real_equity is not None else None

    @property
    def available_buying_power(self) -> Decimal | None:
        if self.real_buying_power is None:
            return None
        return max(self.real_buying_power - self.open_notional, Decimal("0"))


class BrokerPool:
    """Aggregate view across all strategies sharing one broker's credentials."""

    def __init__(
        self,
        firm: str,
        session_factory: Callable | None = None,
        balance_fetcher: BalanceFetcher | None = None,
    ) -> None:
        self.firm = firm
        self._session_factory = session_factory or get_session
        self._balance_fetcher = balance_fetcher or get_balance_fetcher(firm)

    def snapshot(self, *, exclude_account_id: int | None = None) -> PoolSnapshot:
        members = self._members()
        real_equity = None
        real_buying_power = None
        if self._balance_fetcher is not None:
            try:
                data = self._balance_fetcher()
                if data:
                    real_equity = Decimal(str(data.get("equity", 0))) if data.get("equity") else None
                    real_buying_power = (
                        Decimal(str(data.get("buying_power", 0)))
                        if data.get("buying_power")
                        else None
                    )
            except Exception as e:
                log.warning("broker-pool balance fetcher for %s failed: %s", self.firm, e)

        risk = Decimal("0")
        notional = Decimal("0")
        open_count = 0
        with self._session_factory() as s:
            member_ids = [m.id for m in members if m.id != exclude_account_id]
            if member_ids:
                rows = (
                    s.execute(
                        select(Trade).where(
                            and_(
                                Trade.account_id.in_(member_ids),
                                Trade.pnl.is_(None),
                                Trade.exit_time.is_(None),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                for t in rows:
                    entry = t.entry_price or Decimal("0")
                    stop = t.stop_loss if t.stop_loss is not None else entry
                    qty = t.quantity or Decimal("0")
                    risk += abs(entry - stop) * qty
                    notional += abs(qty * entry)
                    open_count += 1

        nominal = sum((m.starting_balance for m in members), start=Decimal("0"))
        return PoolSnapshot(
            firm=self.firm,
            member_count=len(members),
            nominal_budget=nominal,
            real_equity=real_equity,
            real_buying_power=real_buying_power,
            committed_risk=risk,
            open_notional=notional,
            open_trades=open_count,
        )

    def _members(self) -> list[Account]:
        with self._session_factory() as s:
            rows = (
                s.execute(select(Account).where(Account.firm == self.firm))
                .scalars()
                .all()
            )
            for r in rows:
                s.expunge(r)
            return list(rows)
