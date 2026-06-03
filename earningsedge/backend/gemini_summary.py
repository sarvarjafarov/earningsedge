"""One-shot post-call summary generation. Lives in its own module so the
orchestrator doesn't have to import the full Live API stack just to call
the regular generate_content path."""
from __future__ import annotations

import json
import os
from typing import Any

from google import genai
from google.genai import types

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
ANALYSIS_MODEL = "gemini-2.5-flash"

SUMMARY_PROMPT = """\
You are EarningsEdge generating a polished post-call analyst report.

You will receive a JSON dump of the complete session record from a live earnings call analysis. Synthesize:
1. A structured report (for on-screen display)
2. A short spoken-language version (for the voice agent to read aloud)

Return EXACTLY ONE valid JSON object with this shape:
{
  "company": {
    "ticker": "string", "name": "string", "sector": "string",
    "quarter": "string", "fiscal_year": "string", "call_date": "string"
  },
  "headline": "One-sentence verdict",
  "metrics_recap": [
    {"label": "Revenue", "value": "...", "estimate": "...", "beat_miss": "beat|miss|inline", "note": "..."}
  ],
  "sentiment_arc": [
    {"phase": "Opening|Prepared remarks|Guidance|Q&A|Closing", "score": 0, "summary": "..."}
  ],
  "competitors_recap": "One paragraph",
  "news_recap": "One paragraph",
  "qa_recap": [{"question": "...", "reason": "..."}],
  "trade_signal": {"signal": "BUY|HOLD|SELL", "confidence": "HIGH|MEDIUM|LOW", "thesis": "...", "key_risk": "..."},
  "user_qa": [{"question": "...", "answer": "..."}],
  "spoken": "EXACTLY 4-5 sentences for the voice agent: company+quarter, biggest beat/miss, one risk, trade signal. Spell out numbers."
}

RULES:
- Use ONLY data from the session record. Never invent figures.
- For the "company" object: ALWAYS populate ticker, name, sector, quarter,
  fiscal_year from the session record's "company" field. These are loaded
  from the briefing and are always present — never substitute "No data
  captured" for company metadata.
- For sections where the session has genuinely no data (empty transcript,
  empty metrics_history, etc.), use empty arrays [] OR return a short
  honest phrase like "Call was brief and no specific metrics were cited"
  — NEVER literally "No data captured".
- metrics_recap: only include metrics that were explicitly cited on the
  call. Do not invent estimates.
- The trade_signal MUST be one of BUY / HOLD / SELL. NEVER use WAIT. If
  there isn't enough evidence for BUY or SELL, return HOLD.
- Output ONLY the JSON object — no markdown fences, no commentary.
"""


async def generate_summary(session_record: dict[str, Any]) -> dict[str, Any]:
    client = genai.Client(api_key=GEMINI_API_KEY)
    payload = json.dumps(session_record, indent=2, default=str)
    prompt = SUMMARY_PROMPT + "\n\nSESSION DATA:\n" + payload
    response = await client.aio.models.generate_content(
        model=ANALYSIS_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.3,
            response_mime_type="application/json",
        ),
    )
    text = getattr(response, "text", None) or "{}"
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"error": "failed to parse summary", "raw": text[:500]}
