"""Earnings calendar — pulled from Financial Modeling Prep.

The autonomy story: every morning, the overnight pipeline asks
"whose earnings call did we miss last night?" and produces a verdict
for each one. The calendar tells us which tickers reported between
the prior afternoon and now, and which are reporting in the next
seven days so we can schedule them.

We use FMP's ``earning_calendar`` endpoint with a date window. FMP's
free tier returns past + upcoming events; we filter to the user's
watchlist.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

_log = logging.getLogger("earningsedge.calendar")

FMP_BASE = "https://financialmodelingprep.com/api/v3"


async def fetch_window(
    from_date: str,
    to_date: str,
) -> list[dict[str, Any]]:
    """Fetch raw FMP earnings calendar for a date window."""
    api_key = os.getenv("FMP_API_KEY", "").strip()
    if not api_key:
        return []
    url = f"{FMP_BASE}/earning_calendar"
    params = {"from": from_date, "to": to_date, "apikey": api_key}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(url, params=params)
            if r.status_code != 200:
                _log.warning("FMP calendar HTTP %s: %s", r.status_code, r.text[:200])
                return []
            data = r.json()
            return data if isinstance(data, list) else []
    except Exception as exc:  # noqa: BLE001
        _log.warning("FMP calendar fetch failed: %s", exc)
        return []


async def upcoming(tickers: list[str], days: int = 7) -> list[dict[str, Any]]:
    """Return scheduled earnings calls in the next `days` for the given tickers."""
    today = datetime.now(timezone.utc).date()
    to = today + timedelta(days=days)
    rows = await fetch_window(today.isoformat(), to.isoformat())
    upper = {t.upper() for t in tickers}
    out = []
    for row in rows:
        sym = (row.get("symbol") or "").upper()
        if sym in upper:
            out.append({
                "ticker": sym,
                "date": row.get("date"),
                "time": row.get("time"),  # "amc" or "bmo" or HH:MM
                "eps_estimate": row.get("epsEstimated"),
                "revenue_estimate": row.get("revenueEstimated"),
                "fiscal_period": row.get("fiscalDateEnding"),
            })
    # Sort by date ascending
    out.sort(key=lambda r: (r.get("date") or "", r.get("time") or ""))
    return out


async def reported_recently(tickers: list[str], hours: int = 18) -> list[dict[str, Any]]:
    """Return calls that reported in the last `hours` for the given tickers.

    Used by the overnight pipeline to find what to process. Defaults to
    18 hours so a 7 AM cron catches a 5 PM after-close call from the
    prior day plus any bmo (before-market-open) print today.
    """
    now = datetime.now(timezone.utc).date()
    earlier = now - timedelta(days=1)
    rows = await fetch_window(earlier.isoformat(), now.isoformat())
    upper = {t.upper() for t in tickers}
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    out = []
    for row in rows:
        sym = (row.get("symbol") or "").upper()
        if sym not in upper:
            continue
        # FMP's `date` is YYYY-MM-DD; time is "amc"/"bmo"/"" or HH:MM:SS
        # Treat amc (after market close) as ~21:00 UTC for the date
        # (4 PM ET in winter, 5 PM ET base — close enough for filtering)
        d = row.get("date")
        if not d:
            continue
        try:
            day = datetime.fromisoformat(d).replace(tzinfo=timezone.utc)
        except Exception:
            continue
        time_str = (row.get("time") or "").lower()
        if time_str == "amc":
            event_at = day.replace(hour=21)
        elif time_str == "bmo":
            event_at = day.replace(hour=13)
        else:
            event_at = day.replace(hour=20)  # default
        if event_at >= cutoff:
            out.append({
                "ticker": sym,
                "date": d,
                "time": row.get("time"),
                "event_at": event_at.isoformat(),
            })
    return out
