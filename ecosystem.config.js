// pm2 ecosystem — one process per strategy per Lumibot's concurrency model.
// Start with:  pm2 start ecosystem.config.js
// Monitor:    pm2 logs / pm2 monit
// Stop all:   pm2 delete all

const shared = {
  cwd: __dirname,
  interpreter: ".venv/bin/python",
  autorestart: true,
  max_restarts: 20,
  min_uptime: "30s",
  restart_delay: 5000,
  env: { PYTHONUNBUFFERED: "1", PYTHONPATH: __dirname },
  merge_logs: true,
};

// Daily kick at 9:25 ET Mon-Fri for NYSE strategies — Lumibot's
// sleep-until-market-open branch can hang overnight; restarting 5 min
// before the bell forces a clean re-init straight into market hours.
// Assumes system TZ is America/New_York.
const nyseDailyKick = { cron_restart: "25 9 * * 1-5" };

module.exports = {
  apps: [
    { name: "rsi2_spy",        script: "run/run_rsi2_spy.py",        ...shared, ...nyseDailyKick },
    { name: "gap_fill_spy",    script: "run/run_gap_fill_spy.py",    ...shared, ...nyseDailyKick },
    { name: "bb_zscore_eurusd",script: "run/run_bb_zscore_eurusd.py",...shared },
    { name: "vwap_sigma_es",   script: "run/run_vwap_sigma_es.py",   ...shared },
    { name: "tiny_gap_es",     script: "run/run_tiny_gap_es.py",     ...shared },
    { name: "bb_btc_4h",       script: "run/run_bb_btc_4h.py",       ...shared },
    {
      name: "dashboard",
      script: ".venv/bin/streamlit",
      args: "run src/trading_bot/dashboard/app.py --server.port 8501 --server.address 0.0.0.0",
      cwd: __dirname,
      interpreter: "none",
      autorestart: true,
      env: { PYTHONUNBUFFERED: "1", PYTHONPATH: __dirname },
      out_file: "logs/dashboard.out.log",
      error_file: "logs/dashboard.err.log",
      merge_logs: true,
    },
  ],
};
