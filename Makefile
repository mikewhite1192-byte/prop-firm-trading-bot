.PHONY: help install test lint fmt clean-apple check setup news dashboard backtest-rsi2 nightly nightly-with-llm run-% db-upgrade db-downgrade deploy-sync deploy-logs deploy-restart deploy-status

PY := .venv/bin/python
PIP := .venv/bin/pip

help:
	@echo "Targets:"
	@echo "  install       create .venv and install project + dev deps"
	@echo "  test          pytest"
	@echo "  lint          ruff + mypy"
	@echo "  fmt           ruff format"
	@echo "  check         setup diagnostic (env + deps + DB)"
	@echo "  setup         install + db-upgrade + seed accounts"
	@echo "  clean-apple   remove AppleDouble ._* files from venv + tree"
	@echo "  news          run scripts/fetch_news_calendar.py (--next for next week too)"
	@echo "  nightly       scripts/nightly_analysis.py"
	@echo "  nightly-with-llm  same + LLM post-mortems (needs ANTHROPIC_API_KEY)"
	@echo "  backtest-rsi2 backtest RSI2_SPY (2024-01-01 through 2026-01-01, Yahoo)"
	@echo "  dashboard     streamlit on :8501"
	@echo "  run-rsi2      launch one strategy (also: run-gapfill, run-bbz, run-vwap,"
	@echo "                run-tinygap, run-bbbtc)"
	@echo "  db-upgrade    alembic upgrade head"
	@echo "  db-downgrade  alembic downgrade -1"
	@echo ""
	@echo "Deploy to DigitalOcean / any Ubuntu VPS (set HOST=user@ip):"
	@echo "  deploy-sync     rsync code + pip install + alembic + pm2 reload"
	@echo "  deploy-status   pm2 list on the droplet"
	@echo "  deploy-logs     tail pm2 logs (all processes)"
	@echo "  deploy-restart  restart all pm2 processes on the droplet"

install:
	python3 -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install -e '.[dev]'

test:
	$(PY) -m pytest tests/ -v

lint:
	.venv/bin/ruff check src tests run scripts
	.venv/bin/mypy --ignore-missing-imports src/trading_bot || true

fmt:
	.venv/bin/ruff format src tests run scripts

clean-apple:
	find . -name '._*' -delete 2>/dev/null || true
	@echo "cleaned"

check:
	$(PY) scripts/check_setup.py

setup: install db-upgrade
	$(PY) scripts/init_db.py

news:
	$(PY) scripts/fetch_news_calendar.py --next

nightly:
	$(PY) scripts/nightly_analysis.py

nightly-with-llm:
	$(PY) scripts/nightly_analysis.py --llm-post-mortem

backtest-rsi2:
	$(PY) scripts/backtest.py --strategy RSI2_SPY --start 2024-01-01 --end 2026-01-01

dashboard:
	.venv/bin/streamlit run src/trading_bot/dashboard/app.py --server.port 8501

db-upgrade:
	.venv/bin/alembic upgrade head

db-downgrade:
	.venv/bin/alembic downgrade -1

run-rsi2:
	$(PY) run/run_rsi2_spy.py

run-gapfill:
	$(PY) run/run_gap_fill_spy.py

run-bbz:
	$(PY) run/run_bb_zscore_eurusd.py

run-vwap:
	$(PY) run/run_vwap_sigma_es.py

run-tinygap:
	$(PY) run/run_tiny_gap_es.py

run-bbbtc:
	$(PY) run/run_bb_btc_4h.py

# ---- deploy ----

HOST ?=

deploy-sync:
	@test -n "$(HOST)" || (echo "set HOST=user@ip — e.g. make deploy-sync HOST=trading_bot@165.227.127.162"; exit 1)
	bash scripts/deploy/sync.sh $(HOST)

deploy-status:
	@test -n "$(HOST)" || (echo "set HOST=user@ip"; exit 1)
	ssh $(HOST) "pm2 list"

deploy-logs:
	@test -n "$(HOST)" || (echo "set HOST=user@ip"; exit 1)
	ssh $(HOST) "pm2 logs --lines 100"

deploy-restart:
	@test -n "$(HOST)" || (echo "set HOST=user@ip"; exit 1)
	ssh $(HOST) "pm2 restart all"
