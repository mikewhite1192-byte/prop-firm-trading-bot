"""OANDA Broker for Lumibot.

Lumibot has no native OANDA support (confirmed via research against
commit 2026-04-17 of the dev branch). This module implements the
``lumibot.brokers.broker.Broker`` interface on top of ``oandapyV20`` so
strategy 3 (BB z-score EUR/USD) runs under the same Lumibot framework
as the Alpaca/Tradovate strategies.

Status (Phase 1 scaffold):
  * Auth, account snapshot, positions, basic market-order submission,
    cancel, fetch-by-id — implemented against the v20 REST API.
  * Historical candles — implemented, returns a Lumibot ``Bars`` object.
  * Streaming (price + transaction) — scaffolded, not yet wired to
    Lumibot's stream-event registration. Without streaming, Lumibot
    falls back to polling, which is acceptable for a 15-minute strategy.
  * Bracket / OTO order types — Lumibot emits these via
    ``stop_loss_price`` / ``take_profit_price`` args. OANDA supports
    attached take-profit and stop-loss on a MARKET order; translation
    is handled in ``_submit_order``.

Polish the streaming path + order-state reconciliation before moving
this strategy to FTMO challenge deployment.
"""

from __future__ import annotations

import logging
from decimal import Decimal

import pandas as pd
from lumibot.brokers.broker import Broker
from lumibot.data_sources.data_source import DataSource
from lumibot.entities import Asset, Bars, Order, Position
from oandapyV20 import API
from oandapyV20.endpoints import accounts, instruments, orders, positions, trades

from trading_bot.config import get_settings

log = logging.getLogger(__name__)


_GRANULARITY_BY_MINUTES = {
    1: "M1",
    2: "M2",
    4: "M4",
    5: "M5",
    10: "M10",
    15: "M15",
    30: "M30",
    60: "H1",
    120: "H2",
    180: "H3",
    240: "H4",
    360: "H6",
    480: "H8",
    720: "H12",
    1440: "D",
    10080: "W",
}


def _timestep_to_minutes(timestep: str | int) -> int | None:
    """Lumibot accepts timesteps like 'minute', '15min', '4h', '1day'. Normalise."""
    if isinstance(timestep, int):
        return timestep
    t = timestep.lower().strip()
    quantity = 1
    if t and t[0].isdigit():
        for i, c in enumerate(t):
            if not c.isdigit():
                quantity = int(t[:i])
                t = t[i:].strip().rstrip("s")
                break
        else:
            return int(t)  # pure number
    units = {"m": 1, "min": 1, "minute": 1, "h": 60, "hour": 60, "d": 1440, "day": 1440}
    if t not in units:
        return None
    return quantity * units[t]


def _granularity_for(timestep: str | int) -> str | None:
    minutes = _timestep_to_minutes(timestep)
    if minutes is None:
        return None
    return _GRANULARITY_BY_MINUTES.get(minutes)


class OandaDataSource(DataSource):
    """Historical candles from OANDA's /instruments/{inst}/candles endpoint."""

    SOURCE = "OANDA"
    MIN_TIMESTEP = "minute"
    TIMESTEP_MAPPING = [
        {"timestep": "minute", "representations": ["minute", "1min", "1m", "M1"]},
        {"timestep": "hour", "representations": ["hour", "1hour", "1h", "H1"]},
        {"timestep": "day", "representations": ["day", "1day", "1d", "D"]},
    ]

    def __init__(self, api: API, account_id: str) -> None:
        super().__init__()
        self._api = api
        self._account_id = account_id

    def get_historical_prices(
        self,
        asset: Asset,
        length: int,
        timestep: str = "minute",
        quote: Asset | None = None,
        **_: dict,
    ) -> Bars | None:
        granularity = _granularity_for(timestep)
        if granularity is None:
            log.warning("oanda: unsupported timestep %s", timestep)
            return None

        instrument = asset.symbol if "_" in asset.symbol else f"{asset.symbol[:3]}_{asset.symbol[3:]}"
        params = {"granularity": granularity, "count": length, "price": "M"}
        req = instruments.InstrumentsCandles(instrument=instrument, params=params)
        try:
            resp = self._api.request(req)
        except Exception as e:
            log.warning("oanda candles failed %s: %s", instrument, e)
            return None

        rows = []
        for c in resp.get("candles", []):
            if not c.get("complete"):
                continue
            mid = c["mid"]
            rows.append(
                {
                    "datetime": pd.to_datetime(c["time"]),
                    "open": float(mid["o"]),
                    "high": float(mid["h"]),
                    "low": float(mid["l"]),
                    "close": float(mid["c"]),
                    "volume": float(c.get("volume", 0)),
                }
            )
        if not rows:
            return None
        df = pd.DataFrame(rows).set_index("datetime")
        return Bars(df, self.SOURCE, asset, quote=quote)

    def get_last_price(self, asset: Asset, quote: Asset | None = None, **_) -> float | None:
        bars = self.get_historical_prices(asset, length=1, timestep="minute", quote=quote)
        if bars is None or bars.df is None or bars.df.empty:
            return None
        return float(bars.df["close"].iloc[-1])

    # DataSource abstracts — none of these apply to OANDA forex, but must exist.
    def get_chains(self, asset: Asset, quote: Asset | None = None, exchange: str | None = None):
        raise NotImplementedError("OANDA does not support options chains")

    def get_strikes(self, asset: Asset, quote: Asset | None = None):
        raise NotImplementedError("OANDA does not support options chains")

    def get_quote(self, asset: Asset, quote: Asset | None = None):
        last = self.get_last_price(asset, quote=quote)
        return {"last": last, "bid": last, "ask": last} if last is not None else None


class OandaBroker(Broker):
    """Lumibot broker bound to an OANDA v20 demo or live account."""

    NAME = "OANDA"

    def __init__(
        self,
        connect_stream: bool = False,
        max_workers: int = 10,
        market: str = "24/5",
        **kwargs,
    ) -> None:
        s = get_settings()
        if not s.oanda_api_token or not s.oanda_account_id:
            raise RuntimeError("OANDA credentials missing in .env (OANDA_API_TOKEN / OANDA_ACCOUNT_ID)")

        self._api = API(access_token=s.oanda_api_token, environment=s.oanda_environment)
        self._account_id = s.oanda_account_id
        data_source = OandaDataSource(self._api, self._account_id)
        # Broker.__init__ reads config["MARKET"] to decide trading-hours
        # scheduling. Forex = 24/5.
        super().__init__(
            name=self.NAME,
            data_source=data_source,
            config={"MARKET": market},
            connect_stream=connect_stream,
            max_workers=max_workers,
            **kwargs,
        )

    # ---- balances / positions ----

    def _get_balances_at_broker(self, quote_asset: Asset, strategy) -> tuple[float, float, float]:
        req = accounts.AccountSummary(accountID=self._account_id)
        resp = self._api.request(req)
        acct = resp["account"]
        nav = float(acct["NAV"])
        cash = float(acct["balance"])
        positions_value = nav - cash
        return cash, positions_value, nav

    def _pull_positions(self, strategy) -> list[Position]:
        req = positions.OpenPositions(accountID=self._account_id)
        resp = self._api.request(req)
        out: list[Position] = []
        for p in resp.get("positions", []):
            long_units = Decimal(p["long"]["units"])
            short_units = Decimal(p["short"]["units"])
            net = long_units + short_units
            if net == 0:
                continue
            side = p["long"] if long_units != 0 else p["short"]
            avg = Decimal(side["averagePrice"]) if side.get("averagePrice") else Decimal("0")
            out.append(
                Position(
                    strategy=strategy.name if strategy else "",
                    asset=Asset(symbol=p["instrument"], asset_type=Asset.AssetType.FOREX),
                    quantity=float(net),
                    orders=[],
                    avg_fill_price=float(avg),
                )
            )
        return out

    def _pull_position(self, strategy, asset: Asset) -> Position | None:
        for p in self._pull_positions(strategy):
            if p.asset.symbol == asset.symbol:
                return p
        return None

    # ---- orders ----

    def _submit_order(self, order: Order) -> Order:
        """Translate a Lumibot Order into OANDA's v20 order schema."""
        instrument = self._to_instrument(order.asset)
        units = int(order.quantity) if order.side == "buy" else -int(order.quantity)

        body: dict = {"instrument": instrument, "units": str(units)}
        if order.order_type == Order.OrderType.MARKET:
            body["type"] = "MARKET"
            body["timeInForce"] = "FOK"
        elif order.order_type == Order.OrderType.LIMIT:
            body["type"] = "LIMIT"
            body["price"] = str(order.limit_price)
            body["timeInForce"] = "GTC"
        else:
            raise NotImplementedError(f"oanda: order type {order.order_type} not wired yet")

        if getattr(order, "stop_loss_price", None):
            body["stopLossOnFill"] = {"price": str(order.stop_loss_price)}
        if getattr(order, "take_profit_price", None):
            body["takeProfitOnFill"] = {"price": str(order.take_profit_price)}

        req = orders.OrderCreate(accountID=self._account_id, data={"order": body})
        resp = self._api.request(req)
        fill_tx = resp.get("orderFillTransaction") or resp.get("orderCreateTransaction") or {}
        order.identifier = str(fill_tx.get("id") or fill_tx.get("orderID") or "")
        order.status = Order.OrderStatus.FILLED if "orderFillTransaction" in resp else Order.OrderStatus.OPEN
        if "orderFillTransaction" in resp:
            order.avg_fill_price = float(fill_tx["price"])
            order.filled_quantity = abs(int(fill_tx["units"]))
        return order

    def _modify_order(self, order: Order, limit_price=None, stop_price=None):
        raise NotImplementedError("oanda modify order — Phase 2")

    def cancel_order(self, order: Order) -> None:
        if not order.identifier:
            return
        req = orders.OrderCancel(accountID=self._account_id, orderID=order.identifier)
        self._api.request(req)
        order.status = Order.OrderStatus.CANCELED

    def _pull_broker_order(self, identifier: str) -> dict | None:
        req = orders.OrderDetails(accountID=self._account_id, orderID=identifier)
        try:
            return self._api.request(req).get("order")
        except Exception as e:
            log.warning("oanda pull order %s: %s", identifier, e)
            return None

    def _pull_broker_all_orders(self) -> list[dict]:
        req = orders.OrdersPending(accountID=self._account_id)
        return self._api.request(req).get("orders", [])

    def _parse_broker_order(self, response: dict, strategy_name: str, strategy_object=None) -> Order:
        side = "buy" if int(response.get("units", 0)) > 0 else "sell"
        qty = abs(int(response.get("units", 0)))
        asset = Asset(symbol=response["instrument"], asset_type=Asset.AssetType.FOREX)
        order = Order(
            strategy=strategy_name,
            asset=asset,
            quantity=qty,
            side=side,
            order_type=Order.OrderType.MARKET,
        )
        order.identifier = str(response.get("id", ""))
        return order

    # ---- streaming — scaffolded, not yet live ----

    def _get_stream_object(self):
        # TODO Phase 2: return an oandapyV20 transactions-stream iterator.
        return None

    def _register_stream_events(self) -> None:
        # TODO Phase 2: wire OANDA transaction types to Lumibot's
        # on_filled / on_canceled / on_new_order hooks.
        return None

    def _run_stream(self) -> None:
        # Until streaming is wired, Lumibot will poll via sleeptime, which
        # is fine for 15-minute strategies.
        return None

    def get_historical_account_value(self) -> dict:
        # OANDA exposes account transactions; for now return an empty dict
        # so Lumibot's dashboard snapshot doesn't crash.
        return {"hourly": None, "daily": None}

    @staticmethod
    def _to_instrument(asset: Asset) -> str:
        return asset.symbol if "_" in asset.symbol else f"{asset.symbol[:3]}_{asset.symbol[3:]}"
