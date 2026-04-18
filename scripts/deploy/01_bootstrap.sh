#!/usr/bin/env bash
# 01_bootstrap.sh — provision a fresh Ubuntu 24.04 droplet for the trading bot.
#
# Run this ONCE on a brand new droplet as root (or with sudo):
#
#     ssh root@<droplet-ip>
#     curl -fsSL https://raw.githubusercontent.com/<your-fork>/trading_bot/master/scripts/deploy/01_bootstrap.sh | bash
#
# Or (safer — read the script first):
#
#     scp scripts/deploy/01_bootstrap.sh root@<droplet-ip>:/root/
#     ssh root@<droplet-ip> 'bash /root/01_bootstrap.sh'
#
# What it does:
#   1. System hardening: non-root user, SSH-key login, ufw firewall, fail2ban.
#   2. Installs Python 3.11+, Node.js 20 (for pm2), Postgres 15, nginx (optional).
#   3. Creates the `trading_bot` service user, Postgres DB + role.
#   4. Installs pm2 globally and wires it to survive reboots.
#
# It does NOT clone the repo or run the app — that's step 02.

set -euo pipefail

APP_USER="trading_bot"
APP_HOME="/opt/trading_bot"
DB_NAME="trading_bot"
DB_USER="trading_bot"
DB_PASS="$(openssl rand -base64 24 | tr -d '/+=' | head -c 24)"
NODE_MAJOR="20"

echo "==> 1/8 apt update + base packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get upgrade -y
apt-get install -y \
    curl ca-certificates gnupg build-essential \
    git ufw fail2ban unattended-upgrades \
    software-properties-common tzdata \
    libssl-dev libffi-dev zlib1g-dev libbz2-dev \
    libsqlite3-dev libreadline-dev liblzma-dev

echo "==> 2/8 python 3.11+"
# Ubuntu 24.04 ships Python 3.12 by default. Good enough.
apt-get install -y python3 python3-venv python3-pip python3-dev
PY_VERSION="$(python3 -V | awk '{print $2}')"
echo "    python $PY_VERSION"

echo "==> 3/8 postgres 15"
apt-get install -y postgresql postgresql-contrib
systemctl enable --now postgresql

echo "==> 4/8 node.js $NODE_MAJOR + pm2"
curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | bash -
apt-get install -y nodejs
npm install -g pm2

echo "==> 5/8 create service user '$APP_USER'"
if ! id "$APP_USER" &>/dev/null; then
    useradd --system --create-home --shell /bin/bash --home-dir "$APP_HOME" "$APP_USER"
    echo "    created $APP_USER with home $APP_HOME"
else
    echo "    $APP_USER already exists, skipping"
fi
install -d -o "$APP_USER" -g "$APP_USER" "$APP_HOME/logs" "$APP_HOME/data"

echo "==> 6/8 create postgres DB + role"
if sudo -iu postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'" | grep -q 1; then
    echo "    role $DB_USER already exists, skipping"
else
    sudo -iu postgres psql -c "CREATE ROLE $DB_USER WITH LOGIN PASSWORD '$DB_PASS';"
    sudo -iu postgres psql -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;"
    sudo -iu postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;"
    echo "    created DB $DB_NAME owned by $DB_USER"
fi

# Write the generated password to the service-user's home so step 02 can pick it up.
echo "DATABASE_URL=postgresql+psycopg://$DB_USER:$DB_PASS@localhost:5432/$DB_NAME" \
    > "$APP_HOME/.db_url"
chown "$APP_USER:$APP_USER" "$APP_HOME/.db_url"
chmod 600 "$APP_HOME/.db_url"

echo "==> 7/8 firewall + fail2ban"
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 8501/tcp comment 'streamlit dashboard'
ufw --force enable
systemctl enable --now fail2ban

echo "==> 8/8 pm2 resurrection on reboot"
# Tell pm2 to start at boot under the service user. This writes a systemd unit.
sudo -u "$APP_USER" bash -c "pm2 startup systemd -u $APP_USER --hp $APP_HOME | tail -1" \
    | bash || true

cat <<EOF

=============================================================
  bootstrap complete.

  postgres DB  : $DB_NAME
  postgres user: $DB_USER
  DATABASE_URL : saved to $APP_HOME/.db_url

  next steps (on this droplet):
    1. clone the repo as $APP_USER:
         sudo -iu $APP_USER
         git clone <your-repo-url> $APP_HOME/app
         cd $APP_HOME/app
         scp/copy your .env into $APP_HOME/app/.env
         cat $APP_HOME/.db_url >> $APP_HOME/app/.env
    2. run scripts/deploy/02_setup_app.sh

  dashboard will be reachable on:
    http://<droplet-ip>:8501
  (consider putting nginx + TLS in front before making public)
=============================================================
EOF
