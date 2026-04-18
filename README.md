# Trading Bot

An automated algorithmic trading system that runs six mean-reversion strategies on paper accounts with the goal of passing prop firm challenges. Built on [Lumibot](https://github.com/Lumiwealth/lumibot) with a custom prop-firm risk engine layered on top. MIT licensed — see `LICENSE`.

> **Not financial advice.** Trading involves substantial risk. Backtest results do not guarantee future performance. This is a research codebase, not investment guidance.

**What's here:**
- 6 concurrent strategies: RSI(2) SPY, SPY gap fade, EUR/USD BB z-score, ES VWAP ±σ, ES tiny gap fill, BTC BB 4H
- Prop-firm-aware risk engine enforcing daily loss / drawdown / consistency / news-blackout / EOD-flat / HFT-cap rules per mode (Challenge vs Funded) and per firm (MyFundedFutures / Bulenox / FTMO)
- Postgres-backed trade log with regime / hour / VIX context for every trade
- Learning layer: Sharpe / Sortino / profit factor / attribution by regime, hour, DOW, VIX bucket, and spec §9 Month-3 / Month-6 culling verdicts
- Streamlit dashboard, Telegram + SMTP notifications, pm2 supervisor config
- 35 unit tests covering indicators, risk engine, and learning module

## Architecture

Lumibot's live-trader does not support multiple strategies in one process (explicit `NotImplementedError` in `lumibot.traders.Trader`), so each strategy runs in its own OS process. Cross-process coordination — news blackouts, consistency-rule tracking, cross-account hedging, global halt broadcast — lives in Postgres via `LISTEN/NOTIFY` and shared tables.

```
                               ┌──────────────┐
                               │  PostgreSQL  │  accounts, trades,
                               │   (shared    │  daily_summary,
                               │    state)    │  pub/sub channels
                               └──────┬───────┘
                                      │
     ┌──────────┬──────────┬──────────┼──────────┬──────────┬──────────┐
     ▼          ▼          ▼          ▼          ▼          ▼          ▼
  rsi2_spy  gap_fill   bb_zscore  vwap_sigma  tiny_gap   bb_btc_4h  dashboard
  (Alpaca)  (Alpaca)   (OANDA)    (Tradovate) (Tradovate)(Alpaca)   (Streamlit)
     │          │          │          │          │          │
     └──────────┴──────────┴──── RiskGatedStrategy ─────────┘
                                       │
                                 RiskEngine.evaluate
                                 (MODE_RULES + FIRM_RULES)
```

Every order path is:

```
strategy.on_trading_iteration()
  └─> self.propose_entry(asset, side, qty, entry, stop, tp?)
        └─> RiskEngine.evaluate(TradeIntent)          # prop-firm gate
              ├─ approved     -> self.create_order + self.submit_order
              └─ halt/reject  -> DB status update + notification + sell_all if hard-stop
```

## Layout

```
src/trading_bot/
├── strategies/               Lumibot Strategy subclasses
│   ├── base.py               RiskGatedStrategy — the risk gate
│   ├── rsi2_spy.py           #1 RSI(2) on SPY
│   ├── gap_fill_spy.py       #2 Gap fade on SPY
│   ├── bb_zscore_eurusd.py   #3 BB z-score EUR/USD  (OANDA)
│   ├── vwap_sigma_es.py      #4 VWAP ±2σ on ES     (Tradovate)
│   ├── tiny_gap_es.py        #5 Tiny gap fill ES   (Tradovate)
│   └── bb_btc_4h.py          #6 BTC BB 4H          (Alpaca crypto)
├── brokers/
│   ├── base_types.py         Framework-free OrderSide enum
│   └── oanda_lumibot.py      Custom Lumibot Broker (OANDA via oandapyV20)
├── risk/                     Mode + firm rule tables, stateless engine
├── db/                       SQLAlchemy models, session factory
├── shared_state/             Cross-process coordination (Postgres LISTEN/NOTIFY)
├── notifications/            Telegram + SMTP dispatcher
├── trade_log/                Trade persistence
├── dashboard/                Streamlit real-time monitor
└── config.py                 pydantic-settings .env loader

run/                          One entrypoint per strategy (pm2-managed)
alembic/                      DB migrations
scripts/init_db.py            Seeds the 6 paper accounts
ecosystem.config.js           pm2 process supervisor config
```

## Brokers in use

| Strategy | Broker | Library |
|---|---|---|
| #1 RSI2 SPY | Alpaca (paper) | `lumibot.brokers.Alpaca` (uses `alpaca-py`) |
| #2 Gap fade SPY | Alpaca (paper) | same |
| #3 BB z-score EUR/USD | OANDA (demo) | custom `OandaBroker` (Lumibot not native — `oandapyV20` under the hood) |
| #4 VWAP σ ES | Tradovate (sim) | `lumibot.brokers.Tradovate` |
| #5 Tiny gap ES | Tradovate (sim) | same |
| #6 BTC BB 4H | Alpaca crypto | `lumibot.brokers.Alpaca` |

Rithmic (Phase 5) and MetaTrader 5 / FTMO (Phase 5) stay out of the live loop for now; MT5/FTMO deploys as an MQL5 EA per the spec, not a Python client.

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'

cp .env.example .env
# Fill ALPACA_API_KEY, ALPACA_API_SECRET, OANDA_*, TRADOVATE_*, DATABASE_URL

alembic revision --autogenerate -m "baseline schema"
alembic upgrade head
python scripts/init_db.py                 # seed 6 paper accounts

# Dry-run one strategy manually
python run/run_rsi2_spy.py

# Run all 6 under pm2
npm i -g pm2   # if not already
pm2 start ecosystem.config.js
pm2 logs
```

Streamlit dashboard runs on port 8501 under the same pm2 config.

## Two operating modes

Every account has a `mode` of `PAPER`, `CHALLENGE`, or `FUNDED`. The risk engine reads a different rule set per mode — see `src/trading_bot/risk/rules.py`. Never bypass the risk engine; strategies submit intents via `propose_entry`, the engine approves, rejects, or halts.

| | Challenge | Funded |
|---|---|---|
| Max risk per trade | 0.75% | 0.5% |
| Daily loss halt / hard stop | 3% / 4% | 2% / 3% |
| Total drawdown warn / stop | 7% / 8% | 7% / 8% |
| Consistency rule | — | No day > 30% of total profit |
| News buffer | 30 min | 30 min (2 min on FTMO-funded) |

Firm-specific overlays (MyFundedFutures HFT cap, FTMO server-request cap, Bulenox 40% consistency) live in `risk/rules.py::FIRM_RULES`.

## Strategy references

Where code was ported from, not written from scratch:
- **#1 RSI(2) SPY**: Zhuo Kai Chen, [MQL5 article 17636](https://www.mql5.com/en/articles/17636)
- **#3 BB z-score EUR/USD**: [vsebastien3 MT5 EA](https://www.mql5.com/en/code/32695), [barabashkakvn RSI+BB](https://www.mql5.com/en/code/20705) — extended with z-score + H4 ADX + session filter
- **#4 VWAP**: VWAP indicator pattern from [eslazarev/vwap-backtrader](https://github.com/eslazarev/vwap-backtrader)
- **#6 BTC BB 4H**: logic adapted from [lhandal/crypto-trading-bot](https://github.com/lhandal/crypto-trading-bot) (FreqTrade, 310★), ported to Lumibot

Strategies #2 and #5 have no strong OSS reference and are implemented fresh per spec.

## Status

### Phase 1 — foundation (done)

- [x] Lumibot foundation + Alpaca/Tradovate native brokers
- [x] Custom OANDA Lumibot Broker (REST paths; streaming scaffolded)
- [x] RiskGatedStrategy base; RiskEngine with mode + firm rules
- [x] 6 strategy classes with real indicator math
- [x] Per-strategy run entrypoints + pm2 supervisor
- [x] Shared-state coordinator (Postgres pub/sub — halt broadcast, news blackout)
- [x] DB schema, Alembic, seed script
- [x] Streamlit dashboard
- [x] Telegram + SMTP notifications

### Phase 2 — teeth + telemetry (done)

- [x] Risk engine reads live balances via AccountSync; firm rules enforced (EOD, HFT cap, weekend flat)
- [x] Trade logger hooks persist every fill with regime / hour / VIX context
- [x] ForexFactory news calendar scraper
- [x] Indicator module (RSI / ATR / ADX / BB / VWAP) with 15 unit tests
- [x] Learning layer: Sharpe / Sortino / profit factor / attribution / spec §9 culling
- [x] Backtest harness (`scripts/backtest.py`) + `backtest_runs` persistence
- [x] Dry-run mode (`DRY_RUN=1`) for live-looking smoke tests with no orders
- [x] Optional LLM trade post-mortem via Anthropic Claude

### Phase 3 — before live paper

- [ ] End-to-end paper round-trip on Alpaca (needs keys + Postgres)
- [ ] Polygon / Databento wiring for intraday backtests of the 5 sub-daily strategies
- [ ] VIX live feed for stock-strategy context capture
- [ ] Crash-recovery reconciliation (DB ↔ broker state on restart)
- [ ] Cross-account hedging enforcement

### Phase 5+ (deferred per spec)

- [ ] Rithmic / NinjaTrader integration (async_rithmic)
- [ ] MetaTrader 5 / FTMO EA deployment
- [ ] Auto parameter tuning (Phase 6)

## Credentials + security

All broker credentials live in `.env`, loaded via `pydantic-settings`. `.env` is gitignored — never commit it. Never hard-code keys. `.env.example` documents the full set of expected variables.

If you fork this repo:
1. Copy `.env.example` to `.env`.
2. Add your own API keys — the repo ships no creds.
3. Before pushing any custom changes, `git grep -i 'key\|secret\|token'` to double-check you haven't accidentally inlined anything.

**No live funds in this repo.** All brokers default to paper / demo / simulator endpoints. You have to explicitly change `ALPACA_BASE_URL`, `OANDA_ENVIRONMENT=live`, and `TRADOVATE_ENVIRONMENT=live` to hit real money.

## Contributing

PRs welcome. Run `make test` and `make lint` before opening one. New strategies should subclass `RiskGatedStrategy` so the prop-firm risk engine applies automatically — see `src/trading_bot/strategies/rsi2_spy.py` as a reference.
