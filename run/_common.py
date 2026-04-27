"""Shared helpers for the per-strategy run_*.py entrypoints.

Each strategy gets its own process (Lumibot raises NotImplementedError on
multi-strategy live traders). These helpers keep the entrypoints tiny:
configure logging, build the right broker, wire Trader, run.
"""

from __future__ import annotations

import logging
import sys

from lumibot.brokers import Alpaca, Tradovate
from lumibot.traders import Trader

from trading_bot.brokers.oanda_lumibot import OandaBroker
from trading_bot.config import get_settings

# Lumibot 4.4.62 bug: Alpaca._await_market_to_close() (alpaca.py:378)
# calls self.process_pending_orders(), which only exists on
# BacktestingBroker. This crashes the strategy loop on every session
# boundary (after-close, pre-market, startup outside market hours).
# Stack: strategy_executor._run_trading_session -> strategy.await_market_to_close
# -> broker._await_market_to_close -> self.process_pending_orders.
# Add a no-op on Alpaca (live order fills come through the websocket
# stream and don't need this hook). Apply unconditionally so a future
# Lumibot release that adds a real method on Broker doesn't silently
# leave the broken Alpaca path in place. Drop when Lumibot upstream fixes.
Alpaca.process_pending_orders = lambda self, strategy=None: None


def _configure_logging() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )


def make_alpaca_broker(paper: bool = True, market: str = "NYSE"):
    """One Alpaca broker instance per strategy. ``market`` controls Lumibot's
    sleep-until-open decision — set to "NYSE" for stock strategies, "24/7"
    for crypto strategies. Passing it here (vs. strategy.set_market) is the
    only thing that actually affects scheduling — broker.market wins.
    """
    s = get_settings()
    return Alpaca(
        dict(
            API_KEY=s.alpaca_api_key,
            API_SECRET=s.alpaca_api_secret,
            PAPER=paper,
            MARKET=market,
        )
    )


def make_tradovate_broker(market: str = "us_futures"):
    s = get_settings()
    return Tradovate(
        dict(
            USERNAME=s.tradovate_username,
            DEDICATED_PASSWORD=s.tradovate_password,
            APP_ID=s.tradovate_app_id or "Lumibot",
            APP_VERSION=s.tradovate_app_version,
            CID=s.tradovate_client_id,
            SECRET=s.tradovate_client_secret,
            IS_PAPER=s.tradovate_environment.lower() != "live",
            MARKET=market,
        )
    )


def make_oanda_broker(market: str = "24/5"):
    return OandaBroker(market=market)


def run_single(strategy_cls, broker, strategy_params: dict | None = None) -> None:
    _configure_logging()
    trader = Trader()
    strategy = strategy_cls(broker=broker, parameters=strategy_params or {})
    trader.add_strategy(strategy)
    trader.run_all()
