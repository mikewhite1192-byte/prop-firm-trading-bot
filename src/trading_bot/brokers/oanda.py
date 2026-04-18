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


_ENV_HOSTS = {
    "practice": "https://api-fxpractice.oanda.com",
    "live": "https://api-fxtrade.oanda.com",
}


class OandaBroker(Broker):
    """OANDA v20 REST adapter for FX demo trading.

    Strategy 3 (EUR/USD BB z-score) uses this adapter. Units are signed:
    positive = long, negative = short — OANDA encodes direction in the
    unit count, not a side flag.
    """

    name = "oanda"

    def __init__(self) -> None:
        s = get_settings()
        if not s.oanda_api_token or not s.oanda_account_id:
            raise BrokerError("OANDA credentials not configured (.env)")
        host = _ENV_HOSTS.get(s.oanda_environment, _ENV_HOSTS["practice"])
        self._account_id = s.oanda_account_id
        self._client = httpx.AsyncClient(
            base_url=host,
            headers={
                "Authorization": f"Bearer {s.oanda_api_token}",
                "Content-Type": "application/json",
                "Accept-Datetime-Format": "RFC3339",
            },
            timeout=30.0,
        )

    async def authenticate(self) -> None:
        await self.get_account()

    async def get_account(self) -> dict:
        r = await self._client.get(f"/v3/accounts/{self._account_id}")
        r.raise_for_status()
        return r.json()["account"]

    async def get_positions(self) -> list[BrokerPosition]:
        r = await self._client.get(f"/v3/accounts/{self._account_id}/openPositions")
        r.raise_for_status()
        out: list[BrokerPosition] = []
        for p in r.json().get("positions", []):
            long_units = Decimal(p["long"]["units"])
            short_units = Decimal(p["short"]["units"])
            net = long_units + short_units
            if net == 0:
                continue
            side = p["long"] if long_units != 0 else p["short"]
            out.append(
                BrokerPosition(
                    symbol=p["instrument"],
                    quantity=net,
                    avg_entry_price=Decimal(side["averagePrice"]) if side.get("averagePrice") else Decimal("0"),
                    unrealized_pnl=Decimal(p.get("unrealizedPL", "0")),
                    raw=p,
                )
            )
        return out

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
        units = quantity if side == OrderSide.BUY else -quantity
        order: dict = {"instrument": symbol, "units": str(units), "timeInForce": "FOK"}

        if order_type == OrderType.MARKET:
            order["type"] = "MARKET"
        elif order_type == OrderType.LIMIT:
            if limit_price is None:
                raise BrokerError("LIMIT order requires limit_price")
            order["type"] = "LIMIT"
            order["price"] = str(limit_price)
            order["timeInForce"] = "GTC"
        elif order_type in (OrderType.STOP, OrderType.STOP_LIMIT):
            if stop_price is None:
                raise BrokerError(f"{order_type.value} order requires stop_price")
            order["type"] = "STOP"
            order["price"] = str(stop_price)
            order["timeInForce"] = "GTC"
            if order_type == OrderType.STOP_LIMIT and limit_price is not None:
                order["priceBound"] = str(limit_price)

        if client_order_id is not None:
            order["clientExtensions"] = {"id": client_order_id}

        r = await self._client.post(
            f"/v3/accounts/{self._account_id}/orders", json={"order": order}
        )
        if r.status_code >= 400:
            raise BrokerError(f"OANDA order rejected: {r.status_code} {r.text}")
        data = r.json()
        return self._parse_order(data, symbol, side, quantity, order_type)

    async def cancel_order(self, order_id: str) -> None:
        r = await self._client.put(
            f"/v3/accounts/{self._account_id}/orders/{order_id}/cancel"
        )
        if r.status_code >= 400:
            raise BrokerError(f"OANDA cancel failed: {r.status_code} {r.text}")

    async def get_order(self, order_id: str) -> BrokerOrder:
        r = await self._client.get(f"/v3/accounts/{self._account_id}/orders/{order_id}")
        r.raise_for_status()
        data = r.json()["order"]
        side = OrderSide.BUY if Decimal(data.get("units", "0")) > 0 else OrderSide.SELL
        qty = abs(Decimal(data.get("units", "0")))
        return self._parse_order({"orderCreateTransaction": data}, data["instrument"], side, qty, OrderType.MARKET)

    async def close(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _parse_order(
        data: dict,
        symbol: str,
        side: OrderSide,
        quantity: Decimal,
        order_type: OrderType,
    ) -> BrokerOrder:
        tx = data.get("orderFillTransaction") or data.get("orderCreateTransaction") or {}
        order_id = tx.get("id") or tx.get("orderID") or ""
        status = OrderStatus.FILLED if "orderFillTransaction" in data else OrderStatus.ACCEPTED
        fill_price = Decimal(tx["price"]) if tx.get("price") else None
        submitted = datetime.fromisoformat(tx["time"].replace("Z", "+00:00")) if tx.get("time") else datetime.now(timezone.utc)
        return BrokerOrder(
            order_id=order_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            status=status,
            submitted_at=submitted,
            filled_price=fill_price,
            filled_qty=quantity if status == OrderStatus.FILLED else None,
            raw=data,
        )
