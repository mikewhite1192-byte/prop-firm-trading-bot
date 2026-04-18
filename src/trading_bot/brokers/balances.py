"""Live broker-balance fetchers.

Registered with :mod:`trading_bot.risk.broker_pool` so the risk engine can
check pool-level exposure against each broker's real account state
before approving any entry.

Every fetcher returns either ``{"equity": float, "buying_power": float, ...}``
or ``None`` if the broker's credentials aren't configured or the API call
fails. ``None`` makes the pool check degrade to a no-op, so backtests and
missing-creds environments don't blow up.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from trading_bot.config import get_settings
from trading_bot.risk.broker_pool import register_balance_fetcher

log = logging.getLogger(__name__)

# Simple per-fetcher 30s in-process cache so the risk engine can call
# these once per order without hammering the broker APIs.
_CACHE_TTL_SECONDS = 30
_cache: dict[str, tuple[float, dict | None]] = {}


def _cached(key: str, fetch_fn) -> dict | None:
    now = time.monotonic()
    if key in _cache:
        ts, data = _cache[key]
        if now - ts < _CACHE_TTL_SECONDS:
            return data
    data = fetch_fn()
    _cache[key] = (now, data)
    return data


def fetch_alpaca_balance() -> dict | None:
    """Pull the Alpaca paper account balance via /v2/account."""
    def _do():
        s = get_settings()
        if not (s.alpaca_api_key and s.alpaca_api_secret):
            return None
        try:
            r = httpx.get(
                f"{s.alpaca_base_url}/v2/account",
                headers={
                    "APCA-API-KEY-ID": s.alpaca_api_key,
                    "APCA-API-SECRET-KEY": s.alpaca_api_secret,
                },
                timeout=10.0,
            )
            r.raise_for_status()
            data = r.json()
            return {
                "equity": float(data.get("equity", 0)),
                "cash": float(data.get("cash", 0)),
                "buying_power": float(data.get("buying_power", 0)),
                "last_equity": float(data.get("last_equity", 0)),
                "portfolio_value": float(data.get("portfolio_value", 0)),
                "account_number": data.get("account_number", ""),
                "status": data.get("status", ""),
            }
        except Exception as e:
            log.warning("alpaca balance fetch failed: %s", e)
            return None

    return _cached("alpaca", _do)


def fetch_oanda_balance() -> dict | None:
    def _do():
        s = get_settings()
        if not (s.oanda_api_token and s.oanda_account_id):
            return None
        try:
            from oandapyV20 import API
            from oandapyV20.endpoints import accounts

            api = API(access_token=s.oanda_api_token, environment=s.oanda_environment)
            r = api.request(accounts.AccountSummary(accountID=s.oanda_account_id))
            acct = r["account"]
            return {
                "equity": float(acct["NAV"]),
                "cash": float(acct["balance"]),
                "buying_power": float(acct["marginAvailable"]),
                "last_equity": float(acct["NAV"]),
            }
        except Exception as e:
            log.warning("oanda balance fetch failed: %s", e)
            return None

    return _cached("oanda", _do)


def fetch_tradovate_balance() -> dict | None:
    def _do():
        s = get_settings()
        if not all(
            [
                s.tradovate_username,
                s.tradovate_password,
                s.tradovate_client_id,
                s.tradovate_client_secret,
            ]
        ):
            return None
        # Tradovate requires a two-step auth that's cheaper to do through
        # Lumibot's Tradovate broker when it exists. For now we no-op and
        # let the broker-pool check degrade gracefully until Tradovate is
        # actually live — at which point we route this through the broker.
        return None

    return _cached("tradovate", _do)


def register_all() -> None:
    """Wire every known firm to its balance fetcher."""
    register_balance_fetcher("Alpaca_Paper", fetch_alpaca_balance)
    register_balance_fetcher("OANDA_Demo", fetch_oanda_balance)
    register_balance_fetcher("Tradovate_Sim", fetch_tradovate_balance)


# Register on import so anyone touching the risk engine gets the fetchers.
register_all()
