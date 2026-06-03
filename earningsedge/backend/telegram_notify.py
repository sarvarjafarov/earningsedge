"""Short Telegram pings (Bot API) — e.g. “summary ready on EarningsEdge”, not full report text."""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

_log = logging.getLogger("uvicorn.error")


def _strip_env(value: str) -> str:
    s = (value or "").strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1].strip()
    return s


def _token() -> str:
    return _strip_env(os.getenv("TELEGRAM_BOT_TOKEN", ""))


def _chat_id() -> str:
    return _strip_env(os.getenv("TELEGRAM_NOTIFY_CHAT_ID", ""))


def telegram_notify_configured() -> bool:
    return bool(_token() and _chat_id())


def build_summary_available_message(
    ticker: str | None,
    company_name: str | None,
) -> str:
    """One short message for the team chat (well under Telegram’s 4096 limit)."""
    name = (company_name or "").strip()
    tick = (ticker or "").strip().upper()
    if name:
        who = name.upper()
    elif tick:
        who = tick
    else:
        who = "THE COMPANY"
    lines = [
        f"EARNINGS CALL FOR {who} ENDED — SUMMARY AVAILABLE ON THE EARNINGS EDGE PLATFORM!",
    ]
    base = os.getenv("EARNINGS_EDGE_PUBLIC_URL", "").strip().rstrip("/")
    if base:
        lines.append(f"Open: {base}")
    return "\n".join(lines)


async def send_telegram_text(text: str) -> dict[str, Any]:
    token = _token()
    chat = _chat_id()
    if not token or not chat:
        raise RuntimeError("Telegram bot token or chat id not configured")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload: dict[str, Any] = {
        "chat_id": chat,
        "text": text[:4096],
        "disable_web_page_preview": True,
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(url, json=payload)
        try:
            data = r.json()
        except Exception:
            _log.warning("Telegram non-JSON response: %s", r.text[:500])
            raise RuntimeError(f"Telegram HTTP {r.status_code}") from None
    if not data.get("ok"):
        desc = data.get("description") or str(data)
        raise RuntimeError(f"Telegram API: {desc}")
    return data
