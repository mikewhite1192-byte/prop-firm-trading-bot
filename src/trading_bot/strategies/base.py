"""RiskGatedStrategy — Lumibot Strategy subclass with mandatory prop-firm
risk-engine gating on every order.

Design constraints:
  * Lumibot raises NotImplementedError on >1 live strategy per Trader, so each
    strategy runs in its own process. Risk state that must be shared across
    processes (daily P&L, consistency tracking, news blackouts, cross-account
    hedging) lives in Postgres — see ``trading_bot.shared_state``.
  * The clean interception point is ``Strategy.submit_order`` (not the broker),
    per the Lumibot deep-dive research. One gate across every broker.
  * Strategies never build Lumibot orders directly — they call
    ``self.propose_entry(...)`` which bundles the risk intent with the order
    so the engine can evaluate the trade *as the strategy intended it*
    (including the intended stop, which a bare market order wouldn't carry).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal

from lumibot.entities import Asset, Order
from lumibot.strategies.strategy import Strategy
from sqlalchemy import select

from trading_bot.brokers.base_types import OrderSide
from trading_bot.db.models import Account, TradeMode
from trading_bot.db.session import get_session
from trading_bot.notifications import NotificationDispatcher, Severity
from trading_bot.risk import RiskDecision, RiskEngine, TradeIntent

log = logging.getLogger(__name__)


class RiskGatedStrategy(Strategy):
    """Base class for all 6 prop-firm strategies.

    Subclasses set the class attrs below and implement
    :meth:`on_trading_iteration`. When they want to enter a trade they call
    :meth:`propose_entry`, which consults :class:`RiskEngine` before any order
    leaves the process.
    """

    #: Firm identifier in the ``accounts`` table (e.g. "Alpaca_Paper").
    firm: str = ""
    #: Unique strategy name; matches ``accounts.strategy_name``.
    strategy_name: str = ""

    _risk_engine = RiskEngine()

    def initialize(self, parameters: dict | None = None) -> None:
        self._notifier = NotificationDispatcher()
        self._account_id = self._resolve_account_id()
        self.log_message(
            f"{self.strategy_name} up | firm={self.firm} account_id={self._account_id}",
        )

    def _resolve_account_id(self) -> int:
        with get_session() as s:
            acct = s.execute(
                select(Account).where(
                    Account.firm == self.firm,
                    Account.strategy_name == self.strategy_name,
                )
            ).scalar_one_or_none()
            if acct is None:
                raise RuntimeError(
                    f"No account row for firm={self.firm} "
                    f"strategy={self.strategy_name}. Run scripts/init_db.py first."
                )
            return acct.id

    def _load_account(self) -> Account:
        """Fetch a fresh Account snapshot for the risk engine."""
        with get_session() as s:
            acct = s.get(Account, self._account_id)
            if acct is None:
                raise RuntimeError(f"Account id={self._account_id} vanished from DB")
            s.expunge(acct)
            return acct

    def propose_entry(
        self,
        *,
        asset: Asset | str,
        side: OrderSide,
        quantity: Decimal,
        entry_price: Decimal,
        stop_loss: Decimal,
        take_profit: Decimal | None = None,
        order_type: str = Order.OrderType.MARKET,
        limit_price: Decimal | None = None,
        reason: str = "",
    ) -> Order | None:
        """Gate an intended entry through the risk engine, then submit if approved.

        Returns the Lumibot :class:`Order` if submitted, else ``None`` when the
        engine rejected the trade. A rejection with ``halt_account=True`` also
        marks the DB account status and emits a notification.
        """
        asset_obj = asset if isinstance(asset, Asset) else Asset(symbol=asset, asset_type="stock")
        account = self._load_account()

        intent = TradeIntent(
            account=account,
            strategy_name=self.strategy_name,
            asset=asset_obj.symbol,
            side=side,
            quantity=quantity,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            now=datetime.now(timezone.utc),
        )
        decision = self._risk_engine.evaluate(intent)
        if not decision.approved:
            self._handle_rejection(intent, decision)
            return None

        order = self.create_order(
            asset_obj,
            float(decision.adjusted_quantity or quantity),
            side.value.lower(),
            order_type=order_type,
            limit_price=float(limit_price) if limit_price is not None else None,
            stop_loss_price=float(stop_loss),
            take_profit_price=float(take_profit) if take_profit is not None else None,
            order_class=Order.OrderClass.BRACKET if take_profit is not None else Order.OrderClass.OTO,
        )
        submitted = self.submit_order(order)
        self.log_message(
            f"SUBMIT {self.strategy_name} {side.value} {quantity} {asset_obj.symbol} "
            f"@ {entry_price} stop={stop_loss} tp={take_profit} — {reason}",
            color="green",
        )
        return submitted

    def _handle_rejection(self, intent: TradeIntent, decision: RiskDecision) -> None:
        self.log_message(
            f"RISK REJECT {self.strategy_name}: {decision.reason}",
            color="red",
        )
        if decision.halt_account:
            self._mark_account_halted(hard_stop=decision.hard_stop)
            severity = Severity.CRITICAL if decision.hard_stop else Severity.WARN
            self._notifier.send(
                severity,
                f"{self.firm}/{self.strategy_name} halted",
                decision.reason,
            )

        if decision.hard_stop:
            # Defensive: flatten everything this strategy holds on a hard stop.
            self.sell_all(cancel_open_orders=True)

    def _mark_account_halted(self, *, hard_stop: bool) -> None:
        from trading_bot.db.models import AccountStatus

        with get_session() as s:
            acct = s.get(Account, self._account_id)
            if acct is None:
                return
            acct.status = AccountStatus.BLOWN if hard_stop else AccountStatus.HALTED

    @property
    def trade_mode(self) -> TradeMode:
        account = self._load_account()
        return TradeMode(account.mode.value)
