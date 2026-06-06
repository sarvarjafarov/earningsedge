"""Live persona pulse — rapid-fire reactions from the five named
investor lenses to whatever just streamed through the live audio.

Why this exists: the ADK Chairman delegating through InMemoryRunner
takes 15-25 seconds per pass — fine for a one-off verdict, far too
slow to look "live" as the call unfolds. This module skips the ADK
machinery entirely and fires five direct Gemini calls in parallel, one
per persona, each with a structured JSON output contract. Total wall
clock is bounded by the slowest single call (~3-5 seconds) instead of
the sum.

Trade-off: no tool calls during the pulse. The pulse personas can't
fetch fundamentals or run vector search — they react to *the audio
language they just heard*. That's the point: when the CFO says "supply
has eased", Burry should react in three seconds, not after a
five-tool fan-out.

The full Chairman synthesis still runs on demand (via ``/api/adk/run``)
for the slower, more grounded verdict.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any

from google import genai
from google.genai import types as genai_types

_log = logging.getLogger("earningsedge.persona_pulse")

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")

# Shared client — creating a new genai.Client per persona per call
# leaked HTTP connection pools and accumulated dyno memory under live audio.
_shared_client = None


def _get_client():
    """Lazily construct and reuse a single genai.Client."""
    global _shared_client
    if _shared_client is None:
        from google import genai
        _shared_client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    return _shared_client


# Each persona's *pulse* prompt is short on purpose — we want a quick
# instinctive read, not a full investment memo.
PERSONA_PROMPTS: dict[str, dict[str, str]] = {
    "wood": {
        "display": "Cathie Wood",
        "lens": "5-year disruptive-innovation",
        "instruction": (
            "You are Cathie Wood — disruptive-innovation lens, "
            "5-year horizon, focus on AI infrastructure, robotics, "
            "genomics, blockchain. You write convexity, exponential "
            "adoption, Wright's Law. You think the market chronically "
            "underprices platform shifts."
        ),
    },
    "burry": {
        "display": "Michael Burry",
        "lens": "forensic accounting bear",
        "instruction": (
            "You are Michael Burry — forensic skeptic. You find the "
            "contradiction the bulls miss. You write clipped, declarative "
            "tells. CFO language shifts, deteriorating metrics, accounting "
            "footnotes. You assume management is hiding something."
        ),
    },
    "druckenmiller": {
        "display": "Stan Druckenmiller",
        "lens": "concentrated macro bets",
        "instruction": (
            "You are Stan Druckenmiller — concentrated-position macro "
            "investor. You score asymmetric 6-12 month setups. You write "
            "decisively about risk/reward, position sizing, and macro "
            "regime fit."
        ),
    },
    "cramer": {
        "display": "Jim Cramer",
        "lens": "narrative momentum",
        "instruction": (
            "You are Jim Cramer — rapid headline reaction, narrative "
            "momentum. You write enthusiastically or skeptically. You "
            "react to tone, PT changes, and management commentary."
        ),
    },
    "marks": {
        "display": "Howard Marks",
        "lens": "cycle-position framework",
        "instruction": (
            "You are Howard Marks — cycle-position lens. You ask 'where "
            "are we in the cycle and is the price compensating us for "
            "the risk'. You write thoughtfully, framework-driven. Restrained."
        ),
    },
}


# Tight JSON contract. We use a response_schema so Gemini fills typed
# fields, avoiding MAX_TOKENS truncation that happens with free-form JSON.
_PULSE_USER_PROMPT_TMPL = """\
NVDA-style live transcript for {ticker} (last ~30 lines):

{transcript}

React in your voice. Quote a specific phrase if relevant.
"""

_PULSE_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "sentiment": {
            "type": "number",
            "description": "-1.0 strongly bearish, 0 neutral, +1.0 strongly bullish",
        },
        "confidence": {
            "type": "string",
            "enum": ["LOW", "MEDIUM", "HIGH"],
        },
        "one_line": {
            "type": "string",
            "description": "One sentence in your voice, max 28 words",
        },
        "flag": {
            "type": "string",
            "enum": ["pattern_match", "accounting_concern", "guidance_signal", "tone_shift", "none"],
        },
    },
    "required": ["sentiment", "confidence", "one_line", "flag"],
}


def _extract_json(text: str) -> dict[str, Any] | None:
    """Pull the first {...} block that parses as JSON."""
    if not text:
        return None
    fenced = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except (TypeError, ValueError):
            pass
    bare = re.search(r"\{[\s\S]{0,2000}?\}", text)
    if bare:
        try:
            return json.loads(bare.group(0))
        except (TypeError, ValueError):
            pass
    return None


async def _one_persona(key: str, ticker: str, transcript: str) -> dict[str, Any]:
    """Run a single persona react. Bounded to ~6 seconds via Gemini's own
    response timing on flash; we don't add extra timeouts."""
    persona = PERSONA_PROMPTS[key]
    try:
        client = _get_client()
        prompt = _PULSE_USER_PROMPT_TMPL.format(ticker=ticker, transcript=transcript)
        # Gemini 3 family flash burns "thinking" tokens before output;
        # disable thinking entirely so our 512-token budget is all output.
        thinking_cfg = genai_types.ThinkingConfig(thinking_budget=0)
        cfg = genai_types.GenerateContentConfig(
            system_instruction=persona["instruction"],
            response_mime_type="application/json",
            response_schema=_PULSE_RESPONSE_SCHEMA,
            temperature=0.4,
            max_output_tokens=512,
            thinking_config=thinking_cfg,
        )
        res = await client.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=cfg,
        )
        raw = (res.text or "").strip()
        parsed = _extract_json(raw) or {}
        flag = parsed.get("flag")
        if flag == "none":
            flag = None
        return {
            "key": key,
            "display": persona["display"],
            "lens": persona["lens"],
            "sentiment": float(parsed.get("sentiment", 0)),
            "confidence": str(parsed.get("confidence", "LOW")).upper(),
            "one_line": parsed.get("one_line") or "(no reaction)",
            "flag": flag,
        }
    except Exception as exc:  # noqa: BLE001
        _log.warning("persona %s pulse failed: %s", key, exc)
        return {
            "key": key,
            "display": persona["display"],
            "lens": persona["lens"],
            "sentiment": 0.0,
            "confidence": "LOW",
            "one_line": "(reaction unavailable)",
            "flag": None,
            "error": str(exc),
        }


async def pulse(
    ticker: str,
    transcript_lines: list[str] | str,
    keys: list[str] | None = None,
) -> dict[str, Any]:
    """Run all named-investor personas in parallel against the recent
    transcript. Returns a dict shaped:

        {
          "ok": True,
          "ticker": "NVDA",
          "personas": [
            {key, display, lens, sentiment, confidence, one_line, flag?},
            ...
          ],
          "elapsed_ms": int,
        }

    All personas always come back; individual failures degrade to a
    LOW-confidence neutral reaction with an error field.
    """
    import time

    if isinstance(transcript_lines, list):
        transcript_text = "\n".join(line.strip() for line in transcript_lines if line.strip())
    else:
        transcript_text = (transcript_lines or "").strip()
    if not transcript_text:
        transcript_text = "(no transcript yet — opening remarks pending)"

    target_keys = keys or list(PERSONA_PROMPTS.keys())
    target_keys = [k for k in target_keys if k in PERSONA_PROMPTS]

    t0 = time.monotonic()
    results = await asyncio.gather(*(_one_persona(k, ticker, transcript_text) for k in target_keys))
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    return {
        "ok": True,
        "ticker": ticker.upper(),
        "personas": results,
        "elapsed_ms": elapsed_ms,
    }
