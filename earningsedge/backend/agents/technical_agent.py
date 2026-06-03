"""TechnicalAgent — one-shot Alpha Vantage indicators + Gemini technical read.

RSI, MACD, SMA trend context for the technical panel.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Awaitable, Callable

from google import genai
from google.genai import types

from agents.specialist_schema import make_driver, make_score_block
from tools import get_stock_data, get_yfinance_snapshot

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
MODEL = "gemini-2.5-flash"

BroadcastFn = Callable[[dict[str, Any]], Awaitable[None]]


def _f(x: Any) -> float | None:
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _latest_ts_row(data: dict[str, Any], block_name: str) -> dict[str, Any] | None:
    """Pick the row for the latest ISO date key (Alpha Vantage order can vary)."""
    ts = data.get(block_name)
    if not isinstance(ts, dict) or not ts:
        return None
    date_keys = [
        k
        for k in ts.keys()
        if isinstance(k, str) and len(k) >= 8 and k[:4].isdigit()
    ]
    if not date_keys:
        return None
    row = ts[max(date_keys)]
    return row if isinstance(row, dict) else None


def _macd_values_from_row(row: dict[str, Any] | None) -> tuple[float | None, float | None, float | None]:
    if not row:
        return None, None, None

    def pick(keys: tuple[str, ...]) -> float | None:
        for k in keys:
            if k not in row:
                continue
            v = row.get(k)
            if v in (None, "", "None", "null"):
                continue
            return _f(v)
        return None

    m = pick(("MACD", "macd"))
    s = pick(("MACD_Signal", "MACD Signal", "signal", "Signal"))
    h = pick(("MACD_Hist", "MACDHist", "MACD Hist", "histogram", "Histogram", "macdHistogram"))
    return m, s, h


async def _snapshot_indicators(ticker: str) -> dict[str, Any]:
    """Pull all indicators in one go from yfinance (via tools)."""
    snap = await get_yfinance_snapshot(ticker)
    if not isinstance(snap, dict) or snap.get("error"):
        return {}
    return snap


async def _fetch_rsi(ticker: str) -> float | None:
    try:
        snap = await _snapshot_indicators(ticker)
        return snap.get("rsi_14")
    except Exception:
        return None


async def _fetch_macd_alpha(ticker: str) -> tuple[float | None, float | None, float | None]:
    try:
        snap = await _snapshot_indicators(ticker)
        return snap.get("macd"), snap.get("macd_signal"), snap.get("macd_hist")
    except Exception:
        return None, None, None


async def _fetch_macd(ticker: str) -> tuple[float | None, float | None, float | None]:
    try:
        m, s, h = await _fetch_macd_alpha(ticker)
        if h is not None or (m is not None and s is not None):
            return m, s, h
        return None, None, None
    except Exception:
        return None, None, None


def _ema(values: list[float], span: int) -> list[float]:
    """Simple EMA over a list of values (oldest->newest)."""
    if not values or span <= 1:
        return values[:]
    alpha = 2.0 / (span + 1.0)
    out: list[float] = []
    prev = values[0]
    out.append(prev)
    for v in values[1:]:
        prev = alpha * v + (1 - alpha) * prev
        out.append(prev)
    return out


def _sma_last(values: list[float], window: int) -> float | None:
    if window <= 0 or len(values) < window:
        return None
    return sum(values[-window:]) / float(window)


def _rsi_last(values: list[float], period: int = 14) -> float | None:
    if len(values) < period + 1:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for i in range(-period, 0):
        d = values[i] - values[i - 1]
        if d >= 0:
            gains.append(d)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(-d)
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss <= 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


async def _compute_indicators_from_series(
    ticker: str,
) -> tuple[float | None, float | None, float | None, float | None, float | None, float | None]:
    """Load indicators from the yfinance snapshot (single consolidated fetch)."""
    try:
        snap = await _snapshot_indicators(ticker)
        return (
            snap.get("macd"),
            snap.get("macd_signal"),
            snap.get("macd_hist"),
            snap.get("rsi_14"),
            snap.get("sma_50"),
            snap.get("sma_200"),
        )
    except Exception:
        return None, None, None, None, None, None


async def _fetch_sma(ticker: str, period: int) -> float | None:
    try:
        snap = await _snapshot_indicators(ticker)
        if period == 50:
            return snap.get("sma_50")
        if period == 200:
            return snap.get("sma_200")
        return None
    except Exception:
        return None


def _trend(price: float | None, sma50: float | None, sma200: float | None) -> str:
    if price is None or sma50 is None or sma200 is None:
        return "MIXED"
    if price > sma50 > sma200:
        return "UPTREND"
    if price < sma50 < sma200:
        return "DOWNTREND"
    return "MIXED"


def _rsi_signal(rsi: float | None) -> str:
    if rsi is None:
        return "NEUTRAL"
    if rsi > 70:
        return "OVERBOUGHT"
    if rsi < 30:
        return "OVERSOLD"
    return "NEUTRAL"


def _macd_signal_from_hist(hist: float | None) -> str:
    if hist is None:
        return "NEUTRAL"
    if hist > 0:
        return "BULLISH"
    if hist < 0:
        return "BEARISH"
    return "NEUTRAL"


def _technical_signal_vectors(trend: str, rsi_sig: str, macd_sig: str) -> tuple[int, int, int]:
    """Return (bullish_push, bearish_push, neutral_count) for trend/RSI/MACD."""
    bull = bear = 0
    if trend == "UPTREND":
        bull += 1
    elif trend == "DOWNTREND":
        bear += 1
    if rsi_sig == "OVERSOLD":
        bull += 1
    elif rsi_sig == "OVERBOUGHT":
        bear += 1
    if macd_sig == "BULLISH":
        bull += 1
    elif macd_sig == "BEARISH":
        bear += 1
    neut = 3 - bull - bear
    return bull, bear, neut


def _technical_agree(trend: str, rsi_sig: str, macd_sig: str) -> bool:
    """True only if trend/RSI/MACD are all aligned (or all neutral)."""
    bull, bear, neut = _technical_signal_vectors(trend, rsi_sig, macd_sig)
    # All three bullish, all three bearish, or all three neutral.
    return (bull == 3 and bear == 0 and neut == 0) or (bear == 3 and bull == 0 and neut == 0) or (
        neut == 3 and bull == 0 and bear == 0
    )


def _build_technical_score_block(
    overall: str,
    trend: str,
    rsi_sig: str,
    macd_sig: str,
    rsi: float | None,
    macd_hist: float | None,
    sma_50: float | None,
    sma_200: float | None,
    current_price: float | None,
    one_line_summary: str,
) -> dict[str, Any]:
    o = str(overall or "NEUTRAL").upper().strip()
    if o == "BULLISH":
        score = 65
    elif o == "BEARISH":
        score = 35
    else:
        score = 50

    if trend == "UPTREND":
        score += 8
    elif trend == "DOWNTREND":
        score -= 8
    if rsi_sig == "OVERSOLD":
        score += 5
    elif rsi_sig == "OVERBOUGHT":
        score -= 5
    if macd_sig == "BULLISH":
        score += 5
    elif macd_sig == "BEARISH":
        score -= 5

    inds = [rsi, macd_hist, sma_50, sma_200, current_price]
    sample_size = sum(1 for x in inds if x is not None)
    freshness = "fresh" if sample_size >= 4 else "stale"
    agree = _technical_agree(trend, rsi_sig, macd_sig)
    if sample_size >= 5 and agree:
        confidence = "HIGH"
    elif sample_size >= 3:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    ols = (one_line_summary or "").strip()
    reason = ols[:200] if ols else f"{trend}/{rsi_sig}/{macd_sig} from {sample_size} indicators."

    drivers: list[dict[str, Any]] = []
    if rsi is not None and rsi_sig != "NEUTRAL":
        drivers.append(
            make_driver(f"RSI {rsi:.0f}", "bearish" if rsi_sig == "OVERBOUGHT" else "bullish", 0.5)
        )
    if macd_hist is not None and macd_sig != "NEUTRAL":
        drivers.append(
            make_driver(
                f"MACD hist {macd_hist:.2f}",
                "bullish" if macd_sig == "BULLISH" else "bearish",
                0.5,
            )
        )
    if trend != "MIXED":
        drivers.append(
            make_driver(
                f"Price {trend.lower()} vs SMA50/SMA200",
                "bullish" if trend == "UPTREND" else "bearish",
                0.7,
            )
        )

    return make_score_block(
        score=score,
        confidence=confidence,
        reason=reason,
        drivers=drivers,
        sample_size=sample_size,
        freshness=freshness,
    )


def _heuristic_overall(trend: str, rsi_sig: str, macd_sig: str) -> str:
    """When Gemini is unavailable, derive BULLISH/BEARISH/NEUTRAL from indicators."""
    bull = 0
    bear = 0
    if trend == "UPTREND":
        bull += 1
    elif trend == "DOWNTREND":
        bear += 1
    if rsi_sig == "OVERSOLD":
        bull += 1
    elif rsi_sig == "OVERBOUGHT":
        bear += 1
    if macd_sig == "BULLISH":
        bull += 1
    elif macd_sig == "BEARISH":
        bear += 1
    if bull > bear:
        return "BULLISH"
    if bear > bull:
        return "BEARISH"
    return "NEUTRAL"


class TechnicalAgent:
    def __init__(
        self,
        broadcast: BroadcastFn,
        ctx: dict[str, Any],
    ) -> None:
        self.broadcast = broadcast
        self.ctx = ctx

    async def run(self) -> None:
        ticker = (self.ctx.get("ticker") or "").strip().upper()
        if not ticker:
            await self.broadcast({
                "type": "status",
                "data": {
                    "state": "error",
                    "message": "technical: no ticker in context",
                },
            })
            return

        # Prefer computing indicators from a single daily series call (more reliable on free tiers),
        # then fall back to dedicated indicator endpoints if needed.
        macd = macd_signal_line = macd_hist = None
        rsi = sma_50 = sma_200 = None
        try:
            macd, macd_signal_line, macd_hist, rsi, sma_50, sma_200 = await _compute_indicators_from_series(ticker)
        except Exception:
            pass
        if rsi is None:
            rsi = await _fetch_rsi(ticker)
        if sma_50 is None:
            sma_50 = await _fetch_sma(ticker, 50)
        if sma_200 is None:
            sma_200 = await _fetch_sma(ticker, 200)
        if macd_hist is None and macd is None and macd_signal_line is None:
            macd, macd_signal_line, macd_hist = await _fetch_macd(ticker)
        stock = await get_stock_data(ticker)

        current_price: float | None = None
        if isinstance(stock, dict) and not stock.get("error"):
            try:
                current_price = float(stock.get("price") or 0) or None
            except (TypeError, ValueError):
                current_price = None

        trend = _trend(current_price, sma_50, sma_200)
        rsi_sig = _rsi_signal(rsi)
        macd_sig = _macd_signal_from_hist(macd_hist)

        prompt = f"""You are a technical analyst for {ticker}.

Current price: {current_price}
RSI (14, daily): {rsi}
MACD: {macd}, MACD signal line: {macd_signal_line}, MACD histogram: {macd_hist}
SMA 50 (daily): {sma_50}
SMA 200 (daily): {sma_200}

Deterministic signals already computed (these are authoritative — DO NOT
override them):
- Trend vs moving averages: {trend} (UPTREND = price > SMA50 > SMA200;
  DOWNTREND = price < SMA50 < SMA200; else MIXED)
- RSI regime: {rsi_sig} (OVERBOUGHT if RSI > 70, OVERSOLD if RSI < 30,
  else NEUTRAL)
- MACD histogram bias: {macd_sig} (BULLISH if hist > 0, BEARISH if hist < 0,
  else NEUTRAL)

Your job: write concise NARRATIVE text that describes these deterministic
signals in plain English. You are NOT asked to agree or disagree with them.

Return JSON only (no markdown):
{{
  "key_level_note": "<e.g. Support at $420 (SMA200), resistance at $455
    (recent high). Focus on price levels, not signal direction.>",
  "one_line_summary": "<one sentence that describes the signals above in
    plain English, e.g. 'Price in uptrend above both MAs, RSI in neutral
    zone, MACD positive — momentum intact'>"
}}

If indicators are missing, state the data gap in one_line_summary but do
NOT attempt to judge direction yourself.
"""

        parsed: dict[str, Any] | None = None
        if GEMINI_API_KEY.strip():
            backoff_s = (2.0, 5.0)
            for attempt in range(3):
                try:
                    client = genai.Client(api_key=GEMINI_API_KEY)
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
        else:
            parsed = None

        overall = _heuristic_overall(trend, rsi_sig, macd_sig)

        if not isinstance(parsed, dict):
            ols = f"{trend.title()}, RSI {rsi_sig.lower()}, MACD {macd_sig.lower()}."
            tech_data = {
                "current_price": current_price,
                "rsi": rsi,
                "macd": macd,
                "macd_signal_line": macd_signal_line,
                "macd_hist": macd_hist,
                "sma_50": sma_50,
                "sma_200": sma_200,
                "trend": trend,
                "rsi_signal": rsi_sig,
                "macd_signal": macd_sig,
                "overall_signal": overall,
                "key_level_note": "",
                "one_line_summary": ols,
            }
            tech_data["score_block"] = _build_technical_score_block(
                overall, trend, rsi_sig, macd_sig, rsi, macd_hist, sma_50, sma_200, current_price, ols
            )
            await self.broadcast({"type": "technical", "data": tech_data})
            return

        ols = str(parsed.get("one_line_summary") or "").strip()
        tech_data = {
            "current_price": current_price,
            "rsi": rsi,
            "macd": macd,
            "macd_signal_line": macd_signal_line,
            "macd_hist": macd_hist,
            "sma_50": sma_50,
            "sma_200": sma_200,
            "trend": trend,
            "rsi_signal": rsi_sig,
            "macd_signal": macd_sig,
            "overall_signal": overall,
            "key_level_note": str(parsed.get("key_level_note") or "").strip(),
            "one_line_summary": ols,
        }
        tech_data["score_block"] = _build_technical_score_block(
            overall, trend, rsi_sig, macd_sig, rsi, macd_hist, sma_50, sma_200, current_price, ols
        )
        await self.broadcast({"type": "technical", "data": tech_data})

    def stop(self) -> None:
        """No background loop — nothing to cancel."""
        pass
