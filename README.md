# Trading Bot

Automated prop firm trading system running 6 concurrent mean-reversion strategies across paper, challenge, and funded accounts. See `tech_spec.docx` for the full specification.

## Project layout

```
src/trading_bot/
├── main.py               APScheduler entrypoint
├── config.py             env-driven settings (pydantic-settings)
├── db/                   SQLAlchemy models, session factory
├── brokers/              Alpaca / OANDA / Tradovate / Rithmic / MT5 adapters
├── strategies/           One module per strategy (6 total)
├── risk/                 Hard-coded prop firm rule enforcement
├── orchestrator/         Master controller coordinating all accounts
├── dashboard/            Streamlit real-time monitoring
├── notifications/        Telegram + email alerts
└── trade_log/            Trade persistence layer
alembic/                  DB migrations
scripts/                  Admin scripts (init_db, seed accounts, etc.)
tests/
```

## Phase 1 status (weeks 1-3)

- [x] Project scaffolding, deps, env template
- [x] DB schema (`trades`, `accounts`, `daily_summary`) + Alembic baseline
- [x] Broker connector base + Alpaca / OANDA / Tradovate auth wiring
- [x] APScheduler entrypoint with market-hours job registration
- [ ] End-to-end paper order round-trip per broker (next)

## Quickstart

```bash
# 1. Install
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'

# 2. Configure
cp .env.example .env   # fill in broker keys + DATABASE_URL

# 3. Bring up Postgres (locally or via Docker), then:
alembic upgrade head
python scripts/init_db.py   # seeds the 6 paper accounts

# 4. Run the orchestrator
trading-bot
```

## Operating modes

Every account has a `mode` of `PAPER`, `CHALLENGE`, or `FUNDED`. The risk engine applies a different rule set per mode — see `src/trading_bot/risk/rules.py`. Never bypass the risk engine; strategies submit intents, the engine approves or rejects.

## Credentials

All broker credentials live in `.env`, loaded via `pydantic-settings`. Never commit `.env`. Never hard-code keys.
