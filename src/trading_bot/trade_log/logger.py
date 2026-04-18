from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from trading_bot.db.models import Direction, ExitReason, MarketRegime, Trade, TradeMode
from trading_bot.db.session import get_session


class TradeLogger:
    """Persists every trade with full context — the learning layer depends on
    this data being complete. Callers should record an opening row on entry
    and update it on exit; never delete or mutate an entry after close.
    """

    @staticmethod
    def record_entry(
        *,
        account_id: int,
        strategy_name: str,
        asset: str,
        direction: Direction,
        entry_price: Decimal,
        quantity: Decimal,
        entry_time: datetime,
        stop_loss: Decimal | None,
        take_profit: Decimal | None,
        mode: TradeMode,
        broker_order_id: str | None = None,
        market_regime: MarketRegime | None = None,
        vix_at_entry: Decimal | None = None,
        notes: str | None = None,
    ) -> int:
        with get_session() as s:
            trade = Trade(
                account_id=account_id,
                strategy_name=strategy_name,
                asset=asset,
                direction=direction,
                entry_price=entry_price,
                quantity=quantity,
                entry_time=entry_time,
                stop_loss=stop_loss,
                take_profit=take_profit,
                mode=mode,
                broker_order_id=broker_order_id,
                market_regime=market_regime,
                vix_at_entry=vix_at_entry,
                day_of_week=entry_time.weekday(),
                hour_of_entry=entry_time.hour,
                notes=notes,
            )
            s.add(trade)
            s.flush()
            return trade.id

    @staticmethod
    def record_exit(
        *,
        trade_id: int,
        exit_price: Decimal,
        exit_time: datetime,
        exit_reason: ExitReason,
        pnl: Decimal,
        pnl_pct: Decimal,
    ) -> None:
        with get_session() as s:
            trade = s.get(Trade, trade_id)
            if trade is None:
                raise ValueError(f"Trade {trade_id} not found")
            trade.exit_price = exit_price
            trade.exit_time = exit_time
            trade.exit_reason = exit_reason
            trade.pnl = pnl
            trade.pnl_pct = pnl_pct
