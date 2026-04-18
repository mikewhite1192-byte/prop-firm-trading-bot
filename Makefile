.PHONY: help install test lint fmt clean-apple check setup news dashboard run-% db-upgrade db-downgrade

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
	@echo "  dashboard     streamlit on :8501"
	@echo "  run-rsi2      launch one strategy (also: run-gapfill, run-bbz, run-vwap,"
	@echo "                run-tinygap, run-bbbtc)"
	@echo "  db-upgrade    alembic upgrade head"
	@echo "  db-downgrade  alembic downgrade -1"

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
