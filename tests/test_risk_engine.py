from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from trading_bot.brokers.base_types import OrderSide
from trading_bot.db.models import Account, AccountMode, AccountStatus
from trading_bot.risk import RiskEngine, TradeIntent


def _account(mode: AccountMode = AccountMode.CHALLENGE, **overrides) -> Account:
    base = dict(
        firm="MyFundedFutures",
        strategy_name="TEST",
        account_size=Decimal("100000"),
        starting_balance=Decimal("100000"),
        current_balance=Decimal("100000"),
        peak_balance=Decimal("100000"),
        current_drawdown_pct=Decimal("0"),
        daily_pnl=Decimal("0"),
        weekly_pnl=Decimal("0"),
        monthly_pnl=Decimal("0"),
        mode=mode,
        status=AccountStatus.ACTIVE,
    )
    base.update(overrides)
    return Account(**base)


def _intent(account: Account, entry: Decimal, stop: Decimal, qty: Decimal = Decimal("1")) -> TradeIntent:
    return TradeIntent(
        account=account,
        strategy_name="TEST",
        asset="ES",
        side=OrderSide.BUY,
        quantity=qty,
        entry_price=entry,
        stop_loss=stop,
        take_profit=None,
        now=datetime.now(timezone.utc),
    )


def test_approves_trade_within_risk_budget():
    acct = _account(mode=AccountMode.CHALLENGE)  # 0.75% = $750 risk budget
    intent = _intent(acct, entry=Decimal("4500"), stop=Decimal("4495"), qty=Decimal("1"))
    decision = RiskEngine().evaluate(intent)
    assert decision.approved


def test_rejects_when_per_trade_risk_exceeds_budget():
    acct = _account(mode=AccountMode.CHALLENGE)
    intent = _intent(acct, entry=Decimal("4500"), stop=Decimal("4400"), qty=Decimal("10"))
    decision = RiskEngine().evaluate(intent)
    assert not decision.approved
    assert "per-trade risk" in decision.reason


def test_halts_on_daily_loss_halt_threshold():
    acct = _account(mode=AccountMode.CHALLENGE, daily_pnl=Decimal("-3500"))  # 3.5% loss
    intent = _intent(acct, entry=Decimal("4500"), stop=Decimal("4499"))
    decision = RiskEngine().evaluate(intent)
    assert not decision.approved
    assert decision.halt_account
    assert not decision.hard_stop


def test_hard_stops_on_daily_loss_hard_threshold():
    acct = _account(mode=AccountMode.CHALLENGE, daily_pnl=Decimal("-4500"))  # 4.5% loss
    intent = _intent(acct, entry=Decimal("4500"), stop=Decimal("4499"))
    decision = RiskEngine().evaluate(intent)
    assert not decision.approved
    assert decision.halt_account
    assert decision.hard_stop


def test_funded_mode_uses_tighter_daily_limits():
    acct = _account(mode=AccountMode.FUNDED, daily_pnl=Decimal("-2500"))
    intent = _intent(acct, entry=Decimal("4500"), stop=Decimal("4499.9"))
    decision = RiskEngine().evaluate(intent)
    assert not decision.approved
    assert decision.halt_account


def test_halted_account_rejects_all_trades():
    acct = _account(status=AccountStatus.HALTED)
    intent = _intent(acct, entry=Decimal("4500"), stop=Decimal("4499"))
    decision = RiskEngine().evaluate(intent)
    assert not decision.approved
    assert "HALTED" in decision.reason
