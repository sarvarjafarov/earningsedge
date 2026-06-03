"""FinalSynthesisAgent — committee supervisor.

Replaces the old cascading-veto logic. Reads every specialist's latest
score_block and runs the weighted committee engine.

Emits the same `trade_signal` WebSocket message shape the frontend expects
(with additional fields: votes, disagreement_spread, mode).
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable

from agents.committee import compute_committee

MIN_WAIT_S = 20
RE_FIRE_INTERVAL_S = 25
MAX_THESIS_DISPLAY = 500

BroadcastFn = Callable[[dict[str, Any]], Awaitable[None]]


def _latest_score_block(rows: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    """Find the most recent score_block in a specialist's result history."""
    if not rows:
        return None
    for row in reversed(rows):
        sb = row.get("score_block")
        if isinstance(sb, dict):
            return sb
        data = row.get("data")
        if isinstance(data, dict):
            sb = data.get("score_block")
            if isinstance(sb, dict):
                return sb
    return None


def _collect_score_blocks(
    results: dict[str, list[dict[str, Any]]],
    ctx: dict[str, Any],
) -> dict[str, dict[str, Any] | None]:
    """Gather score_blocks from all specialists into a single dict."""
    blocks: dict[str, dict[str, Any] | None] = {}

    blocks["metrics"] = _latest_score_block(results.get("metrics"))
    blocks["sentiment"] = _latest_score_block(results.get("sentiment"))
    blocks["macro"] = _latest_score_block(results.get("macro"))
    blocks["technical"] = _latest_score_block(results.get("technical"))
    blocks["peer"] = _latest_score_block(results.get("peer"))

    preloaded_news = ctx.get("preloaded_news") or {}
    if isinstance(preloaded_news, dict):
        blocks["news"] = preloaded_news.get("score_block")
    else:
        blocks["news"] = None

    analyst_opinion = ctx.get("analyst_opinion") or {}
    if isinstance(analyst_opinion, dict):
        blocks["analyst"] = analyst_opinion.get("score_block")
    else:
        blocks["analyst"] = None

    return blocks


def _clip(s: Any, n: int) -> str:
    t = str(s or "").strip()
    return t if len(t) <= n else t[: n - 1] + "…"


def _build_trade_signal_payload(
    committee: dict[str, Any],
    ctx: dict[str, Any],
) -> dict[str, Any]:
    mode = committee.get("mode", "coverage")
    digest_lines: list[str] = []

    sorted_votes = sorted(
        committee.get("votes", []),
        key=lambda v: v.get("effective_weight", 0),
        reverse=True,
    )
    for vote in sorted_votes[:3]:
        reason_short = _clip(vote["reason"], 90)
        digest_lines.append(
            f"{vote['name'].title()}: {reason_short}"
        )
    if committee.get("disagreement_spread", 0) >= 30:
        digest_lines.append(
            f"Specialists disagree ({committee['disagreement_spread']}-pt spread) — see committee for details."
        )

    return {
        "signal": committee["signal"],
        "confidence": committee["confidence"],
        "thesis": _clip(committee["thesis"], MAX_THESIS_DISPLAY),
        "key_risk": _clip(committee["key_risk"], 240),
        # New committee fields
        "committee_score": committee["score"],
        "votes": committee["votes"],
        "mode": committee["mode"],
        "missing_specialists": committee["missing_specialists"],
        "disagreement_spread": committee["disagreement_spread"],
        # Legacy-compatibility fields the frontend already reads
        "digest_lines": digest_lines,
        "synthesis_mode": "live" if mode == "live" else "precall",
    }


async def emit_coverage_trade_signal(
    broadcast: BroadcastFn,
    ctx: dict[str, Any],
    results: dict[str, list[dict[str, Any]]],
) -> None:
    score_blocks = _collect_score_blocks(results, ctx)
    prev_signal = ctx.get("_last_committee_signal")
    committee = compute_committee(
        score_blocks, mode="coverage", previous_signal=prev_signal
    )
    ctx["_last_committee_signal"] = committee["signal"]

    payload = _build_trade_signal_payload(committee, ctx)
    await broadcast({
        "type": "trade_signal",
        "data": payload,
    })


class FinalSynthesisAgent:
    """Supervisor that emits committee-based trade signals during a live call."""

    def __init__(
        self,
        broadcast: BroadcastFn,
        ctx: dict[str, Any],
        results: dict[str, list[dict[str, Any]]],
    ) -> None:
        self.broadcast = broadcast
        self.ctx = ctx
        self.results = results
        self._fired = False
        self._last_fire_at = 0.0
        self._stop = asyncio.Event()

    async def run(self) -> None:
        start = self.ctx.get("session_start") or time.time()

        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=3.0)
                return
            except asyncio.TimeoutError:
                pass

            elapsed = time.time() - start
            sentiments = self.results.get("sentiment") or []

            if not self._fired:
                if elapsed < MIN_WAIT_S:
                    continue
                if len(sentiments) < 1:
                    continue
                await self._fire()
                self._fired = True
                self._last_fire_at = time.time()
                continue

            if time.time() - self._last_fire_at >= RE_FIRE_INTERVAL_S:
                await self._fire()
                self._last_fire_at = time.time()

    async def _fire(self) -> None:
        score_blocks = _collect_score_blocks(self.results, self.ctx)
        prev_signal = self.ctx.get("_last_committee_signal")
        committee = compute_committee(
            score_blocks, mode="live", previous_signal=prev_signal
        )
        self.ctx["_last_committee_signal"] = committee["signal"]

        payload = _build_trade_signal_payload(committee, self.ctx)
        await self.broadcast({
            "type": "trade_signal",
            "data": payload,
        })

    def stop(self) -> None:
        self._stop.set()
