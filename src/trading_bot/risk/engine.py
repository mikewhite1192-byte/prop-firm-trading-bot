from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_DOWN, Decimal
from typing import Callable

import pytz
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trading_bot.brokers.base_types import OrderSide
from trading_bot.db.models import (
    Account,
    AccountMode,
    AccountStatus,
    NewsWindow,
    StrategyDailyPnL,
    Trade,
)
from trading_bot.risk.rules import FIRM_RULES, MODE_RULES, FirmRules, ModeRules

log = logging.getLogger(__name__)

SessionFactory = Callable[[], Session]

_FIRM_EOD_TIMEZONES = {
    "CT": "America/Chicago",
    "CST": "America/Chicago",
    "CDT": "America/Chicago",
    "ET": "America/New_York",
    "EST": "America/New_York",
    "EDT": "America/New_York",
}


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

    MODE_RULES (challenge/funded) are fast, pure checks against the Account
    snapshot. FIRM_RULES need small DB queries: today's trade count for the
    HFT cap, and the per-firm daily P&L roll-up for the funded consistency
    rule. We inject a ``session_factory`` so the engine stays stateless —
    call-sites with no DB (unit tests) just omit it and the firm checks
    permissively no-op.
    """

    def __init__(self, session_factory: SessionFactory | None = None) -> None:
        self._session_factory = session_factory

    def evaluate(self, intent: TradeIntent) -> RiskDecision:
        mode_rules = MODE_RULES[intent.account.mode.value]
        firm_rules = FIRM_RULES.get(intent.account.firm, FirmRules())

        status_decision = self._check_status(intent)
        if status_decision is not None:
            return status_decision

        # Before gating, try to shrink quantity to fit the per-trade cap.
        # Preserves the strategy's signal rather than rejecting outright on a
        # ~1% size miscalculation. Only shrinks — never grows beyond the intent.
        shrunk_qty = self._fit_quantity(intent, mode_rules)
        if shrunk_qty is not None and shrunk_qty != intent.quantity:
            intent.quantity = shrunk_qty

        per_trade_decision = self._check_per_trade_risk(intent, mode_rules)
        if per_trade_decision is not None:
            return per_trade_decision

        daily_decision = self._check_daily_loss(intent, mode_rules)
        if daily_decision is not None:
            return daily_decision

        dd_decision = self._check_total_drawdown(intent, mode_rules)
        if dd_decision is not None:
            return dd_decision

        firm_decision = self._check_firm_rules(intent, firm_rules)
        if firm_decision is not None:
            return firm_decision

        if intent.account.mode == AccountMode.FUNDED:
            consistency_decision = self._check_consistency_rule(intent, mode_rules, firm_rules)
            if consistency_decision is not None:
                return consistency_decision

        news_decision = self._check_news_blackout(intent, mode_rules, firm_rules)
        if news_decision is not None:
            return news_decision

        return RiskDecision(approved=True, adjusted_quantity=intent.quantity)

    # --- individual rule checks ---

    @staticmethod
    def _check_status(intent: TradeIntent) -> RiskDecision | None:
        if intent.account.status != AccountStatus.ACTIVE:
            return RiskDecision(
                approved=False, reason=f"account status={intent.account.status.value}"
            )
        return None

    @staticmethod
    def _check_per_trade_risk(intent: TradeIntent, mode: ModeRules) -> RiskDecision | None:
        per_trade_risk = _per_trade_risk(intent)
        max_risk_dollar = intent.account.current_balance * mode.max_risk_per_trade_pct
        if per_trade_risk > max_risk_dollar:
            return RiskDecision(
                approved=False,
                reason=f"per-trade risk ${per_trade_risk:.2f} exceeds "
                f"max ${max_risk_dollar:.2f} ({mode.max_risk_per_trade_pct:.2%})",
            )
        return None

    @staticmethod
    def _fit_quantity(intent: TradeIntent, mode: ModeRules) -> Decimal | None:
        """If the intent is over-sized, return the largest qty that still fits."""
        max_risk_dollar = intent.account.current_balance * mode.max_risk_per_trade_pct
        distance = abs(intent.entry_price - intent.stop_loss)
        if distance <= 0 or intent.quantity <= 0:
            return None
        per_trade_risk = distance * intent.quantity
        if per_trade_risk <= max_risk_dollar:
            return None
        shrunk = (max_risk_dollar / distance).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
        if shrunk <= 0:
            return None
        log.info(
            "risk: shrinking qty %s -> %s to fit %.2f%% cap",
            intent.quantity,
            shrunk,
            float(mode.max_risk_per_trade_pct * 100),
        )
        return shrunk

    @staticmethod
    def _check_daily_loss(intent: TradeIntent, mode: ModeRules) -> RiskDecision | None:
        loss_pct = _loss_pct(intent.account.daily_pnl, intent.account.starting_balance)
        if loss_pct >= mode.max_daily_loss_hard_pct:
            return RiskDecision(
                approved=False,
                reason=f"daily loss {loss_pct:.2%} >= hard stop "
                f"{mode.max_daily_loss_hard_pct:.2%}",
                halt_account=True,
                hard_stop=True,
            )
        if loss_pct >= mode.max_daily_loss_halt_pct:
            return RiskDecision(
                approved=False,
                reason=f"daily loss {loss_pct:.2%} >= halt {mode.max_daily_loss_halt_pct:.2%}",
                halt_account=True,
            )
        return None

    @staticmethod
    def _check_total_drawdown(intent: TradeIntent, mode: ModeRules) -> RiskDecision | None:
        dd = intent.account.current_drawdown_pct
        if dd >= mode.max_total_drawdown_stop_pct:
            return RiskDecision(
                approved=False,
                reason=f"total drawdown {dd:.2%} >= stop {mode.max_total_drawdown_stop_pct:.2%}",
                halt_account=True,
                hard_stop=True,
            )
        return None

    def _check_firm_rules(
        self, intent: TradeIntent, firm: FirmRules
    ) -> RiskDecision | None:
        if firm.eod_flat_local_time and _is_past_eod_local(intent.now, firm.eod_flat_local_time):
            return RiskDecision(
                approved=False,
                reason=f"past EOD flat time {firm.eod_flat_local_time}",
            )

        if firm.weekend_flat and _is_weekend_flat(intent.now):
            return RiskDecision(approved=False, reason="weekend flat — no new entries")

        if firm.hft_cap_trades_per_day is not None:
            count = self._todays_trade_count(intent.account.id, intent.now)
            if count is not None and count >= firm.hft_cap_trades_per_day:
                return RiskDecision(
                    approved=False,
                    reason=f"firm HFT cap {firm.hft_cap_trades_per_day} reached ({count} today)",
                )

        return None

    def _check_consistency_rule(
        self, intent: TradeIntent, mode: ModeRules, firm: FirmRules
    ) -> RiskDecision | None:
        limit = firm.consistency_rule_pct or mode.consistency_max_day_pct
        if limit is None:
            return None
        today_pnl, total_pnl = self._firm_pnl_roll_up(intent.account.firm, intent.now)
        if total_pnl is None or total_pnl <= 0:
            return None  # no positive profit to compare against yet
        ratio = today_pnl / total_pnl if total_pnl > 0 else Decimal("0")
        if ratio > limit:
            return RiskDecision(
                approved=False,
                reason=f"consistency rule: day={today_pnl:.2f} is {ratio:.2%} "
                f"of total={total_pnl:.2f} (limit {limit:.2%})",
            )
        return None

    def _check_news_blackout(
        self, intent: TradeIntent, mode: ModeRules, firm: FirmRules
    ) -> RiskDecision | None:
        buffer_minutes = firm.strict_news_buffer_minutes or mode.news_buffer_minutes
        window = self._active_news_window(intent.now, timedelta(minutes=buffer_minutes))
        if window is not None:
            return RiskDecision(
                approved=False,
                reason=f"news blackout: {window.event} {window.starts_at:%Y-%m-%d %H:%M} "
                f"({window.currency}, ±{buffer_minutes}m)",
            )
        return None

    # --- DB helpers — safe no-ops when no session_factory configured ---

    def _todays_trade_count(self, account_id: int, now: datetime) -> int | None:
        if self._session_factory is None:
            return None
        day_start = _utc_day_start(now)
        with self._session_factory() as s:
            count = s.execute(
                select(func.count(Trade.id)).where(
                    Trade.account_id == account_id,
                    Trade.entry_time >= day_start,
                )
            ).scalar_one()
        return int(count)

    def _firm_pnl_roll_up(
        self, firm: str, now: datetime
    ) -> tuple[Decimal, Decimal | None]:
        if self._session_factory is None:
            return Decimal("0"), None
        today = now.astimezone(timezone.utc).date()
        with self._session_factory() as s:
            today_pnl = s.execute(
                select(func.coalesce(func.sum(StrategyDailyPnL.pnl), 0)).where(
                    StrategyDailyPnL.firm == firm,
                    StrategyDailyPnL.trade_date == today,
                )
            ).scalar_one()
            total_pnl = s.execute(
                select(func.coalesce(func.sum(StrategyDailyPnL.pnl), 0)).where(
                    StrategyDailyPnL.firm == firm
                )
            ).scalar_one()
        return Decimal(str(today_pnl)), Decimal(str(total_pnl))

    def _active_news_window(self, now: datetime, buffer: timedelta) -> NewsWindow | None:
        if self._session_factory is None:
            return None
        utc = now.astimezone(timezone.utc)
        with self._session_factory() as s:
            window = (
                s.execute(
                    select(NewsWindow).where(
                        NewsWindow.starts_at - buffer <= utc,
                        NewsWindow.ends_at + buffer >= utc,
                        NewsWindow.impact == "HIGH",
                    )
                )
                .scalars()
                .first()
            )
        return window


def _per_trade_risk(intent: TradeIntent) -> Decimal:
    distance = abs(intent.entry_price - intent.stop_loss)
    return distance * intent.quantity


def _loss_pct(daily_pnl: Decimal, starting_balance: Decimal) -> Decimal:
    if daily_pnl >= 0 or starting_balance == 0:
        return Decimal("0")
    return abs(daily_pnl) / starting_balance


def _utc_day_start(now: datetime) -> datetime:
    utc = now.astimezone(timezone.utc)
    return utc.replace(hour=0, minute=0, second=0, microsecond=0)


def _is_past_eod_local(now: datetime, eod_spec: str) -> bool:
    """eod_spec examples: '15:59 CT', '15:55 ET'."""
    try:
        hhmm, tz_abbr = eod_spec.split()
        hh, mm = [int(x) for x in hhmm.split(":")]
        tz_name = _FIRM_EOD_TIMEZONES.get(tz_abbr, "America/New_York")
        tz = pytz.timezone(tz_name)
    except Exception:
        log.warning("unrecognised eod_flat_local_time=%s; ignoring", eod_spec)
        return False
    local = now.astimezone(tz)
    if local.weekday() >= 5:
        return True  # weekends count as past-EOD for open-bars too
    return (local.hour, local.minute) >= (hh, mm)


def _is_weekend_flat(now: datetime) -> bool:
    """True if Fri 15:55 ET or later, or Saturday/Sunday. Spec: flat by Friday 3:55 PM ET."""
    et = now.astimezone(pytz.timezone("America/New_York"))
    if et.weekday() in (5, 6):
        return True
    if et.weekday() == 4 and (et.hour, et.minute) >= (15, 55):
        return True
    return False
