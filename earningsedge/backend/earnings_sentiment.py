"""Earnings-call sentiment engine — rules-banded + LLM-semantic hybrid.

Architecture:
  1. Materiality pre-filter (regex) — drops fluffy sentences cheap.
  2. Per-sentence LLM classifier — extracts dimension scores, trigger
     flags, entities, mixed signals. The LLM is instructed to use ONLY
     deterministic score bands (15/25/40/50/60/75/88) so scoring is
     reproducible instead of random.
  3. Rolling per-dimension history (deque) — weighted by confidence.
  4. Deterministic aggregation formula — weighted dimension deltas plus
     a separate risk overlay.

The engine is called from `agents/sentiment_agent.py`. Results feed both
the existing `sentiment` dashboard message (backward compat: score,
trend, bullish_phrases, bearish_phrases) AND a new `dimension_scores` +
`risk_overlay` payload the frontend can render as bars.

Non-goals (skipped intentionally for this codebase):
  - Q&A section detection (requires structural parsing)
  - Cross-statement contradiction tracking (stateful, complex)
  - Entity-level rolling sentiment UI (we extract entities but only log
    them internally for now)
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from google import genai
from google.genai import types

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
ANALYSIS_MODEL = "gemini-2.5-flash"

# ---------- Dimension definitions and weights ----------

DIMENSIONS = [
    "results_vs_expectations",
    "forward_outlook",
    "demand_strength",
    "supply_execution",
    "margins_profitability",
    "customer_mix",
    "competitive_position",
    "regulatory_geographic_risk",
    "management_credibility",
    "q_and_a_quality",
]

DIMENSION_WEIGHTS: dict[str, float] = {
    "results_vs_expectations": 0.20,
    "forward_outlook": 0.25,
    "demand_strength": 0.15,
    "supply_execution": 0.10,
    "margins_profitability": 0.10,
    "management_credibility": 0.10,
    "customer_mix": 0.05,
    "competitive_position": 0.05,
    # regulatory_geographic_risk is intentionally EXCLUDED from overall
    # sentiment — it feeds the risk_overlay instead.
    # q_and_a_quality is a supporting dimension, not weighted.
}

CONFIDENCE_MULTIPLIER: dict[str, float] = {"high": 1.0, "medium": 0.7, "low": 0.4}

# Deterministic score bands the LLM must use. Keeps scoring reproducible.
SCORE_BANDS = [15, 25, 40, 50, 60, 75, 88]

# ---------- Materiality pre-filter ----------

# Broad materiality pre-filter. The goal is to let through anything that
# MIGHT be material and let the LLM classifier make the final call via
# its own is_material field. Being too strict here silently drops real
# signals ("we continued to see strong growth this quarter" was failing
# the old regex because it didn't contain any of the exact tokens).
_MATERIAL_HINTS = re.compile(
    r"("
    # numbers of any kind (dollars, percentages, multiples)
    r"\$[\d,.]+|"
    r"\b\d+(?:\.\d+)?\s*(?:percent|%|bps|basis points|billion|million|trillion)\b|"
    r"\b\d+\s*(?:x|times)\b|"
    # core finance vocabulary
    r"\b(?:revenue|sales|earnings|eps|profit|income|loss|margin|gross|operating|net)\b|"
    r"\b(?:guidance|outlook|forecast|consensus|estimate|guide|reaffirm|reiterat)\b|"
    # growth and direction
    r"\b(?:grew|grow|growth|growing|accelerat|decelerat|expand|contract|decline|declining|decreas|increas)\b|"
    r"\b(?:improv|deterior|ramp|scaling|scale|surge|plunge)\b|"
    # tone / qualitative signals
    r"\b(?:strong|weak|robust|healthy|soft|tough|challenging|uncertain|confident|cautious|optimistic|pessimistic)\b|"
    r"\b(?:record|exceptional|outstanding|solid|resilient|disappointing|disappointed)\b|"
    # business dynamics
    r"\b(?:demand|supply|backlog|bookings|pipeline|orders?|inventory|capacity|allocation|utilization)\b|"
    r"\b(?:customer|client|enterprise|consumer|segment|product|platform|market|share)\b|"
    # beat / miss / comparison language
    r"\b(?:raised|lowered|beat|beating|miss|missed|exceed|exceeded|surpass|surpassed|fell short|in.line|above|below)\b|"
    r"\b(?:year.over.year|quarter.over.quarter|yoy|qoq|sequential)\b|"
    r"\b(?:versus|compared|relative to|against)\b|"
    # risk
    r"\b(?:risk|headwind|tailwind|pressure|constrain|concentrat|diversif|regulator|tariff|export|license|china|sanction)\b|"
    r"\b(?:exposure|impact|threat|opportunity)\b|"
    # competitive position / ranking claims
    r"\b(?:we are|we're|our)\s+(?:the|number|#1|market leader|dominant|leading)|"
    r"\b(?:largest|biggest|strongest|best|leader|leading|dominant|first|only)\b|"
    # magnitude adjectives
    r"\b(?:tripled|doubled|halved|significant|significantly|material|materially|meaningful|substantial|modest|slight)\b|"
    r"\b(?:all[- ]time|record|unprecedented|multi[- ]year)\b|"
    # forward-looking verbs
    r"\b(?:expect|anticipate|plan|target|aim|project|believe|continue|intend)\b|"
    # guidance direction
    r"\b(?:accelerat|decelerat|pickup|slowdown|momentum)\b"
    r")",
    re.IGNORECASE,
)


def is_materially_relevant(sentence: str) -> bool:
    """Cheap pre-filter so we don't burn LLM calls on operator greetings
    and forward-looking-statement boilerplate."""
    if not sentence or len(sentence) < 12:
        return False
    if _MATERIAL_HINTS.search(sentence):
        return True
    return False


# ---------- LLM-driven per-statement analyzer ----------

ANALYZE_PROMPT = """\
You are an earnings-call sentiment engine. Analyze ONE sentence spoken on a live earnings call and return a structured JSON analysis.

Return JSON with this exact shape (omit any field you don't need):

{
  "is_material": true | false,
  "materiality_reason": "<one short sentence>",
  "is_mixed": true | false,
  "mixed_note": "<if mixed, describe the tension in one sentence>",
  "dimensions": [
    {
      "name": "results_vs_expectations | forward_outlook | demand_strength | supply_execution | margins_profitability | customer_mix | competitive_position | regulatory_geographic_risk | management_credibility | q_and_a_quality",
      "score": 15 | 25 | 40 | 50 | 60 | 75 | 88,
      "confidence": "low | medium | high",
      "reason": "<one short sentence — must paraphrase concrete content from the sentence (numbers, guidance, product, geography) — not generic praise>",
      "evidence_type": "numeric | explicit_guidance | qualitative_specific | qualitative_vague | inferred"
    }
  ],
  "trigger_flags": [
    "record_quarter" | "beat" | "miss" | "guidance_raise" | "guidance_cut" |
    "demand_strength" | "demand_softness" | "supply_constraint" | "supply_improving" |
    "margin_expansion" | "margin_pressure" | "customer_concentration" | "diversification" |
    "regulatory_risk" | "important_quote" | "contradiction_or_tension"
  ],
  "entities": [
    {"name": "<company/product/geography>", "type": "company | product | geography | regulator | customer_type", "sentiment": 15 | 25 | 40 | 50 | 60 | 75 | 88, "reason": "<why>"}
  ],
  "bullish_phrase": "<a short paraphrase of the positive signal, or null>",
  "bearish_phrase": "<a short paraphrase of the negative signal, or null>",
  "suggested_investor_question": "<if this statement opens a material gap, what would an analyst ask? else null>"
}

HARD RULES:

1. MATERIALITY: set is_material=false for operator scripts, forward-looking-statement disclaimers, thank-yous, and any sentence with NO concrete business signal. If is_material=false, return ONLY {"is_material": false, "materiality_reason": "..."}.

2. SCORE BANDS: every dimension score MUST be exactly one of: 15 (strongly negative), 25 (negative), 40 (mildly negative), 50 (neutral), 60 (mildly positive), 75 (positive), 88 (strongly positive). No other values allowed.

3. CALIBRATION — read these rules carefully, they override your instinct to pick extreme bands:

   a. QUALITATIVE-ONLY signals (no numbers, no comparison to an outlook) MAX at 60-65 regardless of how absolute the language sounds. "Demand remains very strong" -> 60 or 65, NEVER 75 or 88. Only numeric quantification or explicit beat-vs-outlook language can justify 75+.

   b. TEMPORARY TAILWINDS cap margin scores at 60. "Gross margin expanded, benefited from favorable component costs" -> 60, NOT 75. The word "benefited from [temporary input]" is a sustainability red flag. Reason must mention that the tailwind is temporary.

   c. STRUCTURAL IMPROVEMENT (mix shift, pricing, scale) can justify 75 on margins_profitability.

   d. NUMERIC BEAT vs explicit guidance or consensus -> 88 on results_vs_expectations with high confidence. Example: "$22.1B, well above our $20B outlook" -> 88, numeric evidence.

   e. DEMAND > SUPPLY language in the same sentence is ALWAYS mixed: demand_strength=88, supply_execution=40, is_mixed=true, trigger_flags must include "supply_constraint" AND "demand_strength" AND "contradiction_or_tension".

   f. CONCENTRATION language ("half from large cloud providers", "top 3 customers represent 60%") -> customer_mix=40 with "customer_concentration" flag. Even if the revenue is large, concentration is a mixed-to-negative signal.

   g. DIVERSIFICATION across industries/geographies -> customer_mix 60-70.

   h. VAGUE OPTIMISM ("excited about the future", "well positioned", "strong quarter") without numbers or specifics -> is_material=FALSE.

   i. GUIDANCE RAISED with numeric support -> forward_outlook 80-88. Guidance cut -> 15-25.

   j. REGULATORY / EXPORT CONTROL / LICENSE restrictions -> regulatory_geographic_risk 15-30. Mitigation commentary alone does NOT erase this — cap at 45 even with mitigation.

4. ONLY SCORE DIMENSIONS ACTUALLY ADDRESSED. If the sentence is about demand, score demand_strength only and leave other dimensions out.

5. MIXED STATEMENTS can score multiple dimensions in opposite directions. Set is_mixed=true and explain the tension in mixed_note.

6. TRIGGER FLAGS — include every flag that applies. "important_quote" for any striking numeric or competitive claim.

7. ENTITIES — include other companies, products, geographies mentioned. Name-drop alone is neutral (50).

8. Return ONLY the JSON object. No markdown fences, no commentary.

SENTENCE TO ANALYZE:
"""


@dataclass
class DimensionHistoryEntry:
    score: int
    confidence: str


BLEND_THRESHOLD = 15  # number of dim entries at which dimensions fully override baseline

# Any scored dimension outside neutral counts as a "driver" for UI (rationale cards).
DRIVER_POS_MIN = 58  # score >= → bullish driver list
DRIVER_NEG_MAX = 42  # score <= → bearish driver list
MAX_DRIVER_ROWS = 48  # trim before top_drivers() picks the best


@dataclass
class EarningsSentimentState:
    """Running state for a single earnings call session."""

    # Per-dimension sliding window of the most recent entries.
    dim_history: dict[str, deque] = field(
        default_factory=lambda: {d: deque(maxlen=30) for d in DIMENSIONS}
    )
    # Cumulative trigger counts (for risk overlay computation).
    trigger_counts: dict[str, int] = field(default_factory=dict)
    # Bullish/bearish phrase history (most recent kept).
    bullish_phrases: deque = field(default_factory=lambda: deque(maxlen=5))
    bearish_phrases: deque = field(default_factory=lambda: deque(maxlen=5))
    mixed_notes: deque = field(default_factory=lambda: deque(maxlen=5))
    # Baseline from analyst consensus. The overall score blends this with
    # the weighted dimension aggregate — baseline dominates early, the
    # dimension aggregate dominates once we've seen enough evidence.
    baseline_score: int = 50
    # Last overall score so we can compute trend.
    last_overall_score: int = 50
    # Count of material statements processed.
    material_count: int = 0
    mixed_count: int = 0
    # Count of LLM classifier failures — surfaces visibility instead of
    # silently dropping statements.
    classifier_errors: int = 0
    # Top drivers by contribution — (score, dimension, reason, quote).
    positive_drivers: list[dict] = field(default_factory=list)
    negative_drivers: list[dict] = field(default_factory=list)
    # Investor questions surfaced by the analyzer.
    investor_questions: list[str] = field(default_factory=list)

    def apply_analysis(
        self,
        analysis: dict,
        sentence: str,
        speaker: str | None = None,
    ) -> None:
        """Fold a single per-statement analysis into the running state."""
        if not analysis.get("is_material"):
            return
        self.material_count += 1
        if analysis.get("is_mixed"):
            self.mixed_count += 1
            if analysis.get("mixed_note"):
                self.mixed_notes.append(analysis["mixed_note"])

        sp = (speaker or "CALL").strip().upper()
        source_label = {
            "YOU": "User (briefing)",
            "CALL": "Earnings webcast",
            "AGENT": "Assistant",
        }.get(sp, sp or "Transcript")

        for dim_obj in analysis.get("dimensions", []) or []:
            name = dim_obj.get("name")
            if name not in DIMENSIONS:
                continue
            score = int(dim_obj.get("score") or 50)
            # Snap to nearest valid band (defensive).
            score = min(SCORE_BANDS, key=lambda b: abs(b - score))
            confidence = dim_obj.get("confidence") or "medium"
            if confidence not in CONFIDENCE_MULTIPLIER:
                confidence = "medium"
            self.dim_history[name].append(DimensionHistoryEntry(score, confidence))

            w_dim = DIMENSION_WEIGHTS.get(name, 0.05)
            conf_m = CONFIDENCE_MULTIPLIER.get(confidence, 0.7)
            # Rough directional pull on the blended composite (for UI only).
            estimated_sway_pts = round(
                (score - 50) / 50.0 * w_dim * conf_m * 22.0,
                2,
            )

            entry = {
                "dimension": name,
                "score": score,
                "delta_from_neutral": score - 50,
                "estimated_sway_pts": estimated_sway_pts,
                "reason": (dim_obj.get("reason") or "").strip(),
                "quote": sentence[:280],
                "confidence": confidence,
                "evidence_type": (dim_obj.get("evidence_type") or "inferred").strip(),
                "source_label": source_label,
                "source_kind": "live_transcript",
            }
            if score >= DRIVER_POS_MIN:
                self.positive_drivers.append(entry)
            elif score <= DRIVER_NEG_MAX:
                self.negative_drivers.append(entry)

        self._trim_driver_list(self.positive_drivers)
        self._trim_driver_list(self.negative_drivers)

        for flag in analysis.get("trigger_flags", []) or []:
            self.trigger_counts[flag] = self.trigger_counts.get(flag, 0) + 1

        if analysis.get("bullish_phrase"):
            self.bullish_phrases.append(analysis["bullish_phrase"])
        if analysis.get("bearish_phrase"):
            self.bearish_phrases.append(analysis["bearish_phrase"])

        if analysis.get("suggested_investor_question"):
            q = analysis["suggested_investor_question"]
            if q and q not in self.investor_questions:
                self.investor_questions.append(q)

    def _trim_driver_list(self, lst: list[dict]) -> None:
        """Keep the highest-impact rows so lists cannot grow without bound."""

        def _impact(e: dict) -> float:
            return abs(int(e.get("score") or 50) - 50) * CONFIDENCE_MULTIPLIER.get(
                e.get("confidence") or "medium", 0.7
            )

        if len(lst) <= MAX_DRIVER_ROWS:
            return
        lst.sort(key=_impact, reverse=True)
        del lst[MAX_DRIVER_ROWS:]

    def dimension_averages(self) -> dict[str, int]:
        """Per-dimension rolling average, weighted by confidence."""
        out: dict[str, int] = {}
        for dim, history in self.dim_history.items():
            if not history:
                continue
            total_weight = 0.0
            weighted_sum = 0.0
            for entry in history:
                w = CONFIDENCE_MULTIPLIER.get(entry.confidence, 0.4)
                weighted_sum += entry.score * w
                total_weight += w
            if total_weight > 0:
                out[dim] = int(round(weighted_sum / total_weight))
        return out

    def overall_score(self) -> int:
        """Blended aggregate — baseline dominates early, dimensions take
        over as real evidence accumulates. Prevents two bugs:

          1. Whip: first dimension fires at 88 and the gauge jumps from
             50 (neutral default) to 88 on a single statement.
          2. Baseline wipeout: returning 50 immediately overwrites the
             analyst-consensus baseline we broadcast at briefing time.
        """
        dim_avgs = self.dimension_averages()
        total_entries = sum(len(h) for h in self.dim_history.values())

        if not dim_avgs or total_entries == 0:
            # Nothing scored yet — return the analyst-consensus baseline.
            return max(0, min(100, self.baseline_score))

        total_weight = 0.0
        weighted_delta = 0.0
        for dim, weight in DIMENSION_WEIGHTS.items():
            if dim not in dim_avgs:
                continue
            weighted_delta += (dim_avgs[dim] - 50) * weight
            total_weight += weight
        if total_weight == 0:
            return max(0, min(100, self.baseline_score))

        dim_score = 50 + (weighted_delta / total_weight)

        # Smooth transition from baseline-only to dimension-only.
        # total_entries=0  → 100% baseline
        # total_entries=BLEND_THRESHOLD → 100% dimensions
        dim_weight = min(1.0, total_entries / BLEND_THRESHOLD)
        baseline_weight = 1.0 - dim_weight
        blended = self.baseline_score * baseline_weight + dim_score * dim_weight
        return max(0, min(100, round(blended)))

    def label(self, score: int) -> str:
        if score < 20:
            return "very_negative"
        if score < 40:
            return "negative"
        if score < 50:
            return "mixed_negative"
        if score <= 54:
            return "neutral"
        if score <= 64:
            return "mixed_positive"
        if score <= 84:
            return "positive"
        return "very_positive"

    def trend(self, current: int) -> str:
        if current > self.last_overall_score + 2:
            return "rising"
        if current < self.last_overall_score - 2:
            return "falling"
        return "stable"

    def risk_overlay(self) -> dict[str, Any]:
        dim_avgs = self.dimension_averages()
        drivers: list[str] = []

        reg_avg = dim_avgs.get("regulatory_geographic_risk")
        if reg_avg is not None and reg_avg < 35:
            drivers.append(f"Regulatory/geographic exposure (dimension avg {reg_avg})")

        supply_avg = dim_avgs.get("supply_execution")
        demand_avg = dim_avgs.get("demand_strength")
        if (
            supply_avg is not None
            and demand_avg is not None
            and supply_avg < 40
            and demand_avg > 70
        ):
            drivers.append(
                f"Supply constrained ({supply_avg}) while demand is strong ({demand_avg})"
            )

        if self.trigger_counts.get("customer_concentration", 0) >= 2:
            drivers.append("Repeated customer concentration flags")

        if self.trigger_counts.get("contradiction_or_tension", 0) >= 2:
            drivers.append("Multiple contradiction or tension flags")

        if self.trigger_counts.get("guidance_cut", 0) >= 1:
            drivers.append("Guidance was cut during the call")

        if self.trigger_counts.get("margin_pressure", 0) >= 2:
            drivers.append("Repeated margin pressure signals")

        if len(drivers) >= 2:
            level = "high"
        elif len(drivers) == 1:
            level = "medium"
        else:
            level = "low"
        return {"level": level, "drivers": drivers}

    def top_drivers(self, limit: int = 6) -> tuple[list[dict], list[dict]]:
        def _impact(e: dict) -> float:
            return abs(int(e.get("score") or 50) - 50) * CONFIDENCE_MULTIPLIER.get(
                e.get("confidence") or "medium", 0.7
            )

        pos = sorted(self.positive_drivers, key=_impact, reverse=True)[:limit]
        neg = sorted(self.negative_drivers, key=_impact, reverse=True)[:limit]
        return pos, neg


# ---------- LLM call wrapper ----------

_client_singleton: genai.Client | None = None


def _client() -> genai.Client:
    global _client_singleton
    if _client_singleton is None:
        _client_singleton = genai.Client(api_key=GEMINI_API_KEY)
    return _client_singleton


async def analyze_statement(
    sentence: str,
    *,
    prior_call_context: str | None = None,
) -> dict[str, Any] | None:
    """Run the LLM classifier on a single material sentence. Returns the
    parsed JSON analysis dict or None on failure.

    When ``prior_call_context`` is set, the model sees a short recap of the
    last reported call for continuity (compare new remarks to that backdrop).
    """
    if not sentence:
        return None
    pc = (prior_call_context or "").strip()
    if pc:
        body = (
            f"{ANALYZE_PROMPT}\n\n"
            "PRIOR REPORTED CALL (context only — score the SENTENCE below, not this recap):\n"
            f"{pc[:4000]}\n\n"
            f"SENTENCE:\n{sentence}"
        )
    else:
        body = f"{ANALYZE_PROMPT}\n{sentence}"
    try:
        client = _client()
        response = await client.aio.models.generate_content(
            model=ANALYSIS_MODEL,
            contents=body,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0,
            ),
        )
        raw = getattr(response, "text", None) or "{}"
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj
    except Exception:
        return None
    return None
