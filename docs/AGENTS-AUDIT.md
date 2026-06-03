# EarningsEdge backend — agent audit

Orchestrator: `earningsedge/backend/orchestrator.py`. Agents that emit dashboard updates use the shared `broadcast` function and participate in session lifecycle where noted.

## Always-on / audio path

| Agent | Module | Role |
|-------|--------|------|
| **TranscriptAgent** | `agents/transcript_agent.py` | Owns Gemini **Live** session; PCM in → `input_transcription` → transcript lines + `transcript_queue` fan-out. |
| **Fan-out** | `orchestrator._fanout_loop` | Copies each transcript sentence to per-agent subscriber queues (not a separate class). |

## Started when `/ws/audio` is open

| Component | Role |
|-----------|------|
| **Identifier loop** | `_identifier_loop` — optional voice briefing text → `start_briefing` (legacy path). |
| **open_session** | Creates transcript agent + queues; preserves ticker/company if already set via `POST /api/coverage`. |

## Started when live phase begins (`start_live`)

These four agents are spawned as parallel asyncio tasks (see `orchestrator.start_live`). The list is exact:

| Agent | Module | Role |
|-------|--------|------|
| **MetricsAgent** | `agents/metrics_agent.py` | Extracts figures from transcript sentences. |
| **SentimentAgent** | `agents/sentiment_agent.py` | Tone / gauge updates. |
| **FinalSynthesisAgent** | `agents/final_synthesis_agent.py` | Final analyst view: intraday signal reconciled with dashboard context. |
| **HighlightLexiconAgent** | `agents/highlight_lexicon_agent.py` | LLM-derived highlight phrases for the transcript UI. |

## Coverage one-shot analysis agents

After `start_briefing` finishes loading fundamentals, peers, news, and related payloads, the orchestrator schedules `_run_analysis_and_coverage_synthesis()` (async task from `start_briefing`). That coroutine:

1. Awaits **`_run_analysis_agents()`** — runs these **one-shot** agents in parallel (`asyncio.gather`):
   - **MacroAgent** (`agents/macro_agent.py`) — FRED macro snapshot + Gemini equity read.
   - **TechnicalAgent** (`agents/technical_agent.py`) — Alpha Vantage indicators + Gemini technical read.
2. Builds a weighted **precall sentiment composite** (`build_precall_composite`) and broadcasts a `sentiment` update.
3. Runs **`emit_coverage_trade_signal`** (from `agents/final_synthesis_agent.py`) for pre-call trade synthesis on the coverage path (not the same long-running task as live `FinalSynthesisAgent.run()`).

`POST /api/coverage` (and alias `POST /api/briefing`) invoke `start_briefing` only — they do **not** start the Live transcript agent or the four live parallel agents above.

## HTTP-only (no Live audio task)

| Agent | Module | Role |
|-------|--------|------|
| **ChatAgent** | `agents/chat_agent.py` | `POST /api/ask` — side-channel Gemini answer using session context + history. |

## Exports

`agents/__init__.py` exports the public agent classes for imports from `orchestrator` and tests.
