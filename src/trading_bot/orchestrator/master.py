from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from trading_bot.brokers.base import Broker
from trading_bot.db.models import Account
from trading_bot.notifications import NotificationDispatcher, Severity
from trading_bot.risk import RiskEngine, TradeIntent
from trading_bot.strategies.base import Strategy

log = logging.getLogger(__name__)


@dataclass(slots=True)
class StrategyBinding:
    """One of the 6 strategy/account/broker triples."""

    strategy: Strategy
    account: Account
    broker: Broker


class MasterOrchestrator:
    """Coordinates all 6 strategy bindings.

    Invariants:
      * Every order goes through the risk engine. No exceptions.
      * Strategies do not talk to brokers directly — they emit signals.
      * Cross-account state (news windows, hedging checks, request counts)
        lives here; per-strategy state lives in the strategy instance.
    """

    def __init__(self, bindings: list[StrategyBinding]) -> None:
        self._bindings = bindings
        self._risk = RiskEngine()
        self._notify = NotificationDispatcher()

    async def tick(self, binding: StrategyBinding, now: datetime) -> None:
        """Invoked by the scheduler at each strategy's cadence."""
        signal = await binding.strategy.check(now)
        if signal is None:
            return

        intent = TradeIntent(
            account=binding.account,
            strategy_name=binding.strategy.name,
            asset=signal.asset,
            side=signal.side,
            quantity=signal.quantity,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            now=now,
        )
        decision = self._risk.evaluate(intent)
        if not decision.approved:
            log.info("risk rejected %s: %s", binding.strategy.name, decision.reason)
            if decision.halt_account:
                await self._notify.send(
                    Severity.CRITICAL if decision.hard_stop else Severity.WARN,
                    f"{binding.account.firm}/{binding.strategy.name} halted",
                    decision.reason,
                )
            return

        # Phase 1 stops here — Phase 2 wires submit_order + TradeLogger.
        log.info(
            "would submit %s %s %s @ %s (stop %s)",
            binding.strategy.name,
            signal.side.value,
            signal.quantity,
            signal.entry_price,
            signal.stop_loss,
        )

    @property
    def bindings(self) -> list[StrategyBinding]:
        return self._bindings
