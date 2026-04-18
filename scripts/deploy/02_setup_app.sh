#!/usr/bin/env bash
# 02_setup_app.sh — set up the trading bot application on a bootstrapped droplet.
#
# Run AS THE SERVICE USER (trading_bot), not root:
#
#     sudo -iu trading_bot
#     cd /opt/trading_bot/app         # where you cloned the repo
#     bash scripts/deploy/02_setup_app.sh
#
# Expects 01_bootstrap.sh to have run first. Assumes the repo is checked
# out at $PWD and a `.env` file exists here with at minimum:
#   ALPACA_API_KEY / ALPACA_API_SECRET
#   DATABASE_URL (from ~/.db_url — append it if you haven't)

set -euo pipefail

APP_HOME="/opt/trading_bot"
APP_DIR="$(pwd)"

if [[ "$(whoami)" != "trading_bot" ]]; then
    echo "run this as the trading_bot user:  sudo -iu trading_bot"
    exit 1
fi

if [[ ! -f "$APP_DIR/.env" ]]; then
    echo ".env not found in $APP_DIR. copy your local .env here and rerun:"
    echo "    scp local/.env trading_bot@<host>:$APP_DIR/.env"
    exit 1
fi

echo "==> 1/5 python venv + install"
if [[ ! -d "$APP_DIR/.venv" ]]; then
    python3 -m venv "$APP_DIR/.venv"
fi
"$APP_DIR/.venv/bin/pip" install --upgrade pip
"$APP_DIR/.venv/bin/pip" install -e '.[dev]'

echo "==> 2/5 alembic migrations"
cd "$APP_DIR"
"$APP_DIR/.venv/bin/alembic" upgrade head

echo "==> 3/5 seed 6 paper accounts (idempotent)"
"$APP_DIR/.venv/bin/python" scripts/init_db.py

echo "==> 4/5 health check"
"$APP_DIR/.venv/bin/python" scripts/check_setup.py || true

echo "==> 5/5 pm2 launch + persist"
# Start all strategies + dashboard via the ecosystem file.
# pm2 will read ecosystem.config.js in the project root.
pm2 delete all 2>/dev/null || true
pm2 start ecosystem.config.js
pm2 save

cat <<EOF

=============================================================
  app setup complete.

  process list  : pm2 list
  tail logs     : pm2 logs
  restart one   : pm2 restart <name>
  restart all   : pm2 restart all

  dashboard     : http://<droplet-ip>:8501

  next:
    * ufw is on — only 22 + 8501 are open.
      To expose the dashboard with HTTPS:
        sudo apt install -y nginx certbot python3-certbot-nginx
        sudo certbot --nginx -d <your-subdomain>
    * add a daily cron for scripts/fetch_news_calendar.py
    * add a daily cron for scripts/nightly_analysis.py
=============================================================
EOF
