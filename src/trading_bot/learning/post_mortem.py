"""LLM-powered trade post-mortem.

For every closed trade, ask Claude for a two-sentence explanation of
what happened and why, given the trade's context. Persists the answer
into ``trades.notes`` so the dashboard shows it next to the trade.

Cost: roughly $0.001 per trade with Haiku 4.5 (input ~400 tokens,
output ~80 tokens). At 6 strategies × 5 trades/day ≈ 30 trades/day ≈
$0.03/day.

This module is optional. If ``anthropic`` isn't installed or
``ANTHROPIC_API_KEY`` isn't set, calls silently no-op so production
never depends on a secondary service.

Typical usage — call from ``scripts/nightly_analysis.py`` after closed
trades are aggregated:

    from trading_bot.learning.post_mortem import annotate_recent_trades
    annotate_recent_trades(lookback_hours=24)
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select

from trading_bot.db.models import Trade
from trading_bot.db.session import get_session

log = logging.getLogger(__name__)

POST_MORTEM_MODEL = "claude-haiku-4-5-20251001"
_POST_MORTEM_MARKER = "[post-mortem]"

_SYSTEM_PROMPT = (
    "You are a disciplined trading-strategy post-mortem analyst. "
    "Given a single closed trade and its context, output exactly two "
    "sentences. First sentence: what happened (direction, hold time, "
    "exit reason, P&L). Second sentence: one plausible reason why, "
    "grounded in the market regime / session / volatility given, and "
    "a suggestion for what to watch for next time. No hedging, no "
    "disclaimers, no trade advice — just concise analysis."
)


def _client():
    try:
        from anthropic import Anthropic
    except ImportError:
        return None
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    return Anthropic(api_key=api_key)


def _prompt_for(trade: Trade) -> str:
    hold = (
        (trade.exit_time - trade.entry_time).total_seconds() / 60
        if trade.exit_time and trade.entry_time
        else None
    )
    hold_str = f"{hold:.0f} min" if hold is not None else "unknown"
    return (
        f"Strategy: {trade.strategy_name}\n"
        f"Asset: {trade.asset}\n"
        f"Direction: {trade.direction.value}\n"
        f"Entry: {trade.entry_price} at {trade.entry_time}\n"
        f"Exit:  {trade.exit_price} at {trade.exit_time} "
        f"({trade.exit_reason.value if trade.exit_reason else 'unknown'})\n"
        f"Hold time: {hold_str}\n"
        f"P&L: {trade.pnl} ({trade.pnl_pct}%)\n"
        f"Regime at entry: {trade.market_regime.value if trade.market_regime else 'UNKNOWN'}\n"
        f"VIX at entry: {trade.vix_at_entry or 'n/a'}\n"
        f"Day/hour: {trade.day_of_week}/{trade.hour_of_entry}\n"
        f"Entry notes: {trade.notes or '(none)'}\n"
    )


def generate_trade_postmortem(trade: Trade) -> str | None:
    """Ask Claude for a two-sentence post-mortem. None if LLM unavailable."""
    client = _client()
    if client is None:
        return None
    try:
        resp = client.messages.create(
            model=POST_MORTEM_MODEL,
            max_tokens=200,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _prompt_for(trade)}],
        )
        # Concatenate text blocks from the response.
        text = "".join(
            block.text for block in resp.content if getattr(block, "type", None) == "text"
        ).strip()
        return text or None
    except Exception as e:
        log.warning("post-mortem call failed for trade %s: %s", trade.id, e)
        return None


def annotate_recent_trades(lookback_hours: int = 24, limit: int = 100) -> int:
    """Write post-mortems into notes for trades closed in the last N hours.

    Skips trades whose notes already contain the post-mortem marker so
    re-runs are idempotent. Returns the number of trades updated.
    """
    if _client() is None:
        log.info("LLM post-mortem skipped: anthropic not installed or ANTHROPIC_API_KEY unset")
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    written = 0
    with get_session() as s:
        candidates = (
            s.execute(
                select(Trade)
                .where(
                    Trade.exit_time.is_not(None),
                    Trade.pnl.is_not(None),
                    Trade.exit_time >= cutoff,
                    or_(Trade.notes.is_(None), ~Trade.notes.like(f"%{_POST_MORTEM_MARKER}%")),
                )
                .order_by(Trade.exit_time.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )

        for trade in candidates:
            commentary = generate_trade_postmortem(trade)
            if not commentary:
                continue
            existing = trade.notes or ""
            marker = f"\n\n{_POST_MORTEM_MARKER} {commentary}"
            trade.notes = (existing + marker).strip()
            written += 1

    log.info("post-mortems written for %d trades", written)
    return written
