"""Entrypoint: Strategy 1 — RSI(2) SPY on Alpaca paper."""

from run._common import make_alpaca_broker, run_single
from trading_bot.strategies.rsi2_spy import RSI2SPY


def main() -> None:
    run_single(RSI2SPY, make_alpaca_broker(paper=True))


if __name__ == "__main__":
    main()
