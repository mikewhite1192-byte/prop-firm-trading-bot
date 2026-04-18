"""Seed the 6 paper accounts the orchestrator expects at boot.

Run once after ``alembic upgrade head``. Idempotent — existing rows are
skipped, not modified.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from trading_bot.db.models import Account, AccountMode, AccountStatus
from trading_bot.db.session import get_session


# Firm names here are the paper broker names — they'll flip to the real
# firm (MyFundedFutures, Bulenox, FTMO) when promoted to challenge.
SEED_ACCOUNTS = [
    ("Alpaca_Paper", "RSI2_SPY", Decimal("100000")),
    ("Alpaca_Paper", "GAPFILL_SPY", Decimal("100000")),
    ("OANDA_Demo", "BBZ_EURUSD", Decimal("100000")),
    ("Tradovate_Sim", "VWAP_SIGMA_ES", Decimal("100000")),
    ("Tradovate_Sim", "TINYGAP_ES", Decimal("100000")),
    ("Alpaca_Paper", "BB_BTC_4H", Decimal("100000")),
]


def main() -> None:
    with get_session() as s:
        for firm, strategy, size in SEED_ACCOUNTS:
            existing = s.execute(
                select(Account).where(
                    Account.firm == firm, Account.strategy_name == strategy
                )
            ).scalar_one_or_none()
            if existing is not None:
                print(f"skip {firm}/{strategy}: exists (id={existing.id})")
                continue
            s.add(
                Account(
                    firm=firm,
                    strategy_name=strategy,
                    account_size=size,
                    starting_balance=size,
                    current_balance=size,
                    peak_balance=size,
                    mode=AccountMode.PAPER,
                    status=AccountStatus.ACTIVE,
                )
            )
            print(f"seed {firm}/{strategy} @ ${size}")


if __name__ == "__main__":
    main()
