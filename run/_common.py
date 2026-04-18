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


def _configure_logging() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )


def make_alpaca_broker(paper: bool = True):
    s = get_settings()
    return Alpaca(
        dict(
            API_KEY=s.alpaca_api_key,
            API_SECRET=s.alpaca_api_secret,
            PAPER=paper,
        )
    )


def make_tradovate_broker():
    s = get_settings()
    return Tradovate(
        dict(
            USERNAME=s.tradovate_username,
            PASSWORD=s.tradovate_password,
            APP_ID=s.tradovate_app_id,
            APP_VERSION=s.tradovate_app_version,
            CID=s.tradovate_client_id,
            SECRET=s.tradovate_client_secret,
            ENVIRONMENT=s.tradovate_environment,
        )
    )


def make_oanda_broker():
    return OandaBroker()


def run_single(strategy_cls, broker, strategy_params: dict | None = None) -> None:
    _configure_logging()
    trader = Trader()
    strategy = strategy_cls(broker=broker, parameters=strategy_params or {})
    trader.add_strategy(strategy)
    trader.run_all()
