from __future__ import annotations

import asyncio
import logging
import signal
from datetime import datetime

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from trading_bot.brokers.alpaca import AlpacaBroker
from trading_bot.brokers.oanda import OandaBroker
from trading_bot.brokers.tradovate import TradovateBroker
from trading_bot.config import get_settings
from trading_bot.db.models import Account
from trading_bot.db.session import get_session
from trading_bot.orchestrator.master import MasterOrchestrator, StrategyBinding
from trading_bot.strategies.bb_btc_4h import BBBTCStrategy
from trading_bot.strategies.bb_zscore_eurusd import BBZScoreEURUSDStrategy
from trading_bot.strategies.gap_fill_spy import GapFillSPYStrategy
from trading_bot.strategies.rsi2_spy import RSI2SPYStrategy
from trading_bot.strategies.tiny_gap_es import TinyGapESStrategy
from trading_bot.strategies.vwap_sigma_es import VWAPSigmaESStrategy

log = logging.getLogger(__name__)


def _load_account(firm: str, strategy_name: str) -> Account:
    with get_session() as s:
        stmt = select(Account).where(
            Account.firm == firm, Account.strategy_name == strategy_name
        )
        account = s.execute(stmt).scalar_one_or_none()
        if account is None:
            raise RuntimeError(
                f"No account row for firm={firm} strategy={strategy_name}. "
                f"Run scripts/init_db.py first."
            )
        s.expunge(account)
        return account


async def _bootstrap() -> tuple[MasterOrchestrator, list]:
    """Build strategy bindings + authenticate brokers.

    Returns the orchestrator and the list of brokers so the shutdown
    handler can close them cleanly.
    """
    alpaca = AlpacaBroker()
    oanda = OandaBroker()
    tradovate = TradovateBroker()
    brokers = [alpaca, oanda, tradovate]

    # Validate connectivity up front — fail fast if any credentials are wrong.
    await asyncio.gather(*(b.authenticate() for b in brokers))

    bindings = [
        StrategyBinding(RSI2SPYStrategy(), _load_account("Alpaca_Paper", "RSI2_SPY"), alpaca),
        StrategyBinding(GapFillSPYStrategy(), _load_account("Alpaca_Paper", "GAPFILL_SPY"), alpaca),
        StrategyBinding(
            BBZScoreEURUSDStrategy(), _load_account("OANDA_Demo", "BBZ_EURUSD"), oanda
        ),
        StrategyBinding(
            VWAPSigmaESStrategy(), _load_account("Tradovate_Sim", "VWAP_SIGMA_ES"), tradovate
        ),
        StrategyBinding(
            TinyGapESStrategy(), _load_account("Tradovate_Sim", "TINYGAP_ES"), tradovate
        ),
        StrategyBinding(BBBTCStrategy(), _load_account("Alpaca_Paper", "BB_BTC_4H"), alpaca),
    ]
    return MasterOrchestrator(bindings), brokers


def _register_jobs(scheduler: AsyncIOScheduler, orchestrator: MasterOrchestrator, tz) -> None:
    """Each strategy gets its own cron trigger per the spec cadence."""
    by_name = {b.strategy.name: b for b in orchestrator.bindings}

    async def fire(name: str) -> None:
        await orchestrator.tick(by_name[name], datetime.now(tz))

    # Cadences from the spec — tighten later based on observed signal quality.
    scheduler.add_job(fire, CronTrigger(hour=15, minute=55, timezone=tz), args=["RSI2_SPY"])
    scheduler.add_job(fire, CronTrigger(hour=9, minute=30, timezone=tz), args=["GAPFILL_SPY"])
    scheduler.add_job(
        fire, CronTrigger(minute="*/15", timezone=pytz.UTC), args=["BBZ_EURUSD"]
    )
    scheduler.add_job(
        fire,
        CronTrigger(hour="10-11", minute="*", day_of_week="mon-fri", timezone=tz),
        args=["VWAP_SIGMA_ES"],
    )
    scheduler.add_job(fire, CronTrigger(hour=9, minute=30, timezone=tz), args=["TINYGAP_ES"])
    scheduler.add_job(fire, CronTrigger(hour="0,4,8,12,16,20", minute=1, timezone=pytz.UTC), args=["BB_BTC_4H"])


async def _run() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    tz = pytz.timezone(settings.timezone)
    orchestrator, brokers = await _bootstrap()
    scheduler = AsyncIOScheduler(timezone=tz)
    _register_jobs(scheduler, orchestrator, tz)
    scheduler.start()
    log.info("orchestrator up with %d strategy bindings", len(orchestrator.bindings))

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    try:
        await stop.wait()
    finally:
        log.info("shutting down")
        scheduler.shutdown(wait=False)
        for b in brokers:
            await b.close()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
