"""Entrypoint: Strategy 3 — BB z-score EUR/USD on OANDA demo."""

from run._common import make_oanda_broker, run_single
from trading_bot.strategies.bb_zscore_eurusd import BBZScoreEURUSD


def main() -> None:
    run_single(BBZScoreEURUSD, make_oanda_broker())


if __name__ == "__main__":
    main()
