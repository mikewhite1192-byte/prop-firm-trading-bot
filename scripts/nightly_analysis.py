"""Nightly learning job.

Runs after the last strategy closes for the day:
  1. Snapshots per-strategy metrics (30d / 90d / all-time) to
     strategy_performance_daily.
  2. Computes attribution slices (regime, hour, day-of-week, VIX bucket)
     and logs highlights.
  3. Applies the spec §9 culling framework — month-3 flag/kill, month-6
     Sharpe rank, promotion verdict — and prints a decision report.

Human-in-the-loop: the job NEVER flips accounts.status automatically.
It surfaces verdicts; the operator promotes / retires strategies by
hand after reviewing the output.

Intended cron cadence: once daily at ~23:55 UTC.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timezone

from trading_bot.learning import (
    annotate_recent_trades,
    attribute_by_day_of_week,
    attribute_by_hour,
    attribute_by_regime,
    attribute_by_vix_bucket,
    month_3_decision,
    month_6_rank,
    promotion_decision,
    snapshot_all,
)

logging.basicConfig(
    level="INFO",
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("nightly_analysis")


STRATEGIES: dict[str, str] = {
    "RSI2_SPY": "Alpaca_Paper",
    "GAPFILL_SPY": "Alpaca_Paper",
    "BBZ_EURUSD": "OANDA_Demo",
    "VWAP_SIGMA_ES": "Tradovate_Sim",
    "TINYGAP_ES": "Tradovate_Sim",
    "BB_BTC_4H": "Alpaca_Paper",
}


def _print_metrics_table(metrics) -> None:
    log.info(
        "%-16s %-14s %-7s %7s %8s %8s %8s %8s %8s %8s",
        "strategy",
        "firm",
        "window",
        "trades",
        "win_rt",
        "sharpe",
        "sortino",
        "pf",
        "maxDD",
        "expect",
    )
    for m in metrics:
        window = "all" if m.window_days == 0 else f"{m.window_days}d"
        fmt = lambda v, pct=False, default="—": (
            default
            if v is None
            else (f"{v:.2%}" if pct else f"{v:.2f}")
        )
        log.info(
            "%-16s %-14s %-7s %7d %8s %8s %8s %8s %8s %8s",
            m.strategy_name,
            m.firm,
            window,
            m.trade_count,
            fmt(m.win_rate, pct=True),
            fmt(m.sharpe),
            fmt(m.sortino),
            fmt(m.profit_factor),
            fmt(m.max_drawdown_pct, pct=True),
            fmt(m.expectancy),
        )


def _print_attribution(strategy: str) -> None:
    log.info("\n--- %s attribution ---", strategy)
    for label, df in (
        ("regime", attribute_by_regime(strategy, window_days=90)),
        ("hour", attribute_by_hour(strategy, window_days=90)),
        ("day_of_week", attribute_by_day_of_week(strategy, window_days=90)),
        ("vix_bucket", attribute_by_vix_bucket(strategy, window_days=90)),
    ):
        if df.empty:
            log.info("  %s: no trades", label)
        else:
            log.info("  %s:\n%s", label, df.to_string())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--phase", choices=["month3", "month6", "auto"], default="auto")
    ap.add_argument("--skip-attribution", action="store_true")
    ap.add_argument(
        "--llm-post-mortem",
        action="store_true",
        help="Generate LLM post-mortems for trades closed in the last 24h "
        "(requires ANTHROPIC_API_KEY and the [llm] extra installed).",
    )
    args = ap.parse_args()

    as_of = datetime.now(timezone.utc).date()
    log.info("snapshotting %d strategies as of %s", len(STRATEGIES), as_of)
    metrics = snapshot_all(list(STRATEGIES.keys()), firms=STRATEGIES, as_of=as_of)

    all_time = [m for m in metrics if m.window_days == 0]
    _print_metrics_table(all_time)

    if not args.skip_attribution:
        for strat in STRATEGIES:
            _print_attribution(strat)

    log.info("\n--- month-3 verdicts ---")
    for m in all_time:
        verdict = month_3_decision(m)
        log.info("  %-16s %-6s %s", verdict.strategy_name, verdict.verdict.value, verdict.reason)

    log.info("\n--- month-6 Sharpe rank ---")
    for v in month_6_rank(all_time):
        log.info("  #%d %-16s %s", v.rank or 0, v.strategy_name, v.reason)

    log.info("\n--- promotion verdicts (Sharpe>1 / WR>55%% / DD<5%%) ---")
    for m in all_time:
        v = promotion_decision(m)
        log.info("  %-16s %-7s %s", v.strategy_name, v.verdict.value, v.reason)

    if args.llm_post_mortem:
        log.info("\n--- LLM post-mortems (last 24h) ---")
        written = annotate_recent_trades(lookback_hours=24)
        log.info("  %d trades annotated", written)

    return 0


if __name__ == "__main__":
    sys.exit(main())
