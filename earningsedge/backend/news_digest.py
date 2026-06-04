"""News digest per ticker — runs the named-investor lenses over the
last 7 days of headlines so the user sees how each persona reads the
*existing* narrative, not just the press-release fundamentals.

Source: Finnhub company-news (free tier, decent volume). We dedupe by
headline, rank by source-weight, take the top N, and produce a one-
line per-headline persona reaction inline so the user can scan it
quickly.

This complements the Chairman synthesis: the synthesis is *what to
do*; the news digest is *what's already moving the market and what
each persona thinks of it*.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

_log = logging.getLogger("earningsedge.news_digest")


async def fetch_news(ticker: str, days: int = 14) -> list[dict[str, Any]]:
    """Pull recent company news from Finnhub."""
    api_key = os.getenv("FINNHUB_API_KEY", "").strip()
    if not api_key:
        return []
    to = datetime.now(timezone.utc).date()
    frm = to - timedelta(days=days)
    url = "https://finnhub.io/api/v1/company-news"
    params = {
        "symbol": ticker.upper(),
        "from": frm.isoformat(),
        "to": to.isoformat(),
        "token": api_key,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(url, params=params)
            if r.status_code != 200:
                _log.warning("Finnhub news HTTP %s: %s", r.status_code, r.text[:200])
                return []
            data = r.json()
            return data if isinstance(data, list) else []
    except Exception as exc:  # noqa: BLE001
        _log.warning("Finnhub news fetch failed: %s", exc)
        return []


# Light source-weighting heuristic — Reuters / WSJ / Bloomberg / FT
# stories dominate retail sentiment; everything else is treated as
# uniform.
_SOURCE_WEIGHTS = {
    "reuters": 5,
    "bloomberg": 5,
    "wsj": 5,
    "wall street journal": 5,
    "financial times": 5,
    "ft": 5,
    "cnbc": 4,
    "barron's": 4,
    "marketwatch": 3,
    "yahoo": 2,
    "seeking alpha": 2,
}


def _weight(source: str | None) -> int:
    if not source:
        return 1
    s = source.strip().lower()
    for key, w in _SOURCE_WEIGHTS.items():
        if key in s:
            return w
    return 1


def rank_news(rows: list[dict[str, Any]], top_n: int = 8) -> list[dict[str, Any]]:
    """Dedupe by headline, rank by source weight + recency, return top N."""
    seen: set[str] = set()
    ranked: list[tuple[int, dict[str, Any]]] = []
    now = datetime.now(timezone.utc).timestamp()
    for r in rows:
        headline = (r.get("headline") or "").strip()
        if not headline or headline.lower() in seen:
            continue
        seen.add(headline.lower())
        source = r.get("source")
        # Recency: stories in the last 24h get +5, last 7d +1.
        ts = r.get("datetime") or 0
        age_h = max(0.0, (now - ts) / 3600) if ts else 168
        recency = 5 if age_h < 24 else (3 if age_h < 72 else 1)
        score = _weight(source) * 2 + recency
        ranked.append((score, {
            "headline": headline,
            "source": source,
            "url": r.get("url"),
            "summary": (r.get("summary") or "")[:280],
            "ts": ts,
            "age_hours": round(age_h, 1),
            "score": score,
        }))
    ranked.sort(key=lambda x: -x[0])
    return [r[1] for r in ranked[:top_n]]


async def digest(ticker: str, days: int = 14, top_n: int = 6) -> dict[str, Any]:
    """Return the top-N ranked news items for a ticker.

    Light: no LLM calls. The Chairman will pull this via its existing
    get_news_sentiment tool when it needs richer scoring. This endpoint
    is for the UI panel that shows the news above the verdict.
    """
    rows = await fetch_news(ticker, days=days)
    if not rows:
        return {"ok": True, "ticker": ticker.upper(), "items": [], "count": 0}
    top = rank_news(rows, top_n=top_n)
    return {"ok": True, "ticker": ticker.upper(), "items": top, "count": len(top)}
