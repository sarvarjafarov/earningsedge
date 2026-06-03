"""Headline-level sentiment for company news (Finnhub has no per-article scores).

Primary path: one batched Gemini JSON call over recent headlines.
Fallback: keyword / phrase heuristics when GEMINI_API_KEY is missing or the model fails.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any

MODEL = "gemini-2.5-flash"

# Phrase-level hints (substring match). Tuned for finance headlines.
_POS_PHRASES = (
    "beat earnings", "beats estimates", "strong buy", "screaming buy", "price target raised",
    "upgrade", "upgraded", "outperform", "overweight", "record revenue", "record quarter",
    "surge", "soars", "soar", "rally", "breakthrough", "partnership", "wins contract",
    "raises guidance", "raises outlook", "bullish", "growth", "expands", "ai chip empire",
)
_NEG_PHRASES = (
    "downgrade", "downgraded", "underperform", "sell rating", "miss", "misses estimates",
    "investigation", "lawsuit", "subpoena", "sec probe", "warning", "cuts guidance",
    "layoff", "layoffs", "recall", "ban", "sanction", "plunge", "plunges", "crash",
    "frustrate", "too late to buy", "selloff", "bearish", "weak demand", "cuts outlook",
    "probe", "alleg", "fraud", "concern", "risk", "struggl", "disappoint",
    # Explicit negation around buying / rating.
    "not buying", "won't buy", "wont buy", "don't buy", "dont buy", "not a buy",
)


def classify_headline_heuristic(headline: str) -> dict[str, Any]:
    t = (headline or "").lower()
    pos = 0.0
    neg = 0.0
    hits: list[str] = []
    for ph in _POS_PHRASES:
        if ph in t:
            pos += 1.2 if len(ph) > 10 else 1.0
            hits.append(f"+{ph}")
    for ph in _NEG_PHRASES:
        if ph in t:
            # Stronger penalty for explicit "not buy" language.
            if ph in ("not buying", "won't buy", "wont buy", "don't buy", "dont buy", "not a buy"):
                neg += 2.0
            else:
                neg += 1.2 if len(ph) > 10 else 1.0
            hits.append(f"-{ph}")
    # Extra single tokens
    if re.search(r"\b(buy|bull)\b.*\b(stock|shares)\b", t) and "too late" not in t:
        pos += 0.5
    if "screaming buy" in t or "strong buy" in t:
        pos += 1.5
    if pos > neg + 0.35:
        lab = "bullish"
        reason = "Keyword scan leans positive (" + ", ".join(hits[:3] or ["tone"]) + ")."
    elif neg > pos + 0.35:
        lab = "bearish"
        reason = "Keyword scan leans negative (" + ", ".join(hits[:3] or ["tone"]) + ")."
    else:
        lab = "neutral"
        reason = "Mixed or non-directional wording vs. baseline keyword lists."
    conf = min(0.82, 0.42 + abs(pos - neg) * 0.12)
    return {"label": lab, "reason": reason[:220], "confidence": round(conf, 2)}


def aggregate_headline_labels(labels: list[str]) -> tuple[str, str]:
    """DEPRECATED: Map per-headline labels to one overall tone + short rationale.

    Newer code should prefer `aggregate_news_records`, which supports richer
    per-article metadata, weighting, and deduplication.
    """
    if not labels:
        return "neutral", ""
    scores: list[int] = []
    for x in labels:
        if x == "bullish":
            scores.append(1)
        elif x == "bearish":
            scores.append(-1)
        else:
            scores.append(0)
    avg = sum(scores) / len(scores)
    b = sum(1 for x in labels if x == "bullish")
    e = sum(1 for x in labels if x == "bearish")
    n = sum(1 for x in labels if x == "neutral")
    rationale = f"Blend of {b} bullish · {e} bearish · {n} neutral (mean tilt {avg:+.2f})."
    if avg > 0.12:
        return "bullish", rationale
    if avg < -0.12:
        return "bearish", rationale
    return "neutral", rationale


# Magnitude multipliers
_MAGNITUDE_WEIGHT = {"major": 3.0, "material": 1.5, "minor": 0.6}

# Event type multipliers — high-signal events carry more weight
_EVENT_WEIGHT = {
    "GUIDANCE": 1.5,
    "LEGAL_REGULATORY": 1.5,
    "EARNINGS": 1.2,
    "ANALYST_ACTION": 1.2,
    "M_AND_A": 1.1,
    "PRODUCT": 1.0,
    "MANAGEMENT": 1.0,
    "MACRO_SECTOR": 0.8,
    "OTHER": 0.8,
}


def aggregate_news_records(
    records: list[dict[str, Any]],
    datetimes: list[Any],
) -> tuple[str, str, float, dict[str, Any]]:
    """Aggregate enriched news records into overall tone, rationale, net_tilt, extras.

    Returns:
      label: "bullish" | "bearish" | "neutral"
      rationale: short plain-English blend description
      net_tilt: -1.0..+1.0 signed weighted tilt
      extras: dict with magnitude_counts, event_counts, and deduplicated count
    """
    if not records:
        return "neutral", "", 0.0, {
            "magnitude_counts": {},
            "event_counts": {},
            "deduplicated_count": 0,
        }

    # Build per-record weights
    scored: list[dict[str, Any]] = []
    for rec, raw_dt in zip(records, datetimes):
        label = rec.get("label", "neutral")
        mag = rec.get("magnitude", "minor")
        etype = rec.get("event_type", "OTHER")
        direction = 1 if label == "bullish" else -1 if label == "bearish" else 0

        mag_w = _MAGNITUDE_WEIGHT.get(mag, 0.6)
        evt_w = _EVENT_WEIGHT.get(etype, 1.0)
        recency_w = recency_weight(raw_dt)
        try:
            confidence = float(rec.get("confidence", 0.5) or 0.5)
        except (TypeError, ValueError):
            confidence = 0.5

        total_weight = mag_w * evt_w * recency_w * confidence
        scored.append({
            "rec": rec,
            "direction": direction,
            "weight": total_weight,
        })

    # Deduplicate: collapse near-identical event+direction stories.
    clusters: dict[tuple, list[dict[str, Any]]] = {}
    for s in scored:
        r = s["rec"]
        key = (r.get("event_type"), s["direction"], r.get("magnitude"))
        clusters.setdefault(key, []).append(s)

    # For each cluster: take top weight + dampened additional contribution
    deduplicated: list[dict[str, Any]] = []
    for key, members in clusters.items():
        members.sort(key=lambda m: m["weight"], reverse=True)
        top = members[0]
        extra = sum(m["weight"] * 0.3 for m in members[1:])
        deduplicated.append({
            "rec": top["rec"],
            "direction": top["direction"],
            "weight": top["weight"] + extra,
            "cluster_size": len(members),
        })

    # Compute signed net tilt
    total_w = sum(d["weight"] for d in deduplicated) or 1.0
    net_tilt = sum(d["direction"] * d["weight"] for d in deduplicated) / total_w
    net_tilt = max(-1.0, min(1.0, net_tilt))

    if net_tilt > 0.15:
        label_out = "bullish"
    elif net_tilt < -0.15:
        label_out = "bearish"
    else:
        label_out = "neutral"

    # Build magnitude and event counts for diagnostics
    mag_counts: dict[str, int] = {}
    evt_counts: dict[str, int] = {}
    for s in scored:
        m = s["rec"].get("magnitude", "minor")
        e = s["rec"].get("event_type", "OTHER")
        mag_counts[m] = mag_counts.get(m, 0) + 1
        evt_counts[e] = evt_counts.get(e, 0) + 1

    # Rationale — English description
    b = sum(1 for s in scored if s["direction"] > 0)
    x = sum(1 for s in scored if s["direction"] < 0)
    n = sum(1 for s in scored if s["direction"] == 0)
    major_n = mag_counts.get("major", 0)
    mat_n = mag_counts.get("material", 0)

    rationale_parts = [f"{b} bullish · {x} bearish · {n} neutral"]
    if major_n:
        rationale_parts.append(f"{major_n} major-magnitude")
    if mat_n:
        rationale_parts.append(f"{mat_n} material")
    if len(deduplicated) < len(scored):
        rationale_parts.append(f"deduped to {len(deduplicated)} events")
    rationale_parts.append(f"weighted tilt {net_tilt:+.2f}")

    rationale = " · ".join(rationale_parts)
    return label_out, rationale, net_tilt, {
        "magnitude_counts": mag_counts,
        "event_counts": evt_counts,
        "deduplicated_count": len(deduplicated),
    }


def _normalize_label(raw: str) -> str:
    s = (raw or "").strip().lower()
    if s in ("bull", "bullish", "positive"):
        return "bullish"
    if s in ("bear", "bearish", "negative"):
        return "bearish"
    return "neutral"


async def classify_headlines(
    ticker: str,
    company_name: str,
    headlines: list[str],
) -> list[dict[str, Any]] | None:
    """Return list aligned with headlines: rich per-article classification.

    Each record includes:
      - label: bullish|bearish|neutral
      - reason: short sentence
      - confidence: 0.0-1.0
      - event_type: enum
      - magnitude: minor|material|major
      - timeframe: today|weeks|quarters|years

    Returns None on model failure (caller may fall back to heuristics).
    """
    api_key = os.getenv("GEMINI_API_KEY", "").strip().strip('"').strip("'")
    if not headlines or not api_key:
        return None
    try:
        from google import genai
        from google.genai import types

        block = "\n".join(f"{i}. {h}" for i, h in enumerate(headlines))
        prompt = f"""You are an equity research assistant. Classify each headline below
for near-term impact on {ticker} ({company_name or ticker}) common stock.

For every headline, extract FIVE fields — not just direction. A descriptive or
neutral-sounding headline can still carry a bearish signal if the underlying
event is bearish (e.g. "Company X announces restructuring" is often bearish
even though the wording is neutral).

direction (label):

"bullish"  — positive for shareholders over the timeframe
"bearish"  — negative for shareholders over the timeframe
"neutral"  — genuinely informational with no directional read

event_type — what kind of event is this?

EARNINGS           — reported results (beats, misses, revenue/margin prints)
GUIDANCE           — forward-looking guidance or outlook changes
ANALYST_ACTION     — upgrades, downgrades, price target changes, initiations
LEGAL_REGULATORY   — lawsuits, probes, settlements, regulatory action, tariffs,
export controls
PRODUCT            — launches, roadmap, benchmarks, customer wins tied to product
M_AND_A            — acquisitions, divestitures, strategic investments
MANAGEMENT         — exec hires, departures, insider activity
MACRO_SECTOR       — macro, sector-wide news that hits this stock via read-through
OTHER              — anything genuinely miscellaneous

magnitude — how material is this event to the thesis?

major     — materially moves the thesis (guidance cuts, major lawsuits,
10%+ upgrades, top-5 customer loss)
material  — meaningful but not thesis-breaking (analyst note, routine upgrade,
minor product launch, contained regulatory item)
minor     — noise, routine commentary, clickbait, broad-market framing

timeframe — how soon does this matter?

today     — same-day price reaction likely
weeks     — plays out over earnings cycle / near-term catalyst window
quarters  — plays out over quarters (execution, guidance validation)
years     — long-horizon strategic implication

reason — one short sentence (≤18 words) citing what in the headline
drove the classification. Never copy the headline verbatim.

Return JSON with this exact shape:
{{
  "items": [
    {{
      "i": 0,
      "label": "bullish|bearish|neutral",
      "reason": "…",
      "confidence": 0.0-1.0,
      "event_type": "EARNINGS|GUIDANCE|ANALYST_ACTION|LEGAL_REGULATORY|PRODUCT|M_AND_A|MANAGEMENT|MACRO_SECTOR|OTHER",
      "magnitude": "minor|material|major",
      "timeframe": "today|weeks|quarters|years"
    }},
    ...
  ]
}}

Headlines (indexed by i):
{block}
"""
        client = genai.Client(api_key=api_key)
        response = await client.aio.models.generate_content(
            model=MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.25,
            ),
        )
        text = getattr(response, "text", "") or "{}"
        parsed = json.loads(text)
        items = parsed.get("items") if isinstance(parsed, dict) else None
        if not isinstance(items, list):
            return None

        allowed_event_type = {
            "EARNINGS",
            "GUIDANCE",
            "ANALYST_ACTION",
            "LEGAL_REGULATORY",
            "PRODUCT",
            "M_AND_A",
            "MANAGEMENT",
            "MACRO_SECTOR",
            "OTHER",
        }
        allowed_magnitude = {"minor", "material", "major"}
        allowed_timeframe = {"today", "weeks", "quarters", "years"}

        def _norm_event_type(x: Any) -> str:
            s = str(x or "").strip().upper()
            return s if s in allowed_event_type else "OTHER"

        def _norm_magnitude(x: Any) -> str:
            s = str(x or "").strip().lower()
            return s if s in allowed_magnitude else "minor"

        def _norm_timeframe(x: Any) -> str:
            s = str(x or "").strip().lower()
            return s if s in allowed_timeframe else "weeks"

        out: list[dict[str, Any]] = []
        by_i: dict[int, dict[str, Any]] = {}
        for it in items:
            if not isinstance(it, dict):
                continue
            try:
                idx = int(it.get("i", -1))
            except (TypeError, ValueError):
                continue
            lab = _normalize_label(str(it.get("label", "neutral")))
            reason = str(it.get("reason", "")).strip()[:240]
            try:
                conf = float(it.get("confidence", 0.65))
            except (TypeError, ValueError):
                conf = 0.65
            conf = max(0.0, min(1.0, conf))
            by_i[idx] = {
                "label": lab,
                "reason": reason or "Model classification.",
                "confidence": round(conf, 2),
                "event_type": _norm_event_type(it.get("event_type")),
                "magnitude": _norm_magnitude(it.get("magnitude")),
                "timeframe": _norm_timeframe(it.get("timeframe")),
            }

        for i in range(len(headlines)):
            if i in by_i:
                out.append(by_i[i])
            else:
                base = classify_headline_heuristic(headlines[i])
                out.append({
                    **base,
                    "event_type": "OTHER",
                    "magnitude": "minor",
                    "timeframe": "weeks",
                })
        if len(out) != len(headlines):
            return None
        return out
    except Exception:
        return None


def format_news_datetime(raw: Any) -> str:
    if raw is None:
        return ""
    try:
        ts = int(raw)
        if ts > 1_000_000_000_000:
            ts = ts // 1000
        return datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M UTC")
    except (TypeError, ValueError, OSError):
        s = str(raw).strip()
        return s[:28] if s else ""


def recency_weight(raw_datetime: Any, now_ts: int | None = None) -> float:
    """Linear decay over a 14-day window.

    Today → 1.0
    14 days ago → 0.0
    Older than 14 days → 0.0
    Unparseable → 0.5 (don't fully drop unknown-age items)
    """
    import time

    if raw_datetime is None:
        return 0.5
    try:
        ts = int(raw_datetime)
        if ts > 1_000_000_000_000:
            ts = ts // 1000
    except (TypeError, ValueError):
        return 0.5
    now = now_ts if now_ts is not None else int(time.time())
    age_days = (now - ts) / 86400.0
    if age_days < 0:
        return 1.0
    if age_days >= 14:
        return 0.0
    return max(0.0, 1.0 - (age_days / 14.0))
