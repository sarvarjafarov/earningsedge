"""Orchestrator — coordinates the multi-agent system.

Owns:
  - shared session state (ticker, company, mode, start time)
  - a single audio_queue (PCM in)
  - a master transcript_queue, fanned out to per-agent subqueues
  - the broadcast function passed in by main.py

Lifecycle:
  start_briefing(ticker, company, quarter, year)
    -> pre-loads data via tools, broadcasts to dashboard
    -> starts TranscriptAgent in BRIEFING mode
  identify_from_text(text)
    -> when called with the user's briefing transcript, runs a small
       gemini-2.5-flash extract and triggers start_briefing
  start_live()
    -> switches TranscriptAgent to LIVE mode
    -> spawns MetricsAgent, SentimentAgent, FinalSynthesisAgent (final trade view),
       HighlightLexiconAgent as parallel asyncio tasks
  stop()
    -> cancels everything, generates summary

  ask_agent (HTTP /api/ask)
    -> ChatAgent — side-channel Q&A, not part of the Live audio agents loop
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Awaitable, Callable

from google import genai
from google.genai import types

from price_stream import PriceStream

from agents import (
    ChatAgent,
    HighlightLexiconAgent,
    MacroAgent,
    MetricsAgent,
    SentimentAgent,
    TechnicalAgent,
    PeerAgent,
    FinalSynthesisAgent,
    TranscriptAgent,
)
from agents.final_synthesis_agent import emit_coverage_trade_signal
from tools import (
    get_analyst_recommendation,
    get_competitors,
    get_consensus_estimates,
    get_earnings_estimates,
    get_fundamentals,
    get_news_sentiment,
    prior_quarter_eps_snapshot,
    ALPHA_VANTAGE_API_KEY,
    reset_cache,
)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

BroadcastFn = Callable[[dict[str, Any]], Awaitable[None]]

IDENTIFY_PROMPT = """The user may say ONLY a company name (one or a few words), or a full sentence — infer everything from that.

Extract company info from this earnings call briefing the user just spoke aloud. Return JSON with these exact fields:

{
  "ticker": "<stock symbol>",
  "company_name": "<full name>",
  "quarter": "Q1|Q2|Q3|Q4",
  "year": "<four-digit fiscal year>"
}

CRITICAL: The ticker is the stock market symbol. You MUST infer it from the company name even when the user does not say the symbol out loud. Examples:
  - "Nvidia" -> "NVDA"
  - "Apple" -> "AAPL"
  - "Microsoft" -> "MSFT"
  - "Alphabet" or "Google" -> "GOOGL"
  - "Amazon" -> "AMZN"
  - "Meta" or "Facebook" -> "META"
  - "Tesla" -> "TSLA"
  - "Netflix" -> "NFLX"
  - "Oracle" -> "ORCL"
  - "Broadcom" -> "AVGO"
  - "AMD" -> "AMD"
  - "Salesforce" -> "CRM"
  - "Palantir" -> "PLTR"
  - "Costco" -> "COST"
  - "JPMorgan" or "JPMorgan Chase" -> "JPM"

Only return `null` for ticker if you genuinely cannot recognise the company. If ANY of the other fields (quarter, year) aren't stated, make your best inference — assume the most recent fiscal year.

Return ONLY the JSON object, no commentary, no markdown fences."""


class AgentResults:
    """Shared store the worker agents push to as they emit. FinalSynthesisAgent
    reads metrics/sentiment plus one-shot dashboard payloads."""

    def __init__(self) -> None:
        self.metrics: list[dict[str, Any]] = []
        self.sentiment: list[dict[str, Any]] = []
        self.macro: list[dict[str, Any]] = []
        self.valuation: list[dict[str, Any]] = []
        self.technical: list[dict[str, Any]] = []
        self.analyst: list[dict[str, Any]] = []
        self.peer: list[dict[str, Any]] = []
        self.accounting: list[dict[str, Any]] = []
        self.options: list[dict[str, Any]] = []

    def reset(self) -> None:
        self.reset_live_streams()
        self.macro.clear()
        self.valuation.clear()
        self.technical.clear()
        self.analyst.clear()
        self.peer.clear()
        self.accounting.clear()
        self.options.clear()

    def reset_live_streams(self) -> None:
        """Clear live-call accumulators only; keep one-shot dashboard payloads."""
        self.metrics.clear()
        self.sentiment.clear()

    def as_dict(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "metrics": self.metrics,
            "sentiment": self.sentiment,
            "macro": self.macro,
            "technical": self.technical,
            "analyst": self.analyst,
            "peer": self.peer,
        }

    def latest_score_blocks(self) -> dict[str, dict[str, Any] | None]:
        """Return {specialist_name: most_recent_score_block_or_None}.

        The synthesizer reads these in a later chunk to compute a weighted
        committee signal. News score comes from ctx.preloaded_news rather
        than a broadcast stream — the synthesizer reads it separately.
        """

        def _pick(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
            for row in reversed(rows):
                # Score block may be at top level (metrics) or under 'data'
                # (sentiment, macro, technical, analyst). Try both.
                sb = row.get("score_block")
                if isinstance(sb, dict):
                    return sb
                data = row.get("data")
                if isinstance(data, dict):
                    sb = data.get("score_block")
                    if isinstance(sb, dict):
                        return sb
            return None

        return {
            "metrics": _pick(self.metrics),
            "sentiment": _pick(self.sentiment),
            "macro": _pick(self.macro),
            "technical": _pick(self.technical),
            "analyst": _pick(self.analyst),
            "peer": _pick(self.peer),
        }


class Orchestrator:
    def __init__(self) -> None:
        self.audio_queue: asyncio.Queue = asyncio.Queue(maxsize=1024)
        self.transcript_queue: asyncio.Queue = asyncio.Queue(maxsize=1024)
        self.subscribers: list[asyncio.Queue] = []
        self.broadcast_fn: BroadcastFn | None = None
        # Session id of the tab that triggered the current orchestrator turn.
        # Stamped onto every broadcast so the WS layer can deliver only to
        # the originating tab. None = global event (legacy behavior).
        self._session_id: str | None = None
        self.state: dict[str, Any] = {
            "ticker": None,
            "company_name": None,
            "sector": None,
            "quarter": None,
            "year": None,
            "mode": "IDLE",
            "session_start": None,
            "baseline_sentiment": 50,  # seeded from analyst recommendations
        }
        self.results = AgentResults()
        self.transcript_agent: TranscriptAgent | None = None
        self.metrics_agent: MetricsAgent | None = None
        self.sentiment_agent: SentimentAgent | None = None
        self.trade_agent: FinalSynthesisAgent | None = None
        self.highlight_lexicon_agent: HighlightLexiconAgent | None = None
        self.chat_agent = ChatAgent()
        self.transcript_task: asyncio.Task | None = None
        self.fanout_task: asyncio.Task | None = None
        self.identifier_task: asyncio.Task | None = None
        self.agent_tasks: list[asyncio.Task] = []
        self.history: list[dict[str, Any]] = []
        self.dashboard_clients: set = set()
        self.lock = asyncio.Lock()
        self._briefing_in_flight = False
        self._briefing_chunks: list[str] = []
        self._preserve_company_on_audio_close = False
        self.price_stream: PriceStream | None = None

    def set_broadcast(self, fn: BroadcastFn) -> None:
        self.broadcast_fn = fn

    def get_mode(self) -> str:
        return self.state.get("mode", "IDLE")

    def is_running(self) -> bool:
        return self.transcript_agent is not None and self.state.get("mode") in {"BRIEFING", "LIVE"}

    async def broadcast(self, msg: dict[str, Any]) -> None:
        # Capture relevant agent emissions for the trade signal agent.
        msg_type = msg.get("type")
        data = msg.get("data") or {}
        if msg_type == "metrics":
            self.results.metrics.append(data)
        elif msg_type == "sentiment":
            self.results.sentiment.append(data)
        elif msg_type == "macro":
            self.results.macro.append(data)
        elif msg_type == "valuation":
            self.results.valuation.append(data)
        elif msg_type == "technical":
            self.results.technical.append(data)
        elif msg_type == "analyst_opinion":
            self.results.analyst.append(data)
        elif msg_type == "peer_valuation":
            self.results.peer.append(data)
        elif msg_type == "accounting":
            self.results.accounting.append(data)
        elif msg_type == "options":
            self.results.options.append(data)
        elif msg_type == "company_identified":
            self.state["ticker"] = data.get("ticker")
            self.state["company_name"] = data.get("company_name")
            self.state["sector"] = data.get("sector")
            self.state["quarter"] = data.get("quarter")
            self.state["year"] = data.get("fiscal_year") or data.get("year")
        elif msg_type == "transcript" and self.state.get("mode") == "BRIEFING":
            tx = (data.get("text") or "").strip()
            if tx:
                self._briefing_chunks.append(tx)

        # Stamp every outgoing message with the current session_id so the WS
        # layer can route it to the right tab. Already-tagged messages
        # (rare — e.g. someone explicitly set session_id upstream) win.
        if self._session_id is not None and "session_id" not in msg:
            msg = {**msg, "session_id": self._session_id}

        if self.broadcast_fn is not None:
            await self.broadcast_fn(msg)

    def set_session_id(self, session_id: str | None) -> None:
        """Tag subsequent broadcasts with this session_id so they reach the
        originating tab only. Called from /api/coverage and /ws/audio entry
        points before the orchestrator does any work for that request."""
        self._session_id = session_id

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=512)
        self.subscribers.append(q)
        return q

    async def feed_audio(self, chunk: bytes) -> None:
        # Drop audio while paused (so the call transcript halts but the
        # session stays alive for user Q&A via /api/ask).
        if self.state.get("paused"):
            return
        # Lightweight visibility: if audio is flowing but transcript is empty, the UI otherwise
        # looks "stuck". Emit a single status ping when the first bytes arrive in LIVE mode.
        if not getattr(self, "_audio_seen_once", False) and self.state.get("mode") == "LIVE":
            self._audio_seen_once = True
            await self.broadcast({
                "type": "status",
                "data": {"state": "running", "message": "receiving call audio… waiting for first transcript line"},
            })
        # Rolling throughput / liveness for debugging live stalls.
        if self.state.get("mode") == "LIVE":
            now = time.monotonic()
            if not hasattr(self, "_audio_bytes_total"):
                self._audio_bytes_total = 0
                self._audio_last_stat_at = now
                self._audio_last_bytes = 0
            self._audio_bytes_total += len(chunk or b"")
            if (now - getattr(self, "_audio_last_stat_at", now)) >= 2.0:
                dt = max(0.001, now - self._audio_last_stat_at)
                dbytes = self._audio_bytes_total - self._audio_last_bytes
                kbps = (dbytes / 1024.0) / dt
                self._audio_last_stat_at = now
                self._audio_last_bytes = self._audio_bytes_total
                await self.broadcast({
                    "type": "status",
                    "data": {"state": "running", "message": f"live audio flowing ~{kbps:.1f} KB/s"},
                })
        try:
            self.audio_queue.put_nowait(chunk)
        except asyncio.QueueFull:
            # Drop a frame rather than block — backpressure on the realtime
            # audio path is unacceptable.
            pass

    async def set_paused(self, paused: bool) -> None:
        self.state["paused"] = bool(paused)
        await self.broadcast({
            "type": "status",
            "data": {
                "state": "paused" if paused else "running",
                "message": "paused" if paused else "resumed",
                "phase": self.state.get("mode", "").lower(),
            },
        })

    async def ask_agent(self, question: str) -> str:
        """Delegate to ChatAgent (side-channel Gemini; parallel to Live audio)."""
        return await self.chat_agent.answer(
            question,
            state=self.state,
            history=self.history,
            broadcast=self.broadcast,
        )

    async def briefing_user_finished(self) -> None:
        """User tapped 'Done speaking' — flush mic transcription and process once."""
        if self.identifier_task:
            self.identifier_task.cancel()
            try:
                await self.identifier_task
            except (asyncio.CancelledError, Exception):
                pass
            self.identifier_task = None

        if self.transcript_agent is not None:
            try:
                await self.transcript_agent.force_flush()
            except Exception:
                pass
        await asyncio.sleep(0.2)

        joined = " ".join(self._briefing_chunks).strip()
        await self._finalize_briefing_voice(joined)
        self._preserve_company_on_audio_close = True
        await self.broadcast({
            "type": "voice_briefing_complete",
            "data": {"ok": True, "had_text": len(joined) >= 3},
        })

    def _looks_like_assistant_query(self, text: str) -> bool:
        """Heuristic: user wants the Jarvis assistant, not only a company name."""
        t = (text or "").strip()
        if len(t) >= 55:
            return True
        tl = t.lower()
        if "?" in tl:
            return True
        return any(
            tl.startswith(p) or f" {p}" in tl
            for p in (
                "what ",
                "how ",
                "why ",
                "when ",
                "where ",
                "explain ",
                "summarize",
                "compare ",
                "should ",
                "tell me",
                "give me",
                "show me",
                "analyze ",
                "walk me",
                "run ",
                "pull ",
            )
        )

    async def _finalize_briefing_voice(self, text: str) -> None:
        if len(text) < 3:
            await self.broadcast({
                "type": "status",
                "data": {
                    "state": "error",
                    "message": "Didn't catch that — speak again, or use the text form.",
                },
            })
            return

        had_ticker_before = bool(self.state.get("ticker"))
        if not had_ticker_before:
            info = await self._identify_company(text)
            if info and info.get("ticker"):
                self._briefing_in_flight = True
                await self.start_briefing(
                    info.get("ticker"),
                    info.get("company_name") or info.get("ticker"),
                    info.get("quarter"),
                    info.get("year"),
                )
                self._briefing_in_flight = False
                return

        if self.state.get("ticker") and self._looks_like_assistant_query(text):
            await self._handle_voice_assistant_command(text, voice_command=False)
            return

        if not had_ticker_before:
            await self._handle_voice_assistant_command(text, voice_command=True)

    async def _handle_voice_assistant_command(self, text: str, *, voice_command: bool) -> None:
        try:
            await self.chat_agent.answer(
                text,
                state=self.state,
                history=self.history,
                broadcast=self.broadcast,
                voice_command=voice_command,
            )
        except Exception as exc:
            await self.broadcast({
                "type": "status",
                "data": {"state": "error", "message": f"assistant: {exc}"},
            })

    async def end_audio_preserve_company(self) -> None:
        """Stop Live / transcript machinery but keep ticker and loaded dashboard context."""
        async with self.lock:
            if self.price_stream is not None:
                try:
                    await self.price_stream.stop()
                except Exception:
                    pass
                self.price_stream = None
            for ag in (
                self.metrics_agent,
                self.sentiment_agent,
                self.trade_agent,
                self.highlight_lexicon_agent,
            ):
                if ag is not None:
                    try:
                        ag.stop()
                    except Exception:
                        pass
            for t in self.agent_tasks:
                t.cancel()
            for t in self.agent_tasks:
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
            self.agent_tasks = []
            self.metrics_agent = None
            self.sentiment_agent = None
            self.trade_agent = None
            self.highlight_lexicon_agent = None

            if self.identifier_task:
                self.identifier_task.cancel()
                try:
                    await self.identifier_task
                except (asyncio.CancelledError, Exception):
                    pass
                self.identifier_task = None

            if self.transcript_agent is not None:
                try:
                    await self.transcript_agent.stop()
                except Exception:
                    pass
                self.transcript_agent = None

            if self.transcript_task:
                self.transcript_task.cancel()
                try:
                    await self.transcript_task
                except (asyncio.CancelledError, Exception):
                    pass
                self.transcript_task = None

            if self.fanout_task:
                self.fanout_task.cancel()
                try:
                    await self.fanout_task
                except (asyncio.CancelledError, Exception):
                    pass
                self.fanout_task = None

            self.subscribers.clear()
            self.audio_queue = asyncio.Queue(maxsize=1024)
            self.transcript_queue = asyncio.Queue(maxsize=1024)
            self.state["mode"] = "IDLE"
            self.state["paused"] = False
            self._briefing_chunks = []
            await self.broadcast({
                "type": "status",
                "data": {"state": "idle", "message": "Voice capture closed — dashboard context kept."},
            })

    async def _fanout_loop(self) -> None:
        try:
            while True:
                sentence = await self.transcript_queue.get()
                for q in list(self.subscribers):
                    try:
                        q.put_nowait(sentence)
                    except asyncio.QueueFull:
                        pass
        except asyncio.CancelledError:
            return

    async def open_session(self) -> None:
        """Open the persistent transcript agent. Called when the audio
        WebSocket connects, before any briefing has been identified."""
        async with self.lock:
            if self.transcript_agent is not None:
                return
            reset_cache()
            self.results.reset_live_streams()
            # CRITICAL: reset briefing-in-flight so the identifier loop can
            # fire again on subsequent sessions. Without this, after the
            # first company is identified the flag stays True forever and
            # every subsequent session skips data preload.
            self._briefing_in_flight = False
            self._briefing_chunks = []
            # If the user already preloaded coverage via POST /api/coverage, keep
            # ticker/company/quarter — otherwise mic-first sessions start empty.
            had_precoverage = bool(
                self.state.get("ticker") and self.state.get("company_name")
            )
            if not had_precoverage:
                self.state.update({
                    "ticker": None,
                    "company_name": None,
                    "sector": None,
                    "quarter": None,
                    "year": None,
                })
            self.state.update({
                "mode": "BRIEFING",
                "session_start": time.time(),
                "baseline_sentiment": 50,
                "paused": False,
            })
            self.subscribers.clear()
            await self.broadcast({
                "type": "status",
                "data": {"state": "connecting", "message": "starting transcript agent"},
            })
            self.transcript_agent = TranscriptAgent(
                self.audio_queue,
                self.transcript_queue,
                self.broadcast,
                self.get_mode,
            )
            self.fanout_task = asyncio.create_task(self._fanout_loop())
            self.transcript_task = asyncio.create_task(self.transcript_agent.run())
            # Subscribe an internal listener that auto-identifies the
            # company from the briefing transcript.
            self.identifier_task = asyncio.create_task(self._identifier_loop())
            await self.broadcast({
                "type": "status",
                "data": {"state": "running", "message": "transcript agent live", "phase": "briefing"},
            })

    async def _identifier_loop(self) -> None:
        """Listens to the briefing transcript for the first usable mention
        of a company / quarter / year and triggers start_briefing exactly
        once. Subscribes to its own copy of the transcript fanout."""
        q = self.subscribe()
        accumulated: list[str] = []
        try:
            while True:
                if self._briefing_in_flight or self.state.get("ticker"):
                    return
                if self.state.get("mode") == "LIVE":
                    return
                try:
                    sentence = await asyncio.wait_for(q.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    return
                if sentence.get("mode") != "BRIEFING":
                    continue
                accumulated.append(sentence.get("text", ""))
                joined = " ".join(accumulated).strip()
                # Short names alone (e.g. "Nvidia", "Meta", "TSMC") must still
                # trigger identification — the old 12-char floor blocked them.
                if len(joined) < 3:
                    continue
                info = await self._identify_company(joined)
                if info and info.get("ticker"):
                    self._briefing_in_flight = True
                    asyncio.create_task(
                        self.start_briefing(
                            info.get("ticker"),
                            info.get("company_name") or info.get("ticker"),
                            info.get("quarter"),
                            info.get("year"),
                        )
                    )
                    return
        except asyncio.CancelledError:
            return

    async def _identify_company(self, text: str) -> dict[str, Any] | None:
        try:
            client = genai.Client(api_key=GEMINI_API_KEY)
            response = await client.aio.models.generate_content(
                model="gemini-2.5-flash",
                contents=f"{IDENTIFY_PROMPT}\n\nBRIEFING: {text}",
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0,
                ),
            )
            raw = getattr(response, "text", "") or "{}"
            obj = json.loads(raw)
        except Exception as exc:
            await self.broadcast({
                "type": "status",
                "data": {"state": "error", "message": f"identify failed: {exc}"},
            })
            return None

        if not isinstance(obj, dict):
            return None
        ticker = obj.get("ticker")
        company = obj.get("company_name")
        # If the model still returned null for ticker but gave us a company
        # name, the inference failed — surface it so we can see it in logs
        # instead of silently giving up.
        if not ticker and company:
            await self.broadcast({
                "type": "status",
                "data": {
                    "state": "error",
                    "message": f"could not map '{company}' to a ticker",
                },
            })
            return None
        if ticker:
            return obj
        return None

    async def start_briefing(
        self,
        ticker: str,
        company_name: str,
        quarter: str | None,
        year: str | int | None,
    ) -> None:
        """Pre-load financial data and broadcast the initial dashboard state."""
        if not ticker:
            return
        self.results.macro.clear()
        self.results.valuation.clear()
        self.results.technical.clear()
        self.results.accounting.clear()
        self.results.options.clear()
        self.results.reset_live_streams()
        self.state.update({
            "ticker": ticker,
            "company_name": company_name,
            "quarter": quarter,
            "year": year,
        })
        if self.state.get("session_start") is None:
            self.state["session_start"] = time.time()

        if ALPHA_VANTAGE_API_KEY:
            self.price_stream = PriceStream(self.broadcast)
            asyncio.create_task(self.price_stream.start(ticker))

        await self.broadcast({
            "type": "company_identified",
            "data": {
                "ticker": ticker,
                "company_name": company_name,
                "sector": None,
                "quarter": quarter,
                "fiscal_year": year,
            },
        })

        # Pre-load all data in parallel. Includes a web-grounded search for
        # consensus revenue + EPS estimates so the dashboard shows the
        # analyst baseline BEFORE the call mentions anything.
        try:
            (
                estimates,
                peers,
                news,
                fundamentals,
                recommendation,
                consensus,
            ) = await asyncio.gather(
                get_earnings_estimates(ticker, quarter, year),
                get_competitors(ticker),
                get_news_sentiment(ticker, company_name),
                get_fundamentals(ticker),
                get_analyst_recommendation(ticker),
                get_consensus_estimates(ticker, company_name, quarter, year),
                return_exceptions=True,
            )
        except Exception:
            (
                estimates,
                peers,
                news,
                fundamentals,
                recommendation,
                consensus,
            ) = ({}, {}, {}, {}, {}, {})

        if not isinstance(estimates, dict):
            estimates = {}
        if not isinstance(peers, dict):
            peers = {}
        if not isinstance(news, dict):
            news = {}
        if not isinstance(fundamentals, dict):
            fundamentals = {}
        if isinstance(recommendation, Exception):
            recommendation = {"error": f"get_analyst_recommendation failed: {recommendation!s}"}
        elif not isinstance(recommendation, dict):
            recommendation = {}
        if not isinstance(consensus, dict):
            consensus = {}

        # Update sector now that we have it from fundamentals.
        if fundamentals.get("sector"):
            self.state["sector"] = fundamentals["sector"]

        # Seed the baseline sentiment score from analyst recommendations.
        if recommendation.get("baseline_score") is not None:
            self.state["baseline_sentiment"] = recommendation["baseline_score"]
        else:
            self.state["baseline_sentiment"] = 50

        if recommendation.get("error"):
            self.state["preloaded_analyst"] = {}
        else:
            self.state["preloaded_analyst"] = dict(recommendation)

        rec = recommendation
        # Do not treat `{}` from gather failures (no `error` key) as a successful load.
        has_analyst_core = (
            isinstance(rec, dict)
            and not rec.get("error")
            and rec.get("baseline_score") is not None
        )
        if has_analyst_core:
            self.state["analyst_opinion"] = rec
            self.state["analyst_opinion_error"] = None
            await self.broadcast({
                "type": "analyst_opinion",
                "data": {
                    "strong_buy": rec.get("strong_buy"),
                    "buy": rec.get("buy"),
                    "hold": rec.get("hold"),
                    "sell": rec.get("sell"),
                    "strong_sell": rec.get("strong_sell"),
                    "total_analysts": rec.get("total_analysts"),
                    "baseline_score": rec.get("baseline_score"),
                    "label": rec.get("label"),
                    "period": rec.get("period"),
                    "target_mean": rec.get("target_mean"),
                    "target_median": rec.get("target_median"),
                    "target_high": rec.get("target_high"),
                    "target_low": rec.get("target_low"),
                    "target_upside_pct": rec.get("target_upside_pct"),
                    "target_spread_pct": rec.get("target_spread_pct"),
                    "target_count": rec.get("target_count"),
                    "target_last_updated": rec.get("target_last_updated"),
                    "trend_label": rec.get("trend_label"),
                    "trend_1m_delta": rec.get("trend_1m_delta"),
                    "trend_3m_delta": rec.get("trend_3m_delta"),
                    "source": rec.get("source"),  # "finnhub" | "yfinance" | "hybrid" | None
                    "score_block": rec.get("score_block"),
                },
            })
        else:
            self.state["analyst_opinion"] = {}
            if isinstance(rec, dict) and rec.get("error"):
                self.state["analyst_opinion_error"] = str(rec.get("error"))
            elif not isinstance(rec, dict) or not rec:
                self.state["analyst_opinion_error"] = (
                    "No analyst recommendation payload (upstream returned empty or invalid data)."
                )
            else:
                self.state["analyst_opinion_error"] = (
                    "Recommendation response was missing a baseline score."
                )

        # Pre-loaded metrics: consensus revenue + EPS (Finnhub calendar first,
        # then Gemini search), plus margin context on peers — not duplicate cards.
        # MetricsAgent fills revenue_reported / eps_reported / guidance from the call.
        eps_estimate_value = consensus.get("eps_estimate") or estimates.get("estimated_eps")
        revenue_estimate_value = consensus.get("revenue_estimate")
        period_label = consensus.get("consensus_period_label") or consensus.get("source_note", "")
        consensus_src = consensus.get("source") or ""
        qh = estimates.get("quarterly_history") if isinstance(estimates, dict) else []
        prior_eps = prior_quarter_eps_snapshot(qh if isinstance(qh, list) else [])
        self.state["preloaded_estimates"] = {
            "revenue_estimate": revenue_estimate_value,
            "eps_estimate": eps_estimate_value,
            "source_note": consensus.get("source_note", ""),
            "consensus_period_label": period_label,
            "consensus_source": consensus_src,
            "fiscal_quarter_label": consensus.get("fiscal_quarter_label"),
            **prior_eps,
        }
        await self.broadcast({
            "type": "metrics",
            "data": {
                "revenue_reported": None,
                "revenue_estimate": revenue_estimate_value,
                "revenue_prior_year": None,
                "eps_reported": None,
                "eps_estimate": eps_estimate_value,
                "consensus_period_label": period_label,
                "consensus_source": consensus_src,
                "estimate_kind": "consensus_analyst_mean_pre_call",
                "gross_margin": fundamentals.get("gross_margin"),
                "operating_margin": fundamentals.get("operating_margin"),
                "guidance_raised": None,
                "guidance_note": "",
                **prior_eps,
            },
        })
        # Prepend the target company itself to the peer list so the table
        # shows the self row first for easy side-by-side comparison.
        # Parse the already-formatted "71.3%" gross margin back to a
        # number so the CompetitorPanel can format it consistently
        # alongside the peers (which come from Finnhub as raw numbers).
        def _num_from_pct(pct: Any) -> float | None:
            if pct is None:
                return None
            if isinstance(pct, (int, float)):
                return float(pct)
            try:
                return float(str(pct).rstrip("%").strip())
            except ValueError:
                return None

        # Peer table expects pe_ratio / ev_ebitda / revenue_growth. yfinance fundamentals
        # expose forward_pe and pe_ratio; prefer forward P/E for "Fwd P/E" when present.
        peer_rows = peers.get("peers") if isinstance(peers, dict) else None
        peer_rows = peer_rows if isinstance(peer_rows, list) else []
        target_from_peers = next(
            (p for p in peer_rows if isinstance(p, dict) and p.get("is_target") is True),
            None,
        )
        target_row = target_from_peers or {
            "ticker": ticker,
            "name": company_name or fundamentals.get("name", ticker),
            "pe_ratio": fundamentals.get("forward_pe") or fundamentals.get("pe_ratio"),
            "ev_ebitda": fundamentals.get("ev_ebitda"),
            "revenue_growth": fundamentals.get("revenue_growth"),
            "gross_margin": _num_from_pct(fundamentals.get("gross_margin")),
            "operating_margin": _num_from_pct(fundamentals.get("operating_margin")),
            "is_target": True,
        }
        combined_peers = [target_row] + peer_rows
        await self.broadcast({
            "type": "competitors",
            "data": {"peers": combined_peers},
        })
        await self.broadcast({
            "type": "news",
            "data": {
                "articles": news.get("articles", []),
                "overall_sentiment": news.get("overall_sentiment", "neutral"),
                "overall_rationale": news.get("overall_rationale", ""),
                "classification_source": news.get("classification_source", ""),
                "score_block": news.get("score_block") if isinstance(news, dict) else None,
            },
        })
        self.state["preloaded_news"] = {
            "overall_sentiment": news.get("overall_sentiment", "neutral"),
            "overall_rationale": news.get("overall_rationale", ""),
            "score_block": news.get("score_block") if isinstance(news, dict) else None,
        }

        # Prior-call recap removed (Octagon dependency). Live transcript is the
        # only transcript source. Keep empty placeholders so other agents/UI
        # code paths remain stable.
        self.state["prior_call"] = {}
        self.state["prior_call_context_for_sentiment"] = ""
        self.state["prior_call_summary_text"] = ""

        # Seed phrase deques for the live SentimentAgent; the dashboard gauge is
        # updated after one-shot agents finish via a weighted composite (news,
        # prior transcript recap, macro, last EPS surprise, analyst baseline).
        if recommendation.get("baseline_score") is not None:
            rec_label = recommendation.get("label", "neutral")
            buys = recommendation.get("strong_buy", 0) + recommendation.get("buy", 0)
            sells = recommendation.get("sell", 0) + recommendation.get("strong_sell", 0)
            total = recommendation.get("total_analysts", 0) or 1

            baseline_bullish = (
                [f"Analyst consensus: {buys}/{total} buy ({rec_label})"] if buys else []
            )
            baseline_bearish = (
                [f"Analyst consensus: {sells}/{total} sell"] if sells else []
            )
            self.state["baseline_bullish_phrases"] = baseline_bullish
            self.state["baseline_bearish_phrases"] = baseline_bearish
        else:
            self.state["baseline_bullish_phrases"] = []
            self.state["baseline_bearish_phrases"] = []

        # One-shot panels, then composite sentiment + pre-call trade synthesis.
        asyncio.create_task(self._run_analysis_and_coverage_synthesis())

    async def _run_analysis_agents(self) -> None:
        """Run all one-shot analysis agents in parallel after briefing data loads."""
        ctx = self.state
        agents_to_run = [
            MacroAgent(self.broadcast, ctx),
            TechnicalAgent(self.broadcast, ctx),
            PeerAgent(self.broadcast, ctx),
        ]
        await asyncio.gather(
            *[agent.run() for agent in agents_to_run],
            return_exceptions=True,
        )

    async def _run_analysis_and_coverage_synthesis(self) -> None:
        try:
            await self._run_analysis_agents()
            if not self.state.get("ticker"):
                return
            # Pre-call composite builder is retired — the committee now produces the
            # authoritative pre-call score. Preserve baseline_sentiment because
            # SentimentAgent uses it to seed its gauge at session start.
            baseline = float(self.state.get("analyst_opinion", {}).get("baseline_score", 50.0))
            try:
                self.state["baseline_sentiment"] = baseline
            except (TypeError, ValueError):
                self.state["baseline_sentiment"] = 50.0
            # No sentiment broadcast here — the committee trade_signal supersedes it.
            await emit_coverage_trade_signal(
                self.broadcast,
                self.state,
                self.results.as_dict(),
            )
        except Exception as exc:
            await self.broadcast({
                "type": "status",
                "data": {
                    "state": "error",
                    "message": f"coverage trade synthesis failed: {exc}",
                },
            })

    async def start_live(self) -> None:
        """Switch to LIVE mode and spawn the parallel worker agents."""
        async with self.lock:
            if self.state.get("mode") == "LIVE":
                return
            self.state["mode"] = "LIVE"
            if self.transcript_agent is None:
                # Audio socket never opened — fail loudly via status.
                await self.broadcast({
                    "type": "status",
                    "data": {"state": "error", "message": "no transcript agent — open /ws/audio first"},
                })
                return

            ctx = self.state
            self.metrics_agent = MetricsAgent(self.subscribe(), self.broadcast, ctx)
            self.sentiment_agent = SentimentAgent(self.subscribe(), self.broadcast, ctx)
            self.trade_agent = FinalSynthesisAgent(self.broadcast, ctx, self.results.as_dict())
            self.highlight_lexicon_agent = HighlightLexiconAgent(
                self.subscribe(), self.broadcast, ctx
            )

            self.agent_tasks = [
                asyncio.create_task(self.metrics_agent.run(), name="metrics_agent"),
                asyncio.create_task(self.sentiment_agent.run(), name="sentiment_agent"),
                asyncio.create_task(self.trade_agent.run(), name="trade_signal_agent"),
                asyncio.create_task(self.highlight_lexicon_agent.run(), name="highlight_lexicon_agent"),
            ]
            await self.broadcast({
                "type": "phase",
                "data": {"phase": "listening"},
            })
            await self.broadcast({
                "type": "status",
                "data": {"state": "running", "message": "4 parallel agents live", "phase": "listening"},
            })

    async def stop(self) -> None:
        """Cancel all agent tasks and trigger summary generation."""
        async with self.lock:
            if self.price_stream is not None:
                try:
                    await self.price_stream.stop()
                except Exception:
                    pass
                self.price_stream = None
            for ag in (
                self.metrics_agent,
                self.sentiment_agent,
                self.trade_agent,
                self.highlight_lexicon_agent,
            ):
                if ag is not None:
                    try:
                        ag.stop()
                    except Exception:
                        pass
            for t in self.agent_tasks:
                t.cancel()
            for t in self.agent_tasks:
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
            self.agent_tasks = []

            if self.identifier_task:
                self.identifier_task.cancel()
                try:
                    await self.identifier_task
                except (asyncio.CancelledError, Exception):
                    pass
                self.identifier_task = None

            if self.transcript_agent is not None:
                try:
                    await self.transcript_agent.stop()
                except Exception:
                    pass
                self.transcript_agent = None

            if self.transcript_task:
                self.transcript_task.cancel()
                try:
                    await self.transcript_task
                except (asyncio.CancelledError, Exception):
                    pass
                self.transcript_task = None

            if self.fanout_task:
                self.fanout_task.cancel()
                try:
                    await self.fanout_task
                except (asyncio.CancelledError, Exception):
                    pass
                self.fanout_task = None

            self.subscribers.clear()
            self.state["mode"] = "IDLE"
            self.state.update({
                "ticker": None,
                "company_name": None,
                "sector": None,
                "quarter": None,
                "year": None,
                "prior_call": {},
                "prior_call_context_for_sentiment": "",
                "prior_call_summary_text": "",
                "preloaded_analyst": {},
                "analyst_opinion": {},
                "analyst_opinion_error": None,
            })
            await self.broadcast({
                "type": "status",
                "data": {"state": "completed", "message": "session ended"},
            })

    # ----- Summary -----
    async def generate_summary(self) -> dict[str, Any]:
        from gemini_summary import generate_summary as _gen
        record = self._build_session_record()

        # Guard against too-short sessions — return a clean "brief session"
        # summary instead of invoking Gemini on near-empty data.
        transcript_lines = len(record.get("transcript", []))
        metrics_count = len(record.get("metrics_history", []))
        sentiment_count = len(record.get("sentiment_history", []))

        # Minimum transcript requirement: we don't attempt a "call analysis"
        # summary unless we have at least a few actual transcript lines.
        if transcript_lines < 3:
            trade_fallback = record.get("trade_signal") if isinstance(record.get("trade_signal"), dict) else None
            if not isinstance(trade_fallback, dict):
                trade_fallback = {
                    "signal": "HOLD",
                    "confidence": "LOW",
                    "thesis": (
                        "Insufficient call audio was captured to form a live trade view. "
                        "The pre-call committee signal on the Company tab remains the best current read."
                    ),
                    "key_risk": (
                        "Share more audio (full earnings webcast tab) for a committee signal that "
                        "actually reflects management's tone and disclosed numbers."
                    ),
                }
            else:
                # Always downgrade confidence when the live transcript is too short.
                trade_fallback = dict(trade_fallback)
                trade_fallback["confidence"] = "LOW"
            return {
                "company": record.get("company", {}),
                "headline": (
                    f"Brief session — only {transcript_lines} transcript lines "
                    f"were captured. A full earnings call summary needs a longer "
                    f"audio sample."
                ),
                "metrics_recap": [],
                "sentiment_arc": [],
                "competitors_recap": "",
                "news_recap": "",
                "qa_recap": [],
                "trade_signal": trade_fallback,
                "user_qa": [],
                "spoken": (
                    f"The earnings session was brief — about {transcript_lines} lines "
                    f"of transcript were captured. For a meaningful summary, share "
                    f"at least ten minutes of the webcast audio. The pre-call "
                    f"committee signal on the Company tab is the best current view."
                ),
            }

        # If we have a few transcript lines but not much else, still generate a
        # narrative summary — but force LOW confidence so the UI/committee
        # doesn't over-interpret sparse data.
        low_signal = transcript_lines < 10 and metrics_count == 0 and sentiment_count < 2
        out = await _gen(record)
        if isinstance(out, dict) and low_signal:
            ts = out.get("trade_signal")
            if isinstance(ts, dict):
                ts2 = dict(ts)
                ts2["confidence"] = "LOW"
                out["trade_signal"] = ts2
        return out

    def _build_session_record(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "company": {
                "ticker": self.state.get("ticker") or "",
                "name": self.state.get("company_name") or "",
                "sector": self.state.get("sector") or "",
                "quarter": self.state.get("quarter") or "",
                "fiscal_year": self.state.get("year") or "",
                "call_date": "",
            },
            "transcript": [],
            "metrics_history": [],
            "sentiment_history": [],
            "news": None,
            "competitors": None,
            "trade_signal": None,
        }
        for msg in self.history:
            t = msg.get("type")
            d = msg.get("data") or {}
            if t == "transcript":
                record["transcript"].append({"speaker": d.get("speaker"), "text": d.get("text")})
            elif t == "metrics":
                record["metrics_history"].append(d)
            elif t == "sentiment":
                record["sentiment_history"].append(d)
            elif t == "news":
                record["news"] = d
            elif t == "competitors":
                record["competitors"] = d
            elif t == "trade_signal":
                record["trade_signal"] = d
        return record
