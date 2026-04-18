from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from trading_bot.brokers.base_types import OrderSide
from trading_bot.db.models import Account, AccountMode, AccountStatus
from trading_bot.risk import RiskEngine, TradeIntent

# Fixed Tuesday 2026-04-07 17:00 UTC = 12:00 ET = 11:00 CT — mid-session,
# well before any firm's EOD flat time, so firm-rule time checks don't
# interfere with pure mode/risk assertions.
_NOW = datetime(2026, 4, 7, 17, 0, tzinfo=timezone.utc)


def _account(
    mode: AccountMode = AccountMode.CHALLENGE,
    firm: str = "Alpaca_Paper",  # no firm-rule time-of-day restrictions
    **overrides,
) -> Account:
    base = dict(
        firm=firm,
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
        now=_NOW,
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


def test_mff_blocks_after_eod_flat():
    # 21:30 UTC on a weekday = 16:30 CT — past MyFundedFutures 15:59 CT flat.
    acct = _account(firm="MyFundedFutures")
    after_eod = datetime(2026, 4, 7, 21, 30, tzinfo=timezone.utc)
    intent = TradeIntent(
        account=acct,
        strategy_name="TEST",
        asset="ES",
        side=OrderSide.BUY,
        quantity=Decimal("1"),
        entry_price=Decimal("4500"),
        stop_loss=Decimal("4499"),
        take_profit=None,
        now=after_eod,
    )
    decision = RiskEngine().evaluate(intent)
    assert not decision.approved
    assert "EOD" in decision.reason


def test_weekend_flat_blocks_saturday():
    acct = _account(firm="Alpaca_Paper")  # weekend_flat default True
    sat = datetime(2026, 4, 11, 15, 0, tzinfo=timezone.utc)  # Saturday
    intent = TradeIntent(
        account=acct,
        strategy_name="TEST",
        asset="SPY",
        side=OrderSide.BUY,
        quantity=Decimal("1"),
        entry_price=Decimal("500"),
        stop_loss=Decimal("499"),
        take_profit=None,
        now=sat,
    )
    decision = RiskEngine().evaluate(intent)
    assert not decision.approved
    assert "weekend" in decision.reason.lower()


def test_friday_late_blocks_new_entries():
    acct = _account(firm="Alpaca_Paper")
    # Friday 20:00 UTC = 16:00 ET, past the 15:55 flat time.
    fri_late = datetime(2026, 4, 10, 20, 0, tzinfo=timezone.utc)
    intent = TradeIntent(
        account=acct,
        strategy_name="TEST",
        asset="SPY",
        side=OrderSide.BUY,
        quantity=Decimal("1"),
        entry_price=Decimal("500"),
        stop_loss=Decimal("499"),
        take_profit=None,
        now=fri_late,
    )
    decision = RiskEngine().evaluate(intent)
    assert not decision.approved
