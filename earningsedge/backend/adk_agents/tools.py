"""ADK tool surface for the EarningsEdge analyst chairman.

Each function is a thin async wrapper around the existing
`tools.py` market-data adapters or `trade_executor.py` paper-trading
client. ADK reads the function signatures and docstrings to advertise
the tools to Gemini 3.

Design notes:
- Tools return plain JSON-serializable dicts (no Pydantic) — ADK passes
  the result straight back to Gemini, so the structure must be
  self-describing.
- Tools never raise — every failure becomes ``{"error": "..."}`` so
  Gemini can reason about it and fall back to alternative tools.
- We keep the tool count small and the names self-explanatory. This
  improves Gemini 3's tool-selection accuracy under the 3-min response
  budget the live UI imposes.
"""
from __future__ import annotations

import logging
from typing import Any

import tools as legacy_tools  # the existing market-data layer
import trade_executor

_log = logging.getLogger("earningsedge.adk.tools")


# Tool bodies inline their own try/except so ADK can read each function's
# real signature (parameter names + type hints). Decorating with
# ``*args, **kwargs`` hides the signature from ADK's auto-schema and
# makes Gemini hallucinate alternate arg shapes when the call fails.


# ---------------------------------------------------------------------------
# Market-data tools
# ---------------------------------------------------------------------------


async def get_stock_quote(ticker: str) -> dict[str, Any]:
    """Return real-time price, day high/low, change %, market cap for a ticker.

    Source: Finnhub quote + Alpha Vantage fallback.
    """
    try:
        return await legacy_tools.get_stock_data(ticker)
    except Exception as exc:  # noqa: BLE001
        _log.warning("get_stock_quote(%s) failed: %s", ticker, exc)
        return {"error": f"{type(exc).__name__}: {exc}"}


async def get_fundamentals(ticker: str) -> dict[str, Any]:
    """Return fundamentals: P/E, EV/EBITDA, revenue growth, margins, EPS, PEG.

    Combines Finnhub + FMP + yfinance. Use this to ground any valuation
    claim — never invent a multiple.
    """
    try:
        return await legacy_tools.get_fundamentals(ticker)
    except Exception as exc:  # noqa: BLE001
        _log.warning("get_fundamentals(%s) failed: %s", ticker, exc)
        return {"error": f"{type(exc).__name__}: {exc}"}


async def get_analyst_consensus(ticker: str) -> dict[str, Any]:
    """Return analyst recommendation distribution and 12-mo price target.

    Returns a `score_block` envelope: ``{score, label, confidence,
    drivers, sample_size, freshness}``. Use the score directly in any
    committee aggregation.
    """
    try:
        return await legacy_tools.get_analyst_recommendation(ticker)
    except Exception as exc:  # noqa: BLE001
        _log.warning("get_analyst_consensus(%s) failed: %s", ticker, exc)
        return {"error": f"{type(exc).__name__}: {exc}"}


async def get_peers(ticker: str) -> dict[str, Any]:
    """Return peer companies and a relative-valuation comparison.

    Use this to surface 'cheap vs sector' or 'rich vs sector' findings.
    """
    try:
        return await legacy_tools.get_competitors(ticker)
    except Exception as exc:  # noqa: BLE001
        _log.warning("get_peers(%s) failed: %s", ticker, exc)
        return {"error": f"{type(exc).__name__}: {exc}"}


async def get_news_sentiment(ticker: str, company_name: str = "") -> dict[str, Any]:
    """Return rolling 7-day news sentiment with article-level breakdown.

    Use this when the user asks about recent narrative or catalyst risk.
    """
    try:
        return await legacy_tools.get_news_sentiment(ticker, company_name)
    except Exception as exc:  # noqa: BLE001
        _log.warning("get_news_sentiment(%s) failed: %s", ticker, exc)
        return {"error": f"{type(exc).__name__}: {exc}"}


async def get_earnings_estimates(ticker: str) -> dict[str, Any]:
    """Return next-quarter EPS and revenue consensus + history of beats.

    Source: Finnhub earnings calendar consensus with FMP fallback.
    """
    try:
        return await legacy_tools.get_earnings_estimates(ticker)
    except Exception as exc:  # noqa: BLE001
        _log.warning("get_earnings_estimates(%s) failed: %s", ticker, exc)
        return {"error": f"{type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------------------
# Paper-trading tools
# ---------------------------------------------------------------------------


async def get_paper_account() -> dict[str, Any]:
    """Return the Alpaca paper account summary: cash, equity, buying power."""
    try:
        return trade_executor.account_snapshot()
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


async def get_paper_positions() -> list[dict[str, Any]]:
    """Return all open paper-trading positions."""
    try:
        return trade_executor.positions_snapshot()
    except Exception as exc:  # noqa: BLE001
        return [{"error": f"{type(exc).__name__}: {exc}"}]


async def draft_paper_trade(
    ticker: str,
    side: str,
    quantity: float,
    rationale: str,
) -> dict[str, Any]:
    """Draft a paper trade for user approval.

    Drafts do NOT execute. They are persisted (via MongoDB MCP when
    available) and surfaced in the UI for one-tap confirm. Use this for
    every actionable recommendation.

    Parameters
    ----------
    ticker:
        Ticker symbol, e.g. "NVDA".
    side:
        "buy" or "sell".
    quantity:
        Number of shares (can be fractional).
    rationale:
        One- or two-sentence reason. Becomes the confirmation tooltip.
    """
    side_norm = side.strip().lower()
    if side_norm not in {"buy", "sell"}:
        return {"error": f"side must be buy or sell, got {side!r}"}
    return {
        "type": "paper_trade_draft",
        "ticker": ticker.upper(),
        "side": side_norm,
        "quantity": float(quantity),
        "rationale": rationale,
        "status": "draft",
    }


# ---------------------------------------------------------------------------
# Persistent memory via MongoDB MCP
# ---------------------------------------------------------------------------


async def remember(collection: str, document: dict[str, Any]) -> dict[str, Any]:
    """Persist a document to MongoDB Atlas via the MongoDB MCP server.

    Use this for verdicts, drafted trades, and committee snapshots so
    they survive across sessions and can be queried later. Falls back to
    in-process logging when MCP is unavailable.

    Parameters
    ----------
    collection:
        One of: ``verdicts``, ``trades``, ``sessions``, ``transcripts``.
    document:
        Any JSON-serializable dict.
    """
    try:
        from atlas_writer import durable_write

        await durable_write(
            "insert-many",
            {"database": "earningsedge", "collection": collection, "documents": [document]},
        )
        return {"ok": True, "collection": collection}
    except Exception as exc:  # noqa: BLE001
        _log.info("remember() fell back to log-only: %s", exc)
        return {"ok": False, "fallback": "log_only", "error": str(exc)}


async def recall(collection: str, query: dict[str, Any], limit: int = 10) -> dict[str, Any]:
    """Read documents from MongoDB Atlas via the MongoDB MCP server.

    Use this to surface prior verdicts on the same ticker, drafted trades
    awaiting confirmation, or recent committee history.
    """
    try:
        from mcp_client import mcp_call

        rows = await mcp_call(
            "find",
            {
                "database": "earningsedge",
                "collection": collection,
                "filter": query,
                "limit": int(limit),
            },
        )
        return {"ok": True, "rows": rows}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "rows": [], "error": str(exc)}


# ---------------------------------------------------------------------------
# Registry — ADK reads this to advertise tools to Gemini
# ---------------------------------------------------------------------------

ALL_TOOLS = [
    get_stock_quote,
    get_fundamentals,
    get_analyst_consensus,
    get_peers,
    get_news_sentiment,
    get_earnings_estimates,
    get_paper_account,
    get_paper_positions,
    draft_paper_trade,
    remember,
    recall,
]
