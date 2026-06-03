"""MacroAgent — one-shot FRED macro snapshot + Gemini equity read.

Runs once on demand (no transcript queue). Fetches `get_macro_snapshot()`,
then asks Gemini 2.5 Flash to interpret the print for equities and the
covered sector.
"""
from __future__ import annotations

import json
import os
import asyncio
from typing import Any, Awaitable, Callable

from google import genai
from google.genai import types

from agents.specialist_schema import make_driver, make_score_block
from tools import get_macro_snapshot

MODEL = "gemini-2.5-flash"

BroadcastFn = Callable[[dict[str, Any]], Awaitable[None]]


def _macro_series_value(block: Any) -> float | None:
    if not isinstance(block, dict):
        return None
    raw = block.get("value")
    if raw in (None, "", "."):
        return None
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _macro_trend_text(block: Any) -> str:
    if not isinstance(block, dict):
        return ""
    tr = block.get("trend")
    if not isinstance(tr, dict):
        return ""
    return " ".join(str(v).lower() for v in tr.values())


def _macro_trend_dir(block: Any) -> str | None:
    """Return 'up' or 'down' if trend implies rising/falling; else None."""
    blob = _macro_trend_text(block)
    tr = block.get("trend") if isinstance(block, dict) else None
    dm = ""
    if isinstance(tr, dict):
        dm = str(tr.get("direction_mom") or "").lower()
    if "rising" in blob or dm == "up":
        return "up"
    if "falling" in blob or dm == "down":
        return "down"
    return None


def _macro_trend_label(block: Any) -> str | None:
    """Return a human label (yoy/mom) containing rising/falling, else None."""
    if not isinstance(block, dict):
        return None
    tr = block.get("trend")
    if not isinstance(tr, dict):
        return None
    for k in ("yoy_label", "mom_label"):
        v = tr.get(k)
        if v:
            s = str(v).strip()
            sl = s.lower()
            if "rising" in sl or "falling" in sl:
                return s
    blob = _macro_trend_text(block)
    if "rising" in blob:
        return "rising"
    if "falling" in blob:
        return "falling"
    return None


def _build_macro_score_block(
    snap: dict[str, Any],
    signal: str,
    equity_summary: str,
    *,
    gemini_parsed: bool,
    sector: str | None = None,
    base_signal: str | None = None,
    sector_signal: str | None = None,
) -> dict[str, Any]:
    sig = str(signal or "NEUTRAL").upper().strip()
    if sig == "TAILWIND":
        score = 65
    elif sig == "HEADWIND":
        score = 35
    else:
        score = 50

    spread_block = snap.get("yield_spread_10y2y")
    spread_val = _macro_series_value(spread_block)
    if spread_val is not None and spread_val < 0:
        score -= 5

    series_keys = ("fed_funds_rate", "cpi", "unemployment_rate", "treasury_10y", "yield_spread_10y2y")
    sample_size = sum(
        1 for k in series_keys if _macro_series_value(snap.get(k)) is not None
    )
    freshness = "fresh" if sample_size >= 4 else "stale"
    if sample_size == 5 and gemini_parsed:
        confidence = "HIGH"
    elif sample_size >= 3:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    eq = (equity_summary or "").strip()
    if eq:
        if sector and sector_signal and base_signal and sector_signal != base_signal:
            reason_prefix = (
                f"Base macro {str(base_signal).lower()} but {sector}-specific {str(sector_signal).lower()}: "
            )
            reason = reason_prefix + eq[:180]
        else:
            reason = eq[:200]
    else:
        reason = f"{sig} macro backdrop from {sample_size} FRED series."

    drivers: list[dict[str, Any]] = []
    for key, direction_map in (
        ("fed_funds_rate", {"rising": "bearish", "falling": "bullish"}),
        ("cpi", {"rising": "bearish", "falling": "bullish"}),
        ("unemployment_rate", {"rising": "bearish", "falling": "bullish"}),
    ):
        block = snap.get(key)
        label = _macro_trend_label(block)
        if not label:
            continue
        lbl_l = label.lower()
        if "rising" in lbl_l:
            dirn = direction_map["rising"]
        elif "falling" in lbl_l:
            dirn = direction_map["falling"]
        else:
            continue
        if isinstance(block, dict):
            val = block.get("value")
            dt = block.get("date")
        else:
            val = None
            dt = None
        evidence = f"{label}: {val} (as of {dt})"
        drivers.append(make_driver(evidence, dirn, 0.6))

    if spread_val is not None and spread_val < 0:
        drivers.append(make_driver("Yield curve inverted", "bearish", 0.5))

    return make_score_block(
        score=score,
        confidence=confidence,
        reason=reason,
        drivers=drivers,
        sample_size=sample_size,
        freshness=freshness,
    )


def _series_line(label: str, block: Any) -> str:
    if not isinstance(block, dict):
        return f"- {label}: (unavailable)"
    val = block.get("value")
    dt = block.get("date")
    if val is None:
        return f"- {label}: (unavailable)"
    extra = ""
    tr = block.get("trend")
    if isinstance(tr, dict) and tr:
        for k in ("yoy_label", "mom_label"):
            if tr.get(k):
                extra = f" [{tr[k]}]"
                break
    return f"- {label}: {val} (as of {dt}){extra}"


class MacroAgent:
    def __init__(
        self,
        broadcast: BroadcastFn,
        ctx: dict[str, Any],
    ) -> None:
        self.broadcast = broadcast
        self.ctx = ctx

    async def run(self) -> None:
        snap = await get_macro_snapshot()
        if not isinstance(snap, dict) or snap.get("error"):
            err = snap.get("error", "macro snapshot failed") if isinstance(snap, dict) else "macro snapshot failed"
            await self.broadcast({
                "type": "status",
                "data": {
                    "state": "error",
                    "message": str(err),
                },
            })
            return

        regime_hint = snap.get("macro_regime", "neutral")
        sector = self.ctx.get("sector")

        pe = snap.get("policy_expectations")
        pe_lines: list[str] = []
        if isinstance(pe, dict) and pe:
            pe_lines = [
                "",
                "Market pricing vs policy (curve proxy — not CME FedWatch):",
                f"- Effective Fed funds (DFF): {pe.get('effective_fed_funds_pct')}% vs 2Y Treasury (GS2): {pe.get('treasury_2y_pct')}%",
                f"- Spread (eff minus 2Y): {pe.get('spread_eff_minus_2y_bps')} bps — {pe.get('interpretation', '')}",
            ]

        lines = [
            "You are a macro strategist. Here are the latest U.S. economic indicators (FRED):",
            "",
            _series_line("Federal funds rate (FEDFUNDS, monthly avg)", snap.get("fed_funds_rate")),
            _series_line("CPI (CPIAUCSL)", snap.get("cpi")),
            _series_line("Unemployment rate (UNRATE)", snap.get("unemployment_rate")),
            _series_line("10-year Treasury yield (GS10)", snap.get("treasury_10y")),
            _series_line("10y minus 2y yield spread (T10Y2Y)", snap.get("yield_spread_10y2y")),
            *pe_lines,
            "",
            f"Rule-of-thumb regime label from policy rate: {regime_hint} (tight if fed funds > 4%, loose if < 2%, else neutral).",
        ]
        if sector:
            lines.extend([
                "",
                f"The user is analyzing a company in this sector: {sector}.",
            ])
        else:
            lines.extend([
                "",
                "No sector context is provided — set sector_note to an empty string.",
            ])
        lines.extend([
            "",
            "Respond with JSON only (no markdown):",
            "- regime: one word exactly: tight, neutral, or loose — the current macro regime.",
            "- equity_summary: at most two short sentences on what this macro backdrop means for equities broadly (no filler).",
            "- sector_note: if a sector was given above, one sentence on what this means SPECIFICALLY for that sector, including whether the sector's typical rate sensitivity, commodity exposure, or consumer dependence is helped or hurt here. If no sector, empty string.",
            "- base_signal: exactly one of: TAILWIND, NEUTRAL, HEADWIND — macro for equities broadly, ignoring sector.",
            "- sector_signal: exactly one of: TAILWIND, NEUTRAL, HEADWIND — macro for the specific sector named above. If no sector was provided, return NEUTRAL.",
        ])
        prompt = "\n".join(lines)

        api_key = os.getenv("GEMINI_API_KEY", "").strip().strip('"').strip("'")
        parsed: dict[str, Any] | None = None
        if api_key:
            backoff_s = (2.0, 5.0)
            for attempt in range(3):
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
                    candidate = json.loads(raw)
                    if isinstance(candidate, dict):
                        parsed = candidate
                        break
                except Exception:
                    if attempt < 2:
                        await asyncio.sleep(backoff_s[attempt])
                        continue
                    parsed = None
                    break

        if not isinstance(parsed, dict):
            # Gemini unavailable (503/high demand/etc). Still render Macro panel with the raw snapshot.
            eq_fb = "Macro snapshot loaded. Gemini summary temporarily unavailable (high demand)."
            base_sig = "NEUTRAL"
            sector_sig = "NEUTRAL"
            sig = "NEUTRAL"
            macro_data = {
                "fed_funds_rate": snap.get("fed_funds_rate"),
                "cpi": snap.get("cpi"),
                "unemployment_rate": snap.get("unemployment_rate"),
                "treasury_10y": snap.get("treasury_10y"),
                "yield_spread_10y2y": snap.get("yield_spread_10y2y"),
                "macro_regime": snap.get("macro_regime"),
                "policy_expectations": snap.get("policy_expectations"),
                "yield_curve": snap.get("yield_curve") or [],
                "equity_summary": eq_fb,
                "sector_note": "" if not sector else "Sector note unavailable until Gemini summary is available.",
                "signal": sig,
                "base_signal": base_sig,
                "sector_signal": sector_sig,
            }
            macro_data["score_block"] = _build_macro_score_block(
                snap,
                sig,
                eq_fb,
                sector=sector,
                base_signal=base_sig,
                sector_signal=sector_sig,
                gemini_parsed=False,
            )
            await self.broadcast({"type": "macro", "data": macro_data})
            return

        equity_summary = str(parsed.get("equity_summary") or "").strip()
        sector_note = str(parsed.get("sector_note") or "").strip()
        if not sector:
            sector_note = ""
        base_sig = str(parsed.get("base_signal") or parsed.get("signal") or "NEUTRAL").upper().strip()
        sector_sig = str(parsed.get("sector_signal") or "NEUTRAL").upper().strip()
        for val, name in ((base_sig, "base_sig"), (sector_sig, "sector_sig")):
            if val not in ("TAILWIND", "NEUTRAL", "HEADWIND"):
                if name == "base_sig":
                    base_sig = "NEUTRAL"
                else:
                    sector_sig = "NEUTRAL"
        final_signal = sector_sig if sector else base_sig
        sig = final_signal

        macro_data = {
            "fed_funds_rate": snap.get("fed_funds_rate"),
            "cpi": snap.get("cpi"),
            "unemployment_rate": snap.get("unemployment_rate"),
            "treasury_10y": snap.get("treasury_10y"),
            "yield_spread_10y2y": snap.get("yield_spread_10y2y"),
            "macro_regime": snap.get("macro_regime"),
            "policy_expectations": snap.get("policy_expectations"),
            "yield_curve": snap.get("yield_curve") or [],
            "equity_summary": equity_summary,
            "sector_note": sector_note,
            "signal": sig,
            "base_signal": base_sig,
            "sector_signal": sector_sig,
        }
        macro_data["score_block"] = _build_macro_score_block(
            snap,
            sig,
            equity_summary,
            sector=sector,
            base_signal=base_sig,
            sector_signal=sector_sig,
            gemini_parsed=True,
        )
        await self.broadcast({"type": "macro", "data": macro_data})

    def stop(self) -> None:
        """No background loop — nothing to cancel."""
        pass
