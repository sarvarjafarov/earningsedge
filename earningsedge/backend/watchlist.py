"""User watchlist storage + API.

The watchlist is the foundation for the autonomy story: the overnight
verdict pipeline only listens to companies the user actually cares
about, the earnings calendar shows what's reporting in the next 7
days, and the morning Telegram briefing summarises the verdicts that
were produced for the user's tickers.

Storage: MongoDB Atlas ``earningsedge.watchlists`` collection. One
document per user with ``{user_id, tickers, updated_at}``. We use
``user_id`` keyed on the session_id today; this would become a real
auth identity in V2.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

import certifi
from pymongo import MongoClient

_log = logging.getLogger("earningsedge.watchlist")

COLLECTION = "watchlists"
DEFAULT_USER = "demo-user"
DEFAULT_WATCHLIST = ["NVDA", "AAPL", "MSFT", "GOOGL", "TSLA", "AMZN"]


def _client() -> MongoClient:
    return MongoClient(
        os.environ["MONGODB_URI"],
        tlsCAFile=certifi.where(),
        serverSelectionTimeoutMS=int(os.getenv("MONGODB_SELECT_TIMEOUT_MS", "5000")),
        socketTimeoutMS=8000,
    )


def _db():
    return _client()[os.getenv("MONGODB_DB", "earningsedge")]


def get_watchlist(user_id: str | None = None) -> list[str]:
    """Read a user's watchlist. Returns the seeded default when nothing
    is stored yet OR when Atlas is unreachable — the demo never shows
    an empty watchlist."""
    uid = user_id or DEFAULT_USER
    try:
        doc = _db()[COLLECTION].find_one({"user_id": uid})
        if doc and isinstance(doc.get("tickers"), list) and doc["tickers"]:
            return [t.upper() for t in doc["tickers"] if isinstance(t, str)]
    except Exception as exc:  # noqa: BLE001
        _log.warning("watchlist read failed for %s: %s", uid, exc)
    return DEFAULT_WATCHLIST[:]


def set_watchlist(tickers: list[str], user_id: str | None = None) -> dict[str, Any]:
    """Replace the user's watchlist. Returns the persisted list."""
    uid = user_id or DEFAULT_USER
    clean = [t.strip().upper() for t in tickers if isinstance(t, str) and t.strip()]
    if not clean:
        return {"ok": False, "error": "no tickers provided"}
    try:
        _db()[COLLECTION].update_one(
            {"user_id": uid},
            {"$set": {
                "user_id": uid,
                "tickers": clean,
                "updated_at": int(time.time() * 1000),
            }},
            upsert=True,
        )
        return {"ok": True, "tickers": clean}
    except Exception as exc:  # noqa: BLE001
        _log.warning("watchlist write failed for %s: %s", uid, exc)
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def add_ticker(ticker: str, user_id: str | None = None) -> dict[str, Any]:
    """Append a ticker if not already on the watchlist."""
    current = get_watchlist(user_id)
    t = ticker.strip().upper()
    if not t:
        return {"ok": False, "error": "empty ticker"}
    if t in current:
        return {"ok": True, "tickers": current, "noop": True}
    return set_watchlist(current + [t], user_id)


def remove_ticker(ticker: str, user_id: str | None = None) -> dict[str, Any]:
    """Remove a ticker from the watchlist."""
    current = get_watchlist(user_id)
    t = ticker.strip().upper()
    return set_watchlist([x for x in current if x != t], user_id) if t in current else {"ok": True, "tickers": current, "noop": True}
