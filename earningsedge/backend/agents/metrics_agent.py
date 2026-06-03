"""MetricsAgent — listens to transcript sentences for financial figures
and emits structured metrics updates compatible with the existing
frontend MetricsPanel schema.

Schema (matches frontend MetricsPanel.js exactly):
  {
    "type": "metrics",
    "data": {
      "revenue_reported": <number or string>,
      "revenue_estimate": <number or string>,
      "revenue_prior_year": <number or string>,
      "eps_reported": <number or string>,
      "eps_estimate": <number or string>,
      "gross_margin": <string>,
      "operating_margin": <string>,
      "guidance_raised": <bool|null>,
      "guidance_note": <string>
    }
  }

The agent uses a small LLM call (gemini-2.5-flash) per sentence that
contains financial language to extract structured fields. Fires at most
once per metric per session — once revenue is reported, additional revenue
mentions update the same metric rather than re-emitting.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Awaitable, Callable

from google import genai
from google.genai import types

from agents.specialist_schema import make_driver, make_score_block
from agents.trade_signal_core import _strip
from tools import get_earnings_estimates, get_fundamentals

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
EXTRACT_MODEL = "gemini-2.5-flash"

_REPORTABLE_KEYS = (
    "revenue_reported",
    "revenue_estimate",
    "revenue_prior_year",
    "eps_reported",
    "eps_estimate",
    "gross_margin",
    "operating_margin",
    "guidance_raised",
    "guidance_note",
)

# Regex patterns that flag a sentence as worth running through the
# extractor. Cheap pre-filter to avoid LLM calls on every sentence.
_FINANCIAL_HINT = re.compile(
    r"(\$?\d+(?:[,.]\d+)?\s*(?:billion|million|trillion|b|m|t)\b)|"
    r"(\b\d+(?:\.\d+)?\s*(?:percent|%)\b)|"
    r"(\beps\b|earnings per share)|"
    r"(gross margin|operating margin|net income)|"
    r"(guidance|outlook|forecast)|"
    r"(revenue|sales)",
    re.IGNORECASE,
)

EXTRACT_PROMPT = """You are an earnings call metric extractor. The user will give you ONE sentence from a live earnings call transcript. Extract any financial figures mentioned and return them as JSON with these exact fields (use null for anything not mentioned):

{
  "revenue_reported": "...",
  "revenue_reported_class": "top_line" | "segment" | "guidance" | "prior_period" | null,
  "revenue_confidence": 0.0-1.0,
  "revenue_prior_year": "...",
  "eps_reported": "...",
  "eps_reported_class": "gaap" | "non_gaap" | "guidance" | "prior_period" | null,
  "eps_confidence": 0.0-1.0,
  "gross_margin": "...",
  "operating_margin": "...",
  "guidance_raised": true|false|null,
  "guidance_note": "..."
}

Rules:
- Return ONLY the JSON object, no markdown, no commentary.
- Numbers should include their unit ($22.1B, 75.5%, $4.93).
- For revenue_reported and eps_reported, ONLY populate if the figure is a
  TOP-LINE (company-wide, reported quarter) actual. Segment breakdowns,
  guidance, and prior-period comparisons go in their respective fields or
  stay null. Set the *_class field accordingly.
- Confidence should reflect how certain the extraction is. 0.9+ for
  "Q4 revenue was $22.1 billion". 0.6 for "cloud revenue was strong, up
  significantly". 0.3 if you're guessing from vague language.
- If the sentence is about a segment (e.g. "data center revenue was $18B"),
  set revenue_reported_class = "segment" and STILL fill revenue_reported —
  but the agent will use class to decide whether to lock the top-line slot.
- guidance_raised is true if the company is raising guidance, false if lowering, null if no guidance change is mentioned.
- If the sentence has no financial figure at all, return all-null fields with
  confidence 0.0.
"""

BroadcastFn = Callable[[dict[str, Any]], Awaitable[None]]

MIN_CANDIDATE_CONF = 0.5
LOCK_CANDIDATE_CONF = 0.85


class MetricsAgent:
    def __init__(
        self,
        transcript_queue: asyncio.Queue,
        broadcast: BroadcastFn,
        ctx: dict[str, Any],
    ) -> None:
        self.queue = transcript_queue
        self.broadcast = broadcast
        self.ctx = ctx  # shared session context (ticker, company, quarter, year)
        self._client: genai.Client | None = None
        self._candidates: dict[str, list[dict[str, Any]]] = {
            "revenue_reported": [],
            "revenue_prior_year": [],
            "eps_reported": [],
            "gross_margin": [],
            "operating_margin": [],
            "guidance_note": [],
        }
        self._guidance_raised: bool | None = None
        self._cumulative: dict[str, Any] = {}
        self._fired: set[str] = set()
        self._stop = asyncio.Event()

    async def run(self) -> None:
        self._client = genai.Client(api_key=GEMINI_API_KEY)
        # Pre-load the estimate so we can show beat/miss as soon as the
        # reported number arrives.
        await self._preload_estimate()
        while not self._stop.is_set():
            try:
                sentence = await asyncio.wait_for(self.queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                return
            text = sentence.get("text", "")
            if not text:
                continue
            if not _FINANCIAL_HINT.search(text):
                continue
            try:
                extracted = await self._extract(text)
            except Exception:
                continue
            await self._merge_and_emit(extracted)

    async def _preload_estimate(self) -> None:
        """Seed the cumulative metrics dict from ctx + fundamentals.
        Does NOT broadcast — the orchestrator already broadcast initial
        metrics from start_briefing. Broadcasting here would just send
        nulls if consensus is still churning and overwrite better data
        the frontend already has."""
        ticker = self.ctx.get("ticker")
        if not ticker:
            return
        try:
            funds = await get_fundamentals(ticker)
        except Exception:
            funds = {}
        if not isinstance(funds, dict) or "error" in funds:
            funds = {}
        preloaded = self.ctx.get("preloaded_estimates") or {}
        # Keep cumulative preloaded (compat with score_block builder).
        self._cumulative = {
            "revenue_estimate": preloaded.get("revenue_estimate"),
            "eps_estimate": preloaded.get("eps_estimate"),
            "consensus_period_label": preloaded.get("consensus_period_label"),
            "consensus_source": preloaded.get("consensus_source"),
            "prior_eps_actual": preloaded.get("prior_eps_actual"),
            "prior_eps_estimate": preloaded.get("prior_eps_estimate"),
            "prior_eps_surprise_pct": preloaded.get("prior_eps_surprise_pct"),
            "prior_eps_period": preloaded.get("prior_eps_period"),
        }
        # Seed stable fundamentals as low-priority candidates (only if not already present).
        for k in ("gross_margin", "operating_margin"):
            v = funds.get(k)
            if v and not self._candidates[k]:
                self._candidates[k].append({"value": v, "confidence": 0.7, "class": None})

    def _best_candidate(self, field: str) -> dict[str, Any] | None:
        pool = self._candidates.get(field, [])
        if not pool:
            return None
        if field == "revenue_reported":
            top_line = [c for c in pool if c.get("class") == "top_line"]
            if top_line:
                return max(top_line, key=lambda c: c.get("confidence", 0.0))
        if field == "eps_reported":
            direct = [c for c in pool if c.get("class") in ("gaap", "non_gaap")]
            if direct:
                return max(direct, key=lambda c: c.get("confidence", 0.0))
        return max(pool, key=lambda c: c.get("confidence", 0.0))

    async def _extract(self, sentence: str) -> dict[str, Any]:
        assert self._client is not None
        response = await self._client.aio.models.generate_content(
            model=EXTRACT_MODEL,
            contents=f"{EXTRACT_PROMPT}\n\nSENTENCE: {sentence}",
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0,
            ),
        )
        text = getattr(response, "text", "") or "{}"
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {}

    def _reportable_sample_size(self) -> int:
        n = 0
        for k in _REPORTABLE_KEYS:
            v = self._cumulative.get(k)
            if v is None:
                continue
            if k == "guidance_note" and not str(v).strip():
                continue
            n += 1
        return n

    def _build_score_block(self) -> dict[str, Any]:
        """Deterministic 0–100 score from cumulative metrics (no LLM)."""
        preloaded = self.ctx.get("preloaded_estimates") or {}
        drivers: list[dict[str, Any]] = []
        score = 50

        rev_r = self._cumulative.get("revenue_reported")
        rev_e = self._cumulative.get("revenue_estimate")
        if rev_e in (None, "", "null"):
            rev_e = preloaded.get("revenue_estimate")

        eps_r = self._cumulative.get("eps_reported")
        eps_e = self._cumulative.get("eps_estimate")
        if eps_e in (None, "", "null"):
            eps_e = preloaded.get("eps_estimate")

        # If we don't have any reported metrics yet, treat as stale.
        if rev_r in (None, "", "null") and eps_r in (None, "", "null"):
            return make_score_block(
                score=50,
                confidence="LOW",
                reason="Awaiting reported metrics from the call.",
                drivers=[],
                sample_size=0,
                freshness="stale",
            )

        revenue_surprise_ok = False
        eps_surprise_ok = False
        rev_verb: str | None = None
        eps_verb: str | None = None

        if rev_r not in (None, "", "null") and rev_e not in (None, "", "null"):
            try:
                r = float(_strip(rev_r))
                e = float(_strip(rev_e))
                if e != 0:
                    surprise_pct = (r - e) / e * 100.0
                    revenue_surprise_ok = True
                    if surprise_pct >= 3:
                        score += 20
                        drivers.append(make_driver(f"Revenue beat by {surprise_pct:.1f}%", "bullish", 0.9))
                        rev_verb = f"beat by {surprise_pct:.1f}%"
                    elif surprise_pct > 0:
                        score += 8
                        drivers.append(make_driver(f"Revenue in-line (+{surprise_pct:.1f}%)", "bullish", 0.5))
                        rev_verb = f"in-line (+{surprise_pct:.1f}%)"
                    elif surprise_pct > -3:
                        score -= 8
                        drivers.append(make_driver(f"Revenue slight miss ({surprise_pct:.1f}%)", "bearish", 0.5))
                        rev_verb = f"slight miss ({surprise_pct:.1f}%)"
                    else:
                        score -= 25
                        drivers.append(make_driver(f"Revenue missed by {surprise_pct:.1f}%", "bearish", 0.9))
                        rev_verb = f"missed by {surprise_pct:.1f}%"
            except (TypeError, ValueError, ZeroDivisionError):
                pass

        if eps_r not in (None, "", "null") and eps_e not in (None, "", "null"):
            try:
                r = float(_strip(eps_r))
                e = float(_strip(eps_e))
                if e != 0:
                    surprise_pct = (r - e) / e * 100.0
                    eps_surprise_ok = True
                    if surprise_pct >= 3:
                        score += 10
                        drivers.append(make_driver(f"EPS beat by {surprise_pct:.1f}%", "bullish", 0.7))
                        eps_verb = f"beat by {surprise_pct:.1f}%"
                    elif surprise_pct > 0:
                        score += 4
                        drivers.append(make_driver(f"EPS in-line (+{surprise_pct:.1f}%)", "bullish", 0.4))
                        eps_verb = f"in-line (+{surprise_pct:.1f}%)"
                    elif surprise_pct > -3:
                        score -= 4
                        drivers.append(make_driver(f"EPS slight miss ({surprise_pct:.1f}%)", "bearish", 0.4))
                        eps_verb = f"slight miss ({surprise_pct:.1f}%)"
                    else:
                        score -= 13
                        drivers.append(make_driver(f"EPS missed by {surprise_pct:.1f}%", "bearish", 0.7))
                        eps_verb = f"missed by {surprise_pct:.1f}%"
            except (TypeError, ValueError, ZeroDivisionError):
                pass

        g = self._cumulative.get("guidance_raised")
        if g is True:
            score += 12
            drivers.append(make_driver("Guidance raised", "bullish", 0.7))
        elif g is False:
            score -= 18
            drivers.append(make_driver("Guidance cut", "bearish", 0.8))

        sample_size = sum(
            1
            for v in (
                self._cumulative.get("revenue_reported"),
                self._cumulative.get("eps_reported"),
                self._cumulative.get("gross_margin"),
                self._cumulative.get("operating_margin"),
                self._cumulative.get("guidance_raised"),
            )
            if v is not None
        )

        freshness = "fresh"
        if revenue_surprise_ok and eps_surprise_ok:
            confidence = "HIGH"
        elif revenue_surprise_ok ^ eps_surprise_ok:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        if revenue_surprise_ok and eps_surprise_ok and rev_verb and eps_verb:
            reason = f"Revenue {rev_verb}, EPS {eps_verb}"
        elif drivers:
            reason = str(drivers[0].get("evidence") or "").strip()
        else:
            reason = "Awaiting reported metrics from the call."

        return make_score_block(
            score=score,
            confidence=confidence,
            reason=reason,
            drivers=drivers,
            sample_size=sample_size,
            freshness=freshness,
        )

    async def _merge_and_emit(self, extracted: dict[str, Any]) -> None:
        changed = False

        def _f(x: Any) -> float:
            try:
                return float(x or 0.0)
            except (TypeError, ValueError):
                return 0.0

        # Revenue candidates
        rv = extracted.get("revenue_reported")
        rv_conf = _f(extracted.get("revenue_confidence"))
        rv_class = extracted.get("revenue_reported_class")
        if rv not in (None, "", "null") and rv_conf >= MIN_CANDIDATE_CONF:
            self._candidates["revenue_reported"].append({
                "value": rv,
                "confidence": rv_conf,
                "class": rv_class or "top_line",
            })
            changed = True

        # EPS candidates
        eps = extracted.get("eps_reported")
        eps_conf = _f(extracted.get("eps_confidence"))
        eps_class = extracted.get("eps_reported_class")
        if eps not in (None, "", "null") and eps_conf >= MIN_CANDIDATE_CONF:
            self._candidates["eps_reported"].append({
                "value": eps,
                "confidence": eps_conf,
                "class": eps_class or "gaap",
            })
            changed = True

        # Simple fields — no class, no confidence scoring. Take first non-null.
        for simple_key in ("revenue_prior_year", "gross_margin", "operating_margin", "guidance_note"):
            v = extracted.get(simple_key)
            if v not in (None, "", "null") and not self._candidates[simple_key]:
                self._candidates[simple_key].append({
                    "value": v,
                    "confidence": 0.7,
                    "class": None,
                })
                changed = True

        # Guidance raised — boolean, first decisive answer wins
        guidance = extracted.get("guidance_raised")
        if guidance is not None and self._guidance_raised is None:
            self._guidance_raised = guidance
            changed = True

        if not changed:
            return

        # Rebuild cumulative from best candidates
        self._cumulative = {}
        for field in (
            "revenue_reported",
            "revenue_prior_year",
            "eps_reported",
            "gross_margin",
            "operating_margin",
            "guidance_note",
        ):
            best = self._best_candidate(field)
            if best is not None:
                self._cumulative[field] = best["value"]

        # Include stable fields from ctx (estimates, prior-year data, etc.)
        preloaded = self.ctx.get("preloaded_estimates") or {}
        for k in (
            "revenue_estimate",
            "eps_estimate",
            "consensus_period_label",
            "consensus_source",
            "prior_eps_actual",
            "prior_eps_estimate",
            "prior_eps_surprise_pct",
            "prior_eps_period",
        ):
            v = preloaded.get(k)
            if v is not None:
                self._cumulative[k] = v

        if self._guidance_raised is not None:
            self._cumulative["guidance_raised"] = self._guidance_raised

        payload = {k: v for k, v in self._cumulative.items() if v is not None}
        payload["score_block"] = self._build_score_block()
        if payload:
            await self.broadcast({"type": "metrics", "data": payload})

    def stop(self) -> None:
        self._stop.set()
