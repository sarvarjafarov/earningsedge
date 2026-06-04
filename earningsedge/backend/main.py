"""FastAPI app for EarningsEdge multi-agent backend.

Endpoints:
  GET  /health          — liveness for probes and load balancers
  POST /api/coverage    — resolve ticker / preload company (same briefing path as /api/briefing)
  POST /api/briefing    — pre-loads data for {company_name, ticker, quarter, year}
  POST /api/start-live  — switches to LIVE mode and spawns parallel agents
  POST /api/stop        — cancels everything
  POST /api/pause       — pause live agents (transcript continues)
  POST /api/resume      — resume paused agents
  POST /api/ask         — ChatAgent side-channel Q&A
  POST /api/summarize   — generates the post-call analyst report
  GET  /api/telegram/status — whether Telegram short-notify is configured
  POST /api/telegram/notify — send “summary ready on EarningsEdge” ping to team chat
  POST /api/audio       — alternative: accepts a single PCM chunk via HTTP
  GET  /api/status      — current session state
  GET  /api/account     — Alpaca paper account (optional)
  GET  /api/positions   — Alpaca positions
  GET  /api/orders      — Alpaca recent orders
  POST /api/order       — Alpaca place order
  WS   /ws              — outbound dashboard updates
  WS   /ws/audio        — inbound binary PCM frames + control text frames
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import FileResponse
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from orchestrator import Orchestrator
from telegram_notify import (
    build_summary_available_message,
    send_telegram_text,
    telegram_notify_configured,
)
from tools import resolve_coverage_inputs
from trade_executor import TradeExecutor

# Hackathon-required integrations: Google Agent Builder + MongoDB MCP.
# Both layers are additive — the legacy /api/coverage path is unchanged.
from atlas_writer import durable_write, writer as _atlas_writer

# Resolve earningsedge/.env regardless of process cwd (reliable for uvicorn, IDEs).
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

_log = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not os.getenv("GEMINI_API_KEY", "").strip():
        _log.warning(
            "GEMINI_API_KEY is not set or empty. "
            "The /health endpoint will still respond, but /ws/audio and Gemini-backed features will not work until it is set."
        )
    for _env, _hint in (
        ("ALPHA_VANTAGE_API_KEY", "Alpha Vantage-backed tools (quotes, earnings, etc.) may fail."),
        ("FMP_API_KEY", "Financial Modeling Prep tools may fail."),
        ("FINNHUB_API_KEY", "Finnhub-backed news/tools may fail."),
    ):
        if not os.getenv(_env, "").strip():
            _log.warning("%s is not set or empty. %s", _env, _hint)
    # Start the durable MongoDB-MCP write queue. No-op if MCP / Atlas are
    # unreachable — writes silently buffer and replay when the cluster recovers.
    await _atlas_writer.start()
    # Warm the in-memory verdict corpus so memory citations work even when
    # Atlas is unreachable. Cheap (single JSON read + lazy embed re-fill).
    async def _warm_corpus() -> None:
        try:
            from verdict_corpus import warm_corpus
            await warm_corpus()
        except Exception as exc:  # noqa: BLE001
            _log.warning("corpus warm-up failed: %s", exc)
    asyncio.create_task(_warm_corpus())
    # Warm the pymongo client in the background so the first user request
    # doesn't pay the Atlas SSL handshake cost. Non-blocking — if the
    # warmup fails we still boot.
    async def _warm_atlas() -> None:
        if not os.getenv("MONGODB_URI", "").strip():
            return
        try:
            from mcp_client import mcp_call
            await asyncio.wait_for(
                mcp_call("find", {
                    "database": os.getenv("MONGODB_DB", "earningsedge"),
                    "collection": "verdicts",
                    "filter": {},
                    "limit": 1,
                }),
                timeout=20.0,
            )
            _log.info("atlas client warmed")
        except Exception as exc:  # noqa: BLE001
            _log.warning("atlas warm-up failed (non-fatal): %s", exc)
    asyncio.create_task(_warm_atlas())
    try:
        yield
    finally:
        await _atlas_writer.stop()


app = FastAPI(title="EarningsEdge Multi-Agent Backend", lifespan=lifespan)

# Defaults cover localhost + 127.0.0.1 and :3001 when :3000 is taken (Docker/WSL, other apps).
_DEV_DEFAULTS = {
    "http://localhost:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:3001",
}
_origins_raw = os.getenv("ALLOWED_ORIGINS", "").strip()
_env_origins = {o.strip() for o in _origins_raw.split(",") if o.strip()}
# Always merge env-provided origins with the dev defaults so a partial .env
# (e.g. only `localhost:3001`) never blocks `127.0.0.1:3001` and vice versa.
_allow_origins = sorted(_env_origins | _DEV_DEFAULTS)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    """Lightweight liveness check (does not validate external APIs or Gemini)."""
    return {"status": "ok"}


orchestrator = Orchestrator()
# Each connected dashboard tab is mapped to its own session_id. Broadcasts
# carry a session_id and are only delivered to clients whose session matches
# (or to all clients when the message has no session_id, e.g. global status).
# This stops cross-tab and cross-user state leaks: User A's `/api/coverage
# {ticker:"NVDA"}` no longer flips User B's loaded ticker to NVDA mid-session.
_dashboard_clients: dict[WebSocket, str | None] = {}
_history: list[dict[str, Any]] = []
_lock = asyncio.Lock()
_executor = TradeExecutor()


async def broadcast(message: dict[str, Any]) -> None:
    """Push a message to dashboard clients matching the message's session_id.

    If the message has no `session_id` field, it's treated as global and
    delivered to every connected client (used for status pings, agent-speech
    audio that's identity-less, etc.). If it has a `session_id`, only clients
    that registered with the same session_id receive it.
    """
    _history.append(message)
    if len(_history) > 1000:
        del _history[:-1000]
    target_session = message.get("session_id")
    payload = json.dumps(message)
    dead: list[WebSocket] = []
    for ws, client_session in list(_dashboard_clients.items()):
        if target_session is not None and client_session != target_session:
            continue
        try:
            await ws.send_text(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _dashboard_clients.pop(ws, None)


orchestrator.set_broadcast(broadcast)
orchestrator.history = _history  # share for summary generation


async def _apply_company_coverage(body: dict[str, Any], session_id: str | None = None) -> dict[str, Any]:
    """Load fundamentals/news/peers/metrics for a ticker — no audio session."""
    quarter = body.get("quarter")
    year = body.get("year")
    resolved = await resolve_coverage_inputs(body.get("ticker"), body.get("company_name"))
    if "error" in resolved:
        return {"ok": False, "error": resolved["error"]}
    ticker = resolved["ticker"]
    company_name = resolved["company_name"]
    # Tag this orchestrator turn with the caller's session so every event the
    # orchestrator broadcasts during this briefing only reaches the originating
    # tab. Other tabs/users keep whatever state they had.
    orchestrator.set_session_id(session_id)
    await orchestrator.start_briefing(ticker, company_name, quarter, year)
    s = orchestrator.state
    ao = s.get("analyst_opinion")
    if not isinstance(ao, dict):
        ao = {}
    err = s.get("analyst_opinion_error")
    return {
        "ok": True,
        "company": {
            "ticker": s.get("ticker"),
            "company_name": s.get("company_name"),
            "sector": s.get("sector"),
            "quarter": s.get("quarter"),
            "fiscal_year": s.get("year"),
        },
        # Same payload as WS `analyst_opinion` — HTTP clients often miss the broadcast
        # (WS connects after coverage, or history is not replayed when no live session).
        "analyst_opinion": ao,
        "analyst_opinion_error": err if isinstance(err, str) and err.strip() else None,
    }


class CoverageRequest(BaseModel):
    """Body for /api/coverage and /api/briefing.

    All fields optional — the resolver is permissive: ticker OR company_name
    is enough to identify a company. Quarter and year are advisory metadata
    we record but don't use for the resolution itself. Using a Pydantic
    model means FastAPI returns 422 (not 500) when the body is missing /
    malformed, instead of an unhandled exception inside `req.json()`.
    """
    ticker: str | None = None
    company_name: str | None = None
    quarter: str | None = None
    year: str | int | None = None


@app.post("/api/coverage")
async def set_coverage(request: Request, body: CoverageRequest) -> dict[str, Any]:
    """Pre-populate the dashboard (which company we cover today) without opening a mic or tab."""
    session_id = request.headers.get("x-session-id") or None
    result = await _apply_company_coverage(body.model_dump(), session_id=session_id)
    # Persist a session document to MongoDB Atlas via MCP — non-blocking,
    # never raises (durable_write absorbs failures into the retry queue).
    if result.get("ok"):
        company = result.get("company") or {}
        asyncio.create_task(durable_write("insert-many", {
            "database": os.getenv("MONGODB_DB", "earningsedge"),
            "collection": "sessions",
            "documents": [{
                "session_id": session_id,
                "ticker": company.get("ticker"),
                "company_name": company.get("company_name"),
                "sector": company.get("sector"),
                "kind": "coverage",
                "ts": int(time.time() * 1000),
            }],
        }))
    return result


@app.post("/api/briefing")
async def briefing(request: Request, body: CoverageRequest) -> dict[str, Any]:
    """Same as /api/coverage — kept for older clients."""
    session_id = request.headers.get("x-session-id") or None
    return await _apply_company_coverage(body.model_dump(), session_id=session_id)


@app.post("/api/start-live")
async def start_live() -> dict[str, Any]:
    if orchestrator.transcript_agent is None:
        return {"ok": False, "error": "no active session — open /ws/audio first"}
    await orchestrator.start_live()
    return {"ok": True}


@app.post("/api/stop")
async def stop_session() -> dict[str, Any]:
    if not orchestrator.is_running():
        _history.clear()
        return {"ok": False, "error": "no session running"}
    await orchestrator.stop()
    _history.clear()
    return {"ok": True}


@app.post("/api/pause")
async def pause_session() -> dict[str, Any]:
    if not orchestrator.is_running():
        return {"ok": False, "error": "no session running"}
    await orchestrator.set_paused(True)
    return {"ok": True, "paused": True}


@app.post("/api/resume")
async def resume_session() -> dict[str, Any]:
    if not orchestrator.is_running():
        return {"ok": False, "error": "no session running"}
    await orchestrator.set_paused(False)
    return {"ok": True, "paused": False}


@app.post("/api/ask")
async def ask_agent(req: Request) -> dict[str, Any]:
    body = await req.json()
    question = body.get("question", "")
    if not isinstance(question, str) or not question.strip():
        return {"ok": False, "error": "empty question"}
    try:
        answer = await orchestrator.ask_agent(question)
    except Exception as exc:
        return {"ok": False, "error": f"ask failed: {exc}"}
    return {"ok": True, "answer": answer}


@app.post("/api/summarize")
async def summarize_session() -> dict[str, Any]:
    if not _history:
        return {"ok": False, "error": "no session data to summarize"}
    try:
        summary = await orchestrator.generate_summary()
    except Exception as exc:
        return {"ok": False, "error": f"summary generation failed: {exc}"}
    await broadcast({"type": "summary", "data": summary})
    return {"ok": True}


@app.get("/api/telegram/status")
async def telegram_status() -> dict[str, Any]:
    """True when TELEGRAM_BOT_TOKEN + TELEGRAM_NOTIFY_CHAT_ID are set (no secrets returned)."""
    return {"ok": True, "notify_available": telegram_notify_configured()}


@app.post("/api/telegram/notify")
async def telegram_notify_summary_available(req: Request) -> dict[str, Any]:
    """Send a short team-chat ping; full summary stays on EarningsEdge (or PDF), not Telegram."""
    if not telegram_notify_configured():
        return {
            "ok": False,
            "error": "Telegram is not configured. Set TELEGRAM_BOT_TOKEN and "
            "TELEGRAM_NOTIFY_CHAT_ID in earningsedge/.env (see .env.example).",
        }
    try:
        body = await req.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    raw_ticker = body.get("ticker")
    raw_name = body.get("company_name")
    ticker = (raw_ticker.strip().upper() if isinstance(raw_ticker, str) else None) or None
    company_name = (raw_name.strip() if isinstance(raw_name, str) else None) or None
    if not ticker and not company_name:
        return {"ok": False, "error": "Send at least ticker or company_name"}
    text = build_summary_available_message(ticker=ticker, company_name=company_name)
    try:
        await send_telegram_text(text)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True}


@app.post("/api/audio")
async def post_audio(req: Request) -> dict[str, Any]:
    """Alternative HTTP path for pushing a single PCM chunk into the
    audio queue. The frontend uses /ws/audio for streaming — this endpoint
    is here for parity with the spec and for ad-hoc testing."""
    body = await req.body()
    if not body:
        return {"ok": False, "error": "empty body"}
    await orchestrator.feed_audio(body)
    return {"ok": True, "bytes": len(body)}


@app.get("/api/status")
async def get_status() -> dict[str, Any]:
    s = orchestrator.state
    return {
        "running": orchestrator.is_running(),
        "mode": s.get("mode"),
        "ticker": s.get("ticker"),
        "company_name": s.get("company_name"),
        "sector": s.get("sector"),
        "quarter": s.get("quarter"),
        "year": s.get("year"),
    }


@app.get("/api/account")
async def get_account() -> dict[str, Any]:
    """Alpaca paper account snapshot (simulated)."""
    return _executor.get_account()


@app.get("/api/pl_analytics")
async def get_pl_analytics() -> dict[str, Any]:
    """Account-level P&L summary (day P&L + total unrealized)."""
    return _executor.get_pl_analytics()


@app.get("/api/positions")
async def get_positions() -> list[dict[str, Any]]:
    """Alpaca paper positions snapshot (simulated)."""
    return _executor.get_positions()


@app.get("/api/orders")
async def get_orders() -> dict[str, Any]:
    """Recent Alpaca paper orders (newest first)."""
    return _executor.get_orders(limit=50)


@app.post("/api/order")
async def place_order(req: Request) -> dict[str, Any]:
    """Submit a market or limit order to Alpaca paper trading.

    Expects: {ticker, side, qty, limit_price (optional)}
    NOTE: UI must provide explicit confirmation before calling this endpoint.
    """
    body = await req.json()
    ticker = body.get("ticker", "")
    side = body.get("side", "buy")
    qty_raw = body.get("qty", 0)
    limit_price = body.get("limit_price")

    try:
        qty = int(qty_raw)
    except (TypeError, ValueError):
        qty = 0

    if not ticker or qty <= 0:
        return {"error": "ticker and qty required"}

    limit_price_f: float | None = None
    if limit_price is not None:
        try:
            limit_price_f = float(limit_price)
        except (TypeError, ValueError):
            limit_price_f = None

    order_result = _executor.submit_order(ticker=ticker, side=side, qty=qty, limit_price=limit_price_f)
    # Persist the trade to MongoDB Atlas via MCP — fire and forget.
    asyncio.create_task(durable_write("insert-many", {
        "database": os.getenv("MONGODB_DB", "earningsedge"),
        "collection": "trades",
        "documents": [{
            "ticker": ticker,
            "side": side,
            "qty": qty,
            "limit_price": limit_price_f,
            "result": order_result if isinstance(order_result, dict) else {"raw": str(order_result)},
            "ts": int(time.time() * 1000),
        }],
    }))
    return order_result


# ---------------------------------------------------------------------------
# Google Cloud Agent Builder (ADK) — hackathon-required entry point.
# The legacy /api/coverage path above stays unchanged; this endpoint runs
# the same set of tools under an LlmAgent so judges (and `adk run`) can
# verify the Agent-Builder integration directly.
# ---------------------------------------------------------------------------


class ADKRunRequest(BaseModel):
    prompt: str
    ticker: str | None = None
    user_id: str = "demo-user"
    session_id: str | None = None


@app.post("/api/adk/run")
async def adk_run(body: ADKRunRequest):
    """Run the EarningsEdge Analyst Chairman as a streaming response.

    Returns Server-Sent Events. Each event is one JSON-encoded payload.
    Heroku's 30s router timeout requires us to emit something *within*
    30 seconds; streaming the tool-call trace as it happens lets the
    full agent reasoning take 60+ seconds without H12 errors.

    Event types:
      - {"type": "start", "agent": ...}
      - {"type": "tool_call", "name": ..., "args": {...}}
      - {"type": "final", "response": "...", "model": ...}
      - {"type": "error", "error": "..."}
    """
    from starlette.responses import StreamingResponse

    try:
        from google.adk.runners import InMemoryRunner
        from google.genai import types as genai_types

        from adk_agents import root_agent
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"ADK not installed: {exc}"}

    if not body.prompt.strip():
        return {"ok": False, "error": "empty prompt"}

    prompt_text = body.prompt
    if body.ticker:
        prompt_text = f"[ticker={body.ticker.upper()}] {prompt_text}"

    async def event_stream():
        def sse(payload: dict[str, Any]) -> str:
            return f"data: {json.dumps(payload)}\n\n"

        # Heartbeat so the router sees bytes within its window.
        yield sse({"type": "start", "agent": "earningsedge_chairman",
                  "model": os.getenv("GEMINI_MODEL", "gemini-3.5-flash")})

        try:
            runner = InMemoryRunner(agent=root_agent, app_name="earningsedge_chairman")
            try:
                session = await runner.session_service.create_session(
                    app_name="earningsedge_chairman",
                    user_id=body.user_id,
                    session_id=body.session_id,
                )
            except TypeError:
                session = await runner.session_service.create_session(
                    app_name="earningsedge_chairman",
                    user_id=body.user_id,
                )

            content = genai_types.Content(role="user", parts=[genai_types.Part(text=prompt_text)])

            final_text: str | None = None
            async for event in runner.run_async(
                user_id=body.user_id,
                session_id=session.id,
                new_message=content,
            ):
                for part in (event.content.parts or []) if event.content else []:
                    if getattr(part, "function_call", None):
                        fc = part.function_call
                        yield sse({
                            "type": "tool_call",
                            "name": fc.name,
                            "args": dict(fc.args) if fc.args else {},
                        })
                    if getattr(part, "text", None) and event.is_final_response():
                        final_text = part.text

            yield sse({"type": "final", "response": final_text or ""})
        except Exception as exc:  # noqa: BLE001
            yield sse({"type": "error", "error": f"adk run failed: {exc}"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering if applicable
        },
    )


@app.post("/api/vector/ensure_index")
async def vector_ensure_index() -> dict[str, Any]:
    """Idempotent — create the Atlas Vector Search index for verdicts.

    Run once after adding the MongoDB URI. The index builds in ~30 s on
    Atlas free tier. Safe to call repeatedly: returns ``already_exists``
    on the no-op path.
    """
    from vector_memory import ensure_index
    return await ensure_index()


@app.post("/api/vector/search")
async def vector_search(body: dict[str, Any]) -> dict[str, Any]:
    """Direct passthrough for the find_similar_past_verdict tool.

    Useful for the UI's 'similar past verdicts' panel and for letting a
    judge confirm the Vector Search path is wired without going through
    Gemini.
    """
    from vector_memory import find_similar_verdicts
    query = (body.get("query") or "").strip()
    ticker = body.get("ticker") or None
    k = int(body.get("k", 5))
    if not query:
        return {"ok": False, "error": "query required"}
    rows = await find_similar_verdicts(query, ticker=ticker, k=k)
    return {"ok": True, "matches": rows}


@app.get("/api/mcp/status")
async def mcp_status() -> dict[str, Any]:
    """Diagnostics for the MongoDB MCP partner-track integration.

    Surfaces everything a judge needs to verify the partner integration
    is live: whether the Atlas URI is configured, whether the MCP server
    URL is reachable, the durable-writer queue, and a probe round-trip.
    """
    from urllib.parse import urlparse
    mongodb_uri = (os.getenv("MONGODB_URI") or os.getenv("MDB_MCP_CONNECTION_STRING") or "").strip()
    mcp_url = (os.getenv("MONGODB_MCP_URL") or "").strip()
    mongodb_db = os.getenv("MONGODB_DB", "earningsedge")

    masked_uri = ""
    if mongodb_uri:
        parsed = urlparse(mongodb_uri)
        host = parsed.hostname or ""
        masked_uri = f"{parsed.scheme}://...@{host}/" if host else "<set>"

    mcp_reachable = False
    if mcp_url:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=2.0) as client:
                r = await client.get(mcp_url.rstrip("/") + "/health")
                mcp_reachable = r.status_code < 500
        except Exception:
            mcp_reachable = False

    probe_ok = False
    probe_error: str | None = None
    if mongodb_uri:
        try:
            from mcp_client import mcp_call
            # Probe by counting docs in our own collection — works on Atlas
            # free tier (no admin privileges needed) and exercises both
            # the auth path and a real read.
            await asyncio.wait_for(
                mcp_call("find", {
                    "database": mongodb_db,
                    "collection": "verdicts",
                    "filter": {},
                    "limit": 1,
                }),
                timeout=10.0,
            )
            probe_ok = True
        except asyncio.TimeoutError:
            probe_error = "probe timed out (Atlas slow from this region)"
        except Exception as exc:  # noqa: BLE001
            probe_error = f"{type(exc).__name__}: {exc}"

    return {
        "ok": True,
        "config": {
            "mongodb_uri": masked_uri or "<unset>",
            "mongodb_db": mongodb_db,
            "mongodb_mcp_url": mcp_url or "<unset>",
        },
        "mcp_server_reachable": mcp_reachable,
        "round_trip_probe": {"ok": probe_ok, "error": probe_error},
        "atlas_writer": _atlas_writer.stats(),
    }


@app.get("/api/gemini/health")
async def gemini_health() -> dict[str, Any]:
    """Probe whether Gemini Live (the live-audio path) is available.

    Returns ``{available, error}``. The UI uses this to disable the
    'Listen live' button gracefully when the key is missing, the quota
    is exhausted (1011), or the project is dunning-blocked (1008) —
    instead of letting the user click and watch nothing happen.
    """
    if not os.getenv("GEMINI_API_KEY", "").strip():
        return {"available": False, "error": "GEMINI_API_KEY is not set"}
    try:
        from google import genai
        from google.genai import types as genai_types

        live_model = os.getenv("GEMINI_LIVE_MODEL", "gemini-3.1-flash-live-preview")
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        cfg = genai_types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            input_audio_transcription=genai_types.AudioTranscriptionConfig(),
        )
        async with client.aio.live.connect(model=live_model, config=cfg):
            return {"available": True, "model": live_model}
    except Exception as exc:  # noqa: BLE001
        msg = f"{type(exc).__name__}: {exc}"
        return {"available": False, "error": msg[:240]}


@app.websocket("/ws")
async def dashboard_ws(ws: WebSocket) -> None:
    """Outbound dashboard event stream (no history replay).

    The backend has a single global orchestrator + a single global event
    history. Replaying that history to new clients leaks one user's session
    state to every other tab in the world: a fresh incognito visit would
    receive `company_identified: NVDA` (or whatever the last user loaded)
    and jump past the empty hero into someone else's loaded dashboard.

    So we deliberately do NOT replay `_history` on connect. Each new tab
    starts fresh and only receives events that broadcast AFTER it connected.

    Trade-off: a tab that refreshes mid-call won't see the transcript
    backlog (only new lines from that point on). Acceptable for our
    single-tenant deploy; multi-tenant isolation would need a real
    rearchitecture (per-session orchestrators, session ids, auth).
    """
    await ws.accept()
    # Each tab passes ?session_id=<uuid> so we can route broadcasts to the
    # right client. Without this, all tabs share state via global broadcast.
    session_id = ws.query_params.get("session_id") or None
    _dashboard_clients[ws] = session_id
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        _dashboard_clients.pop(ws, None)


@app.websocket("/ws/audio")
async def audio_ws(ws: WebSocket) -> None:
    """Inbound PCM frames + control text frames.

    Binary: 16 kHz mono 16-bit PCM, ~100 ms each.
    Text:   {"control": "phase", "phase": "briefing"|"listening"}
            {"control": "source", "source": "mic"|"tab"}
            {"control": "briefing_done"}  — user finished speaking (push-to-talk)
            {"control": "end"}
    """
    await ws.accept()
    # Tag the orchestrator so transcript / transcript_partial / agent_audio
    # broadcasts during this live call reach only the originating tab.
    session_id = ws.query_params.get("session_id") or None
    orchestrator.set_session_id(session_id)
    if orchestrator.transcript_agent is not None:
        await ws.close(code=4009, reason="another capture session is already active")
        return

    if not os.getenv("GEMINI_API_KEY", "").strip():
        await broadcast({
            "type": "status",
            "data": {
                "state": "error",
                "message": (
                    "Server misconfiguration: GEMINI_API_KEY is not set. "
                    "Set it in the process environment or in `.env` for local Docker."
                ),
            },
        })
        await ws.close(code=1011, reason="GEMINI_API_KEY not configured")
        return

    _history.clear()
    try:
        await orchestrator.open_session()
    except Exception as exc:
        await broadcast({
            "type": "status",
            "data": {"state": "error", "message": f"failed to open session: {exc}"},
        })
        await ws.close(code=1011, reason=f"open failed: {exc}")
        return

    try:
        while True:
            msg = await ws.receive()
            mtype = msg.get("type")
            if mtype == "websocket.disconnect":
                break
            data_bytes = msg.get("bytes")
            data_text = msg.get("text")
            if data_bytes:
                await orchestrator.feed_audio(data_bytes)
            elif data_text is not None:
                if await _handle_control(data_text):
                    break
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        await broadcast({
            "type": "status",
            "data": {"state": "error", "message": f"audio ws error: {exc}"},
        })
    finally:
        try:
            if getattr(orchestrator, "_preserve_company_on_audio_close", False):
                orchestrator._preserve_company_on_audio_close = False
                await orchestrator.end_audio_preserve_company()
            else:
                await orchestrator.stop()
        except Exception:
            pass
        try:
            await ws.close()
        except Exception:
            pass


async def _handle_control(data_text: str) -> bool:
    """Process a control text frame. Return True if the loop should exit."""
    text = data_text.strip()
    if not text:
        return False
    if text.lower() == "end":
        return True
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return False
    if not isinstance(parsed, dict):
        return False
    control = parsed.get("control")
    if control == "end":
        return True
    if control == "briefing_done":
        await orchestrator.briefing_user_finished()
        return False
    if control == "phase":
        phase = parsed.get("phase")
        if isinstance(phase, str) and phase.lower() == "listening":
            await orchestrator.start_live()
        # briefing phase is the default — no-op
    elif control == "source":
        # TranscriptAgent doesn't currently use source labeling beyond
        # mode (BRIEFING vs LIVE). Source switches in LIVE mode label
        # everything as CALL. Track for future granularity.
        pass
    return False


_static = Path(__file__).resolve().parent / "static"


class SPAStaticFiles(StaticFiles):
    """StaticFiles that falls back to index.html on 404.

    Plain `StaticFiles(html=True)` only serves `index.html` for the literal
    `/` request — every other path that isn't a real file 404s. That breaks
    every client-side React Router route (e.g., `/app`): a fresh visit or
    refresh returns FastAPI's `{"detail":"Not Found"}` JSON instead of the
    SPA shell. This subclass returns `index.html` for any non-asset miss so
    React Router can take over on the client.

    Asset misses (anything under `/static/`, plus typical web manifests)
    still return a real 404 — we don't want to mask broken bundle paths
    by serving the SPA shell.
    """

    _ASSET_PREFIXES = ("static/", "assets/")
    _ASSET_FILES = {
        "favicon.ico",
        "favicon-16x16.png",
        "favicon-32x32.png",
        "robots.txt",
        "manifest.json",
        "sitemap.xml",
    }

    async def get_response(self, path: str, scope: Scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
            normalized = path.lstrip("/")
            if normalized.startswith(self._ASSET_PREFIXES):
                raise
            if normalized in self._ASSET_FILES:
                raise
            index = Path(self.directory) / "index.html"
            if index.is_file():
                return FileResponse(index)
            raise


if _static.is_dir():
    app.mount("/", SPAStaticFiles(directory=str(_static), html=True), name="static")
