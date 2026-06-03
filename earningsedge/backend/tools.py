"""External API tool implementations.

All async, all return dicts. On any failure return {"error": "..."} — never
raise. Per-session caching: each (tool, ticker) call is fetched once and
reused for the rest of the session via reset_cache() at session start.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from agents.specialist_schema import make_driver, make_score_block
from news_classification import (
    aggregate_headline_labels,
    aggregate_news_records,
    classify_headline_heuristic,
    classify_headlines,
    format_news_datetime,
    recency_weight,
)

_APP_ROOT = Path(__file__).resolve().parent.parent
# Always load earningsedge/.env regardless of uvicorn working directory.
load_dotenv(_APP_ROOT / ".env", override=False)

ALPHA_VANTAGE_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY", "")
FMP_API_KEY = os.getenv("FMP_API_KEY", "")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
FRED_API_KEY = os.getenv("FRED_API_KEY", "")

_log = logging.getLogger(__name__)

_HTTP_TIMEOUT = httpx.Timeout(20.0, connect=10.0)
_CACHE: dict[str, dict[str, Any]] = {}

# TTL cache for snapshot-like tools (separate from session-scoped _CACHE).
# Used to avoid repeated expensive calls while still refreshing periodically.
_TTL_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_YF_SNAPSHOT_TTL_SEC = 300.0  # 5 minutes
_YF_SNAPSHOT_LOCK = asyncio.Lock()

# Alpha Vantage free tier: ~1 request per second. Serialize all calls process-wide.
_AV_HTTP_LOCK = asyncio.Lock()
# Alpha Vantage free tier is effectively ~5 requests/minute.
# Use a conservative gap so parallel agents don't immediately trigger "Note" rate-limit responses.
_AV_MIN_GAP_SEC = 13.0
_av_next_allowed_monotonic = 0.0


async def alpha_vantage_request(params: dict[str, Any]) -> Any:
    """GET alphavantage.co/query with apikey; enforces spacing so parallel agents
    (valuation + technical + earnings, etc.) do not burst past the free limit."""
    global _av_next_allowed_monotonic
    if not ALPHA_VANTAGE_API_KEY:
        return {}
    q = {**params, "apikey": ALPHA_VANTAGE_API_KEY}
    async with _AV_HTTP_LOCK:
        now = time.monotonic()
        if now < _av_next_allowed_monotonic:
            await asyncio.sleep(_av_next_allowed_monotonic - now)
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                resp = await client.get("https://www.alphavantage.co/query", params=q)
                resp.raise_for_status()
                data = resp.json()
            _av_next_allowed_monotonic = time.monotonic() + _AV_MIN_GAP_SEC
            return data
        except Exception:
            _av_next_allowed_monotonic = time.monotonic() + _AV_MIN_GAP_SEC
            raise

_FINNHUB_HEADERS = {"X-Finnhub-Token": FINNHUB_API_KEY}
_FINNHUB_BASE = "https://finnhub.io/api/v1"


def reset_cache() -> None:
    _CACHE.clear()
    _TTL_CACHE.clear()


def _cache_get(key: str) -> dict[str, Any] | None:
    return _CACHE.get(key)


def _cache_put(key: str, value: dict[str, Any]) -> dict[str, Any]:
    _CACHE[key] = value
    return value


def _ttl_cache_get(key: str) -> dict[str, Any] | None:
    entry = _TTL_CACHE.get(key)
    if entry is None:
        return None
    expires_at, value = entry
    if time.time() >= expires_at:
        _TTL_CACHE.pop(key, None)
        return None
    return value


def _ttl_cache_put(key: str, value: dict[str, Any], ttl_sec: float) -> dict[str, Any]:
    _TTL_CACHE[key] = (time.time() + ttl_sec, value)
    return value


async def get_finnhub_macd_latest(ticker: str) -> tuple[float | None, float | None, float | None]:
    """Latest MACD line, signal, histogram from Finnhub /indicator (daily). Fallback when Alpha Vantage MACD is empty."""
    sym = (ticker or "").strip().upper()
    if not sym or not FINNHUB_API_KEY:
        return None, None, None
    now = int(time.time())
    from_ts = now - 800 * 86400
    params = {
        "symbol": sym,
        "resolution": "D",
        "from": from_ts,
        "to": now,
        "indicator": "macd",
    }
    data: dict[str, Any] | None = None
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, headers=_FINNHUB_HEADERS) as client:
            for path in ("/stock/indicator", "/indicator"):
                resp = await client.get(f"{_FINNHUB_BASE}{path}", params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    break
    except Exception:
        return None, None, None
    if not isinstance(data, dict):
        return None, None, None

    def _last_numeric(arr: Any) -> float | None:
        if not isinstance(arr, list):
            return None
        for x in reversed(arr):
            if x is None:
                continue
            try:
                v = float(x)
            except (TypeError, ValueError):
                continue
            return v
        return None

    m = _last_numeric(data.get("macd"))
    s = _last_numeric(data.get("macdSignal") or data.get("signal"))
    h = _last_numeric(
        data.get("macdHistogram")
        or data.get("histogram")
        or data.get("macd_hist")
    )
    return m, s, h


async def _get_json(url: str, params: dict[str, Any] | None = None) -> Any:
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()


async def get_stock_data(ticker: str) -> dict[str, Any]:
    sym = ticker.strip().upper()
    cache_key = f"stock:{sym}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    def _fetch() -> dict[str, Any]:
        import yfinance as yf

        t = yf.Ticker(sym)
        fi = t.fast_info
        price = fi.last_price
        prev = fi.previous_close
        change_pct = ((price - prev) / prev * 100) if prev else None
        return {
            "ticker": sym,
            "price": price,
            "previous_close": prev,
            "change_pct": round(change_pct, 2) if change_pct is not None else None,
            "day_high": fi.day_high,
            "day_low": fi.day_low,
        }

    try:
        result = await asyncio.get_event_loop().run_in_executor(None, _fetch)
        return _cache_put(cache_key, result)
    except Exception as exc:
        return {"error": f"get_stock_data: {exc}"}


async def _finnhub_get(endpoint: str, params: dict[str, Any]) -> Any:
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, headers=_FINNHUB_HEADERS) as client:
        resp = await client.get(f"{_FINNHUB_BASE}/{endpoint}", params=params)
        resp.raise_for_status()
        return resp.json()


async def get_fundamentals(ticker: str) -> dict[str, Any]:
    sym = ticker.strip().upper()
    cache_key = f"fund:{sym}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    # Source 1: yfinance
    def _fetch_yf() -> dict[str, Any]:
        import yfinance as yf

        info = yf.Ticker(sym).info or {}
        return {
            "pe_ratio": _sanity_check_pe(info.get("trailingPE")),
            "forward_pe": _sanity_check_pe(info.get("forwardPE")),
            "ev_ebitda": _sanity_check_ev_ebitda(info.get("enterpriseToEbitda")),
            "beta": info.get("beta"),
            "gross_margin": _normalize_percentage(
                info.get("grossMargins"),
                source="yfinance",
                field_name="gross_margin",
            ),
            "operating_margin": _normalize_percentage(
                info.get("operatingMargins"),
                source="yfinance",
                field_name="operating_margin",
            ),
            "revenue_growth": _normalize_percentage(
                info.get("revenueGrowth"),
                source="yfinance",
                field_name="revenue_growth",
            ),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "name": info.get("longName"),
            "_source": "yfinance",
        }

    try:
        yf_data = await asyncio.get_event_loop().run_in_executor(None, _fetch_yf)
    except Exception as exc:
        yf_data = {"error": f"yfinance fetch failed: {exc}", "_source": "yfinance"}

    # Source 2: Finnhub (cross-check)
    fh_data: dict[str, Any] = {}
    try:
        metric_doc = await _finnhub_get("stock/metric", {"symbol": sym, "metric": "all"})
        if isinstance(metric_doc, dict):
            m = metric_doc.get("metric") or {}
            fh_data = {
                "pe_ratio": _sanity_check_pe(m.get("peBasicExclExtraTTM")),
                "forward_pe": _sanity_check_pe(m.get("forwardPE")),
                "ev_ebitda": _sanity_check_ev_ebitda(m.get("evEbitdaTTM")),
                "gross_margin": _normalize_percentage(
                    m.get("grossMarginTTM"),
                    source="finnhub",
                    field_name="gross_margin",
                ),
                "operating_margin": _normalize_percentage(
                    m.get("operatingMarginTTM") or m.get("operatingMarginAnnual"),
                    source="finnhub",
                    field_name="operating_margin",
                ),
                "revenue_growth": _normalize_percentage(
                    m.get("revenueGrowthTTMYoy"),
                    source="finnhub",
                    field_name="revenue_growth",
                ),
            }
    except Exception:
        fh_data = {}

    # Reconcile values
    result: dict[str, Any] = {
        "ticker": sym,
        "name": yf_data.get("name") or sym,
        "sector": yf_data.get("sector"),
        "industry": yf_data.get("industry"),
        "beta": yf_data.get("beta"),
        "_yf_source": "yfinance",
        "_fh_source": "finnhub" if fh_data else None,
        "_disagreements": [],
    }

    for field in (
        "pe_ratio",
        "forward_pe",
        "ev_ebitda",
        "gross_margin",
        "operating_margin",
        "revenue_growth",
    ):
        yf_v = yf_data.get(field) if isinstance(yf_data, dict) else None
        fh_v = fh_data.get(field) if isinstance(fh_data, dict) else None
        if yf_v is not None and fh_v is not None:
            try:
                relative_diff = abs(yf_v - fh_v) / max(abs(yf_v), abs(fh_v), 0.01)
                absolute_diff = abs(yf_v - fh_v)
                is_ratio = field in ("pe_ratio", "forward_pe", "ev_ebitda")
                big_disagreement = relative_diff > 0.25 or (is_ratio and absolute_diff > 15)
                if big_disagreement:
                    if is_ratio:
                        chosen = max(yf_v, fh_v)
                    elif field == "revenue_growth":
                        chosen = min(yf_v, fh_v)
                    else:
                        chosen = (yf_v + fh_v) / 2.0
                    result[field] = chosen
                    result["_disagreements"].append({
                        "field": field,
                        "yf": yf_v,
                        "fh": fh_v,
                        "chosen": chosen,
                    })
                else:
                    result[field] = round((yf_v + fh_v) / 2.0, 2)
            except (TypeError, ValueError, ZeroDivisionError):
                result[field] = yf_v if yf_v is not None else fh_v
        else:
            result[field] = yf_v if yf_v is not None else fh_v

    if isinstance(yf_data, dict) and yf_data.get("error") and not fh_data:
        return {"error": yf_data["error"]}

    return _cache_put(cache_key, result)


async def get_yfinance_snapshot(ticker: str) -> dict[str, Any]:
    """Fetch a single consolidated snapshot from Yahoo Finance.

    Cache key: `yf_snapshot:{ticker}`, TTL 5 minutes.
    On any failure returns `{"error": "..."}`.
    """
    sym = (ticker or "").strip().upper()
    if not sym:
        return {"error": "get_yfinance_snapshot: ticker is required"}

    cache_key = f"yf_snapshot:{sym}"
    cached = _ttl_cache_get(cache_key)
    if cached is not None:
        return cached

    async with _YF_SNAPSHOT_LOCK:
        # Another concurrent coroutine may have filled the cache while we waited.
        cached = _ttl_cache_get(cache_key)
        if cached is not None:
            return cached

        def _fetch_sync() -> dict[str, Any]:
            try:
                import yfinance as yf  # pip package: yfinance
                import pandas as pd
                import pandas_ta as ta  # pip package: pandas-ta (module: pandas_ta)

                t = yf.Ticker(sym)

                # --- Current price ---
                info: dict[str, Any]
                try:
                    info = t.info or {}
                except Exception:
                    info = {}

                current_price = info.get("currentPrice")
                if current_price is None:
                    try:
                        fast_info = getattr(t, "fast_info", None)
                        if isinstance(fast_info, dict):
                            current_price = fast_info.get("last_price")
                        else:
                            current_price = getattr(fast_info, "last_price", None)
                    except Exception:
                        current_price = None

                try:
                    current_price = float(current_price) if current_price is not None else None
                except (TypeError, ValueError):
                    current_price = None

                # --- Fundamentals from info ---
                pe_ratio = info.get("forwardPE")
                if pe_ratio is None:
                    pe_ratio = info.get("trailingPE")
                try:
                    pe_ratio = float(pe_ratio) if pe_ratio is not None else None
                except (TypeError, ValueError):
                    pe_ratio = None

                ev_ebitda = info.get("enterpriseToEbitda") or info.get("evToEbitda") or info.get("enterpriseToEbitda")
                try:
                    ev_ebitda = float(ev_ebitda) if ev_ebitda is not None else None
                except (TypeError, ValueError):
                    ev_ebitda = None

                try:
                    beta = float(info.get("beta")) if info.get("beta") is not None else None
                except (TypeError, ValueError):
                    beta = None

                try:
                    gross_margin = float(info.get("grossMargins")) if info.get("grossMargins") is not None else None
                except (TypeError, ValueError):
                    gross_margin = None

                try:
                    operating_margin = float(info.get("operatingMargins")) if info.get("operatingMargins") is not None else None
                except (TypeError, ValueError):
                    operating_margin = None

                # --- Technical indicators via pandas_ta on daily history (1y, then 6mo fallback) ---
                hist_1y = t.history(period="1y")
                _log.info(
                    "get_yfinance_snapshot[%s] t.history(period='1y'): empty=%s shape=%s len=%s columns=%s",
                    sym,
                    getattr(hist_1y, "empty", True) if hist_1y is not None else None,
                    getattr(hist_1y, "shape", None) if hist_1y is not None else None,
                    len(hist_1y) if hist_1y is not None and hasattr(hist_1y, "__len__") else None,
                    list(hist_1y.columns) if hist_1y is not None and hasattr(hist_1y, "columns") else None,
                )

                hist = hist_1y
                history_period = "1y"
                if hist is None or getattr(hist, "empty", True):
                    hist = t.history(period="6mo")
                    history_period = "6mo"
                    _log.info(
                        "get_yfinance_snapshot[%s] t.history(period='6mo') fallback: empty=%s shape=%s len=%s columns=%s",
                        sym,
                        getattr(hist, "empty", True) if hist is not None else None,
                        getattr(hist, "shape", None) if hist is not None else None,
                        len(hist) if hist is not None and hasattr(hist, "__len__") else None,
                        list(hist.columns) if hist is not None and hasattr(hist, "columns") else None,
                    )

                if hist is None or getattr(hist, "empty", True):
                    return {"error": f"get_yfinance_snapshot: no history for {sym} (tried 1y and 6mo)"}
                if "Close" not in hist.columns:
                    return {"error": f"get_yfinance_snapshot: history missing Close for {sym}"}

                hist = hist.sort_index()
                close = hist["Close"].dropna()
                if close.empty:
                    return {"error": f"get_yfinance_snapshot: Close empty after cleaning for {sym}"}

                _log.info(
                    "get_yfinance_snapshot[%s] using history_period=%s close_bars=%s",
                    sym,
                    history_period,
                    len(close),
                )

                def _last_num(series: Any) -> float | None:
                    try:
                        s = series.dropna()
                        if getattr(s, "empty", True):
                            return None
                        return float(s.iloc[-1])
                    except Exception:
                        return None

                rsi_14 = _last_num(ta.rsi(close, length=14))

                macd: float | None = None
                macd_signal: float | None = None
                macd_hist: float | None = None
                macd_df = ta.macd(close, fast=12, slow=26, signal=9)
                if macd_df is None or getattr(macd_df, "empty", True) or not hasattr(macd_df, "columns"):
                    _log.warning(
                        "get_yfinance_snapshot[%s] MACD not computable on available history (bars=%s)",
                        sym,
                        len(close),
                    )
                else:
                    macd_line_col = "MACD_12_26_9" if "MACD_12_26_9" in macd_df.columns else macd_df.columns[0]
                    macd_signal_col = "MACDs_12_26_9" if "MACDs_12_26_9" in macd_df.columns else (macd_df.columns[1] if len(macd_df.columns) > 1 else macd_df.columns[0])
                    macd_hist_col = "MACDh_12_26_9" if "MACDh_12_26_9" in macd_df.columns else (macd_df.columns[2] if len(macd_df.columns) > 2 else macd_df.columns[0])

                    macd = _last_num(macd_df[macd_line_col])
                    macd_signal = _last_num(macd_df[macd_signal_col])
                    macd_hist = _last_num(macd_df[macd_hist_col])

                sma_50 = _last_num(ta.sma(close, length=50))
                sma_200 = _last_num(ta.sma(close, length=200))

                return {
                    "ticker": sym,
                    "current_price": current_price,
                    "rsi_14": rsi_14,
                    "macd": macd,
                    "macd_signal": macd_signal,
                    "macd_hist": macd_hist,
                    "sma_50": sma_50,
                    "sma_200": sma_200,
                    "pe_ratio": pe_ratio,
                    "ev_ebitda": ev_ebitda,
                    "beta": beta,
                    "gross_margin": gross_margin,
                    "operating_margin": operating_margin,
                }
            except Exception as exc:
                return {"error": f"get_yfinance_snapshot: {exc}"}

        result = await asyncio.to_thread(_fetch_sync)
        if not isinstance(result, dict) or result.get("error"):
            # Do not cache errors; allow retries.
            return result if isinstance(result, dict) else {"error": f"get_yfinance_snapshot: unexpected result for {sym}"}

        return _ttl_cache_put(cache_key, result, _YF_SNAPSHOT_TTL_SEC)


# Consumer brand → (ticker, official corporate name). Finnhub's symbol-search
# matches against the registered corporate name ("Alphabet Inc.", "Meta
# Platforms Inc."), so users typing "google" or "facebook" get
# `{"count":0,"result":[]}` from the API. This map catches the most common
# brand vs. legal-name mismatches before we hit Finnhub.
_BRAND_ALIASES: dict[str, tuple[str, str]] = {
    "google": ("GOOGL", "Alphabet Inc."),
    "alphabet": ("GOOGL", "Alphabet Inc."),
    "youtube": ("GOOGL", "Alphabet Inc."),
    "android": ("GOOGL", "Alphabet Inc."),
    "facebook": ("META", "Meta Platforms Inc."),
    "instagram": ("META", "Meta Platforms Inc."),
    "whatsapp": ("META", "Meta Platforms Inc."),
    "meta": ("META", "Meta Platforms Inc."),
    "fb": ("META", "Meta Platforms Inc."),
    "twitter": ("X", "X Corp (formerly Twitter, now private — limited data)"),
    "x.com": ("X", "X Corp (formerly Twitter, now private — limited data)"),
    "amazon": ("AMZN", "Amazon.com Inc."),
    "aws": ("AMZN", "Amazon.com Inc."),
    "apple": ("AAPL", "Apple Inc."),
    "iphone": ("AAPL", "Apple Inc."),
    "tesla": ("TSLA", "Tesla Inc."),
    "microsoft": ("MSFT", "Microsoft Corp."),
    "windows": ("MSFT", "Microsoft Corp."),
    "azure": ("MSFT", "Microsoft Corp."),
    "office": ("MSFT", "Microsoft Corp."),
    "openai partner": ("MSFT", "Microsoft Corp."),
    "nvidia": ("NVDA", "NVIDIA Corp."),
    "netflix": ("NFLX", "Netflix Inc."),
    "uber": ("UBER", "Uber Technologies Inc."),
    "airbnb": ("ABNB", "Airbnb Inc."),
    "spotify": ("SPOT", "Spotify Technology SA"),
    "shopify": ("SHOP", "Shopify Inc."),
    "salesforce": ("CRM", "Salesforce Inc."),
    "oracle": ("ORCL", "Oracle Corp."),
    "intel": ("INTC", "Intel Corp."),
    "amd": ("AMD", "Advanced Micro Devices Inc."),
    "disney": ("DIS", "The Walt Disney Co."),
    "starbucks": ("SBUX", "Starbucks Corp."),
    "mcdonalds": ("MCD", "McDonald's Corp."),
    "mcdonald's": ("MCD", "McDonald's Corp."),
    "walmart": ("WMT", "Walmart Inc."),
    "target": ("TGT", "Target Corp."),
    "nike": ("NKE", "Nike Inc."),
    "coca cola": ("KO", "The Coca-Cola Company"),
    "coca-cola": ("KO", "The Coca-Cola Company"),
    "pepsi": ("PEP", "PepsiCo Inc."),
    "berkshire": ("BRK.B", "Berkshire Hathaway Inc."),
    "berkshire hathaway": ("BRK.B", "Berkshire Hathaway Inc."),
    "jp morgan": ("JPM", "JPMorgan Chase & Co."),
    "jpmorgan": ("JPM", "JPMorgan Chase & Co."),
    "goldman": ("GS", "The Goldman Sachs Group Inc."),
    "goldman sachs": ("GS", "The Goldman Sachs Group Inc."),
    "boeing": ("BA", "The Boeing Company"),
    "ford": ("F", "Ford Motor Company"),
    "gm": ("GM", "General Motors Company"),
    "general motors": ("GM", "General Motors Company"),
    "exxon": ("XOM", "Exxon Mobil Corp."),
    "exxonmobil": ("XOM", "Exxon Mobil Corp."),
    "chevron": ("CVX", "Chevron Corp."),
    "palantir": ("PLTR", "Palantir Technologies Inc."),
    "snowflake": ("SNOW", "Snowflake Inc."),
    "databricks": ("", "Databricks (private — not publicly traded)"),
    "stripe": ("", "Stripe (private — not publicly traded)"),
    "spacex": ("", "SpaceX (private — not publicly traded)"),
    "openai": ("MSFT", "OpenAI is private; Microsoft (MSFT) is the largest investor"),
    "anthropic": ("", "Anthropic (private — not publicly traded)"),
}


async def resolve_coverage_inputs(
    ticker_in: str | None,
    company_in: str | None,
) -> dict[str, Any]:
    """Resolve user input to (ticker, company_name). Exactly one field or both may be set.

    - Both: normalize ticker, use provided company name.
    - Ticker only: fill company_name from Finnhub profile via get_fundamentals.
    - Company / name only: try the brand-alias map first (catches "google"
      → GOOGL etc.), then Finnhub symbol search, then a friendlier error.
    """
    t_raw = (ticker_in or "").strip()
    t = t_raw.upper()
    cn = (company_in or "").strip()
    if not t and not cn:
        return {"error": "Enter a stock ticker or company name."}
    if t and cn:
        return {"ticker": t, "company_name": cn}

    # The frontend has a single primary input; users frequently type a brand
    # name into the "ticker" field ("google", "microsoft"). Try the brand-alias
    # map on whichever field was filled — if it matches, treat it as a name
    # lookup so we don't waste a 10s Yahoo round-trip on a delisted symbol.
    candidate = (t_raw if t else cn)
    cand_norm = " ".join(candidate.lower().split())
    alias = _BRAND_ALIASES.get(cand_norm)
    if alias is not None:
        ticker, official = alias
        if not ticker:
            return {"error": f"{official}"}
        return {"ticker": ticker, "company_name": official}

    if t:
        fund = await get_fundamentals(t)
        if isinstance(fund, dict) and fund.get("error"):
            return {"error": f"Could not verify ticker {t}. Check the symbol and try again."}
        name = (fund.get("name") or "").strip() or t
        return {"ticker": t, "company_name": name}

    if not FINNHUB_API_KEY:
        return {"error": "FINNHUB_API_KEY is required to look up a ticker from a company name."}
    try:
        data = await _finnhub_get("search", {"q": cn[:64]})
    except Exception as exc:
        return {"error": f"Symbol search failed: {exc}"}
    rows = (data or {}).get("result") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows:
        return {
            "error": (
                f"No listing found for '{cn}'. Try the ticker symbol "
                "(e.g. AAPL, NVDA) or the official corporate name "
                "(e.g. 'Alphabet Inc' instead of 'Google')."
            )
        }
    pick: dict[str, Any] | None = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        typ = (row.get("type") or "").lower()
        if "stock" in typ or "depositary" in typ or "receipt" in typ:
            pick = row
            break
    if pick is None and isinstance(rows[0], dict):
        pick = rows[0]
    if not pick:
        return {"error": f"No listing found for '{cn}'."}
    sym = (pick.get("symbol") or "").strip().upper()
    if not sym:
        return {"error": f"No listing found for '{cn}'."}
    desc = (pick.get("description") or cn).strip()
    return {"ticker": sym, "company_name": desc}


def _recommendation_row_normalised(row: dict[str, Any]) -> float | None:
    """Same -1..+1 consensus as get_analyst_recommendation baseline (line ~576)."""
    strong_buy = int(row.get("strongBuy") or 0)
    buy = int(row.get("buy") or 0)
    hold = int(row.get("hold") or 0)
    sell = int(row.get("sell") or 0)
    strong_sell = int(row.get("strongSell") or 0)
    total = strong_buy + buy + hold + sell + strong_sell
    if total <= 0:
        return None
    weighted = strong_buy * 2 + buy * 1 - sell * 1 - strong_sell * 2
    return weighted / (2 * total)


def _float_or_none(x: Any) -> float | None:
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _normalize_percentage(
    value: Any,
    *,
    source: str,
    field_name: str = "",
) -> float | None:
    """Return a percentage value in % form (e.g. 34.5, not 0.345).

    source: "finnhub" (already %-form) or "yfinance" (decimal-form).
    Out-of-range values are clamped to None with a log warning.
    """
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None

    if source == "yfinance":
        # yfinance returns decimals: 0.732 for 73.2%.
        v = v * 100.0
    elif source == "finnhub":
        # Finnhub returns percentages directly: 34.34 for 34.34%.
        # Guard against a rare case where the API returned a decimal.
        if abs(v) < 1 and field_name in ("revenue_growth", "gross_margin", "operating_margin"):
            v = v * 100.0

    # Sanity clamp. Growth can be extreme for small companies, but anything
    # outside -99% to +500% is clearly a data error.
    if field_name == "revenue_growth":
        if v < -99 or v > 500:
            return None
    # Margins must be between -100% and +100%.
    if field_name in ("gross_margin", "operating_margin"):
        if v < -100 or v > 100:
            return None

    return round(v, 2)


def _sanity_check_pe(value: Any) -> float | None:
    """Return a P/E ratio in a sane range, else None.

    P/E < 0 means the company has negative earnings — not a valid P/E.
    P/E > 500 means either negligible earnings or bad data — drop.
    """
    if value is None:
        return None
    try:
        pe = float(value)
    except (TypeError, ValueError):
        return None
    if pe <= 0 or pe > 500:
        return None
    return round(pe, 2)


def _sanity_check_ev_ebitda(value: Any) -> float | None:
    """Return an EV/EBITDA ratio in a sane range, else None.

    Negative EV/EBITDA means negative EBITDA — report but don't use for
    valuation math. Above 200 is noise from small EBITDA base.
    """
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v < -50 or v > 200:
        return None
    return round(v, 2)


def _build_analyst_score_block(rec: dict[str, Any]) -> dict[str, Any]:
    """Build score block from the final analyst dict (Finnhub or hybrid).

    Four inputs are blended:
      1. Consensus score (buy/sell/hold vote) — 0-100, most important
      2. Price-target upside vs current price — magnitude and direction
      3. Rating trend (upgrading/downgrading) — momentum kicker
      4. Target spread — penalizes confidence when analysts disagree widely
    """
    drivers: list[dict[str, Any]] = []

    # Input 1: consensus score (0-100 already)
    consensus = rec.get("baseline_score")
    try:
        consensus_i = int(consensus) if consensus is not None else None
    except (TypeError, ValueError):
        consensus_i = None

    # Start score from consensus (or neutral)
    score = float(consensus_i if consensus_i is not None else 50)

    if consensus_i is not None:
        total = rec.get("total_analysts") or 0
        buy = rec.get("buy") or 0
        sell = rec.get("sell") or 0
        sb = rec.get("strong_buy") or 0
        ss = rec.get("strong_sell") or 0
        direction = (
            "bullish"
            if consensus_i >= 65
            else "bearish"
            if consensus_i <= 35
            else "neutral"
        )
        drivers.append(
            make_driver(
                f"Consensus {consensus_i}/100 ({sb + buy} buy vs {sell + ss} sell, "
                f"{total} analysts)",
                direction,
                0.8,
            )
        )

    # Input 2: price-target upside
    upside = rec.get("target_upside_pct")
    try:
        upside_f = float(upside) if upside is not None else None
    except (TypeError, ValueError):
        upside_f = None

    if upside_f is not None:
        # Scale upside into a -15 to +15 score adjustment.
        upside_adj = max(-15.0, min(15.0, upside_f * 0.3))
        score += upside_adj
        if upside_f >= 10:
            d = "bullish"
        elif upside_f <= -5:
            d = "bearish"
        else:
            d = "neutral"
        target_mean = rec.get("target_mean")
        tm_s = f"${target_mean:.0f}" if isinstance(target_mean, (int, float)) else "target"
        drivers.append(
            make_driver(
                f"Price target {tm_s} implies {upside_f:+.1f}% from current",
                d,
                0.7,
            )
        )

    # Input 3: rating trend
    trend = str(rec.get("trend_label") or "").lower().strip()
    if trend == "upgrading":
        score += 5
        drivers.append(
            make_driver(
                "Consensus is trending up (recent upgrades outweigh downgrades)",
                "bullish",
                0.5,
            )
        )
    elif trend == "downgrading":
        score -= 7
        drivers.append(
            make_driver(
                "Consensus is trending down (recent downgrades)",
                "bearish",
                0.6,
            )
        )

    # Input 4: target spread — used for CONFIDENCE penalty, not score
    spread = rec.get("target_spread_pct")
    try:
        spread_f = float(spread) if spread is not None else None
    except (TypeError, ValueError):
        spread_f = None

    # Confidence rules
    has_consensus = consensus_i is not None
    has_upside = upside_f is not None
    has_trend = trend in ("upgrading", "downgrading")
    wide_spread = spread_f is not None and spread_f > 40

    if has_consensus and has_upside and not wide_spread and has_trend:
        confidence = "HIGH"
    elif has_consensus and has_upside:
        confidence = "MEDIUM"
    elif has_consensus or has_upside:
        confidence = "LOW"
    else:
        confidence = "LOW"

    if wide_spread:
        if confidence == "HIGH":
            confidence = "MEDIUM"
        drivers.append(
            make_driver(
                f"Wide analyst disagreement (range is {spread_f:.0f}% of mean target)",
                "neutral",
                0.3,
            )
        )

    # Sample size and freshness
    total = rec.get("total_analysts") or 0
    target_count = rec.get("target_count") or 0
    sample_size = max(int(total), int(target_count))
    freshness = "fresh" if sample_size >= 5 else "stale" if sample_size >= 1 else "stale"

    # Reason — one line, prefer upside as most actionable
    if upside_f is not None and consensus_i is not None:
        reason = (
            f"{rec.get('label', 'neutral').title()} consensus ({consensus_i}/100) "
            f"with {upside_f:+.1f}% implied upside from mean target."
        )
    elif consensus_i is not None:
        reason = f"{rec.get('label', 'neutral').title()} consensus ({consensus_i}/100); no price target available."
    else:
        reason = "Analyst coverage unavailable for this ticker."

    return make_score_block(
        score=score,
        confidence=confidence,
        reason=reason,
        drivers=drivers,
        sample_size=sample_size,
        freshness=freshness,
    )


async def _yfinance_analyst_fallback(ticker: str) -> dict[str, Any]:
    """Fallback analyst data from yfinance when Finnhub returns empty.

    yfinance ``Ticker.info`` exposes targetMeanPrice / targetHighPrice /
    targetLowPrice / targetMedianPrice / numberOfAnalystOpinions /
    recommendationMean / recommendationKey. ``Ticker.recommendations`` is a
    DataFrame of rating bucket counts by period.

    Returns a dict mirroring ``get_analyst_recommendation`` output shape.
    Returns ``{"error": ...}`` on failure.
    """
    sym = ticker.strip().upper()

    def _fetch() -> dict[str, Any]:
        import yfinance as yf

        t = yf.Ticker(sym)
        info = t.info or {}
        try:
            rec_df = t.recommendations
        except Exception:
            rec_df = None
        rec_rows: list[dict[str, Any]] = []
        if rec_df is not None and hasattr(rec_df, "empty") and not rec_df.empty:
            try:
                rec_rows = rec_df.head(3).to_dict("records")
            except Exception:
                rec_rows = []
        return {"info": info, "rec_rows": rec_rows}

    try:
        loop = asyncio.get_running_loop()
        payload = await loop.run_in_executor(None, _fetch)
    except Exception as exc:
        return {"error": f"yfinance fallback failed: {exc}"}

    info = payload.get("info") or {}
    rec_rows: list[dict[str, Any]] = list(payload.get("rec_rows") or [])

    current_price: float | None = None
    try:
        cp = info.get("currentPrice") or info.get("regularMarketPrice")
        current_price = float(cp) if cp is not None else None
    except (TypeError, ValueError):
        current_price = None
    if current_price is None:
        try:
            quote = await get_stock_data(sym)
        except Exception:
            quote = {}
        if isinstance(quote, dict) and not quote.get("error"):
            try:
                current_price = float(quote.get("price") or 0) or None
            except (TypeError, ValueError):
                current_price = None

    def _as_float(x: Any) -> float | None:
        if x is None:
            return None
        try:
            return float(x)
        except (TypeError, ValueError):
            return None

    target_mean = _as_float(info.get("targetMeanPrice"))
    target_median = _as_float(info.get("targetMedianPrice"))
    target_high = _as_float(info.get("targetHighPrice"))
    target_low = _as_float(info.get("targetLowPrice"))
    target_count_raw = info.get("numberOfAnalystOpinions")
    try:
        target_count = int(target_count_raw) if target_count_raw is not None else None
    except (TypeError, ValueError):
        target_count = None

    target_upside_pct: float | None = None
    if target_mean is not None and current_price is not None and current_price > 0:
        target_upside_pct = round((target_mean - current_price) / current_price * 100.0, 1)

    target_spread_pct: float | None = None
    if (
        target_high is not None
        and target_low is not None
        and target_mean is not None
        and abs(target_mean) > 1e-12
    ):
        try:
            target_spread_pct = round((target_high - target_low) / target_mean * 100.0, 1)
        except (TypeError, ValueError, ZeroDivisionError):
            target_spread_pct = None

    def _pick_int(row: dict[str, Any], keys: tuple[str, ...]) -> int:
        for k in keys:
            v = row.get(k)
            if v is not None:
                try:
                    return int(v)
                except (TypeError, ValueError):
                    continue
        return 0

    strong_buy = buy = hold = sell = strong_sell = 0
    if rec_rows:
        latest = rec_rows[0]
        strong_buy = _pick_int(latest, ("strongBuy", "Strong Buy"))
        buy = _pick_int(latest, ("buy", "Buy"))
        hold = _pick_int(latest, ("hold", "Hold"))
        sell = _pick_int(latest, ("sell", "Sell"))
        strong_sell = _pick_int(latest, ("strongSell", "Strong Sell"))

    total = strong_buy + buy + hold + sell + strong_sell

    baseline_score = 50
    if total > 0:
        weighted = strong_buy * 2 + buy * 1 - sell * 1 - strong_sell * 2
        normalised = weighted / (2 * total)
        baseline_score = int(round((normalised + 1) * 50))
    else:
        rec_mean = _as_float(info.get("recommendationMean"))
        if rec_mean is not None:
            baseline_score = max(0, min(100, int(round((5.0 - rec_mean) / 4.0 * 100.0))))

    if baseline_score >= 70:
        label = "bullish"
    elif baseline_score <= 40:
        label = "bearish"
    else:
        label = "neutral"

    def _weighted_score(row: dict[str, Any]) -> float | None:
        sb = _pick_int(row, ("strongBuy", "Strong Buy"))
        b = _pick_int(row, ("buy", "Buy"))
        h = _pick_int(row, ("hold", "Hold"))
        s = _pick_int(row, ("sell", "Sell"))
        ss = _pick_int(row, ("strongSell", "Strong Sell"))
        tot = sb + b + h + s + ss
        if tot <= 0:
            return None
        w = sb * 2 + b * 1 - s * 1 - ss * 2
        return w / (2 * tot)

    trend_1m_delta: float | None = None
    trend_3m_delta: float | None = None
    if len(rec_rows) >= 2:
        w0 = _weighted_score(rec_rows[0])
        w1 = _weighted_score(rec_rows[1])
        if w0 is not None and w1 is not None:
            trend_1m_delta = round(w0 - w1, 3)
    if len(rec_rows) >= 3:
        w0 = _weighted_score(rec_rows[0])
        w2 = _weighted_score(rec_rows[-1])
        if w0 is not None and w2 is not None:
            trend_3m_delta = round(w0 - w2, 3)

    if (trend_1m_delta is not None and trend_1m_delta > 0.05) or (
        trend_3m_delta is not None and trend_3m_delta > 0.10
    ):
        trend_label = "upgrading"
    elif (trend_1m_delta is not None and trend_1m_delta < -0.05) or (
        trend_3m_delta is not None and trend_3m_delta < -0.10
    ):
        trend_label = "downgrading"
    else:
        trend_label = "stable"

    total_analysts: int | None = total if total > 0 else target_count

    return {
        "ticker": sym,
        "period": None,
        "strong_buy": strong_buy,
        "buy": buy,
        "hold": hold,
        "sell": sell,
        "strong_sell": strong_sell,
        "total_analysts": total_analysts if total_analysts is not None else 0,
        "baseline_score": baseline_score,
        "label": label,
        "target_mean": target_mean,
        "target_median": target_median,
        "target_high": target_high,
        "target_low": target_low,
        "target_upside_pct": target_upside_pct,
        "target_spread_pct": target_spread_pct,
        "target_count": target_count,
        "target_last_updated": None,
        "trend_label": trend_label,
        "trend_1m_delta": trend_1m_delta,
        "trend_3m_delta": trend_3m_delta,
        "source": "yfinance",
    }


async def get_analyst_recommendation(ticker: str) -> dict[str, Any]:
    """Analyst consensus: Finnhub first, then yfinance (and merge) when Finnhub is empty."""
    sym = ticker.strip().upper()
    cache_key = f"rec:{sym}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    result: dict[str, Any]
    try:
        if not (FINNHUB_API_KEY or "").strip():
            result = {
                "error": (
                    "FINNHUB_API_KEY is not set. Add it to earningsedge/.env "
                    "(repo: copy .env.example → .env), then restart the API server."
                ),
            }
        else:
            data = await _finnhub_get("stock/recommendation", {"symbol": sym})
            if not isinstance(data, list) or not data:
                result = {"error": "no recommendation data"}
            else:
                latest = data[0]
                strong_buy = int(latest.get("strongBuy") or 0)
                buy = int(latest.get("buy") or 0)
                hold = int(latest.get("hold") or 0)
                sell = int(latest.get("sell") or 0)
                strong_sell = int(latest.get("strongSell") or 0)
                total = strong_buy + buy + hold + sell + strong_sell

                if total > 0:
                    weighted = (strong_buy * 2 + buy * 1 - sell * 1 - strong_sell * 2)
                    normalised = weighted / (2 * total)
                    baseline_score = int(round((normalised + 1) * 50))
                else:
                    baseline_score = 50

                if baseline_score >= 70:
                    label = "bullish"
                elif baseline_score <= 40:
                    label = "bearish"
                else:
                    label = "neutral"

                rows_3 = [r for r in data[:3] if isinstance(r, dict)]
                latest_n = _recommendation_row_normalised(rows_3[0]) if rows_3 else None
                prior_n = _recommendation_row_normalised(rows_3[1]) if len(rows_3) >= 2 else None
                oldest_n = _recommendation_row_normalised(rows_3[2]) if len(rows_3) >= 3 else None

                trend_1m_delta: float | None = None
                if latest_n is not None and prior_n is not None:
                    trend_1m_delta = latest_n - prior_n

                trend_3m_delta: float | None = None
                if latest_n is not None and oldest_n is not None and len(rows_3) >= 3:
                    trend_3m_delta = latest_n - oldest_n

                if (trend_1m_delta is not None and trend_1m_delta > 0.05) or (
                    trend_3m_delta is not None and trend_3m_delta > 0.10
                ):
                    trend_label = "upgrading"
                elif (trend_1m_delta is not None and trend_1m_delta < -0.05) or (
                    trend_3m_delta is not None and trend_3m_delta < -0.10
                ):
                    trend_label = "downgrading"
                else:
                    trend_label = "stable"

                pt_data: dict[str, Any] | None = None
                try:
                    pt_raw = await _finnhub_get("stock/price-target", {"symbol": sym})
                    pt_data = pt_raw if isinstance(pt_raw, dict) else None
                except Exception:
                    pt_data = None

                target_mean = target_median = target_high = target_low = None
                target_last_updated: str | None = None
                target_count: int | None = None
                if pt_data and pt_data.get("targetMean") is not None:
                    target_mean = _float_or_none(pt_data.get("targetMean"))
                    target_median = _float_or_none(pt_data.get("targetMedian"))
                    target_high = _float_or_none(pt_data.get("targetHigh"))
                    target_low = _float_or_none(pt_data.get("targetLow"))
                    lu = pt_data.get("lastUpdated")
                    target_last_updated = str(lu).strip() if lu not in (None, "") else None
                    na = pt_data.get("numberOfAnalysts")
                    if na is not None:
                        try:
                            target_count = int(na)
                        except (TypeError, ValueError):
                            target_count = None

                quote = await get_stock_data(sym)
                current_price: float | None = None
                if isinstance(quote, dict) and not quote.get("error"):
                    try:
                        current_price = float(quote.get("price") or 0) or None
                    except (TypeError, ValueError):
                        current_price = None

                target_upside_pct: float | None = None
                if target_mean is not None and current_price is not None and abs(current_price) > 1e-12:
                    target_upside_pct = round((target_mean - current_price) / current_price * 100.0, 1)

                target_spread_pct: float | None = None
                if (
                    target_high is not None
                    and target_low is not None
                    and target_mean is not None
                    and abs(target_mean) > 1e-12
                ):
                    target_spread_pct = round((target_high - target_low) / target_mean * 100.0, 1)

                result = {
                    "ticker": sym,
                    "period": latest.get("period"),
                    "strong_buy": strong_buy,
                    "buy": buy,
                    "hold": hold,
                    "sell": sell,
                    "strong_sell": strong_sell,
                    "total_analysts": total,
                    "baseline_score": baseline_score,
                    "label": label,
                    "trend_1m_delta": trend_1m_delta,
                    "trend_3m_delta": trend_3m_delta,
                    "trend_label": trend_label,
                    "target_mean": target_mean,
                    "target_median": target_median,
                    "target_high": target_high,
                    "target_low": target_low,
                    "target_last_updated": target_last_updated,
                    "target_count": target_count,
                    "target_upside_pct": target_upside_pct,
                    "target_spread_pct": target_spread_pct,
                }
    except Exception as exc:
        result = {"error": f"get_analyst_recommendation: {exc}"}

    # Two independent signals — either half missing triggers yfinance fallback
    # for that half only.
    buckets_missing = (
        result.get("error") is not None
        or not (result.get("total_analysts") or 0)
    )
    targets_missing = result.get("target_mean") is None

    if buckets_missing or targets_missing:
        fallback = await _yfinance_analyst_fallback(ticker)
        if isinstance(fallback, dict) and not fallback.get("error"):
            merged: dict[str, Any]
            if buckets_missing and not targets_missing:
                # Finnhub has targets, yfinance fills buckets.
                merged = dict(result)
                for key in (
                    "strong_buy", "buy", "hold", "sell", "strong_sell",
                    "total_analysts", "baseline_score", "label",
                    "trend_label", "trend_1m_delta", "trend_3m_delta",
                ):
                    fv = fallback.get(key)
                    if fv not in (None, "", 0) or key in ("trend_label",):
                        merged[key] = fv
                merged["source"] = "hybrid"
            elif targets_missing and not buckets_missing:
                # Finnhub has buckets, yfinance fills price targets.
                merged = dict(result)
                for key in (
                    "target_mean", "target_median", "target_high", "target_low",
                    "target_upside_pct", "target_spread_pct", "target_count",
                    "target_last_updated",
                ):
                    fv = fallback.get(key)
                    if fv is not None:
                        merged[key] = fv
                merged["source"] = "hybrid"
            else:
                # Both missing from Finnhub — yfinance is primary.
                merged = dict(fallback)
                # Preserve any non-trivial Finnhub value that slipped through.
                for k, v in (result or {}).items():
                    if v not in (None, 0, "") and k != "error":
                        merged[k] = v
                merged["source"] = "yfinance"
            merged["score_block"] = _build_analyst_score_block(merged)
            return _cache_put(cache_key, merged)
        # yfinance also failed — return Finnhub result as-is with source tag.
        result["source"] = "finnhub"
        result["score_block"] = _build_analyst_score_block(result)
        return _cache_put(cache_key, result)

    # Finnhub delivered everything — tag and cache.
    result["source"] = "finnhub"
    result["score_block"] = _build_analyst_score_block(result)
    return _cache_put(cache_key, result)


def prior_quarter_eps_snapshot(quarterly_history: list[dict[str, Any]]) -> dict[str, Any]:
    """Latest quarter with reported EPS vs consensus — surprise % for dashboard analytics."""
    out: dict[str, Any] = {
        "prior_eps_actual": None,
        "prior_eps_estimate": None,
        "prior_eps_surprise_pct": None,
        "prior_eps_period": None,
    }
    for row in quarterly_history or []:
        act = row.get("reported_eps")
        if act is None:
            continue
        try:
            a = float(act)
        except (TypeError, ValueError):
            continue
        est = row.get("estimated_eps")
        ef: float | None = None
        if est is not None:
            try:
                ef = float(est)
            except (TypeError, ValueError):
                ef = None
        sp = row.get("surprise_pct")
        pct: float | None = None
        if sp is not None:
            try:
                pct = float(sp)
            except (TypeError, ValueError):
                pct = None
        if pct is None and ef is not None and abs(ef) > 1e-9:
            pct = (a - ef) / abs(ef) * 100.0
        out["prior_eps_actual"] = a
        out["prior_eps_estimate"] = ef
        out["prior_eps_surprise_pct"] = pct
        out["prior_eps_period"] = row.get("fiscal_date")
        break
    return out


async def get_earnings_estimates(
    ticker: str,
    quarter: str | None = None,
    year: str | int | None = None,
) -> dict[str, Any]:
    sym = ticker.strip().upper()
    cache_key = f"estimates:{sym}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    def _fetch() -> dict[str, Any]:
        import math

        import yfinance as yf

        def _nz(x: Any) -> Any:
            if x is None:
                return None
            if isinstance(x, float) and math.isnan(x):
                return None
            return x

        t = yf.Ticker(sym)
        hist = t.earnings_history
        if hist is None or hist.empty:
            return {"ticker": sym, "quarterly_history": []}
        rows = []
        for idx, row in hist.iterrows():
            r = row  # pandas Series
            reported = _nz(r.get("epsActual"))
            estimated = _nz(r.get("epsEstimate"))
            surprise = _nz(r.get("surprisePercent"))
            qname = str(r.get("quarter", "") or "").strip()
            if not qname:
                qname = str(idx)[:16]
            rows.append({
                "fiscal_date": qname,
                "reported_eps": reported,
                "estimated_eps": estimated,
                "surprise_pct": surprise,
            })
        return {"ticker": sym, "quarterly_history": rows[:8]}

    try:
        result = await asyncio.get_event_loop().run_in_executor(None, _fetch)
        return _cache_put(cache_key, result)
    except Exception as exc:
        return {"error": f"get_earnings_estimates: {exc}"}


PEER_FALLBACK: dict[str, list[str]] = {
    "NVDA": ["AMD", "INTC", "AVGO", "QCOM", "TSM"],
    "AMD":  ["NVDA", "INTC", "AVGO", "QCOM", "TSM"],
    "INTC": ["AMD", "NVDA", "AVGO", "QCOM", "TXN"],
    "AAPL": ["MSFT", "GOOGL", "META", "AMZN", "SONY"],
    "MSFT": ["GOOGL", "AAPL", "META", "AMZN", "ORCL"],
    "GOOGL": ["META", "MSFT", "AAPL", "AMZN", "NFLX"],
    "GOOG": ["META", "MSFT", "AAPL", "AMZN", "NFLX"],
    "META": ["GOOGL", "SNAP", "PINS", "MSFT", "NFLX"],
    "AMZN": ["WMT", "COST", "TGT", "MSFT", "GOOGL"],
    "TSLA": ["F", "GM", "RIVN", "LCID", "TM"],
    "NFLX": ["DIS", "WBD", "PARA", "AMZN", "GOOGL"],
    "ORCL": ["MSFT", "CRM", "SAP", "IBM", "GOOGL"],
    "CRM":  ["MSFT", "ORCL", "NOW", "WDAY", "ADBE"],
    "ADBE": ["MSFT", "CRM", "NOW", "INTU", "ORCL"],
    "AVGO": ["NVDA", "AMD", "QCOM", "TSM", "INTC"],
    "QCOM": ["AVGO", "NVDA", "INTC", "TSM", "MRVL"],
    "PLTR": ["SNOW", "DDOG", "NET", "CRWD", "MSFT"],
    "SNOW": ["PLTR", "DDOG", "NET", "MDB", "CFLT"],
    "JPM":  ["BAC", "WFC", "GS", "MS", "C"],
    "BAC":  ["JPM", "WFC", "GS", "MS", "C"],
}


def _compute_peg(forward_pe: Any, revenue_growth_pct: Any) -> float | None:
    """PEG = P/E / growth%.

    Both inputs must be in standard form:
      forward_pe: a P/E ratio (e.g. 25.0)
      revenue_growth_pct: growth IN PERCENT (e.g. 20.0 for 20% growth)

    Returns None when the result wouldn't be meaningfully comparable across
    peers:
      - Inputs missing or non-positive
      - Growth too low (<5%): PEG is mathematically dominated by noise and
        the ratio mostly reports the P/E itself
      - Growth too high (>50%): TTM growth for hyper-growth names creates
        artificially small PEGs (e.g. 25/70 = 0.36) that mislead retail
        readers comparing against peers with normal growth rates. Forward
        growth would be lower; we don't have a clean forward source, so we
        suppress rather than mislead.
      - Result outside 0.1–10: most likely a data error (mismatched units
        between sources, growth in wrong scale, etc.)
    """
    try:
        pe = float(forward_pe) if forward_pe is not None else None
        growth_pct = float(revenue_growth_pct) if revenue_growth_pct is not None else None
    except (TypeError, ValueError):
        return None
    if pe is None or growth_pct is None:
        return None
    if pe <= 0 or growth_pct <= 0:
        return None
    if growth_pct < 5.0 or growth_pct > 50.0:
        return None
    peg = pe / growth_pct
    if peg < 0.1 or peg > 10.0:
        return None
    return round(peg, 2)


async def get_competitors(ticker: str) -> dict[str, Any]:
    cache_key = f"peers:{ticker.upper()}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    # Also fetch target ticker's own metrics for peer comparison
    try:
        target_funds = await get_fundamentals(ticker)
    except Exception:
        target_funds = {}
    if not isinstance(target_funds, dict) or "error" in target_funds:
        target_funds = {}
    target_entry = {
        "ticker": ticker,
        "name": target_funds.get("name") or ticker,
        "pe_ratio": target_funds.get("forward_pe") or target_funds.get("pe_ratio"),
        "ev_ebitda": target_funds.get("ev_ebitda"),
        "revenue_growth": target_funds.get("revenue_growth"),
        "gross_margin": target_funds.get("gross_margin"),
        "operating_margin": target_funds.get("operating_margin"),
        "is_target": True,
        "_disagreements": target_funds.get("_disagreements") or [],
    }
    target_entry["peg"] = _compute_peg(
        target_entry["pe_ratio"],
        target_entry["revenue_growth"],
    )

    peer_tickers: list[str] = []
    try:
        def _tickers_from_fmp_peers(peers_data: Any) -> list[str]:
            """Normalize FMP peer payloads across legacy + stable endpoints."""
            out: list[str] = []

            # Legacy (`/api/v4/stock_peers`): [{symbol, peersList:[...]}, ...]
            if isinstance(peers_data, list) and peers_data:
                first = peers_data[0] if isinstance(peers_data[0], dict) else {}
                plist = first.get("peersList") if isinstance(first, dict) else None
                if isinstance(plist, list) and plist:
                    for sym in plist:
                        s = str(sym or "").strip().upper()
                        if s:
                            out.append(s)
                    return out

            # Stable (`/stable/stock-peers`): [{symbol, companyName, ...}, ...]
            if isinstance(peers_data, list):
                for row in peers_data:
                    if isinstance(row, dict):
                        sym = str(row.get("symbol") or "").strip().upper()
                        if sym:
                            out.append(sym)
                    elif isinstance(row, str):
                        sym = row.strip().upper()
                        if sym:
                            out.append(sym)

            # Rare dict-shaped responses — best-effort.
            if isinstance(peers_data, dict):
                plist = peers_data.get("peersList")
                if isinstance(plist, list):
                    for sym in plist:
                        s = str(sym or "").strip().upper()
                        if s:
                            out.append(s)

            return out

        peers_data = await _get_json(
            # Legacy `/api/v4/stock_peers` is gated to old subscriptions (Aug 31, 2025).
            # Stable route is documented here:
            # https://site.financialmodelingprep.com/developer/docs/stable/peers
            "https://financialmodelingprep.com/stable/stock-peers",
            params={"symbol": ticker, "apikey": FMP_API_KEY},
        )
        peer_tickers = _tickers_from_fmp_peers(peers_data)[:5]

        # If the stable endpoint returns empty (plan/key/rate-limit quirks), fall back once.
        if not peer_tickers:
            peers_legacy = await _get_json(
                "https://financialmodelingprep.com/api/v4/stock_peers",
                params={"symbol": ticker, "apikey": FMP_API_KEY},
            )
            peer_tickers = _tickers_from_fmp_peers(peers_legacy)[:5]
    except Exception:
        peer_tickers = []
    if not peer_tickers:
        peer_tickers = PEER_FALLBACK.get(ticker.upper(), [])[:5]
    # Remove the target ticker from its own peer list — FMP and fallback
    # sometimes include it. Case-insensitive comparison.
    target_upper = ticker.upper()
    peer_tickers = [p for p in peer_tickers if p and p.upper() != target_upper]
    # Deduplicate peer tickers (case-insensitive, preserving order)
    seen: set[str] = set()
    deduped: list[str] = []
    for p in peer_tickers:
        u = p.upper()
        if u not in seen:
            seen.add(u)
            deduped.append(p)
    peer_tickers = deduped
    if not peer_tickers:
        return {"error": "no peers"}
    try:

        peers: list[dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, headers=_FINNHUB_HEADERS) as client:
            for peer in peer_tickers:
                try:
                    profile_resp = await client.get(
                        f"{_FINNHUB_BASE}/stock/profile2", params={"symbol": peer}
                    )
                    metric_resp = await client.get(
                        f"{_FINNHUB_BASE}/stock/metric",
                        params={"symbol": peer, "metric": "all"},
                    )
                    profile = profile_resp.json() if profile_resp.status_code == 200 else {}
                    metric_doc = metric_resp.json() if metric_resp.status_code == 200 else {}
                    m = (metric_doc or {}).get("metric") or {}
                    peers.append({
                        "ticker": peer,
                        "name": profile.get("name") or peer,
                        "pe_ratio": _sanity_check_pe(m.get("forwardPE") or m.get("peBasicExclExtraTTM")),
                        "ev_ebitda": _sanity_check_ev_ebitda(m.get("evEbitdaTTM")),
                        "revenue_growth": _normalize_percentage(
                            m.get("revenueGrowthTTMYoy"),
                            source="finnhub",
                            field_name="revenue_growth",
                        ),
                        "gross_margin": _normalize_percentage(
                            m.get("grossMarginTTM"),
                            source="finnhub",
                            field_name="gross_margin",
                        ),
                        "operating_margin": _normalize_percentage(
                            m.get("operatingMarginTTM") or m.get("operatingMarginAnnual"),
                            source="finnhub",
                            field_name="operating_margin",
                        ),
                        "is_target": False,
                    })
                except Exception:
                    peers.append({"ticker": peer, "name": peer, "is_target": False})

        for p in peers:
            p["peg"] = _compute_peg(p.get("pe_ratio"), p.get("revenue_growth"))

        return _cache_put(cache_key, {"ticker": ticker, "peers": [target_entry] + peers})
    except Exception as exc:
        return {"error": f"get_competitors: {exc}"}


async def get_news_sentiment(ticker: str, company_name: str = "") -> dict[str, Any]:
    """Recent ticker-relevant headlines from Finnhub. Free tier doesn't
    expose per-article sentiment scores, so the analysis layer infers
    sentiment from headlines."""
    cache_key = f"news2:{ticker.upper()}:{company_name.upper()}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    try:
        today = datetime.utcnow().date()
        from_date = today - timedelta(days=14)
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            news_resp = await client.get(
                "https://finnhub.io/api/v1/company-news",
                params={
                    "symbol": ticker,
                    "from": from_date.isoformat(),
                    "to": today.isoformat(),
                    "token": FINNHUB_API_KEY,
                },
            )
        if news_resp.status_code != 200:
            return {"error": f"finnhub HTTP {news_resp.status_code}"}
        news_items = news_resp.json() or []
        if not isinstance(news_items, list):
            news_items = []

        needles: list[str] = [ticker.upper()]
        skip = {"INC", "CORP", "CORPORATION", "COMPANY", "LTD", "LIMITED", "CO", "PLC", "THE"}
        if company_name:
            for word in company_name.split():
                w = word.strip(",.").upper()
                if w and len(w) >= 3 and w not in skip:
                    needles.append(w)
                    break

        relevant = [
            it for it in news_items
            if any(n in (it.get("headline") or "").upper() for n in needles)
        ]
        candidates = (relevant if relevant else news_items)[:8]
        headlines = [str(it.get("headline") or "") for it in candidates]
        cls_list = await classify_headlines(ticker, company_name, headlines)
        used_llm = cls_list is not None
        if cls_list is None:
            cls_list = [classify_headline_heuristic(h) for h in headlines]
        datetimes_list = [it.get("datetime") for it in candidates]
        overall_sentiment, overall_rationale, net_tilt_signed, agg_extras = (
            aggregate_news_records(cls_list, datetimes_list)
        )
        labels = [c["label"] for c in cls_list]
        articles: list[dict[str, Any]] = []
        for it, cls in zip(candidates, cls_list):
            raw_dt = it.get("datetime")
            articles.append({
                "headline": it.get("headline", ""),
                "url": it.get("url", ""),
                "source": it.get("source", "") or "",
                "datetime": raw_dt,
                "published_at": format_news_datetime(raw_dt),
                "sentiment": cls["label"],
                "sentiment_reason": cls.get("reason", ""),
                "sentiment_confidence": cls.get("confidence", 0.5),
                "event_type": cls.get("event_type", "OTHER"),
                "magnitude": cls.get("magnitude", "minor"),
                "timeframe": cls.get("timeframe", "weeks"),
                "recency_weight": round(recency_weight(raw_dt), 2),
            })
        n_art = len(articles)
        if n_art == 0:
            score_block = make_score_block(
                score=50,
                confidence="LOW",
                reason="No recent news found.",
                drivers=[],
                sample_size=0,
                freshness="stale",
            )
        else:
            score = int(round(50 + net_tilt_signed * 40))
            sample_size = n_art
            freshness = "fresh" if sample_size >= 4 else "stale"
            if sample_size >= 8 and abs(net_tilt_signed) > 0.4:
                confidence = "HIGH"
            elif sample_size < 3:
                confidence = "LOW"
            else:
                confidence = "MEDIUM"
            reason = (overall_rationale or "").strip()[:200]
            if not reason:
                reason = f"Headline tilt {overall_sentiment} across {sample_size} articles."

            scored_with_weight: list[tuple[float, dict[str, Any]]] = []
            for idx, (rec_cls, raw_dt) in enumerate(zip(cls_list, datetimes_list)):
                lab = str(rec_cls.get("label") or "").lower()
                if lab == "neutral":
                    continue
                mag_w = {"major": 3.0, "material": 1.5, "minor": 0.6}.get(
                    rec_cls.get("magnitude", "minor"), 0.6
                )
                evt_w = {
                    "GUIDANCE": 1.5,
                    "LEGAL_REGULATORY": 1.5,
                    "EARNINGS": 1.2,
                    "ANALYST_ACTION": 1.2,
                    "M_AND_A": 1.1,
                    "PRODUCT": 1.0,
                    "MANAGEMENT": 1.0,
                    "MACRO_SECTOR": 0.8,
                    "OTHER": 0.8,
                }.get(rec_cls.get("event_type", "OTHER"), 1.0)
                rec_w = recency_weight(raw_dt)
                try:
                    conf = float(rec_cls.get("confidence", 0.5))
                except (TypeError, ValueError):
                    conf = 0.5
                total_w = mag_w * evt_w * rec_w * conf
                headline = str(candidates[idx].get("headline") or "")
                scored_with_weight.append((total_w, {
                    "headline": headline[:100],
                    "label": lab,
                    "event_type": rec_cls.get("event_type", "OTHER"),
                    "magnitude": rec_cls.get("magnitude", "minor"),
                }))
            scored_with_weight.sort(key=lambda t: t[0], reverse=True)

            drivers: list[dict[str, Any]] = []
            driver_weights = [0.8, 0.6, 0.4]
            for i, (_w, item) in enumerate(scored_with_weight[:3]):
                drivers.append(make_driver(
                    f"[{item['event_type']}/{item['magnitude']}] {item['headline']}",
                    item["label"],
                    driver_weights[i] if i < len(driver_weights) else 0.3,
                ))

            score_block = make_score_block(
                score=score,
                confidence=confidence,
                reason=reason,
                drivers=drivers,
                sample_size=sample_size,
                freshness=freshness,
            )

        return _cache_put(cache_key, {
            "ticker": ticker,
            "articles": articles,
            "overall_sentiment": overall_sentiment,
            "overall_rationale": overall_rationale,
            "classification_source": "gemini" if used_llm else "heuristic",
            "score_block": score_block,
            "agg_extras": agg_extras,
        })
    except Exception as exc:
        return {"error": f"get_news_sentiment: {exc}"}


import re as _re


_EPS_PATTERNS = [
    # "$1.54", "$4.93 per share", "EPS of $4.93", "earnings per share of $4.93"
    _re.compile(r"\$\s*(\d+(?:\.\d{1,2})?)\s*(?:per\s+share)", _re.IGNORECASE),
    _re.compile(r"(?:EPS|earnings per share)[^$]*\$\s*(\d+(?:\.\d{1,2})?)", _re.IGNORECASE),
    _re.compile(r"\b(\d+\.\d{2})\s*(?:per\s+share)", _re.IGNORECASE),
    _re.compile(r"(?:EPS|earnings per share)[^\d]*(\d+\.\d{2})", _re.IGNORECASE),
]

_REVENUE_PATTERNS = [
    _re.compile(r"\$\s*(\d+(?:\.\d+)?)\s*(billion|million|trillion|b|m|t)\b", _re.IGNORECASE),
    _re.compile(r"(\d+(?:\.\d+)?)\s*(billion|million|trillion)\s*dollars?", _re.IGNORECASE),
]


def _regex_fallback_eps(summary: str) -> str | None:
    for pat in _EPS_PATTERNS:
        m = pat.search(summary)
        if m:
            try:
                value = float(m.group(1))
                if 0 < value < 100:  # sanity bounds for quarterly EPS
                    return f"${value:.2f}"
            except ValueError:
                continue
    return None


def _regex_fallback_revenue(summary: str) -> str | None:
    for pat in _REVENUE_PATTERNS:
        m = pat.search(summary)
        if m:
            try:
                value = float(m.group(1))
                unit = (m.group(2) or "").lower()
                if unit.startswith("b"):
                    return f"${value:.2f}B"
                if unit.startswith("t"):
                    return f"${value:.2f}T"
                if unit.startswith("m"):
                    return f"${value:.0f}M"
            except (ValueError, IndexError):
                continue
    return None


async def _search_and_extract(
    client: Any,
    query: str,
    field: str,
) -> tuple[str | None, str]:
    """Run a grounded web search and extract a single field from the
    summary. Returns (extracted_value_or_None, raw_summary)."""
    from google.genai import types

    try:
        response = await client.aio.models.generate_content(
            model="gemini-2.5-flash",
            contents=query,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                temperature=0.1,
            ),
        )
        summary = getattr(response, "text", "") or ""
    except Exception:
        return None, ""
    if not summary:
        return None, ""

    extract_prompt = (
        f"Extract the analyst consensus {field} from this text. Return JSON:\n"
        f'{{"{field}": "<e.g. $66.1B or $4.93 or null>"}}\n'
        "Be lenient — accept any clearly-stated consensus, expectations, or "
        "estimates for the specified quarter, including variations like "
        '"analysts expect", "consensus of", "guided to", "forecast of".\n'
        "If the text mentions multiple estimates, pick the most recent or "
        "the Wall Street consensus. Use null only if no number is stated "
        "clearly. Return ONLY the JSON object.\n\n"
        f"TEXT:\n{summary}"
    )
    try:
        from google.genai import types as _types
        extract_resp = await client.aio.models.generate_content(
            model="gemini-2.5-flash",
            contents=extract_prompt,
            config=_types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0,
            ),
        )
        import json as _json
        parsed = _json.loads(getattr(extract_resp, "text", "") or "{}")
        if isinstance(parsed, dict):
            value = parsed.get(field)
            if value and str(value).strip().lower() not in ("null", "none", ""):
                return str(value).strip(), summary
    except Exception:
        pass

    return None, summary


async def _finnhub_earnings_calendar_consensus(
    ticker: str,
    company_name: str,
    quarter: str | None,
    year: str | int | None,
) -> dict[str, Any] | None:
    """Upcoming (or specified) fiscal period EPS + revenue consensus from Finnhub
    earnings calendar — structured, same source many terminals use."""
    if not FINNHUB_API_KEY:
        return None
    sym = ticker.strip().upper()
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, headers=_FINNHUB_HEADERS) as client:
            resp = await client.get(
                f"{_FINNHUB_BASE}/calendar/earnings",
                params={
                    "from": "2024-01-01",
                    "to": "2028-12-31",
                    "symbol": sym,
                },
            )
            if resp.status_code != 200:
                return None
            payload = resp.json() if isinstance(resp.json(), dict) else {}
            rows = payload.get("earningsCalendar") or []
    except Exception:
        return None
    if not isinstance(rows, list) or not rows:
        return None

    def _parse_q(q: str | None) -> int | None:
        if not q:
            return None
        s = str(q).strip().upper().replace("Q", "")
        try:
            n = int(s)
            return n if 1 <= n <= 4 else None
        except ValueError:
            return None

    def _parse_y(y: str | int | None) -> int | None:
        if y is None or str(y).strip() == "":
            return None
        try:
            return int(str(y).strip()[:4])
        except ValueError:
            return None

    qwant = _parse_q(quarter)
    ywant = _parse_y(year)

    picked: dict[str, Any] | None = None
    if qwant is not None and ywant is not None:
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                rq = int(row.get("quarter") or 0)
                ry = int(row.get("year") or 0)
            except (TypeError, ValueError):
                continue
            if rq == qwant and ry == ywant:
                picked = row
                break

    if picked is None:
        today = datetime.utcnow().date()
        dated: list[tuple[Any, dict[str, Any]]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            ds = (row.get("date") or "")[:10]
            try:
                d = datetime.strptime(ds, "%Y-%m-%d").date()
            except ValueError:
                continue
            dated.append((d, row))
        dated.sort(key=lambda x: x[0])
        for d, row in dated:
            if d >= today - timedelta(days=21) and (
                row.get("epsEstimate") is not None or row.get("revenueEstimate") is not None
            ):
                picked = row
                break
        if picked is None and dated:
            picked = dated[0][1]

    if picked is None:
        return None

    eps_e = picked.get("epsEstimate")
    rev_e = picked.get("revenueEstimate")
    if eps_e is None and rev_e is None:
        return None

    fq = picked.get("quarter")
    fy = picked.get("year")
    report_date = picked.get("date") or ""
    label = (
        f"Analyst consensus (pre-report) · fiscal Q{fq} FY{fy} · "
        f"report window {report_date} · source: Finnhub earnings calendar"
    )
    return {
        "ticker": sym,
        "quarter": quarter,
        "year": year,
        "revenue_estimate": rev_e,
        "eps_estimate": eps_e,
        "consensus_period_label": label,
        "estimate_period_end": report_date,
        "fiscal_quarter_label": f"Q{fq} FY{fy}",
        "source": "finnhub_calendar",
        "source_note": label,
    }


async def get_consensus_estimates(
    ticker: str,
    company_name: str,
    quarter: str | None,
    year: str | int | None,
) -> dict[str, Any]:
    """Quarterly EPS + revenue consensus: prefer Finnhub earnings calendar
    (structured). Fallback: Gemini + search (noisy — may confuse annual vs quarterly)."""
    cache_key = f"consensus:{ticker.upper()}:{quarter}:{year}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    fin = await _finnhub_earnings_calendar_consensus(ticker, company_name, quarter, year)
    if fin is not None and (fin.get("eps_estimate") is not None or fin.get("revenue_estimate") is not None):
        return _cache_put(cache_key, fin)

    if not GEMINI_API_KEY:
        return _cache_put(cache_key, {
            "ticker": ticker.upper(),
            "quarter": quarter,
            "year": year,
            "revenue_estimate": None,
            "eps_estimate": None,
            "source": "none",
            "source_note": "Set FINNHUB_API_KEY for calendar consensus or GEMINI_API_KEY for web search fallback.",
        })

    try:
        from google import genai
        client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as exc:
        return {"error": f"get_consensus_estimates: client init: {exc}"}

    q_str = quarter or "next"
    y_str = str(year) if year else ""

    rev_query = (
        f"For {company_name} ({ticker}) ONLY: Wall Street analyst consensus for "
        f"SINGLE QUARTER revenue (NOT TTM, NOT annual total) for {q_str} fiscal year {y_str}. "
        f"Return the consensus for that one quarter in dollars (e.g. ~$80B for a large chip quarter), "
        f"not full-year revenue."
    )
    eps_query = (
        f"For {company_name} ({ticker}) ONLY: Wall Street analyst consensus diluted EPS for "
        f"SINGLE QUARTER {q_str} fiscal year {y_str} (NOT annual EPS, NOT TTM). "
        f"Typical quarterly EPS is a small dollar amount (e.g. $1.50–$3.00), not hundreds."
    )

    import asyncio as _asyncio
    (rev_value, rev_summary), (eps_value, eps_summary) = await _asyncio.gather(
        _search_and_extract(client, rev_query, "revenue_estimate"),
        _search_and_extract(client, eps_query, "eps_estimate"),
    )

    if not rev_value and rev_summary:
        rev_value = _regex_fallback_revenue(rev_summary)
    if not eps_value and eps_summary:
        eps_value = _regex_fallback_eps(eps_summary)
    if not eps_value and rev_summary:
        eps_value = _regex_fallback_eps(rev_summary)
    if not rev_value and eps_summary:
        rev_value = _regex_fallback_revenue(eps_summary)

    return _cache_put(cache_key, {
        "ticker": ticker.upper(),
        "quarter": quarter,
        "year": year,
        "revenue_estimate": rev_value,
        "eps_estimate": eps_value,
        "consensus_period_label": (
            f"Web-derived consensus (verify) · requested {q_str} FY{y_str} · source: Gemini search"
        ),
        "source": "gemini_search",
        "source_note": f"Web-assisted estimates for {company_name} {q_str} FY{y_str} — prefer Finnhub calendar when available.",
        "raw_search": ((rev_summary or "") + "\n---\n" + (eps_summary or ""))[:800],
    })


async def web_search(query: str) -> dict[str, Any]:
    """Grounded web search via Gemini with the google_search tool."""
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=GEMINI_API_KEY)
        response = await client.aio.models.generate_content(
            model="gemini-2.5-flash",
            contents=query,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                temperature=0.2,
            ),
        )
        text = getattr(response, "text", "") or ""
        url = ""
        try:
            cands = getattr(response, "candidates", None) or []
            if cands:
                gm = getattr(cands[0], "grounding_metadata", None)
                if gm:
                    chunks = getattr(gm, "grounding_chunks", None) or []
                    for c in chunks:
                        web = getattr(c, "web", None)
                        if web and getattr(web, "uri", None):
                            url = web.uri
                            break
        except Exception:
            pass
        return {"query": query, "summary": text, "source_url": url}
    except Exception as exc:
        return {"error": f"web_search: {exc}"}


async def get_sec_filing(ticker: str) -> dict[str, Any]:
    sym = ticker.strip().upper()
    cache_key = f"sec_filing:{sym}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    try:
        params = {
            "q": f'"{sym}"',
            "dateRange": "custom",
            "startdt": "2024-01-01",
            "forms": "10-K,10-Q",
        }
        headers = {"User-Agent": _SEC_EDGAR_UA}
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, headers=headers) as client:
            resp = await client.get(
                "https://efts.sec.gov/LATEST/search-index",
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        return {"error": f"get_sec_filing: {exc}"}

    try:
        hits = (data.get("hits") or {}).get("hits") or []
        if not hits:
            return {"error": "no SEC filings in response"}
        first = hits[0]
        src = first.get("_source") or {}
        file_date = src.get("file_date") or src.get("display_date_filed") or ""
        form_type = src.get("form_type") or ""
        entity_name = src.get("entity_name") or ""
        accession_no = src.get("accession_no") or src.get("adsh") or ""
        if not accession_no and isinstance(first.get("_id"), str):
            accession_no = first["_id"].split(":")[0]
        edgar_url = (
            f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={sym}"
            "&type=10-K&dateb=&owner=include&count=5"
        )
        return _cache_put(cache_key, {
            "ticker": sym,
            "form_type": str(form_type) if form_type else "",
            "filed_date": str(file_date) if file_date else "",
            "entity_name": str(entity_name) if entity_name else "",
            "accession_no": str(accession_no) if accession_no else "",
            "edgar_url": edgar_url,
        })
    except Exception as exc:
        return {"error": f"get_sec_filing: parse: {exc}"}


def _fred_parse_float(raw: Any) -> float | None:
    if raw is None or raw == ".":
        return None
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return None


async def _fred_history_desc(
    client: httpx.AsyncClient,
    series_id: str,
    limit: int,
) -> list[tuple[str, float]]:
    """Newest-first (date, value) pairs from FRED."""
    try:
        resp = await client.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={
                "series_id": series_id,
                "api_key": FRED_API_KEY,
                "file_type": "json",
                "sort_order": "desc",
                "limit": limit,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        obs = data.get("observations") or []
        out: list[tuple[str, float]] = []
        for o in obs:
            dt = str(o.get("date") or "")
            v = _fred_parse_float(o.get("value"))
            if v is not None and dt:
                out.append((dt, v))
        return out
    except Exception:
        return []


def _series_block(value: float | None, date: str | None, trend: dict[str, Any]) -> dict[str, Any]:
    b: dict[str, Any] = {
        "value": None if value is None else f"{value:.6g}",
        "date": date or "",
    }
    if trend:
        b["trend"] = trend
    return b


def _trend_monthly_levels(levels: list[tuple[str, float]], *, unit: str) -> dict[str, Any]:
    """levels newest-first. unit: 'pct_pp' (rate in %), 'index', 'spread_pp'."""
    if len(levels) < 2:
        return {}
    v0, v1 = levels[0][1], levels[1][1]
    mom = v0 - v1
    out: dict[str, Any] = {
        "mom_change": round(mom, 4),
        "mom_label": _fmt_mom_label(mom, unit),
        "direction_mom": "up" if mom > 1e-6 else ("down" if mom < -1e-6 else "flat"),
    }
    if len(levels) > 12:
        yoy = v0 - levels[12][1]
        out["yoy_change"] = round(yoy, 4)
        out["yoy_label"] = _fmt_yoy_label(yoy, unit)
    return out


def _fmt_mom_label(mom: float, unit: str) -> str:
    if unit == "index":
        return f"{mom:+.3f} MoM"
    if unit == "spread_pp":
        return f"{mom:+.2f} pp vs 1m ago"
    return f"{mom:+.2f} pp vs 1m ago"


def _fmt_yoy_label(yoy: float, unit: str) -> str:
    if unit == "index":
        return f"{yoy:+.3f} vs 12m ago"
    return f"{yoy:+.2f} pp vs 12m ago"


def _trend_cpi_yoy(levels: list[tuple[str, float]]) -> dict[str, Any]:
    """CPI index: YoY % is headline; MoM % annualized optional."""
    if len(levels) < 13:
        return {}
    idx0, idx12 = levels[0][1], levels[12][1]
    if idx12 <= 0:
        return {}
    yoy_pct = (idx0 / idx12 - 1.0) * 100.0
    out: dict[str, Any] = {
        "yoy_inflation_pct": round(yoy_pct, 2),
        "yoy_label": f"YoY {yoy_pct:+.2f}%",
        "direction_mom": "up" if yoy_pct > 0.1 else ("down" if yoy_pct < -0.1 else "flat"),
    }
    if len(levels) >= 2:
        i0, i1 = levels[0][1], levels[1][1]
        if i1 > 0:
            mom_pct = (i0 / i1 - 1.0) * 100.0
            out["mom_inflation_pct"] = round(mom_pct, 3)
            out["mom_label"] = f"{mom_pct:+.3f}% MoM"
    return out


def _trend_daily_change(levels: list[tuple[str, float]], lag: int) -> dict[str, Any]:
    if len(levels) <= lag:
        return {}
    chg = levels[0][1] - levels[lag][1]
    return {
        "chg_vs_trading_lag": round(chg, 3),
        "lag_days": lag,
        "mom_label": f"{chg:+.2f} vs ~{lag} sessions ago",
        "direction_mom": "up" if chg > 0.01 else ("down" if chg < -0.01 else "flat"),
    }


def _policy_curve_expectations(eff: float, gs2: float) -> dict[str, Any]:
    """Rough market tilt: effective policy vs 2Y Treasury (not CME FedWatch)."""
    gap_pp = eff - gs2
    gap_bps = gap_pp * 100.0
    cuts_equiv = max(0, min(16, round(gap_pp / 0.25))) if gap_pp > 0 else 0
    hikes_equiv = max(0, min(16, round(-gap_pp / 0.25))) if gap_pp < 0 else 0
    if gap_pp > 0.05:
        interp = (
            f"2Y Treasury is ~{abs(gap_bps):.0f} bps below effective Fed funds — "
            f"curve embeds ~{cuts_equiv}×25 bp of easing vs spot (rough proxy)."
        )
    elif gap_pp < -0.05:
        interp = (
            f"2Y Treasury is ~{abs(gap_bps):.0f} bps above effective Fed funds — "
            f"curve embeds ~{hikes_equiv}×25 bp of hikes vs spot (rough proxy)."
        )
    else:
        interp = "2Y vs effective funds roughly aligned — limited directional pricing."
    return {
        "effective_fed_funds_pct": round(eff, 3),
        "treasury_2y_pct": round(gs2, 3),
        "spread_eff_minus_2y_pp": round(gap_pp, 3),
        "spread_eff_minus_2y_bps": round(gap_bps, 1),
        "cuts_priced_25bp_equiv": cuts_equiv,
        "hikes_priced_25bp_equiv": hikes_equiv,
        "interpretation": interp,
    }


def _yield_curve_point(
    levels: list[tuple[str, float]] | None,
    label: str,
    tenor_years: float,
) -> dict[str, Any] | None:
    if not levels:
        return None
    dt, y = levels[0][0], levels[0][1]
    return {
        "label": label,
        "tenor_years": tenor_years,
        "yield_pct": round(float(y), 3),
        "as_of": dt,
    }


async def get_macro_snapshot() -> dict[str, Any]:
    cache_key = "macro_snapshot_v3"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    if not FRED_API_KEY:
        return {"error": "FRED_API_KEY not configured"}

    monthly_ids = [
        ("FEDFUNDS", "fed_funds_rate", "pct_pp"),
        ("CPIAUCSL", "cpi", "cpi_yoy"),
        ("UNRATE", "unemployment_rate", "pct_pp"),
        ("GS10", "treasury_10y", "pct_pp"),
    ]

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            n_m = len(monthly_ids)
            bundle = await asyncio.gather(
                *[_fred_history_desc(client, sid, 15) for sid, _, _ in monthly_ids],
                _fred_history_desc(client, "T10Y2Y", 35),
                _fred_history_desc(client, "DFF", 35),
                _fred_history_desc(client, "GS2", 35),
                _fred_history_desc(client, "TB3MS", 12),
                _fred_history_desc(client, "GS5", 12),
                _fred_history_desc(client, "GS30", 12),
            )
        month_parts = list(bundle[:n_m])
        spread_hist, dff_hist, gs2_hist = bundle[n_m], bundle[n_m + 1], bundle[n_m + 2]
        tb3_hist, gs5_hist, gs30_hist = bundle[n_m + 3], bundle[n_m + 4], bundle[n_m + 5]
    except Exception:
        month_parts = [[] for _ in monthly_ids]
        spread_hist, dff_hist, gs2_hist = [], [], []
        tb3_hist, gs5_hist, gs30_hist = [], [], []

    out: dict[str, Any] = {}
    fed_rate: float | None = None

    for (sid, key, kind), levels in zip(monthly_ids, month_parts):
        if not levels:
            out[key] = {"value": None, "date": None}
            continue
        dt, val = levels[0][0], levels[0][1]
        if key == "fed_funds_rate":
            fed_rate = val
        trend: dict[str, Any] = {}
        if kind == "cpi_yoy":
            trend = _trend_cpi_yoy(levels)
        else:
            trend = _trend_monthly_levels(levels, unit="pct_pp")
        out[key] = _series_block(val, dt, trend)

    # 10Y-2Y spread: daily — ~1 month change
    if spread_hist:
        sdt, sv = spread_hist[0][0], spread_hist[0][1]
        tr = _trend_daily_change(spread_hist, 21)
        out["yield_spread_10y2y"] = _series_block(sv, sdt, tr)
    else:
        out["yield_spread_10y2y"] = {"value": None, "date": None}

    # Policy vs curve (cuts / hikes priced — proxy)
    policy_expectations: dict[str, Any] | None = None
    if dff_hist and gs2_hist:
        eff = dff_hist[0][1]
        g2 = gs2_hist[0][1]
        policy_expectations = _policy_curve_expectations(eff, g2)

    if fed_rate is None and isinstance(out.get("fed_funds_rate"), dict):
        try:
            fed_rate = float(str(out["fed_funds_rate"].get("value") or ""))
        except (TypeError, ValueError):
            fed_rate = None

    if fed_rate is None:
        regime = "neutral"
    elif fed_rate > 4.0:
        regime = "tight"
    elif fed_rate < 2.0:
        regime = "loose"
    else:
        regime = "neutral"

    out["macro_regime"] = regime
    out["policy_expectations"] = policy_expectations

    # Treasury spot curve (FRED): 3M bill + 2s/5s/10s/30s constant-maturity — for UI chart.
    gs10_levels = month_parts[3] if len(month_parts) > 3 else []
    curve_pts: list[dict[str, Any]] = []
    for pt in (
        _yield_curve_point(tb3_hist, "3M", 0.25),
        _yield_curve_point(gs2_hist, "2Y", 2.0),
        _yield_curve_point(gs5_hist, "5Y", 5.0),
        _yield_curve_point(gs10_levels, "10Y", 10.0),
        _yield_curve_point(gs30_hist, "30Y", 30.0),
    ):
        if pt:
            curve_pts.append(pt)
    out["yield_curve"] = curve_pts

    return _cache_put(cache_key, out)
