from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


class BrokerError(Exception):
    pass


class OrderSide(str, enum.Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, enum.Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


class OrderStatus(str, enum.Enum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"


@dataclass(slots=True)
class BrokerOrder:
    order_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    status: OrderStatus
    submitted_at: datetime
    filled_price: Decimal | None = None
    filled_qty: Decimal | None = None
    stop_price: Decimal | None = None
    limit_price: Decimal | None = None
    raw: dict | None = None


@dataclass(slots=True)
class BrokerPosition:
    symbol: str
    quantity: Decimal
    avg_entry_price: Decimal
    market_value: Decimal | None = None
    unrealized_pnl: Decimal | None = None
    raw: dict | None = None


class Broker(ABC):
    """Abstract broker adapter.

    Implementations must be idempotent on reconnect: the orchestrator may
    re-invoke authenticate() after a network glitch, and that must not
    produce duplicate sessions.
    """

    name: str

    @abstractmethod
    async def authenticate(self) -> None: ...

    @abstractmethod
    async def get_account(self) -> dict: ...

    @abstractmethod
    async def get_positions(self) -> list[BrokerPosition]: ...

    @abstractmethod
    async def submit_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: Decimal,
        order_type: OrderType = OrderType.MARKET,
        limit_price: Decimal | None = None,
        stop_price: Decimal | None = None,
        client_order_id: str | None = None,
    ) -> BrokerOrder: ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> None: ...

    @abstractmethod
    async def get_order(self, order_id: str) -> BrokerOrder: ...

    @abstractmethod
    async def close(self) -> None: ...
