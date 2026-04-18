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
from trading_bot.db.models import (
    Account,
    Direction,
    ExitReason,
    Trade,
    TradeMode,
)
from trading_bot.db.session import get_session
from trading_bot.notifications import NotificationDispatcher, Severity
from trading_bot.risk import RiskDecision, RiskEngine, TradeIntent
from trading_bot.shared_state import AccountSync, broadcast_halt, register_strategy_trade
from trading_bot.trade_log import TradeLogger

log = logging.getLogger(__name__)

_risk_engine_singleton = RiskEngine(session_factory=get_session)


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

    _risk_engine = _risk_engine_singleton

    def initialize(self, parameters: dict | None = None) -> None:
        self._notifier = NotificationDispatcher()
        self._account_id = self._resolve_account_id()
        self._account_sync = AccountSync(
            account_id=self._account_id,
            firm=self.firm,
            strategy_name=self.strategy_name,
        )
        self._open_trades: dict[str, int] = {}   # broker order id -> trade row id
        self._sync_account_state()
        self.log_message(
            f"{self.strategy_name} up | firm={self.firm} account_id={self._account_id}"
        )

    def before_starting_trading(self) -> None:
        # Fresh balance snapshot at market open.
        self._sync_account_state()

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
        with get_session() as s:
            acct = s.get(Account, self._account_id)
            if acct is None:
                raise RuntimeError(f"Account id={self._account_id} vanished from DB")
            s.expunge(acct)
            return acct

    def _sync_account_state(self) -> Account | None:
        try:
            return self._account_sync.refresh(
                portfolio_value=float(self.portfolio_value),
                cash=float(self.cash) if self.cash is not None else 0.0,
            )
        except Exception as e:  # never let a sync error kill a trading iteration
            log.warning("account sync failed: %s", e)
            return None

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
        asset_obj = (
            asset
            if isinstance(asset, Asset)
            else Asset(symbol=asset, asset_type=Asset.AssetType.STOCK)
        )
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
            order_class=Order.OrderClass.BRACKET
            if take_profit is not None
            else Order.OrderClass.OTO,
        )
        submitted = self.submit_order(order)
        self._record_entry(submitted or order, intent, asset_obj, reason)
        return submitted

    # --- Lumibot lifecycle hooks ---

    def on_filled_order(self, position, order, price, quantity, multiplier):
        identifier = str(getattr(order, "identifier", ""))
        trade_id = self._open_trades.get(identifier)
        if trade_id is not None:
            self._update_trade_fill(trade_id, price, quantity)
        else:
            # Bracket child filled (stop or take-profit) — close the open trade for this asset.
            self._record_exit_for_child_fill(order, price, quantity)

        self._sync_account_state()

    def on_canceled_order(self, order):
        identifier = str(getattr(order, "identifier", ""))
        trade_id = self._open_trades.pop(identifier, None)
        if trade_id is not None:
            log.info("trade %s canceled (broker_order_id=%s)", trade_id, identifier)

    # --- trade record helpers ---

    def _record_entry(
        self,
        order: Order,
        intent: TradeIntent,
        asset: Asset,
        reason: str,
    ) -> None:
        direction = Direction.LONG if intent.side == OrderSide.BUY else Direction.SHORT
        trade_id = TradeLogger.record_entry(
            account_id=self._account_id,
            strategy_name=self.strategy_name,
            asset=asset.symbol,
            direction=direction,
            entry_price=intent.entry_price,
            quantity=intent.quantity,
            entry_time=intent.now,
            stop_loss=intent.stop_loss,
            take_profit=intent.take_profit,
            mode=TradeMode(intent.account.mode.value),
            broker_order_id=str(getattr(order, "identifier", "")) or None,
            notes=reason or None,
        )
        if order is not None and getattr(order, "identifier", None):
            self._open_trades[str(order.identifier)] = trade_id
        self.log_message(
            f"SUBMIT {self.strategy_name} {intent.side.value} {intent.quantity} "
            f"{asset.symbol} @ {intent.entry_price} stop={intent.stop_loss} — {reason}",
            color="green",
        )

    def _update_trade_fill(self, trade_id: int, price: float, quantity: float) -> None:
        with get_session() as s:
            trade = s.get(Trade, trade_id)
            if trade is None:
                return
            trade.entry_price = Decimal(str(price))
            trade.quantity = Decimal(str(quantity))

    def _record_exit_for_child_fill(self, order, price, quantity) -> None:
        """A bracket child (stop or TP) filled; close the matching open trade."""
        symbol = getattr(getattr(order, "asset", None), "symbol", None)
        if symbol is None:
            return
        now = datetime.now(timezone.utc)
        with get_session() as s:
            trade = (
                s.execute(
                    select(Trade)
                    .where(
                        Trade.account_id == self._account_id,
                        Trade.asset == symbol,
                        Trade.exit_price.is_(None),
                    )
                    .order_by(Trade.entry_time.desc())
                    .limit(1)
                )
                .scalars()
                .first()
            )
            if trade is None:
                return
            exit_price = Decimal(str(price))
            qty = trade.quantity
            pnl = (exit_price - trade.entry_price) * qty * (
                Decimal("1") if trade.direction == Direction.LONG else Decimal("-1")
            )
            pnl_pct = (pnl / (trade.entry_price * qty)) if trade.entry_price * qty != 0 else Decimal("0")
            trade.exit_price = exit_price
            trade.exit_time = now
            trade.pnl = pnl
            trade.pnl_pct = pnl_pct.quantize(Decimal("0.0001"))
            trade.exit_reason = _guess_exit_reason(trade, exit_price)

        # Roll into the per-firm daily P&L tally for the consistency rule.
        try:
            register_strategy_trade(
                firm=self.firm,
                strategy_name=self.strategy_name,
                pnl=float(pnl),
                trade_date=now,
            )
        except Exception as e:
            log.warning("register_strategy_trade failed: %s", e)

    # --- rejection handling ---

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
            try:
                broadcast_halt(self.strategy_name, decision.reason)
            except Exception as e:
                log.warning("halt broadcast failed: %s", e)

        if decision.hard_stop:
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


def _guess_exit_reason(trade: Trade, exit_price: Decimal) -> ExitReason:
    """Rough heuristic — tightens later with the broker's actual child-order type."""
    if trade.stop_loss is not None:
        if trade.direction == Direction.LONG and exit_price <= trade.stop_loss:
            return ExitReason.STOP
        if trade.direction == Direction.SHORT and exit_price >= trade.stop_loss:
            return ExitReason.STOP
    if trade.take_profit is not None:
        if trade.direction == Direction.LONG and exit_price >= trade.take_profit:
            return ExitReason.TAKE_PROFIT
        if trade.direction == Direction.SHORT and exit_price <= trade.take_profit:
            return ExitReason.TAKE_PROFIT
    return ExitReason.SIGNAL
