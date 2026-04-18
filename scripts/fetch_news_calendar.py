"""Populate ``news_windows`` from ForexFactory's public JSON feed.

ForexFactory publishes the current week's economic calendar at a stable
URL (no auth, no API key). Each entry has ``title`` / ``country`` / ``date``
(ISO with tz) / ``impact`` (High, Medium, Low, None). We upsert HIGH-impact
events so the risk engine can blackout trading around them.

Intended cadence: run this once per day via cron (see scripts/cron/
notes in README). Safe to run more often — it's idempotent on
(event, starts_at, currency).

Usage:
    python scripts/fetch_news_calendar.py           # this week
    python scripts/fetch_news_calendar.py --next    # next week too
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select

from trading_bot.db.models import NewsWindow
from trading_bot.db.session import get_session

logging.basicConfig(
    level="INFO", format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stdout
)
log = logging.getLogger("news_calendar")


THIS_WEEK_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
NEXT_WEEK_URL = "https://nfs.faireconomy.media/ff_calendar_nextweek.json"
EVENT_DURATION = timedelta(minutes=5)


def _fetch(url: str) -> list[dict]:
    log.info("fetching %s", url)
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        r = client.get(url)
        r.raise_for_status()
    return r.json()


def _normalise_impact(raw: str) -> str:
    return (raw or "").strip().upper()  # "HIGH", "MEDIUM", "LOW", "HOLIDAY", ""


def upsert_events(entries: list[dict], min_impact: str = "HIGH") -> int:
    """Insert/update only entries matching min_impact. Returns rows written."""
    written = 0
    now = datetime.now(timezone.utc)
    with get_session() as s:
        for e in entries:
            impact = _normalise_impact(e.get("impact", ""))
            if impact != min_impact:
                continue
            date_raw = e.get("date")
            if not date_raw:
                continue
            try:
                starts_at = datetime.fromisoformat(date_raw)
            except ValueError:
                log.warning("skip unparseable date: %r", date_raw)
                continue
            if starts_at.tzinfo is None:
                starts_at = starts_at.replace(tzinfo=timezone.utc)
            starts_at = starts_at.astimezone(timezone.utc)
            ends_at = starts_at + EVENT_DURATION
            event = e.get("title", "unknown")[:64]
            currency = (e.get("country") or "")[:8]

            existing = s.execute(
                select(NewsWindow).where(
                    NewsWindow.event == event,
                    NewsWindow.starts_at == starts_at,
                    NewsWindow.currency == currency,
                )
            ).scalar_one_or_none()
            if existing is None:
                s.add(
                    NewsWindow(
                        event=event,
                        currency=currency,
                        impact=impact[:16],
                        starts_at=starts_at,
                        ends_at=ends_at,
                        source="forexfactory",
                        fetched_at=now,
                    )
                )
            else:
                existing.ends_at = ends_at
                existing.impact = impact[:16]
                existing.fetched_at = now
            written += 1
    return written


def prune_stale(older_than: timedelta = timedelta(days=14)) -> int:
    cutoff = datetime.now(timezone.utc) - older_than
    with get_session() as s:
        stale = s.execute(
            select(NewsWindow).where(NewsWindow.ends_at < cutoff)
        ).scalars().all()
        for row in stale:
            s.delete(row)
    return len(stale)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--next", action="store_true", help="also fetch next week's feed")
    ap.add_argument("--min-impact", default="HIGH", choices=["HIGH", "MEDIUM", "LOW"])
    ap.add_argument("--no-prune", action="store_true")
    args = ap.parse_args()

    urls = [THIS_WEEK_URL]
    if args.next:
        urls.append(NEXT_WEEK_URL)

    total_written = 0
    for url in urls:
        try:
            entries = _fetch(url)
            total_written += upsert_events(entries, min_impact=args.min_impact)
        except Exception as e:
            log.error("fetch failed for %s: %s", url, e)

    log.info("upserted %d %s events", total_written, args.min_impact)
    if not args.no_prune:
        pruned = prune_stale()
        log.info("pruned %d stale rows", pruned)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
