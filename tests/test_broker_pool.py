"""Broker-pool arithmetic + engine integration tests.

Uses a throwaway in-memory SQLite so the pool can do real SQL aggregation
without needing a live Postgres.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from trading_bot.brokers.base_types import OrderSide
from trading_bot.db.models import (
    Account,
    AccountMode,
    AccountStatus,
    Base,
    Direction,
    Trade,
    TradeMode,
)
from trading_bot.risk.broker_pool import BrokerPool
from trading_bot.risk.engine import RiskEngine, TradeIntent


_NOW = datetime(2026, 4, 7, 17, 0, tzinfo=timezone.utc)


@pytest.fixture
def db_session_factory():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    def _factory():
        return _ContextSession(SessionLocal())

    yield _factory
    engine.dispose()


class _ContextSession:
    """Wrap a Session in a context manager matching our get_session API."""

    def __init__(self, session: Session):
        self._session = session

    def __enter__(self):
        return self._session

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self._session.commit()
        else:
            self._session.rollback()
        self._session.close()
        return False


def _seed_account(session_factory, firm: str, strategy: str, starting: str = "100000") -> Account:
    with session_factory() as s:
        acct = Account(
            firm=firm,
            strategy_name=strategy,
            account_size=Decimal(starting),
            starting_balance=Decimal(starting),
            current_balance=Decimal(starting),
            peak_balance=Decimal(starting),
            mode=AccountMode.PAPER,
            status=AccountStatus.ACTIVE,
        )
        s.add(acct)
        s.flush()
        s.refresh(acct)
        acct_id = acct.id
    # Re-fetch from a fresh session so the caller gets a detached copy.
    with session_factory() as s:
        acct = s.get(Account, acct_id)
        s.expunge(acct)
        return acct


def _open_trade(session_factory, account_id: int, entry: str, stop: str, qty: str) -> None:
    with session_factory() as s:
        t = Trade(
            account_id=account_id,
            strategy_name="TEST",
            asset="SPY",
            direction=Direction.LONG,
            entry_price=Decimal(entry),
            quantity=Decimal(qty),
            entry_time=_NOW,
            stop_loss=Decimal(stop),
            mode=TradeMode.PAPER,
        )
        s.add(t)


# ---------- pool arithmetic ----------


def test_empty_pool_has_zero_exposure(db_session_factory):
    _seed_account(db_session_factory, "Alpaca_Paper", "RSI2_SPY")
    _seed_account(db_session_factory, "Alpaca_Paper", "GAPFILL_SPY")
    # Override the live fetcher (which the brokers module auto-registers)
    # so the test doesn't hit the real Alpaca API.
    pool = BrokerPool(
        "Alpaca_Paper",
        session_factory=db_session_factory,
        balance_fetcher=lambda: None,
    )
    snap = pool.snapshot()
    assert snap.member_count == 2
    assert snap.open_trades == 0
    assert snap.committed_risk == Decimal("0")
    assert snap.open_notional == Decimal("0")
    assert snap.real_equity is None


def test_pool_sums_risk_and_notional_across_members(db_session_factory):
    a1 = _seed_account(db_session_factory, "Alpaca_Paper", "RSI2_SPY")
    a2 = _seed_account(db_session_factory, "Alpaca_Paper", "GAPFILL_SPY")
    _open_trade(db_session_factory, a1.id, entry="500", stop="490", qty="10")
    _open_trade(db_session_factory, a2.id, entry="200", stop="198", qty="5")

    pool = BrokerPool("Alpaca_Paper", session_factory=db_session_factory)
    snap = pool.snapshot()
    # a1: risk = 10*10 = 100, notional = 10*500 = 5000
    # a2: risk = 2*5 = 10, notional = 5*200 = 1000
    assert snap.committed_risk == Decimal("110")
    assert snap.open_notional == Decimal("6000")
    assert snap.open_trades == 2


def test_pool_excludes_caller_account(db_session_factory):
    a1 = _seed_account(db_session_factory, "Alpaca_Paper", "RSI2_SPY")
    a2 = _seed_account(db_session_factory, "Alpaca_Paper", "GAPFILL_SPY")
    _open_trade(db_session_factory, a1.id, entry="500", stop="490", qty="10")  # risk=100
    _open_trade(db_session_factory, a2.id, entry="200", stop="198", qty="5")   # risk=10

    pool = BrokerPool("Alpaca_Paper", session_factory=db_session_factory)
    snap = pool.snapshot(exclude_account_id=a1.id)
    assert snap.committed_risk == Decimal("10")   # only a2 counted
    assert snap.open_notional == Decimal("1000")
    assert snap.open_trades == 1


def test_pool_isolates_different_firms(db_session_factory):
    a_alpaca = _seed_account(db_session_factory, "Alpaca_Paper", "RSI2_SPY")
    a_oanda = _seed_account(db_session_factory, "OANDA_Demo", "BBZ_EURUSD")
    _open_trade(db_session_factory, a_alpaca.id, entry="500", stop="490", qty="10")
    _open_trade(db_session_factory, a_oanda.id, entry="1.1", stop="1.095", qty="10000")

    alpaca_pool = BrokerPool("Alpaca_Paper", session_factory=db_session_factory)
    oanda_pool = BrokerPool("OANDA_Demo", session_factory=db_session_factory)

    alpaca_snap = alpaca_pool.snapshot()
    oanda_snap = oanda_pool.snapshot()

    assert alpaca_snap.committed_risk == Decimal("100")
    assert alpaca_snap.open_trades == 1
    assert oanda_snap.open_trades == 1
    # OANDA risk: |1.1 - 1.095| * 10000 = 50
    assert oanda_snap.committed_risk == Decimal("50")


# ---------- engine integration ----------


def test_engine_shrinks_quantity_when_pool_risk_tight(db_session_factory):
    # Pool has one other strategy already risking $600. Real equity $100k,
    # mode hard cap 4% = $4000 pool-risk budget. Remaining = $3400.
    # New intent wants distance $10 * qty 500 = $5000 risk — too much.
    # Engine should shrink to fit $3400 / $10 = 340 qty.
    a1 = _seed_account(db_session_factory, "Alpaca_Paper", "RSI2_SPY")
    a2 = _seed_account(db_session_factory, "Alpaca_Paper", "GAPFILL_SPY")
    _open_trade(db_session_factory, a1.id, entry="500", stop="440", qty="10")  # risk=600

    engine = RiskEngine(session_factory=db_session_factory)
    # Inject a fake balance fetcher for the Alpaca pool — $100k equity, plenty BP.
    engine._pool_cache["Alpaca_Paper"] = BrokerPool(
        "Alpaca_Paper",
        session_factory=db_session_factory,
        balance_fetcher=lambda: {"equity": 100000, "buying_power": 200000},
    )

    intent = TradeIntent(
        account=a2,
        strategy_name="GAPFILL_SPY",
        asset="SPY",
        side=OrderSide.BUY,
        quantity=Decimal("500"),
        entry_price=Decimal("500"),
        stop_loss=Decimal("490"),
        take_profit=None,
        now=_NOW,
    )
    decision = engine.evaluate(intent)
    assert decision.approved
    assert decision.adjusted_quantity is not None
    # Got shrunk below the 500 originally requested.
    assert decision.adjusted_quantity < Decimal("500")


def test_engine_rejects_when_pool_has_no_room(db_session_factory):
    # Real equity $10, so 4% hard cap = $0.40 of pool risk. Existing trade
    # already uses $100. No positive qty fits.
    a1 = _seed_account(db_session_factory, "Alpaca_Paper", "RSI2_SPY")
    a2 = _seed_account(db_session_factory, "Alpaca_Paper", "GAPFILL_SPY")
    _open_trade(db_session_factory, a1.id, entry="500", stop="400", qty="1")

    engine = RiskEngine(session_factory=db_session_factory)
    engine._pool_cache["Alpaca_Paper"] = BrokerPool(
        "Alpaca_Paper",
        session_factory=db_session_factory,
        balance_fetcher=lambda: {"equity": 10, "buying_power": 20},
    )

    intent = TradeIntent(
        account=a2,
        strategy_name="GAPFILL_SPY",
        asset="SPY",
        side=OrderSide.BUY,
        quantity=Decimal("0.01"),
        entry_price=Decimal("500"),
        stop_loss=Decimal("490"),
        take_profit=None,
        now=_NOW,
    )
    decision = engine.evaluate(intent)
    assert not decision.approved
    assert "pool" in decision.reason.lower()


def test_engine_noop_when_balance_unknown(db_session_factory):
    """When the broker balance fetcher returns None (backtest / creds off),
    pool check must not block otherwise-valid trades."""
    a1 = _seed_account(db_session_factory, "Alpaca_Paper", "RSI2_SPY")

    engine = RiskEngine(session_factory=db_session_factory)
    engine._pool_cache["Alpaca_Paper"] = BrokerPool(
        "Alpaca_Paper",
        session_factory=db_session_factory,
        balance_fetcher=lambda: None,
    )

    intent = TradeIntent(
        account=a1,
        strategy_name="RSI2_SPY",
        asset="SPY",
        side=OrderSide.BUY,
        quantity=Decimal("1"),
        entry_price=Decimal("500"),
        stop_loss=Decimal("499"),
        take_profit=None,
        now=_NOW,
    )
    decision = engine.evaluate(intent)
    assert decision.approved
