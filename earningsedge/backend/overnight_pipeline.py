"""Overnight verdict pipeline — the agentic core.

This module implements the "EarningsEdge sleeps so you don't have to"
loop. Invoked by Heroku Scheduler at ~6 AM ET on weekdays:

  python -m overnight_pipeline

For each user with a watchlist:
  1. Ask the earnings calendar which of their tickers reported in the
     last ~18 hours.
  2. For each such ticker, run the ADK Chairman with a focused prompt
     that explicitly invokes the named-investor lenses + the memory
     loop.
  3. Persist the verdict + the tool-call trace to MongoDB Atlas
     ``earningsedge.morning_briefings``.
  4. If Telegram is configured, push a single rolled-up "good morning"
     message that summarises the verdicts and links back to the cockpit.

The whole pipeline is idempotent — if it runs twice on the same day it
re-uses the cached verdict for tickers it already processed.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from dotenv import load_dotenv

load_dotenv()

_log = logging.getLogger("earningsedge.overnight")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

BRIEFINGS_COLLECTION = "morning_briefings"


def _today_key() -> str:
    """YYYY-MM-DD in UTC — idempotency key for a single nightly run."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).date().isoformat()


async def _run_chairman_for_ticker(ticker: str) -> dict[str, Any]:
    """Run the ADK Chairman for a single ticker and return the structured result."""
    from google.adk.runners import InMemoryRunner
    from google.genai import types as genai_types
    from adk_agents import root_agent

    prompt = (
        f"Run a focused overnight verdict on {ticker}. Pick the single most "
        "relevant named-investor sub-agent (Cathie Wood / Michael Burry / "
        "Druckenmiller / Cramer / Marks) for this ticker's situation and "
        "transfer to them once. Call find_similar_past_verdict FIRST to "
        "anchor in memory. Synthesize a 4-sentence verdict with action, "
        "confidence, key driver, and named dissent if any. "
        "Finally call remember_verdict so this entry seeds tomorrow's memory."
    )
    runner = InMemoryRunner(agent=root_agent, app_name="earningsedge_chairman")
    session = await runner.session_service.create_session(
        app_name="earningsedge_chairman",
        user_id="overnight-pipeline",
    )
    content = genai_types.Content(role="user", parts=[
        genai_types.Part(text=f"[ticker={ticker}] {prompt}"),
    ])

    final_text: str | None = None
    tool_calls: list[dict[str, Any]] = []
    try:
        async for event in runner.run_async(
            user_id="overnight-pipeline",
            session_id=session.id,
            new_message=content,
        ):
            for part in (event.content.parts or []) if event.content else []:
                if getattr(part, "function_call", None):
                    fc = part.function_call
                    tool_calls.append({"name": fc.name, "args": dict(fc.args) if fc.args else {}})
                if getattr(part, "text", None) and event.is_final_response():
                    final_text = part.text
    except Exception as exc:  # noqa: BLE001
        _log.warning("overnight chairman failed for %s: %s", ticker, exc)
        return {"ticker": ticker, "ok": False, "error": str(exc)}

    return {
        "ticker": ticker,
        "ok": True,
        "response": final_text or "",
        "tool_calls": tool_calls,
        "model": os.getenv("GEMINI_MODEL", "gemini-3.5-flash"),
    }


def _format_briefing(verdicts: list[dict[str, Any]]) -> str:
    """Compose the Telegram morning briefing text."""
    if not verdicts:
        return (
            "🌅 *Good morning from EarningsEdge*\n\n"
            "No watchlist earnings calls overnight. Markets open in a few hours — "
            "rest of the cockpit is ready when you are."
        )
    lines = ["🌅 *Good morning from EarningsEdge*", ""]
    lines.append(f"Overnight: *{len(verdicts)} verdict(s)* on your watchlist.")
    lines.append("")
    for v in verdicts:
        if not v.get("ok"):
            lines.append(f"⚠️ *{v['ticker']}* — pipeline error ({v.get('error', 'see logs')[:80]})")
            continue
        head = (v.get("response", "") or "").split("\n", 1)[0][:160]
        lines.append(f"*{v['ticker']}* — {head}")
    lines.append("")
    public = os.getenv("EARNINGS_EDGE_PUBLIC_URL") or "https://earningsedge-3391b61f61d9.herokuapp.com"
    lines.append(f"Full reasoning + paper-trade drafts: {public}")
    return "\n".join(lines)


async def _persist_briefing(
    user_id: str, tickers_processed: list[str], verdicts: list[dict[str, Any]]
) -> None:
    """Save the run to MongoDB so the morning UI can load it."""
    try:
        from atlas_writer import durable_write
        await durable_write("insert-many", {
            "database": os.getenv("MONGODB_DB", "earningsedge"),
            "collection": BRIEFINGS_COLLECTION,
            "documents": [{
                "user_id": user_id,
                "date": _today_key(),
                "tickers_processed": tickers_processed,
                "verdicts": verdicts,
                "ts": int(time.time() * 1000),
            }],
        })
    except Exception as exc:  # noqa: BLE001
        _log.warning("briefing persist failed: %s", exc)


async def run(user_id: str = "demo-user", force: bool = False) -> dict[str, Any]:
    """Run the overnight pipeline once for a single user."""
    from watchlist import get_watchlist
    from earnings_calendar import reported_recently

    tickers = get_watchlist(user_id)
    if not tickers:
        return {"ok": True, "skipped": "no watchlist"}

    if not force:
        # Idempotency: did we already run today?
        try:
            from mcp_client import mcp_call
            existing = await mcp_call("find", {
                "database": os.getenv("MONGODB_DB", "earningsedge"),
                "collection": BRIEFINGS_COLLECTION,
                "filter": {"user_id": user_id, "date": _today_key()},
                "limit": 1,
            })
            if existing:
                return {"ok": True, "skipped": "already ran today", "date": _today_key()}
        except Exception:
            pass  # if Atlas check fails, fall through and re-run

    events = await reported_recently(tickers, hours=18)
    target_tickers = [e["ticker"] for e in events]
    _log.info("overnight: watchlist=%s reported=%s", tickers, target_tickers)

    if not target_tickers:
        # Even with nothing to do, send a short briefing so the user knows
        # the pipeline ran. Useful for the demo too.
        await _persist_briefing(user_id, [], [])
        await _send_telegram(_format_briefing([]))
        return {"ok": True, "processed": [], "briefing_sent": True}

    verdicts: list[dict[str, Any]] = []
    for tk in target_tickers:
        try:
            v = await _run_chairman_for_ticker(tk)
        except Exception as exc:  # noqa: BLE001
            v = {"ticker": tk, "ok": False, "error": str(exc)}
        verdicts.append(v)

    await _persist_briefing(user_id, target_tickers, verdicts)
    await _send_telegram(_format_briefing(verdicts))

    return {"ok": True, "processed": target_tickers, "verdicts": verdicts}


async def _send_telegram(text: str) -> None:
    try:
        from telegram_notify import send_telegram_text, telegram_notify_configured
        if not telegram_notify_configured():
            _log.info("Telegram not configured — skipping push")
            return
        await send_telegram_text(text)
    except Exception as exc:  # noqa: BLE001
        _log.warning("Telegram push failed: %s", exc)


if __name__ == "__main__":  # `python -m overnight_pipeline`
    import sys
    user = sys.argv[1] if len(sys.argv) > 1 else "demo-user"
    force = "--force" in sys.argv
    result = asyncio.run(run(user_id=user, force=force))
    print(result)
