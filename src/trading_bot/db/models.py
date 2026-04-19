from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class AccountMode(str, enum.Enum):
    PAPER = "PAPER"
    CHALLENGE = "CHALLENGE"
    FUNDED = "FUNDED"


class AccountStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    HALTED = "HALTED"
    PASSED = "PASSED"
    BLOWN = "BLOWN"


class TradeMode(str, enum.Enum):
    PAPER = "PAPER"
    CHALLENGE = "CHALLENGE"
    FUNDED = "FUNDED"


class Direction(str, enum.Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class ExitReason(str, enum.Enum):
    SIGNAL = "SIGNAL"
    STOP = "STOP"
    TAKE_PROFIT = "TAKE_PROFIT"
    TIME = "TIME"
    RISK_HALT = "RISK_HALT"
    MANUAL = "MANUAL"


class MarketRegime(str, enum.Enum):
    TRENDING = "TRENDING"
    RANGING = "RANGING"
    VOLATILE = "VOLATILE"
    UNKNOWN = "UNKNOWN"


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    firm: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_name: Mapped[str] = mapped_column(String(64), nullable=False)
    account_size: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    starting_balance: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    current_balance: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    peak_balance: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    current_drawdown_pct: Mapped[Decimal] = mapped_column(
        Numeric(8, 4), nullable=False, default=Decimal("0")
    )
    daily_pnl: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=Decimal("0"))
    weekly_pnl: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), nullable=False, default=Decimal("0")
    )
    monthly_pnl: Mapped[Decimal] = mapped_column(
        Numeric(18, 2), nullable=False, default=Decimal("0")
    )
    mode: Mapped[AccountMode] = mapped_column(
        Enum(AccountMode, name="account_mode"), nullable=False, default=AccountMode.PAPER
    )
    status: Mapped[AccountStatus] = mapped_column(
        Enum(AccountStatus, name="account_status"), nullable=False, default=AccountStatus.ACTIVE
    )
    broker_account_ref: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    trades: Mapped[list[Trade]] = relationship(back_populates="account", cascade="all, delete-orphan")
    daily_summaries: Mapped[list[DailySummary]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )

    __table_args__ = (UniqueConstraint("firm", "strategy_name", name="uq_account_firm_strategy"),)


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    strategy_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    asset: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    direction: Mapped[Direction] = mapped_column(Enum(Direction, name="trade_direction"), nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    exit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    exit_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pnl: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    pnl_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    stop_loss: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    take_profit: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    exit_reason: Mapped[ExitReason | None] = mapped_column(Enum(ExitReason, name="exit_reason"))
    market_regime: Mapped[MarketRegime | None] = mapped_column(
        Enum(MarketRegime, name="market_regime")
    )
    vix_at_entry: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    day_of_week: Mapped[int | None] = mapped_column(Integer)
    hour_of_entry: Mapped[int | None] = mapped_column(Integer)
    mode: Mapped[TradeMode] = mapped_column(Enum(TradeMode, name="trade_mode"), nullable=False)
    broker_order_id: Mapped[str | None] = mapped_column(String(128))
    notes: Mapped[str | None] = mapped_column(Text)

    account: Mapped[Account] = relationship(back_populates="trades")


class NewsWindow(Base):
    """Populated by the news-calendar scraper; consulted by is_news_blackout."""

    __tablename__ = "news_windows"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    event: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    impact: Mapped[str] = mapped_column(String(16), nullable=False)  # HIGH, MEDIUM, LOW
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="forexfactory")
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("event", "starts_at", "currency", name="uq_news_event_start_ccy"),
    )


class StrategyDailyPnL(Base):
    """Per-firm / per-strategy daily P&L roll-up for the funded consistency rule
    ("no single day > 30% of total profit"). Written by the trade logger on every
    fill; read by RiskEngine before approving funded-mode entries.
    """

    __tablename__ = "strategy_daily_pnl"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    firm: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    strategy_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    pnl: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=Decimal("0"))
    trade_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("firm", "strategy_name", "trade_date", name="uq_strategy_pnl_day"),
    )


class StrategyPerformanceDaily(Base):
    """Nightly snapshot of the learning layer's rollup per strategy.

    Source of truth is always the ``trades`` table; this is a denormalised
    cache refreshed by ``scripts/nightly_analysis.py`` so the dashboard and
    culling logic can read fast without re-aggregating thousands of rows.
    """

    __tablename__ = "strategy_performance_daily"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    strategy_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    firm: Mapped[str] = mapped_column(String(64), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    window_days: Mapped[int] = mapped_column(Integer, nullable=False)  # 30, 90, all
    trade_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    win_rate: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    avg_winner: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    avg_loser: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    profit_factor: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    expectancy: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    sharpe: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    sortino: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    max_drawdown_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    recovery_factor: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    best_day_pnl: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    worst_day_pnl: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "strategy_name", "as_of_date", "window_days", name="uq_perf_strategy_date_window"
        ),
    )


class StrategyHeartbeat(Base):
    """Per-strategy liveness ping. Updated on every on_trading_iteration so
    the dashboard can flag a stuck or dead strategy within one expected
    cadence interval. One row per strategy_name."""

    __tablename__ = "strategy_heartbeats"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    strategy_name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    firm: Mapped[str] = mapped_column(String(64), nullable=False)
    last_tick_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_decision: Mapped[str] = mapped_column(
        String(128), nullable=False, default="boot"
    )
    iteration_count_today: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    iterations_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sleeptime: Mapped[str] = mapped_column(String(16), nullable=False, default="")


class BacktestRun(Base):
    """Persisted backtest results — the "did this strategy ever work" ledger.

    Every run of scripts/backtest.py appends a row so we can track:
      * parameter drift (same strategy, different params -> different Sharpe)
      * data-source drift (Yahoo vs Polygon on the same window)
      * code drift (re-run after changes, compare to last accepted run)
    """

    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    strategy_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    data_source: Mapped[str] = mapped_column(String(64), nullable=False)  # yahoo, polygon, etc.
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    budget: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    final_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    total_return_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    trade_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    win_rate: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    sharpe: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    max_drawdown_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    parameters_json: Mapped[str | None] = mapped_column(Text)
    trades_csv_path: Mapped[str | None] = mapped_column(String(256))
    tearsheet_path: Mapped[str | None] = mapped_column(String(256))
    notes: Mapped[str | None] = mapped_column(Text)
    run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DailySummary(Base):
    __tablename__ = "daily_summary"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    summary_date: Mapped[date] = mapped_column(Date, nullable=False)
    total_trades: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    winning_trades: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    losing_trades: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    gross_pnl: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=Decimal("0"))
    net_pnl: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=Decimal("0"))
    win_rate: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    avg_winner: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    avg_loser: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    max_drawdown_intraday: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    consistency_rule_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    daily_loss_limit_used_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))

    account: Mapped[Account] = relationship(back_populates="daily_summaries")

    __table_args__ = (
        UniqueConstraint("account_id", "summary_date", name="uq_daily_summary_account_date"),
    )
