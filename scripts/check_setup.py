"""Diagnostic: tell the user exactly what's wired up and what's missing.

Run before `pm2 start ecosystem.config.js` to catch config drift early.

    python scripts/check_setup.py
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

from trading_bot.config import get_settings


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


def _mask(value: str) -> str:
    if not value:
        return "<unset>"
    return value[:4] + "…" + value[-4:] if len(value) > 10 else "***"


def _check_env() -> list[Check]:
    s = get_settings()
    rows = [
        Check(
            "ALPACA credentials",
            ok=bool(s.alpaca_api_key and s.alpaca_api_secret),
            detail=f"key={_mask(s.alpaca_api_key)} account_id={s.alpaca_account_id or '<unset>'}",
        ),
        Check(
            "OANDA credentials",
            ok=bool(s.oanda_api_token and s.oanda_account_id),
            detail=f"token={_mask(s.oanda_api_token)} account={s.oanda_account_id or '<unset>'} "
            f"env={s.oanda_environment}",
        ),
        Check(
            "Tradovate credentials",
            ok=all(
                [
                    s.tradovate_username,
                    s.tradovate_password,
                    s.tradovate_client_id,
                    s.tradovate_client_secret,
                ]
            ),
            detail=f"user={s.tradovate_username or '<unset>'} env={s.tradovate_environment}",
        ),
        Check(
            "DATABASE_URL",
            ok=bool(s.database_url),
            detail=s.database_url.split("@")[-1] if "@" in s.database_url else s.database_url,
        ),
        Check(
            "Telegram (optional)",
            ok=True,
            detail="configured"
            if s.telegram_bot_token and s.telegram_chat_id
            else "not configured — alerts only via log",
        ),
        Check(
            "SMTP (optional)",
            ok=True,
            detail="configured" if s.smtp_host and s.smtp_username else "not configured",
        ),
    ]
    return rows


def _check_db() -> list[Check]:
    rows: list[Check] = []
    try:
        from sqlalchemy import text

        from trading_bot.db.session import engine

        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        rows.append(Check("DB reachable", ok=True, detail=str(engine.url)))
    except Exception as e:
        rows.append(Check("DB reachable", ok=False, detail=f"{type(e).__name__}: {e}"))
        return rows

    try:
        from sqlalchemy import select

        from trading_bot.db.models import Account, NewsWindow, StrategyDailyPnL
        from trading_bot.db.session import get_session

        with get_session() as s:
            acct_count = s.execute(select(Account)).all()
            news_count = s.execute(select(NewsWindow)).all()
            pnl_count = s.execute(select(StrategyDailyPnL)).all()
        rows.append(
            Check(
                "schema + seeds",
                ok=len(acct_count) == 6,
                detail=f"accounts={len(acct_count)} news={len(news_count)} pnl={len(pnl_count)} "
                f"(need 6 accounts)",
            )
        )
    except Exception as e:
        rows.append(Check("schema + seeds", ok=False, detail=f"{type(e).__name__}: {e}"))

    return rows


def _check_imports() -> list[Check]:
    rows: list[Check] = []
    modules = [
        "lumibot",
        "alpaca",
        "oandapyV20",
        "sqlalchemy",
        "psycopg",
        "httpx",
        "pandas",
        "numpy",
    ]
    for mod in modules:
        try:
            __import__(mod)
            rows.append(Check(f"import {mod}", ok=True))
        except Exception as e:
            rows.append(Check(f"import {mod}", ok=False, detail=str(e)))
    return rows


def _render(title: str, checks: list[Check]) -> bool:
    print(f"\n== {title} ==")
    all_ok = True
    for c in checks:
        mark = "OK  " if c.ok else "FAIL"
        print(f"  [{mark}] {c.name:<26} {c.detail}")
        if not c.ok:
            all_ok = False
    return all_ok


def main() -> int:
    print("Trading bot setup diagnostic")
    print("=" * 60)

    all_ok = True
    all_ok &= _render("Python deps", _check_imports())
    all_ok &= _render("Env / credentials", _check_env())
    all_ok &= _render("Database", _check_db())

    print()
    if all_ok:
        print("All checks passed. Safe to run: pm2 start ecosystem.config.js")
        return 0
    print("Some checks failed. Fix the FAIL lines above before running live.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
