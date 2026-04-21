"""Entrypoint: Strategy 6 — BTC BB 4H on Alpaca crypto paper."""

from run._common import make_alpaca_broker, run_single
from trading_bot.strategies.bb_btc_4h import BBBTC4H


def main() -> None:
    # Alpaca broker scoped to 24/7 so Lumibot doesn't sleep BTC at NYSE close.
    run_single(BBBTC4H, make_alpaca_broker(paper=True, market="24/7"))


if __name__ == "__main__":
    main()
