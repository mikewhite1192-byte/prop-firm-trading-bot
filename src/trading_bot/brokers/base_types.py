"""Broker-agnostic enums used by the risk engine and strategy contracts.

Kept separate from Lumibot's `Order.OrderSide` so the risk engine stays
framework-free and independently testable.
"""

from __future__ import annotations

import enum


class OrderSide(str, enum.Enum):
    BUY = "BUY"
    SELL = "SELL"
