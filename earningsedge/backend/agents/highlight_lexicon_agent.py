"""HighlightLexiconAgent — analyst trigger extraction over transcript lines.

This is a *second* agent: it consumes the same transcript fan-out as
MetricsAgent / SentimentAgent, but its only job is to propose **which
substrings** in recent speech deserve visual emphasis for financial analysis.

Research — what analysts listen for on earnings calls (non-exhaustive):

1. **Guidance & expectations** — management’s forward view vs consensus:
   reaffirmed, raised, lowered, withdrawn, outlook, forecast, guidance,
   implicit vs explicit, “comfortable with”, “tracking to”.

2. **Revenue quality** — durability of growth: organic vs inorganic (M&A),
   constant currency, backlog, bookings, pipeline, ARR/NRR, churn/retention.

3. **Profitability** — gross margin, operating margin, EBITDA, leverage,
   cost actions, efficiency, restructuring, one-time items.

4. **Capital & balance sheet** — buybacks, dividends, capex, free cash flow,
   debt, liquidity, M&A / synergies.

5. **Risk & macro** — FX, tariffs, regulation, competition, execution,
   supply chain, seasonality.

6. **Strategic / product** — launches, TAM, partnerships, AI/product narrative
   *when tied to monetization or differentiation*.

The model is asked to return **verbatim substrings** from the supplied excerpt
so the frontend can safely highlight them with regex. Updates are debounced to
limit API cost.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Awaitable, Callable

from google import genai
from google.genai import types

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
MODEL = "gemini-2.5-flash"

# Wait for enough new transcript before calling the model.
MIN_CHARS_SINCE_LAST = 450
# Wall-clock debounce between LLM calls (seconds).
DEBOUNCE_S = 14.0
# Rolling text window sent to the model (characters).
MAX_EXCERPT = 14_000
# Cap merged phrases kept client-side / per response.
MAX_TRIGGERS = 220

EXTRACT_PROMPT = """You are a buy-side equity analyst assistant. Below is a contiguous EXCERPT from a live earnings call transcript (management or Q&A).

Task: extract **up to 40** short substrings that a financial analyst would flag as high-signal for modeling, sentiment, or risk — NOT generic filler.

Rules:
- Each "phrase" MUST be copied **verbatim** from the EXCERPT (same spelling; case may match the excerpt). Do not invent text.
- Prefer **multi-word phrases** when they carry meaning (e.g. "raised full-year guidance", "organic growth", "gross margin expansion").
- **tone**:
  - "bullish" — clearly positive for the equity story (beats, acceleration, share gains, raised guide, strong demand).
  - "bearish" — clearly negative (miss, lowered guide, weakness, headwinds, contraction).
  - "material" — neutral but analytically important (KPI names, guidance verbs, macro/legal, M&A, product milestones without clear sentiment).
- Skip: boilerplate ("good afternoon"), pure greetings, and single generic words unless they are essential ("guidance" alone is weak; prefer "reaffirmed guidance").

Return ONLY valid JSON:
{
  "triggers": [
    {"phrase": "<verbatim substring>", "tone": "bullish"|"bearish"|"material"}
  ]
}

No markdown fences, no commentary."""

BroadcastFn = Callable[[dict[str, Any]], Awaitable[None]]


class HighlightLexiconAgent:
    def __init__(
        self,
        transcript_queue: asyncio.Queue,
        broadcast: BroadcastFn,
        ctx: dict[str, Any],
    ) -> None:
        self.queue = transcript_queue
        self.broadcast = broadcast
        self.ctx = ctx
        self._client: genai.Client | None = None
        self._stop = asyncio.Event()
        self._rolling: str = ""
        self._chars_since_run = 0
        self._last_run_mono: float = 0.0
        self._triggers: list[dict[str, str]] = []
        self._merge_lock = asyncio.Lock()

    async def run(self) -> None:
        self._client = genai.Client(api_key=GEMINI_API_KEY)
        self._last_run_mono = time.monotonic() - DEBOUNCE_S
        while not self._stop.is_set():
            try:
                sentence = await asyncio.wait_for(self.queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                return
            text = (sentence.get("text") or "").strip()
            if not text:
                continue
            # Focus on call audio; briefing (YOU) is usually short — still index for context.
            self._rolling = (self._rolling + " " + text).strip()
            if len(self._rolling) > MAX_EXCERPT:
                self._rolling = self._rolling[-MAX_EXCERPT:]
            self._chars_since_run += len(text)

            now = time.monotonic()
            if self._chars_since_run < MIN_CHARS_SINCE_LAST:
                continue
            if now - self._last_run_mono < DEBOUNCE_S:
                continue
            self._last_run_mono = now
            self._chars_since_run = 0
            excerpt = self._rolling
            asyncio.create_task(self._run_extract(excerpt))

    async def _run_extract(self, excerpt: str) -> None:
        if self._stop.is_set() or len(excerpt) < 120:
            return
        if self._client is None:
            return
        ticker = self.ctx.get("ticker") or "UNKNOWN"
        try:
            raw = await self._client.aio.models.generate_content(
                model=MODEL,
                contents=f"{EXTRACT_PROMPT}\n\nTICKER CONTEXT: {ticker}\n\n---EXCERPT---\n{excerpt}\n---END---",
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.2,
                ),
            )
            text = getattr(raw, "text", "") or "{}"
            obj = json.loads(text)
        except Exception:
            return
        triggers_raw = obj.get("triggers") if isinstance(obj, dict) else None
        if not isinstance(triggers_raw, list):
            return

        excerpt_lower = excerpt.lower()
        merged: dict[str, dict[str, str]] = {}
        for item in triggers_raw:
            if not isinstance(item, dict):
                continue
            phrase = (item.get("phrase") or "").strip()
            tone = (item.get("tone") or "material").lower()
            if tone not in ("bullish", "bearish", "material"):
                tone = "material"
            if len(phrase) < 3 or len(phrase) > 120:
                continue
            if phrase.lower() not in excerpt_lower:
                # Model hallucinated or paraphrased — skip
                continue
            key = phrase.lower()
            merged[key] = {"phrase": phrase, "tone": tone}

        async with self._merge_lock:
            keys = set(merged.keys())
            self._triggers = [t for t in self._triggers if t.get("phrase", "").lower() not in keys]
            self._triggers.extend(merged.values())
            while len(self._triggers) > MAX_TRIGGERS:
                self._triggers.pop(0)
            payload = list(self._triggers)

        await self.broadcast({
            "type": "highlight_lexicon",
            "data": {"triggers": payload},
        })

    def stop(self) -> None:
        self._stop.set()
