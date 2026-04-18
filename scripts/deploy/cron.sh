#!/usr/bin/env bash
# cron.sh — install the scheduled jobs (news scraper + nightly analysis).
#
# Run on the droplet as the trading_bot user:
#     sudo -iu trading_bot bash /opt/trading_bot/app/scripts/deploy/cron.sh

set -euo pipefail

APP_DIR="/opt/trading_bot/app"
PY="$APP_DIR/.venv/bin/python"

CRON=$(cat <<EOF
# news calendar — once daily at 06:00 UTC
0 6 * * * cd $APP_DIR && $PY scripts/fetch_news_calendar.py --next >> $APP_DIR/logs/cron_news.log 2>&1

# nightly performance snapshot + culling verdicts at 23:55 UTC
55 23 * * * cd $APP_DIR && $PY scripts/nightly_analysis.py >> $APP_DIR/logs/cron_nightly.log 2>&1
EOF
)

# Merge with any existing crontab without clobbering manual entries.
{ crontab -l 2>/dev/null | grep -vF "fetch_news_calendar.py" | grep -vF "nightly_analysis.py"; echo "$CRON"; } | crontab -

echo "installed. current crontab:"
crontab -l
