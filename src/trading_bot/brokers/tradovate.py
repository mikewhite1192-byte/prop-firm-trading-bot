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
    "demo": "https://demo.tradovateapi.com/v1",
    "live": "https://live.tradovateapi.com/v1",
}

_ORDER_TYPE_WIRE = {
    OrderType.MARKET: "Market",
    OrderType.LIMIT: "Limit",
    OrderType.STOP: "Stop",
    OrderType.STOP_LIMIT: "StopLimit",
}


class TradovateBroker(Broker):
    """Tradovate REST adapter for ES/MES/MNQ futures sim trading.

    Authentication flow: POST /auth/accessTokenRequest with a name/password
    bundle, receive an access token good for ~80 minutes. The orchestrator
    calls authenticate() on startup and on 401 from any downstream call.

    Strategies 2, 4, 5 use this adapter.
    """

    name = "tradovate"

    def __init__(self) -> None:
        s = get_settings()
        missing = [
            k
            for k, v in {
                "TRADOVATE_USERNAME": s.tradovate_username,
                "TRADOVATE_PASSWORD": s.tradovate_password,
                "TRADOVATE_APP_ID": s.tradovate_app_id,
                "TRADOVATE_CLIENT_ID": s.tradovate_client_id,
                "TRADOVATE_CLIENT_SECRET": s.tradovate_client_secret,
            }.items()
            if not v
        ]
        if missing:
            raise BrokerError(f"Tradovate credentials not configured: {', '.join(missing)}")

        host = _ENV_HOSTS.get(s.tradovate_environment, _ENV_HOSTS["demo"])
        self._settings = s
        self._client = httpx.AsyncClient(base_url=host, timeout=30.0)
        self._access_token: str | None = None
        self._token_expires_at: datetime | None = None
        self._account_id: int | None = None
        self._account_spec: str | None = None

    async def authenticate(self) -> None:
        payload = {
            "name": self._settings.tradovate_username,
            "password": self._settings.tradovate_password,
            "appId": self._settings.tradovate_app_id,
            "appVersion": self._settings.tradovate_app_version,
            "cid": self._settings.tradovate_client_id,
            "sec": self._settings.tradovate_client_secret,
        }
        r = await self._client.post("/auth/accessTokenRequest", json=payload)
        if r.status_code >= 400:
            raise BrokerError(f"Tradovate auth failed: {r.status_code} {r.text}")
        data = r.json()
        if "accessToken" not in data:
            raise BrokerError(f"Tradovate auth returned no token: {data}")

        self._access_token = data["accessToken"]
        self._client.headers["Authorization"] = f"Bearer {self._access_token}"
        if data.get("expirationTime"):
            self._token_expires_at = datetime.fromisoformat(
                data["expirationTime"].replace("Z", "+00:00")
            )

        # Resolve primary account id/spec for subsequent order placement.
        acct = await self._client.get("/account/list")
        acct.raise_for_status()
        accounts = acct.json()
        if not accounts:
            raise BrokerError("Tradovate returned no accounts for this user")
        self._account_id = accounts[0]["id"]
        self._account_spec = accounts[0]["name"]

    async def get_account(self) -> dict:
        self._require_token()
        r = await self._client.get(f"/account/item?id={self._account_id}")
        r.raise_for_status()
        return r.json()

    async def get_positions(self) -> list[BrokerPosition]:
        self._require_token()
        r = await self._client.get(f"/position/list?accountId={self._account_id}")
        r.raise_for_status()
        out: list[BrokerPosition] = []
        for p in r.json():
            net = Decimal(str(p.get("netPos", 0)))
            if net == 0:
                continue
            out.append(
                BrokerPosition(
                    symbol=str(p.get("contractId", "")),
                    quantity=net,
                    avg_entry_price=Decimal(str(p.get("netPrice", 0))),
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
        self._require_token()
        payload: dict = {
            "accountSpec": self._account_spec,
            "accountId": self._account_id,
            "action": "Buy" if side == OrderSide.BUY else "Sell",
            "symbol": symbol,
            "orderQty": int(quantity),
            "orderType": _ORDER_TYPE_WIRE[order_type],
            "isAutomated": True,
        }
        if limit_price is not None:
            payload["price"] = float(limit_price)
        if stop_price is not None:
            payload["stopPrice"] = float(stop_price)
        if client_order_id is not None:
            payload["text"] = client_order_id

        r = await self._client.post("/order/placeOrder", json=payload)
        if r.status_code >= 400:
            raise BrokerError(f"Tradovate order rejected: {r.status_code} {r.text}")
        data = r.json()
        if data.get("failureReason"):
            raise BrokerError(f"Tradovate order failed: {data['failureReason']}")

        return BrokerOrder(
            order_id=str(data.get("orderId") or data.get("id") or ""),
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            status=OrderStatus.ACCEPTED,
            submitted_at=datetime.now(timezone.utc),
            stop_price=stop_price,
            limit_price=limit_price,
            raw=data,
        )

    async def cancel_order(self, order_id: str) -> None:
        self._require_token()
        r = await self._client.post("/order/cancelOrder", json={"orderId": int(order_id)})
        if r.status_code >= 400:
            raise BrokerError(f"Tradovate cancel failed: {r.status_code} {r.text}")

    async def get_order(self, order_id: str) -> BrokerOrder:
        self._require_token()
        r = await self._client.get(f"/order/item?id={order_id}")
        r.raise_for_status()
        data = r.json()
        side = OrderSide.BUY if data.get("action") == "Buy" else OrderSide.SELL
        qty = Decimal(str(data.get("orderQty", 0)))
        return BrokerOrder(
            order_id=str(data.get("id", order_id)),
            symbol=str(data.get("symbol", "")),
            side=side,
            order_type=OrderType.MARKET,
            quantity=qty,
            status=OrderStatus.ACCEPTED,
            submitted_at=datetime.now(timezone.utc),
            raw=data,
        )

    async def close(self) -> None:
        await self._client.aclose()

    def _require_token(self) -> None:
        if not self._access_token:
            raise BrokerError("Tradovate not authenticated; call authenticate() first")
