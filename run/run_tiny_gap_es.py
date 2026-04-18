"""Entrypoint: Strategy 5 — Tiny gap fill ES on Tradovate sim."""

from run._common import make_tradovate_broker, run_single
from trading_bot.strategies.tiny_gap_es import TinyGapES


def main() -> None:
    run_single(TinyGapES, make_tradovate_broker())


if __name__ == "__main__":
    main()
