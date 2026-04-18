from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from trading_bot.brokers.base import OrderSide


@dataclass(slots=True)
class StrategySignal:
    """Intent to enter a trade. Consumed by the risk engine."""

    asset: str
    side: OrderSide
    entry_price: Decimal
    stop_loss: Decimal
    take_profit: Decimal | None
    quantity: Decimal
    now: datetime
    reason: str = ""


class Strategy(ABC):
    """Base class for the 6 strategies.

    Subclasses override ``check`` and return either a signal or None.
    The orchestrator schedules ``check`` at the strategy's chosen
    cadence (minute bars, hourly, session open, etc.).
    """

    name: str
    asset: str
    timeframe: str

    @abstractmethod
    async def check(self, now: datetime) -> StrategySignal | None: ...
