# Deploying to DigitalOcean (or any Ubuntu 24.04 VPS)

End-to-end walkthrough: fresh droplet → live paper trading + dashboard online. Takes ~20 min once the droplet exists.

## 0. Prereqs

- A DigitalOcean droplet (or any Ubuntu 24.04 VPS). Minimum spec per the spec: **4 vCPU, 8 GB RAM**. A **$24/mo 2-vCPU / 4 GB** droplet is workable for paper testing; bump to the spec-recommended 4-vCPU when you go funded.
- Your SSH public key added to the droplet at provision time (DO asks during droplet creation).
- Alpaca / OANDA / Tradovate credentials in your local `.env`.
- Everything passing `make check` + `make test` locally.

## 1. Bootstrap the droplet (once, as root)

```bash
ssh root@<droplet-ip>
# paste + run the bootstrap script:
bash <(curl -sSL https://raw.githubusercontent.com/<your-fork>/trading_bot/master/scripts/deploy/01_bootstrap.sh)
```

Or safer — scp it and read first:
```bash
scp scripts/deploy/01_bootstrap.sh root@<ip>:/root/
ssh root@<ip> 'bash /root/01_bootstrap.sh'
```

This installs Python, Node+pm2, Postgres 15, creates the `trading_bot` service user, generates a Postgres role + DB with a random password (saved to `/opt/trading_bot/.db_url`), enables `ufw` + `fail2ban`, and wires pm2 to start on reboot.

## 2. Push the code to the droplet (from your Mac)

```bash
# one-time: clone via the trading_bot user
ssh trading_bot@<ip>
mkdir -p /opt/trading_bot/app && cd /opt/trading_bot/app
git clone <your-repo-url> .
exit
```

or rsync if you haven't published the repo yet:

```bash
# from your laptop
rsync -avz --exclude '.env' --exclude '.venv/' --exclude '.git/' --exclude 'logs/' \
  ./ trading_bot@<ip>:/opt/trading_bot/app/
```

## 3. Put `.env` on the droplet

The bootstrap wrote a `DATABASE_URL` to `/opt/trading_bot/.db_url`. Append that line to your `.env` as you move it:

```bash
# on your Mac
scp .env trading_bot@<ip>:/opt/trading_bot/app/.env

# on the droplet
ssh trading_bot@<ip>
cat /opt/trading_bot/.db_url >> /opt/trading_bot/app/.env
```

Open `.env` once and remove the SQLite `DATABASE_URL` line — you want only the Postgres one now.

## 4. Run the app setup script (as the trading_bot user)

```bash
sudo -iu trading_bot
cd /opt/trading_bot/app
bash scripts/deploy/02_setup_app.sh
```

This creates the venv, installs deps, runs migrations against Postgres, seeds the 6 accounts, runs the setup diagnostic, and starts all 6 strategy processes + the dashboard via pm2.

## 5. Install the scheduled jobs

```bash
bash scripts/deploy/cron.sh
```

Installs two cron entries:
- **06:00 UTC**: pull the news calendar (ForexFactory).
- **23:55 UTC**: nightly analysis (per-strategy metrics snapshot + culling verdicts).

## 6. Verify + visit the dashboard

```bash
pm2 list          # should show 7 processes online (6 strategies + dashboard)
pm2 logs          # tail live output
```

Visit `http://<droplet-ip>:8501` — your dashboard.

**Before exposing publicly**, put it behind nginx + Let's Encrypt:

```bash
sudo apt install -y nginx certbot python3-certbot-nginx
sudo tee /etc/nginx/sites-available/trading_bot <<'NGINX'
server {
    listen 80;
    server_name <your-domain>;
    location / { proxy_pass http://127.0.0.1:8501; proxy_set_header Host $host; proxy_set_header X-Real-IP $remote_addr; proxy_http_version 1.1; proxy_set_header Upgrade $http_upgrade; proxy_set_header Connection "upgrade"; }
}
NGINX
sudo ln -sf /etc/nginx/sites-available/trading_bot /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d <your-domain>
```

Then re-run `ufw deny 8501` and keep only 80/443 open.

## Iteration workflow (after initial deploy)

Once everything's up, iterating from your Mac is one command:

```bash
make deploy-sync HOST=trading_bot@<ip>
```

That rsyncs the code (skipping `.env`, `.venv`, logs, DB, etc.), reinstalls if `pyproject.toml` changed, runs any new Alembic migrations, and hot-reloads pm2.

## Pm2 cheat sheet

```bash
pm2 list                      # status of all processes
pm2 logs                      # tail everything
pm2 logs rsi2_spy             # tail one
pm2 restart rsi2_spy          # restart one
pm2 reload ecosystem.config.js  # zero-downtime reload after code change
pm2 delete all                # stop + remove everything
pm2 save                      # persist current process list for reboot
pm2 monit                     # htop-style live view
```

## If something goes wrong

| Symptom | Fix |
|---|---|
| pm2 shows `errored` state | `pm2 logs <name>` to see the traceback. Most often: a strategy can't find its broker credentials in `.env`. |
| Strategy loops rapidly on boot | Usually a missing DB seed. `python scripts/init_db.py`. |
| Dashboard shows "DB unreachable" | `systemctl status postgresql` + check `DATABASE_URL` in `.env` matches the creds in `/opt/trading_bot/.db_url`. |
| `make deploy-sync` fails on permissions | SSH in and run `chown -R trading_bot:trading_bot /opt/trading_bot/app`. |
| Dashboard public but no HTTPS | Step 6's nginx + certbot block. Don't run funded accounts over plain HTTP. |

## Backups (do this before going funded)

```bash
# on the droplet, add a daily cron:
sudo tee /etc/cron.daily/pgbackup <<'SH'
#!/bin/bash
set -e
mkdir -p /var/backups/trading_bot
DATE=$(date +%Y%m%d)
sudo -u postgres pg_dump trading_bot | gzip > /var/backups/trading_bot/trading_bot-$DATE.sql.gz
find /var/backups/trading_bot -name '*.sql.gz' -mtime +30 -delete
SH
sudo chmod +x /etc/cron.daily/pgbackup
```

Optionally mirror to DO Spaces or S3 via `s3cmd` / `rclone`.
