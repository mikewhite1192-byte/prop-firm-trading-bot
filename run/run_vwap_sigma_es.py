"""Entrypoint: Strategy 4 — VWAP +/-2σ ES on Tradovate sim."""

from run._common import make_tradovate_broker, run_single
from trading_bot.strategies.vwap_sigma_es import VWAPSigmaES


def main() -> None:
    run_single(VWAPSigmaES, make_tradovate_broker())


if __name__ == "__main__":
    main()
