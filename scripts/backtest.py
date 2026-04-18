"""Backtest harness.

Runs a strategy against historical data via Lumibot's backtesting
engine and logs the outcome to the ``backtest_runs`` table so runs can
be compared over time.

Data sources supported in Phase 2:
  * yahoo   — free daily stock OHLC (works for RSI2_SPY out of the box;
              other strategies need intraday data and a paid source).
  * pandas  — bring-your-own DataFrame (if you already have data on disk).

Intraday strategies (gap_fill_spy, vwap_sigma_es, tiny_gap_es,
bb_zscore_eurusd, bb_btc_4h) need a minute-bar datasource:
  * Polygon or Databento for stocks / futures
  * Alpaca historical for stocks / crypto (set ALPACA_* env vars)
  * OANDA for forex
Wire those by passing the matching Lumibot ``*DataBacktesting`` class.

Usage:
    python scripts/backtest.py --strategy RSI2_SPY --start 2024-01-01 --end 2026-01-01
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, date
from decimal import Decimal
from pathlib import Path

from lumibot.backtesting import YahooDataBacktesting

from trading_bot.db.models import BacktestRun
from trading_bot.db.session import get_session
from trading_bot.strategies.bb_btc_4h import BBBTC4H
from trading_bot.strategies.bb_zscore_eurusd import BBZScoreEURUSD
from trading_bot.strategies.gap_fill_spy import GapFillSPY
from trading_bot.strategies.rsi2_spy import RSI2SPY
from trading_bot.strategies.tiny_gap_es import TinyGapES
from trading_bot.strategies.vwap_sigma_es import VWAPSigmaES

logging.basicConfig(
    level="INFO", format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stdout
)
log = logging.getLogger("backtest")


STRATEGIES: dict[str, type] = {
    "RSI2_SPY": RSI2SPY,
    "GAPFILL_SPY": GapFillSPY,
    "BBZ_EURUSD": BBZScoreEURUSD,
    "VWAP_SIGMA_ES": VWAPSigmaES,
    "TINYGAP_ES": TinyGapES,
    "BB_BTC_4H": BBBTC4H,
}

DATA_SOURCES = {
    "yahoo": YahooDataBacktesting,
}

# Which strategies are Yahoo-runnable (daily stocks). The rest need intraday
# feeds, so the harness refuses with a clear error until the source is set up.
YAHOO_COMPATIBLE = {"RSI2_SPY"}


def run(
    strategy_name: str,
    start: date,
    end: date,
    budget: float,
    data_source: str,
    output_dir: Path,
) -> BacktestRun:
    if strategy_name not in STRATEGIES:
        raise ValueError(
            f"Unknown strategy {strategy_name!r}. Options: {', '.join(STRATEGIES)}"
        )
    strategy_cls = STRATEGIES[strategy_name]

    if data_source == "yahoo" and strategy_name not in YAHOO_COMPATIBLE:
        raise ValueError(
            f"{strategy_name} uses sub-daily bars; Yahoo is daily-only. "
            f"Use --data-source polygon / alpaca / oanda instead once wired up."
        )
    if data_source not in DATA_SOURCES:
        raise ValueError(
            f"Data source {data_source!r} not wired up yet. "
            f"Supported: {list(DATA_SOURCES)}"
        )
    datasource_cls = DATA_SOURCES[data_source]

    output_dir.mkdir(parents=True, exist_ok=True)
    slug = f"{strategy_name}_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}"
    trades_file = str(output_dir / f"{slug}_trades.csv")
    tearsheet_file = str(output_dir / f"{slug}_tearsheet.html")

    log.info(
        "starting backtest: strategy=%s data=%s range=%s..%s budget=$%.0f",
        strategy_name,
        data_source,
        start,
        end,
        budget,
    )

    start_dt = datetime.combine(start, datetime.min.time())
    end_dt = datetime.combine(end, datetime.min.time())

    result = strategy_cls.backtest(
        datasource_cls,
        backtesting_start=start_dt,
        backtesting_end=end_dt,
        budget=budget,
        show_plot=False,
        show_tearsheet=False,
        save_tearsheet=True,
        tearsheet_file=tearsheet_file,
        trades_file=trades_file,
        show_progress_bar=False,
        quiet_logs=True,
    )

    return _persist_run(
        strategy_name=strategy_name,
        data_source=data_source,
        start=start,
        end=end,
        budget=budget,
        result=result,
        trades_file=trades_file,
        tearsheet_file=tearsheet_file,
    )


def _persist_run(
    *,
    strategy_name: str,
    data_source: str,
    start: date,
    end: date,
    budget: float,
    result,
    trades_file: str,
    tearsheet_file: str,
) -> BacktestRun:
    """Extract metrics from Lumibot's result + write a BacktestRun row."""
    # Lumibot returns (result_dict, strategy_obj) or just a dict depending on
    # the version / config. Be defensive.
    metrics: dict = {}
    if isinstance(result, tuple) and len(result) >= 1 and isinstance(result[0], dict):
        metrics = result[0]
    elif isinstance(result, dict):
        metrics = result

    final_value = metrics.get("portfolio_value") or metrics.get("final_value")
    total_return = metrics.get("total_return")
    sharpe = metrics.get("sharpe") or metrics.get("sharpe_ratio")
    max_dd_raw = metrics.get("max_drawdown") or metrics.get("max_drawdown_pct")
    # Lumibot returns max_drawdown as {"drawdown": float, "date": Timestamp}
    if isinstance(max_dd_raw, dict):
        max_dd = max_dd_raw.get("drawdown")
    else:
        max_dd = max_dd_raw
    trade_count = int(
        metrics.get("total_trades")
        or metrics.get("num_trades")
        or _count_trades_from_parquet(trades_file.replace(".csv", ".parquet"))
        or 0
    )
    win_rate = metrics.get("win_rate")

    with get_session() as s:
        row = BacktestRun(
            strategy_name=strategy_name,
            data_source=data_source,
            start_date=start,
            end_date=end,
            budget=Decimal(str(budget)),
            final_value=_dec(final_value),
            total_return_pct=_dec(total_return, scale="0.0001"),
            trade_count=trade_count,
            win_rate=_dec(win_rate, scale="0.0001"),
            sharpe=_dec(sharpe, scale="0.0001"),
            max_drawdown_pct=_dec(max_dd, scale="0.0001"),
            parameters_json=json.dumps({k: v for k, v in metrics.items() if _is_json_safe(v)}),
            trades_csv_path=trades_file if os.path.exists(trades_file) else None,
            tearsheet_path=tearsheet_file if os.path.exists(tearsheet_file) else None,
        )
        s.add(row)
        s.flush()
        s.refresh(row)
        s.expunge(row)

    log.info(
        "backtest done: %s final=$%s return=%s trades=%d sharpe=%s maxDD=%s",
        strategy_name,
        final_value,
        total_return,
        trade_count,
        sharpe,
        max_dd,
    )
    log.info("  trades CSV : %s", trades_file)
    log.info("  tearsheet  : %s", tearsheet_file)
    return row


def _count_trades_from_parquet(path: str) -> int:
    """Lumibot writes a trade-events parquet. Count buy-side events as trades
    (each round-trip = one buy + at least one sell so buys ≈ round-trips)."""
    if not os.path.exists(path):
        return 0
    try:
        import pandas as pd

        df = pd.read_parquet(path)
        if "side" in df.columns:
            return int((df["side"].astype(str).str.lower() == "buy").sum())
        return len(df)
    except Exception as e:
        log.warning("could not read trade-events parquet %s: %s", path, e)
        return 0


def _dec(value, scale: str = "0.01") -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value)).quantize(Decimal(scale))
    except Exception:
        return None


def _is_json_safe(v) -> bool:
    return isinstance(v, (int, float, str, bool, type(None)))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--strategy", required=True, choices=list(STRATEGIES))
    ap.add_argument("--start", required=True, help="YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD")
    ap.add_argument("--budget", type=float, default=100_000.0)
    ap.add_argument("--data-source", default="yahoo", choices=list(DATA_SOURCES))
    ap.add_argument("--output-dir", default="logs/backtests")
    args = ap.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()
    run(args.strategy, start, end, args.budget, args.data_source, Path(args.output_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
