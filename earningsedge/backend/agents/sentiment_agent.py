"""SentimentAgent — rules-banded + LLM-semantic earnings-call sentiment.

Key architectural decisions (all fixes for observed bugs):

1. NO PREMATURE INITIAL EMIT. The orchestrator already broadcasts a
   baseline `sentiment` message during `start_briefing` that includes
   the score from analyst consensus AND a baseline phrase. If the
   SentimentAgent emits immediately on startup with empty state, it
   clobbers that baseline on the frontend (which replaces sentiment
   state rather than merging). We only emit AFTER the first material
   analysis lands.

2. BASELINE + DIMENSION BLEND. `EarningsSentimentState.overall_score()`
   blends the analyst-consensus baseline with the weighted dimension
   aggregate — baseline dominates early, dimensions take over as
   evidence accumulates (at BLEND_THRESHOLD entries). This eliminates
   both the "whip on first dimension" bug and the "collapse to 50 when
   empty" bug.

3. CONCURRENT LLM CALLS. The materiality classifier is rate-limited to
   3 concurrent calls via an `asyncio.Semaphore`. A burst of material
   sentences processes in parallel instead of serially, so sentiment
   updates don't lag the call. If too many are in flight at once (>6)
   we drop the oldest rather than let the queue back up indefinitely.

4. ERROR VISIBILITY. LLM failures (rate limit, parse error) increment
   `classifier_errors` and the count rides in every broadcast so the
   frontend / user can see when the engine is blocked instead of silently
   sitting still.

5. BASELINE PHRASES PERSIST. The orchestrator stashes a baseline
   phrase (e.g. "Analyst consensus: 66/71 buy (bullish)") into
   `ctx["baseline_bullish_phrases"]`. The agent seeds its phrase deques
   from this so the first real emit doesn't wipe the analyst-consensus
   note.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any, Awaitable, Callable

from agents.specialist_schema import make_driver, make_score_block
from earnings_sentiment import (
    EarningsSentimentState,
    analyze_statement,
    is_materially_relevant,
)

OPERATOR_RE = re.compile(
    r"(operator|forward.?looking statements|safe harbor|please refer to our|"
    r"thank you for joining|thank you for standing by|please note that|"
    r"this presentation contains|cautionary statement)",
    re.IGNORECASE,
)

HEDGE_RE = re.compile(
    r"\b(we'll see|we will see|we remain confident|remain confident|"
    r"it's too early|too early to say|it is too early|back half|"
    r"second half of|in the back half|timing|lumpy|working through|"
    r"near.?term|approximately|somewhat|some|a bit of|a little|"
    r"moderate|modest|headwind|choppiness)\b",
    re.IGNORECASE,
)

EMIT_DEBOUNCE_S = 0.4  # snappier UI — was 0.8
MAX_CONCURRENT_LLM = 3
MAX_INFLIGHT_TASKS = 6  # drop sentences if too many classifications are pending

BroadcastFn = Callable[[dict[str, Any]], Awaitable[None]]


class SentimentAgent:
    def __init__(
        self,
        transcript_queue: asyncio.Queue,
        broadcast: BroadcastFn,
        ctx: dict[str, Any],
    ) -> None:
        self.queue = transcript_queue
        self.broadcast = broadcast
        self.ctx = ctx
        self._state = EarningsSentimentState()

        # Seed baseline from analyst consensus (set by orchestrator at
        # briefing time). Used by EarningsSentimentState.overall_score()
        # to smoothly blend into dimension-based scoring as evidence
        # accumulates.
        baseline = int(ctx.get("baseline_sentiment") or 50)
        self._state.baseline_score = max(0, min(100, baseline))
        self._state.last_overall_score = self._state.baseline_score

        # Seed baseline phrases so the frontend doesn't show an empty
        # bullish/bearish list on the first emit.
        for p in (ctx.get("baseline_bullish_phrases") or []):
            self._state.bullish_phrases.append(p)
        for p in (ctx.get("baseline_bearish_phrases") or []):
            self._state.bearish_phrases.append(p)

        self._stop = asyncio.Event()
        self._emit_pending = False
        self._inflight: set[asyncio.Task[None]] = set()
        self._sem = asyncio.Semaphore(MAX_CONCURRENT_LLM)
        self._prior_ctx = (ctx.get("prior_call_context_for_sentiment") or "").strip() or None

    async def run(self) -> None:
        # NO initial emit — the orchestrator's briefing broadcast already
        # seeded the gauge with the baseline score. Emitting from an empty
        # state here would clobber it.
        while not self._stop.is_set():
            try:
                sentence = await asyncio.wait_for(self.queue.get(), timeout=0.4)
            except asyncio.TimeoutError:
                self._reap_done()
                continue
            except asyncio.CancelledError:
                return

            if sentence.get("mode") != "LIVE":
                continue
            text = (sentence.get("text") or "").strip()
            if not text or OPERATOR_RE.search(text):
                continue
            if not is_materially_relevant(text):
                continue

            self._reap_done()
            # Backpressure: if too many LLM calls are already in flight,
            # skip this sentence rather than let the queue grow
            # unboundedly. Better to lose a sentence than hang the agent.
            if len(self._inflight) >= MAX_INFLIGHT_TASKS:
                continue

            task = asyncio.create_task(self._process_sentence(sentence))
            self._inflight.add(task)

        # On stop, let any in-flight tasks finish quickly.
        self._reap_done()

    def _reap_done(self) -> None:
        done = {t for t in self._inflight if t.done()}
        self._inflight -= done

    async def _process_sentence(self, sentence: dict[str, Any]) -> None:
        """Run the LLM classifier under the concurrency semaphore and
        fold the result into state. Any failure bumps `classifier_errors`
        and still triggers an emit so the frontend can see the error count
        climbing."""
        text = (sentence.get("text") or "").strip()
        speaker = sentence.get("speaker")
        async with self._sem:
            try:
                analysis = await analyze_statement(
                    text,
                    prior_call_context=self._prior_ctx,
                )
            except Exception:
                analysis = None

        if analysis is None:
            self._state.classifier_errors += 1
            await self._schedule_emit()
            return
        if not analysis.get("is_material"):
            # Non-material sentences don't trigger an emit — they're
            # just dropped silently. The cached state remains.
            return

        if analysis and analysis.get("is_material"):
            hedge_hits = len(HEDGE_RE.findall(text))
            if hedge_hits > 0:
                dim_scores = analysis.get("dimensions") or {}
                penalty = min(hedge_hits * 4, 15)
                for dim_key in ("forward_outlook", "demand_strength"):
                    if dim_key in dim_scores:
                        current = float(dim_scores[dim_key] or 50)
                        dim_scores[dim_key] = max(0, current - penalty)
                analysis["dimensions"] = dim_scores
                analysis.setdefault("hedge_count", 0)
                analysis["hedge_count"] = hedge_hits

        phase = (self.ctx.get("current_phase") or "").lower().strip()
        if phase == "qa" and analysis and analysis.get("is_material"):
            dim_scores = analysis.get("dimensions") or {}
            for dim_key, v in list(dim_scores.items()):
                if v is None:
                    continue
                current = float(v)
                delta = current - 50.0
                amplified = 50.0 + delta * 1.5
                dim_scores[dim_key] = max(0, min(100, amplified))
            analysis["dimensions"] = dim_scores
            analysis["phase_amplified"] = True

        self._state.apply_analysis(analysis, text, speaker)
        await self._schedule_emit()

    async def _schedule_emit(self) -> None:
        """Debounce emits so a burst produces one dashboard update."""
        if self._emit_pending:
            return
        self._emit_pending = True

        async def _delayed() -> None:
            try:
                await asyncio.sleep(EMIT_DEBOUNCE_S)
                await self._emit()
            finally:
                self._emit_pending = False

        asyncio.create_task(_delayed())

    async def _emit(self) -> None:
        overall = self._state.overall_score()
        trend = self._state.trend(overall)
        self._state.last_overall_score = overall
        label = self._state.label(overall)
        dim_avgs = self._state.dimension_averages()
        risk = self._state.risk_overlay()
        pos_drivers, neg_drivers = self._state.top_drivers()

        # Build bullish / bearish phrase lists — prefer the rolling
        # phrase history, fall back to driver reasons.
        bullish = list(self._state.bullish_phrases)
        if not bullish:
            bullish = [d["reason"] for d in pos_drivers if d.get("reason")]
        bearish = list(self._state.bearish_phrases)
        if not bearish:
            bearish = [d["reason"] for d in neg_drivers if d.get("reason")]

        payload = {
            "type": "sentiment",
            "data": {
                # Legacy fields — the frontend SentimentGauge reads these.
                "score": overall,
                "trend": trend,
                "bullish_phrases": list(bullish)[:5],
                "bearish_phrases": list(bearish)[:5],
                # New fields — the gauge renders dimension bars + risk.
                "overall_label": label,
                "dimension_scores": dim_avgs,
                "risk_overlay": risk,
                "mixed_count": self._state.mixed_count,
                "material_count": self._state.material_count,
                "classifier_errors": self._state.classifier_errors,
                "top_positive_drivers": pos_drivers[:6],
                "top_negative_drivers": neg_drivers[:6],
                "mixed_notes": list(self._state.mixed_notes),
                "suggested_investor_questions": list(
                    self._state.investor_questions[-5:]
                ),
                # Explicitly flag when we're in "mostly baseline" mode so
                # the frontend could render a "gathering evidence…" hint
                # if the user wants it later.
                "evidence_count": sum(len(h) for h in self._state.dim_history.values()),
            },
        }
        pa = self.ctx.get("preloaded_analyst")
        if isinstance(pa, dict) and pa and not pa.get("error"):
            payload["data"]["analyst_enrichment"] = {
                "trend_label": pa.get("trend_label"),
                "trend_1m_delta": pa.get("trend_1m_delta"),
                "trend_3m_delta": pa.get("trend_3m_delta"),
                "target_mean": pa.get("target_mean"),
                "target_median": pa.get("target_median"),
                "target_high": pa.get("target_high"),
                "target_low": pa.get("target_low"),
                "target_last_updated": pa.get("target_last_updated"),
                "target_count": pa.get("target_count"),
                "target_upside_pct": pa.get("target_upside_pct"),
                "target_spread_pct": pa.get("target_spread_pct"),
            }
        mat = int(self._state.material_count)
        err = int(self._state.classifier_errors)
        if mat >= 15 and err < 3:
            conf = "HIGH"
        elif mat < 5 or (mat > 0 and err >= mat // 2):
            conf = "LOW"
        else:
            conf = "MEDIUM"
        if mat == 0:
            reason = "Gauge on analyst baseline — no material call sentences yet."
            fresh = "stale"
        else:
            reason = (
                f"Weighted dimension blend across {mat} material statements; "
                f"risk {risk.get('level', 'low')}."
            )
            fresh = "fresh"

        prior_stats = getattr(self._state, "prior_agreement", None)
        prior_note = ""
        if isinstance(prior_stats, dict):
            more_optimistic = prior_stats.get("more_optimistic", 0)
            more_cautious = prior_stats.get("more_cautious", 0)
            if more_cautious > more_optimistic + 3:
                prior_note = " Tone more cautious than prior call."
            elif more_optimistic > more_cautious + 3:
                prior_note = " Tone more constructive than prior call."
        if prior_note:
            reason = f"{reason}{prior_note}"
        drv_weights = (0.8, 0.6, 0.4)
        drivers: list[dict[str, Any]] = []
        for i, d in enumerate(pos_drivers[:3]):
            rtxt = str(d.get("reason") or "").strip()
            if rtxt:
                w = drv_weights[i] if i < len(drv_weights) else 0.4
                drivers.append(make_driver(rtxt, "bullish", w))
        for i, d in enumerate(neg_drivers[:3]):
            rtxt = str(d.get("reason") or "").strip()
            if rtxt:
                w = drv_weights[i] if i < len(drv_weights) else 0.4
                drivers.append(make_driver(rtxt, "bearish", w))
        score_block = make_score_block(
            score=int(overall),
            confidence=conf,
            reason=reason,
            drivers=drivers,
            sample_size=mat,
            freshness=fresh,
        )
        payload["data"]["score_block"] = score_block
        await self.broadcast(payload)

    def stop(self) -> None:
        self._stop.set()
