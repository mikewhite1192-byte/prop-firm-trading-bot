#!/usr/bin/env bash
# sync.sh — rsync local code to the droplet + reload pm2.
#
# Usage:
#     bash scripts/deploy/sync.sh <user@host>
#
# Example:
#     bash scripts/deploy/sync.sh trading_bot@165.227.127.162
#
# What it syncs:
#   * src/, run/, scripts/, alembic/, ecosystem.config.js, pyproject.toml,
#     Makefile, tests/, .streamlit/
#
# What it does NOT sync:
#   * .env   (secrets stay on the droplet; edit via `ssh ... nano .env`)
#   * .venv  (droplet has its own)
#   * logs/, data/, trading_bot.db, tech_spec.docx, .git, __pycache__

set -euo pipefail

HOST="${1:-}"
if [[ -z "$HOST" ]]; then
    echo "usage: $0 <user@host>  (e.g. trading_bot@165.227.127.162)"
    exit 1
fi

REMOTE_DIR="/opt/trading_bot/app"

echo "==> rsync → $HOST:$REMOTE_DIR"
rsync -avz --delete \
    --exclude '.env' \
    --exclude '.venv/' \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude '.pytest_cache/' \
    --exclude '.mypy_cache/' \
    --exclude '.ruff_cache/' \
    --exclude '.git/' \
    --exclude 'logs/' \
    --exclude 'data/' \
    --exclude 'trading_bot.db*' \
    --exclude 'tech_spec.docx' \
    --exclude '._*' \
    --exclude '.DS_Store' \
    ./ \
    "$HOST:$REMOTE_DIR/"

echo "==> pip install -e '.' (if pyproject changed)"
ssh "$HOST" "cd $REMOTE_DIR && .venv/bin/pip install -e '.[dev]' 2>&1 | tail -3"

echo "==> alembic upgrade head"
ssh "$HOST" "cd $REMOTE_DIR && .venv/bin/alembic upgrade head"

echo "==> pm2 reload"
ssh "$HOST" "pm2 reload ecosystem.config.js --cwd $REMOTE_DIR"

echo "==> done. pm2 status:"
ssh "$HOST" "pm2 list"
