"""Spec §9 paper-trading culling framework.

Month 3: kill any paper account down more than 8% with no recovery,
         flag any with Sharpe < 0.5.
Month 6: rank all 6 by Sharpe. Top 3 advance to challenge consideration.
         Strategies with Sharpe > 1.0 AND win_rate > 55% AND max_dd < 5%
         are promoted.

Used by scripts/nightly_analysis.py on the appropriate cadence. Never
flips ``accounts.status`` automatically — surfaces verdicts for human
review so the operator stays in the loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from trading_bot.learning.performance import PerformanceMetrics


class CullVerdict(str, Enum):
    KEEP = "KEEP"
    FLAG = "FLAG"
    KILL = "KILL"
    PROMOTE = "PROMOTE"


@dataclass(slots=True)
class CullingVerdict:
    strategy_name: str
    verdict: CullVerdict
    rank: int | None
    reason: str
    metrics: PerformanceMetrics


def month_3_decision(metrics: PerformanceMetrics) -> CullingVerdict:
    reasons: list[str] = []
    verdict = CullVerdict.KEEP

    if metrics.max_drawdown_pct is not None and metrics.max_drawdown_pct > 0.08:
        reasons.append(f"max DD {metrics.max_drawdown_pct:.2%} > 8%")
        verdict = CullVerdict.KILL

    if metrics.sharpe is not None and metrics.sharpe < 0.5:
        reasons.append(f"Sharpe {metrics.sharpe:.2f} < 0.5")
        if verdict == CullVerdict.KEEP:
            verdict = CullVerdict.FLAG

    if verdict == CullVerdict.KEEP:
        reasons.append("within month-3 tolerances")

    return CullingVerdict(
        strategy_name=metrics.strategy_name,
        verdict=verdict,
        rank=None,
        reason="; ".join(reasons),
        metrics=metrics,
    )


def month_6_rank(all_metrics: list[PerformanceMetrics]) -> list[CullingVerdict]:
    """Rank by Sharpe (not raw P&L — spec §9). Top 3 advance."""
    ranked = sorted(
        all_metrics,
        key=lambda m: (m.sharpe if m.sharpe is not None else -1e9),
        reverse=True,
    )
    out: list[CullingVerdict] = []
    for idx, m in enumerate(ranked, start=1):
        advance = idx <= 3 and (m.sharpe or 0) > 0
        verdict = CullVerdict.KEEP if advance else CullVerdict.FLAG
        reason = f"rank {idx} by Sharpe={m.sharpe:.2f}" if m.sharpe is not None else "no Sharpe data"
        if advance:
            reason += " — advance to challenge"
        out.append(
            CullingVerdict(
                strategy_name=m.strategy_name,
                verdict=verdict,
                rank=idx,
                reason=reason,
                metrics=m,
            )
        )
    return out


def promotion_decision(metrics: PerformanceMetrics) -> CullingVerdict:
    """Spec §9: Sharpe > 1.0, win_rate > 0.55, max_dd < 5%."""
    if metrics.trade_count < 20:
        return CullingVerdict(
            strategy_name=metrics.strategy_name,
            verdict=CullVerdict.FLAG,
            rank=None,
            reason=f"insufficient trades ({metrics.trade_count}) — need ≥20",
            metrics=metrics,
        )

    failures: list[str] = []
    if metrics.sharpe is None or metrics.sharpe <= 1.0:
        failures.append(f"Sharpe {metrics.sharpe}")
    if metrics.win_rate is None or metrics.win_rate <= 0.55:
        failures.append(f"win_rate {metrics.win_rate}")
    if metrics.max_drawdown_pct is None or metrics.max_drawdown_pct >= 0.05:
        failures.append(f"max_dd {metrics.max_drawdown_pct}")

    if not failures:
        return CullingVerdict(
            strategy_name=metrics.strategy_name,
            verdict=CullVerdict.PROMOTE,
            rank=None,
            reason=f"Sharpe={metrics.sharpe:.2f} WR={metrics.win_rate:.2%} "
            f"DD={metrics.max_drawdown_pct:.2%}",
            metrics=metrics,
        )
    return CullingVerdict(
        strategy_name=metrics.strategy_name,
        verdict=CullVerdict.KEEP,
        rank=None,
        reason="fails promotion: " + ", ".join(failures),
        metrics=metrics,
    )
