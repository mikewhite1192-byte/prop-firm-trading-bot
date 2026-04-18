"""Entrypoint: Strategy 2 — Gap fill fade on SPY / Alpaca paper."""

from run._common import make_alpaca_broker, run_single
from trading_bot.strategies.gap_fill_spy import GapFillSPY


def main() -> None:
    run_single(GapFillSPY, make_alpaca_broker(paper=True))


if __name__ == "__main__":
    main()
