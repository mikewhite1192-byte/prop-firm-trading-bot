from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from trading_bot.brokers.base import OrderSide
from trading_bot.db.models import Account, AccountMode, AccountStatus
from trading_bot.risk.rules import FIRM_RULES, MODE_RULES, FirmRules, ModeRules


@dataclass(slots=True)
class TradeIntent:
    account: Account
    strategy_name: str
    asset: str
    side: OrderSide
    quantity: Decimal
    entry_price: Decimal
    stop_loss: Decimal
    take_profit: Decimal | None
    now: datetime


@dataclass(slots=True)
class RiskDecision:
    approved: bool
    reason: str = ""
    halt_account: bool = False
    hard_stop: bool = False
    adjusted_quantity: Decimal | None = None


class RiskEngine:
    """Gatekeeper between strategies and brokers.

    A strategy computes an intent and passes it to ``evaluate``. The engine
    is authoritative: if it returns an unapproved decision, the order does
    not go out, full stop. Strategies must not second-guess the engine.

    The engine is deliberately stateless — it reads the current account
    snapshot each call. The orchestrator owns in-memory state (news
    windows, daily request counts) and attaches it to the intent when
    those rules are implemented.
    """

    def evaluate(self, intent: TradeIntent) -> RiskDecision:
        mode_rules = MODE_RULES[intent.account.mode.value]
        firm_rules = FIRM_RULES.get(intent.account.firm, FirmRules())

        if intent.account.status != AccountStatus.ACTIVE:
            return RiskDecision(
                approved=False, reason=f"account status={intent.account.status.value}"
            )

        per_trade_risk = self._per_trade_risk(intent)
        max_risk_dollar = (
            intent.account.current_balance * mode_rules.max_risk_per_trade_pct
        )
        if per_trade_risk > max_risk_dollar:
            return RiskDecision(
                approved=False,
                reason=f"per-trade risk ${per_trade_risk:.2f} exceeds "
                f"max ${max_risk_dollar:.2f} ({mode_rules.max_risk_per_trade_pct:.2%})",
            )

        daily_loss_pct = self._loss_pct(intent.account.daily_pnl, intent.account.starting_balance)
        if daily_loss_pct >= mode_rules.max_daily_loss_hard_pct:
            return RiskDecision(
                approved=False,
                reason=f"daily loss {daily_loss_pct:.2%} >= hard stop "
                f"{mode_rules.max_daily_loss_hard_pct:.2%}",
                halt_account=True,
                hard_stop=True,
            )
        if daily_loss_pct >= mode_rules.max_daily_loss_halt_pct:
            return RiskDecision(
                approved=False,
                reason=f"daily loss {daily_loss_pct:.2%} >= halt "
                f"{mode_rules.max_daily_loss_halt_pct:.2%}",
                halt_account=True,
            )

        dd = intent.account.current_drawdown_pct
        if dd >= mode_rules.max_total_drawdown_stop_pct:
            return RiskDecision(
                approved=False,
                reason=f"total drawdown {dd:.2%} >= stop {mode_rules.max_total_drawdown_stop_pct:.2%}",
                halt_account=True,
                hard_stop=True,
            )

        if (
            intent.account.mode == AccountMode.FUNDED
            and mode_rules.consistency_max_day_pct is not None
        ):
            # Soft check — orchestrator should size down as approaching the limit.
            pass

        if firm_rules.no_cross_account_hedging:
            # Enforcement requires cross-account position state from the orchestrator.
            pass

        return RiskDecision(approved=True, adjusted_quantity=intent.quantity)

    @staticmethod
    def _per_trade_risk(intent: TradeIntent) -> Decimal:
        distance = abs(intent.entry_price - intent.stop_loss)
        return distance * intent.quantity

    @staticmethod
    def _loss_pct(daily_pnl: Decimal, starting_balance: Decimal) -> Decimal:
        if daily_pnl >= 0 or starting_balance == 0:
            return Decimal("0")
        return abs(daily_pnl) / starting_balance
