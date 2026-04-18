from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import httpx

from trading_bot.brokers.base import (
    Broker,
    BrokerError,
    BrokerOrder,
    BrokerPosition,
    OrderSide,
    OrderStatus,
    OrderType,
)
from trading_bot.config import get_settings


_ORDER_TYPE_WIRE = {
    OrderType.MARKET: "market",
    OrderType.LIMIT: "limit",
    OrderType.STOP: "stop",
    OrderType.STOP_LIMIT: "stop_limit",
}

_STATUS_MAP = {
    "new": OrderStatus.ACCEPTED,
    "accepted": OrderStatus.ACCEPTED,
    "pending_new": OrderStatus.PENDING,
    "filled": OrderStatus.FILLED,
    "partially_filled": OrderStatus.PARTIALLY_FILLED,
    "canceled": OrderStatus.CANCELED,
    "rejected": OrderStatus.REJECTED,
    "expired": OrderStatus.CANCELED,
}


class AlpacaBroker(Broker):
    """Alpaca REST adapter for stocks + crypto paper trading.

    Uses the trading API endpoint configured via ALPACA_BASE_URL (paper by
    default). Market data lives at ALPACA_DATA_URL and is not used here —
    data feed is a separate module.
    """

    name = "alpaca"

    def __init__(self) -> None:
        s = get_settings()
        if not s.alpaca_api_key or not s.alpaca_api_secret:
            raise BrokerError("Alpaca credentials not configured (.env)")
        self._client = httpx.AsyncClient(
            base_url=s.alpaca_base_url,
            headers={
                "APCA-API-KEY-ID": s.alpaca_api_key,
                "APCA-API-SECRET-KEY": s.alpaca_api_secret,
            },
            timeout=30.0,
        )

    async def authenticate(self) -> None:
        # Alpaca uses per-request headers; validate by hitting /v2/account.
        await self.get_account()

    async def get_account(self) -> dict:
        r = await self._client.get("/v2/account")
        r.raise_for_status()
        return r.json()

    async def get_positions(self) -> list[BrokerPosition]:
        r = await self._client.get("/v2/positions")
        r.raise_for_status()
        return [
            BrokerPosition(
                symbol=p["symbol"],
                quantity=Decimal(p["qty"]),
                avg_entry_price=Decimal(p["avg_entry_price"]),
                market_value=Decimal(p["market_value"]) if p.get("market_value") else None,
                unrealized_pnl=Decimal(p["unrealized_pl"]) if p.get("unrealized_pl") else None,
                raw=p,
            )
            for p in r.json()
        ]

    async def submit_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: Decimal,
        order_type: OrderType = OrderType.MARKET,
        limit_price: Decimal | None = None,
        stop_price: Decimal | None = None,
        client_order_id: str | None = None,
    ) -> BrokerOrder:
        payload: dict = {
            "symbol": symbol,
            "qty": str(quantity),
            "side": side.value.lower(),
            "type": _ORDER_TYPE_WIRE[order_type],
            "time_in_force": "day",
        }
        if limit_price is not None:
            payload["limit_price"] = str(limit_price)
        if stop_price is not None:
            payload["stop_price"] = str(stop_price)
        if client_order_id is not None:
            payload["client_order_id"] = client_order_id

        r = await self._client.post("/v2/orders", json=payload)
        if r.status_code >= 400:
            raise BrokerError(f"Alpaca order rejected: {r.status_code} {r.text}")
        return self._parse_order(r.json())

    async def cancel_order(self, order_id: str) -> None:
        r = await self._client.delete(f"/v2/orders/{order_id}")
        if r.status_code not in (204, 207):
            raise BrokerError(f"Alpaca cancel failed: {r.status_code} {r.text}")

    async def get_order(self, order_id: str) -> BrokerOrder:
        r = await self._client.get(f"/v2/orders/{order_id}")
        r.raise_for_status()
        return self._parse_order(r.json())

    async def close(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _parse_order(data: dict) -> BrokerOrder:
        status = _STATUS_MAP.get(data.get("status", ""), OrderStatus.PENDING)
        return BrokerOrder(
            order_id=data["id"],
            symbol=data["symbol"],
            side=OrderSide(data["side"].upper()),
            order_type=OrderType(data["order_type"].upper().replace("STOP_LIMIT", "STOP_LIMIT")),
            quantity=Decimal(data["qty"]),
            status=status,
            submitted_at=datetime.fromisoformat(data["submitted_at"].replace("Z", "+00:00"))
            if data.get("submitted_at")
            else datetime.now(timezone.utc),
            filled_price=Decimal(data["filled_avg_price"])
            if data.get("filled_avg_price")
            else None,
            filled_qty=Decimal(data["filled_qty"]) if data.get("filled_qty") else None,
            stop_price=Decimal(data["stop_price"]) if data.get("stop_price") else None,
            limit_price=Decimal(data["limit_price"]) if data.get("limit_price") else None,
            raw=data,
        )
