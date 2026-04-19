"""Live market quotes + headlines for the dashboard top strip.

Data sources:
  * Markets: yfinance (free, bundled with Lumibot). Covers indices, FX,
    crypto, commodities, VIX, rates — everything you'd see on a CNBC
    ticker bar.
  * News: Alpaca News API (free with any Alpaca account). Falls back
    gracefully if the key isn't set.

Both functions are decorated with ``st.cache_data`` so the dashboard
doesn't hammer the sources on every rerun. 60s TTL for markets,
5 min for headlines.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable

import httpx
import pandas as pd
import streamlit as st

from trading_bot.config import get_settings

log = logging.getLogger(__name__)


MARKET_SYMBOLS: dict[str, str] = {
    "S&P 500": "^GSPC",
    "NASDAQ": "^NDX",
    "DOW": "^DJI",
    "RUSSELL": "^RUT",
    "VIX": "^VIX",
    "DXY": "DX-Y.NYB",
    "GOLD": "GC=F",
    "OIL": "CL=F",
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
    "EUR/USD": "EURUSD=X",
    "10Y": "^TNX",
}


@st.cache_data(ttl=30, show_spinner=False)
def fetch_broker_balances() -> dict[str, dict]:
    """Pull live equity from every broker whose creds are configured.

    Returns e.g.:
        {"Alpaca_Paper": {"equity": 100000, "buying_power": 200000, ...},
         "OANDA_Demo":  {"equity": 100000, "buying_power": 100000, ...}}
    """
    out: dict[str, dict] = {}
    alp = fetch_alpaca_balance()
    if alp:
        out["Alpaca_Paper"] = alp
    try:
        from trading_bot.brokers.balances import fetch_oanda_balance

        oan = fetch_oanda_balance()
        if oan:
            out["OANDA_Demo"] = oan
    except Exception:
        pass
    return out


@st.cache_data(ttl=30, show_spinner=False)
def fetch_alpaca_balance() -> dict | None:
    """Pull the real Alpaca paper-account balance.

    Our DB has per-strategy nominal books ($100k each). This is the
    actual one shared pool those strategies trade against.
    """
    s = get_settings()
    if not (s.alpaca_api_key and s.alpaca_api_secret):
        return None
    try:
        r = httpx.get(
            f"{s.alpaca_base_url}/v2/account",
            headers={
                "APCA-API-KEY-ID": s.alpaca_api_key,
                "APCA-API-SECRET-KEY": s.alpaca_api_secret,
            },
            timeout=10.0,
        )
        r.raise_for_status()
        data = r.json()
        return {
            "equity": float(data.get("equity", 0)),
            "cash": float(data.get("cash", 0)),
            "buying_power": float(data.get("buying_power", 0)),
            "last_equity": float(data.get("last_equity", 0)),
            "portfolio_value": float(data.get("portfolio_value", 0)),
            "account_number": data.get("account_number", ""),
            "status": data.get("status", ""),
        }
    except Exception as e:
        log.warning("alpaca balance fetch failed: %s", e)
        return None


@st.cache_data(ttl=60, show_spinner=False)
def fetch_markets() -> list[dict]:
    """Returns a list of {label, symbol, price, change_pct} dicts.

    Silently returns an empty list on network errors — the dashboard
    hides the strip when empty rather than showing broken tiles.
    """
    try:
        import yfinance as yf

        symbols = list(MARKET_SYMBOLS.values())
        df = yf.download(
            symbols,
            period="5d",
            interval="1d",
            progress=False,
            auto_adjust=True,
            threads=True,
        )
    except Exception as e:
        log.warning("market fetch failed: %s", e)
        return []

    if df is None or df.empty:
        return []

    # yfinance returns a multi-column DF with ("Close","SPY")-style keys
    # when multiple symbols are requested.
    if isinstance(df.columns, pd.MultiIndex):
        if "Close" in df.columns.get_level_values(0):
            close = df["Close"]
        else:
            return []
    else:
        close = df

    results: list[dict] = []
    for label, sym in MARKET_SYMBOLS.items():
        if sym not in close.columns:
            continue
        series = close[sym].dropna()
        if len(series) < 2:
            continue
        last = float(series.iloc[-1])
        prev = float(series.iloc[-2])
        change = (last - prev) / prev if prev else 0
        # Format rate-level things differently — VIX + 10Y are already %ages.
        results.append(
            {
                "label": label,
                "symbol": sym,
                "price": last,
                "change_pct": change,
            }
        )
    return results


@st.cache_data(ttl=300, show_spinner=False)
def fetch_headlines(symbols: Iterable[str] = ("SPY", "QQQ", "BTCUSD"), limit: int = 30) -> list[dict]:
    """Pull recent headlines from Alpaca's News API (v1beta1).

    Returns [{title, source, url, ts}]. Free with any Alpaca key.
    """
    s = get_settings()
    if not (s.alpaca_api_key and s.alpaca_api_secret):
        return []
    try:
        r = httpx.get(
            "https://data.alpaca.markets/v1beta1/news",
            params={"limit": limit, "symbols": ",".join(symbols), "sort": "desc"},
            headers={
                "APCA-API-KEY-ID": s.alpaca_api_key,
                "APCA-API-SECRET-KEY": s.alpaca_api_secret,
            },
            timeout=10.0,
        )
        r.raise_for_status()
    except Exception as e:
        log.warning("alpaca news fetch failed: %s", e)
        return []

    rows = r.json().get("news", [])
    out: list[dict] = []
    for n in rows:
        try:
            ts = datetime.fromisoformat(n["created_at"].replace("Z", "+00:00"))
        except Exception:
            ts = datetime.now(timezone.utc)
        out.append(
            {
                "title": n.get("headline", "").strip(),
                "source": n.get("source", "alpaca"),
                "url": n.get("url", ""),
                "ts": ts,
                "symbols": n.get("symbols", []),
            }
        )
    return out


def market_tile(item: dict) -> str:
    """Render one market tile: label, price, colored change %."""
    label = item["label"]
    price = item["price"]
    chg = item["change_pct"]
    cls = "pos" if chg > 0 else "neg" if chg < 0 else "zero"
    arrow = "▲" if chg > 0 else "▼" if chg < 0 else "·"

    # Formatting: rates + VIX in %, crypto/indices with commas, FX with 4 decimals.
    if label in ("VIX", "10Y"):
        price_txt = f"{price:.2f}"
    elif label in ("EUR/USD",):
        price_txt = f"{price:.4f}"
    elif price >= 1000:
        price_txt = f"{price:,.2f}"
    else:
        price_txt = f"{price:,.2f}"

    return (
        f'<span class="mkt-tile">'
        f'<span class="mkt-lbl">{label}</span>'
        f'<span class="mkt-val">{price_txt}</span>'
        f'<span class="mkt-chg {cls}">{arrow} {chg:+.2%}</span>'
        f"</span>"
    )


def news_tape_html(headlines: list[dict], max_items: int = 20) -> str:
    if not headlines:
        return ""
    items = headlines[:max_items]
    # Duplicate the list so the CSS marquee loops seamlessly.
    def _one(n: dict) -> str:
        age = (datetime.now(timezone.utc) - n["ts"]).total_seconds() / 60
        age_s = f"{int(age)}m ago" if age < 60 else f"{int(age/60)}h ago"
        syms = "".join(f'<span class="headline-sym">{s}</span>' for s in n["symbols"][:3])
        return (
            f'<span class="headline">'
            f'<span class="headline-dot">◆</span>'
            f'{syms}'
            f'<span class="headline-title">{n["title"]}</span>'
            f'<span class="headline-meta">{n["source"]} · {age_s}</span>'
            f"</span>"
        )

    rendered = "".join(_one(n) for n in items)
    return f"""
    <div class="newstape">
        <div class="newstape-track">{rendered}{rendered}</div>
    </div>
    """
