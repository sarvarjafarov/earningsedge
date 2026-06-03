"""Deprecated: prior-call recap.

EarningsEdge previously generated a pre-call recap using an external transcript
provider. That dependency was removed; live call transcript is the only
transcript source now.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import re
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google import genai
from google.genai import types

# `earningsedge/` — same folder as `backend/`. Used to find `.env` regardless of
# whether uvicorn's cwd is `earningsedge`, `earningsedge/backend`, or elsewhere.
_APP_ROOT = Path(__file__).resolve().parent.parent


def _resolve_gemini_api_key() -> str:
    """Load `.env` from likely locations, then read GEMINI_API_KEY (call-time)."""
    candidates = [
        _APP_ROOT / ".env",
        Path.cwd() / ".env",
    ]
    if Path.cwd().name == "backend":
        candidates.append(Path.cwd().parent / ".env")
    for env_path in candidates:
        if env_path.is_file():
            load_dotenv(env_path, override=False)
    key = os.getenv("GEMINI_API_KEY", "").strip().strip('"').strip("'")
    if key:
        return key
    try:
        from tools import GEMINI_API_KEY as _tools_key

        return str(_tools_key or "").strip()
    except Exception:
        return ""


# Best-effort at import (summarize still calls _resolve_gemini_api_key()).
load_dotenv(_APP_ROOT / ".env", override=False)
GEMINI_API_KEY = _resolve_gemini_api_key()
MODEL = "gemini-2.5-flash"
MAX_TRANSCRIPT_CHARS = 120_000


_TRANSIENT_GEMINI_PATTERNS = (
    # Common Gemini overload messages
    re.compile(r"\b503\b", re.IGNORECASE),
    re.compile(r"\bunavailable\b", re.IGNORECASE),
    re.compile(r"high\s+demand", re.IGNORECASE),
    re.compile(r"resource\s+exhausted", re.IGNORECASE),
    re.compile(r"rate\s*limit", re.IGNORECASE),
    re.compile(r"quota", re.IGNORECASE),
)


def _looks_transient_gemini_error(exc: Exception) -> bool:
    msg = str(exc) or ""
    # Some SDK exceptions include dict-ish payloads in the message.
    return any(p.search(msg) for p in _TRANSIENT_GEMINI_PATTERNS)


async def _sleep_with_jitter(seconds: float) -> None:
    if seconds <= 0:
        return
    # Jitter to avoid stampeding herd when multiple users retry.
    jitter = seconds * random.uniform(0.0, 0.2)
    await asyncio.sleep(seconds + jitter)


def _period_label(date_s: str, quarter: str, year: str) -> str:
    q = (quarter or "").strip()
    y = (year or "").strip()
    d = (date_s or "").strip()
    if q and y:
        return f"Q{q.replace('Q', '').strip()} {y}".strip()
    if d:
        return d[:10]
    return "latest available"


async def summarize_prior_earnings_call(
    *,
    ticker: str,
    company_name: str,
    transcript_text: str,
    date_s: str,
    quarter: str,
    year: str,
) -> dict[str, Any]:
    """Return summary + key_points + watch_items, or error-filled dict."""
    sym = (ticker or "").strip().upper()
    period = _period_label(date_s, quarter, year)
    base: dict[str, Any] = {
        "ticker": sym,
        "period_label": period,
        "call_date": (date_s or "")[:32],
        "source": "Octagon transcript + Gemini summary",
        "summary": "",
        "key_points": [],
        "watch_items": [],
        "error": "",
    }
    api_key = _resolve_gemini_api_key()
    if not api_key:
        base["error"] = (
            "GEMINI_API_KEY not configured — set GEMINI_API_KEY in earningsedge/.env "
            "(same folder as backend/) and restart the API server."
        )
        return base
    blob = (transcript_text or "").strip()
    if not blob:
        base["error"] = "empty transcript text"
        return base
    blob = blob[:MAX_TRANSCRIPT_CHARS]

    prompt = f"""You are a sell-side equity analyst. Read this PRIOR earnings call transcript for {company_name} ({sym}), period {period}.

Produce a concise recap for someone who is about to listen to the NEXT call. Focus on:
- Reported results vs expectations (if stated), guidance, and tone
- Key KPIs, segments, demand/supply, margins
- Risks, uncertainties, and open questions from Q&A
- What to listen for in the upcoming call (continuity / deltas)

Return JSON only with this exact shape:
{{
  "summary": "<2-4 short paragraphs, plain text, no markdown>",
  "key_points": ["<bullet 1>", "... up to 8 items"],
  "watch_items": ["<what to verify or listen for next call>", "... up to 5 items"]
}}

TRANSCRIPT:
{blob}
"""

    # Gemini can return transient 503/UNAVAILABLE during demand spikes.
    # Retry briefly so a temporary overload doesn't become a hard UI failure.
    delays_s = (1.0, 2.0, 4.0, 8.0, 12.0)  # ~27s total (+ jitter)
    last_exc: Exception | None = None
    for attempt in range(len(delays_s) + 1):
        try:
            client = genai.Client(api_key=api_key)
            response = await client.aio.models.generate_content(
                model=MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.2,
                ),
            )
            raw = getattr(response, "text", "") or "{}"
            obj = json.loads(raw)
            last_exc = None
            break
        except Exception as exc:
            last_exc = exc
            is_transient = _looks_transient_gemini_error(exc)
            if attempt >= len(delays_s) or not is_transient:
                obj = None
                break
            await _sleep_with_jitter(delays_s[attempt])

    if last_exc is not None:
        if _looks_transient_gemini_error(last_exc):
            base["error"] = (
                "Gemini summary is temporarily unavailable due to high demand. "
                "Please try again in ~1 minute."
            )
        else:
            base["error"] = f"summarize failed: {last_exc}"
        return base

    if not isinstance(obj, dict):
        base["error"] = "invalid model response"
        return base

    base["summary"] = str(obj.get("summary") or "").strip()
    kps = obj.get("key_points")
    if isinstance(kps, list):
        base["key_points"] = [str(x).strip() for x in kps if str(x).strip()][:8]
    w = obj.get("watch_items")
    if isinstance(w, list):
        base["watch_items"] = [str(x).strip() for x in w if str(x).strip()][:5]

    if not base["summary"] and not base["key_points"]:
        base["error"] = "model returned empty summary"
    return base


def build_agent_context_blob(prior: dict[str, Any]) -> str:
    """Compact text for sentiment/chat/QA prompts (max ~4.5k chars)."""
    parts: list[str] = []
    if prior.get("summary"):
        parts.append(str(prior["summary"])[:2800])
    kps = prior.get("key_points") or []
    if kps:
        parts.append("Key points (prior call): " + " | ".join(str(x) for x in kps[:8]))
    w = prior.get("watch_items") or []
    if w:
        parts.append("Watch next call: " + " | ".join(str(x) for x in w[:5]))
    out = "\n\n".join(parts).strip()
    return out[:4500]
