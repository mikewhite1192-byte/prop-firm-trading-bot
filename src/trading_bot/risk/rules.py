from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class ModeRules:
    """Hard limits per operating mode (challenge vs funded)."""

    max_daily_loss_halt_pct: Decimal   # halt trading for the day
    max_daily_loss_hard_pct: Decimal   # hard stop — flatten + lock
    max_total_drawdown_warn_pct: Decimal
    max_total_drawdown_stop_pct: Decimal
    max_risk_per_trade_pct: Decimal
    consistency_max_day_pct: Decimal | None  # funded only
    daily_profit_lock_pct: Decimal | None    # funded only (% of weekly target)
    news_buffer_minutes: int                  # flat before/after FOMC+NFP


# Per spec §4.1
MODE_RULES: dict[str, ModeRules] = {
    "CHALLENGE": ModeRules(
        max_daily_loss_halt_pct=Decimal("0.03"),
        max_daily_loss_hard_pct=Decimal("0.04"),
        max_total_drawdown_warn_pct=Decimal("0.07"),
        max_total_drawdown_stop_pct=Decimal("0.08"),
        max_risk_per_trade_pct=Decimal("0.0075"),
        consistency_max_day_pct=None,
        daily_profit_lock_pct=None,
        news_buffer_minutes=30,
    ),
    "FUNDED": ModeRules(
        max_daily_loss_halt_pct=Decimal("0.02"),
        max_daily_loss_hard_pct=Decimal("0.03"),
        max_total_drawdown_warn_pct=Decimal("0.07"),
        max_total_drawdown_stop_pct=Decimal("0.08"),
        max_risk_per_trade_pct=Decimal("0.005"),
        consistency_max_day_pct=Decimal("0.30"),
        daily_profit_lock_pct=Decimal("0.30"),
        news_buffer_minutes=30,
    ),
    # Paper uses challenge risk so the learning layer sees realistic behaviour.
    "PAPER": ModeRules(
        max_daily_loss_halt_pct=Decimal("0.03"),
        max_daily_loss_hard_pct=Decimal("0.04"),
        max_total_drawdown_warn_pct=Decimal("0.07"),
        max_total_drawdown_stop_pct=Decimal("0.08"),
        max_risk_per_trade_pct=Decimal("0.0075"),
        consistency_max_day_pct=None,
        daily_profit_lock_pct=None,
        news_buffer_minutes=30,
    ),
}


@dataclass(frozen=True, slots=True)
class FirmRules:
    """Firm-specific rules layered on top of mode rules."""

    eod_drawdown_only: bool = False          # MyFundedFutures
    hft_cap_trades_per_day: int | None = None  # MyFundedFutures: 200
    server_request_cap_per_day: int | None = None  # FTMO: 2000
    positions_cap_per_day: int | None = None       # FTMO: 2000
    min_trade_hold_seconds: int | None = None      # FTMO: 60s on 50% of trades
    strict_news_buffer_minutes: int | None = None  # FTMO funded: 2
    weekend_flat: bool = True
    no_cross_account_hedging: bool = True
    eod_flat_local_time: str | None = None   # e.g. "15:59 CT"
    consistency_rule_pct: Decimal | None = None  # Bulenox: 0.40 (pauses payouts)


# Per spec §4.2
FIRM_RULES: dict[str, FirmRules] = {
    "MyFundedFutures": FirmRules(
        eod_drawdown_only=True,
        hft_cap_trades_per_day=200,
        no_cross_account_hedging=True,
        eod_flat_local_time="15:59 CT",
    ),
    "Bulenox": FirmRules(
        consistency_rule_pct=Decimal("0.40"),
        no_cross_account_hedging=True,
        eod_flat_local_time="15:59 CST",
    ),
    "FTMO": FirmRules(
        server_request_cap_per_day=2000,
        positions_cap_per_day=2000,
        min_trade_hold_seconds=60,
        strict_news_buffer_minutes=2,
    ),
    "Alpaca_Paper": FirmRules(),
    "OANDA_Demo": FirmRules(),
    "Tradovate_Sim": FirmRules(),
}
