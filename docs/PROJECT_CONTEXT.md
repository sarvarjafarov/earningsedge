# EarningsEdge — project context (handoff)

**Purpose:** Single source of truth for **new chats** and teammates. Update after major decisions.

---

## Vision (north star)

- **Universal company workspace:** pull **financial + operational** context for **any** supported company (expand coverage over time: filings, KPIs, supply chain, segment data, etc.).
- **Decision support:** surface **buy / sell / hold**-style outputs from modeled signals, with **risk, uncertainty, and “not financial advice”** surfaced in the product — not autonomous trading.
- **Monetization (product direction):** value is in **timely, integrated analysis** (subscriptions, pro tiers, API, institutional seats) — implementation is business/legal scope outside this file.

---

## What exists today (snapshot)

| Layer | Stack |
|-------|--------|
| Backend | FastAPI, uvicorn, WebSockets, multi-agent orchestration, Gemini (Live + Flash), Alpha Vantage / FMP / Finnhub via `tools.py` |
| Frontend | React (CRA), dashboard, company coverage form, tab audio capture, transcript highlighting (static + `HighlightLexiconAgent`), chat (`ChatAgent`) |
| Deploy | Docker (local) |

---

## Tools inventory (what we “have under hand”)

### A — Implemented in the app (`earningsedge/backend/tools.py`)

These run in-process; env keys in `earningsedge/.env` (`ALPHA_VANTAGE_API_KEY`, `FMP_API_KEY`, `FINNHUB_API_KEY`, `GEMINI_API_KEY`).

| Function | Sources / behavior |
|----------|-------------------|
| `get_stock_data` | Alpha Vantage `GLOBAL_QUOTE` (price, change, volume) |
| `get_fundamentals` | Finnhub profile + metrics (margins, P/E, EV/EBITDA, beta, 52w range, growth) |
| `get_analyst_recommendation` | Finnhub recommendation counts → 0–100 baseline sentiment |
| `get_earnings_estimates` | Alpha Vantage `EARNINGS` (quarterly reported vs estimated EPS, surprise) |
| `get_competitors` | FMP stock peers + Finnhub metrics per peer (fallback ticker map if FMP empty) |
| `get_news_sentiment` | Finnhub company news (headlines; sentiment left for agents to infer) |
| `get_consensus_estimates` | Gemini 2.5 Flash + Google Search grounding (revenue + EPS consensus for named quarter) |
| `web_search` | Gemini + Google Search grounding (general queries) |

Session-scoped caching via `reset_cache()` at session start.

### B — Research catalog only (not wired into the Python app)

**[mcp-research.json](../mcp-research.json)** (repo root) is a **shortlist + avoid list + per-server notes** for optional **Cursor MCP** servers and **Claude skills** (install separately). It does **not** add runtime capabilities until you configure MCP in Cursor or integrate APIs into `tools.py`.

**Ranked MCP shortlist (from that file):** octagon-mcp, sec-edgar-mcp, financial-datasets-mcp, alpha-vantage-mcp, finance-tools-mcp, fmp-mcp, fred-mcp-server, yfmcp, polygon-mcp-server, finnhub-mcp.

**Capability themes (high level):**

- **Filings & narrative:** SEC EDGAR (CIK, filings, XBRL, insider forms), Octagon (10-K/Q/8-K, transcripts, 13F, broad coverage).
- **Statements & prices:** Financial Datasets, Alpha Vantage MCP, FMP MCP, Yahoo/yfinance MCP, Polygon (bars, quotes).
- **Macro:** FRED (800k+ series), finance-tools-mcp (FRED + Fear & Greed + CNBC).
- **Avoid:** Documented in `avoid_list` in `mcp-research.json` (deprecated Quandl wrappers, broken FRED fork, Bloomberg terminal dependency, stale SSE-only servers, fake Polygon repo).

**Skills (external):** Anthropic financial modeling / statement analysis cookbooks, enterprise financial plugins, SkillsMP, Market Expert, scientific/quant skills, MCPBundles FMP transcripts — all **optional** installs, not repo dependencies.

**Template for new research:** [docs/mcp-research.template.json](./mcp-research.template.json).

---

## User flows (current)

1. **Load company** — User enters ticker + name (+ optional quarter/year) → `POST /api/coverage` → dashboard tiles populate (no mic).
2. **Earnings call** — User shares browser tab with audio → live transcript + parallel agents (metrics, sentiment, fact-check, QA, trade signal, highlight lexicon).
3. **Ask analyst** — `POST /api/ask` side-channel Q&A over session context.
4. **Voice identify (optional)** — Legacy mic path to identify company from speech.

---

## Architecture bullets

- **TranscriptAgent** owns Gemini Live audio; other agents consume **transcript fan-out** queues after `start_live`.
- **ChatAgent** is HTTP-only; does not share the Live websocket loop.
- **Orchestrator** `open_session` **preserves** ticker/company when reconnecting audio if coverage was already loaded via API.
- **`stop()`** clears company fields on the orchestrator for a clean next session.

---

## Decisions (log)

| Date | Decision |
|------|----------|
| 2026-Q2 | Dashboard is default home; company via form + `/api/coverage`; earnings call is separate from coverage preload. |
| 2026-Q2 | Transcript recv loop: do not reconnect Live socket after every model turn (see `transcript_agent.py`). |
| 2026-Q2 | Chat answers are **text-only** (no browser TTS). |
| 2026-Q2 | MCP research handoff: `docs/mcp-research.template.json`; filled catalog: root `mcp-research.json`. |

*Append new rows as the product grows.*

---

## Roadmap hooks (not implemented — safe to extend)

- **Nav sections:** Research, Filings, + Sections — placeholders in UI for more modules.
- **MCP / external tools:** See root **`mcp-research.json`** + wire chosen servers in Cursor; promote stable APIs into `tools.py` when product needs them server-side.
- **Broader data types:** SEC narrative, supply chain, credit, macro — new `tools` + agents + panels.
- **Backtesting / paper trading:** separate service; keep compliance boundaries explicit.

---

## For the next assistant

1. Read this file + **`.cursor/rules/earningsedge-always.mdc`**.
2. Touching agents? Read **`docs/AGENTS-AUDIT.md`**.
3. After substantive work, add a **one-line bullet** under **Decisions** or **Changelog** below.

---

## Changelog (short)

| When | Note |
|------|------|
| 2026-04-12 | Added PROJECT_CONTEXT, Cursor rules, AGENTS.md for chat handoff. |
| 2026-04-12 | Documented `mcp-research.json` + split **implemented** `tools.py` vs **MCP/skills catalog**. |
| 2026-04-16 | Added `get_yfinance_snapshot()` (TTL cached) for techs + options-chain metrics. |
| 2026-04-16 | Redesigned peer comparison grid + dense list row styling (Bloomberg-ish). |
| 2026-04-16 | Added README + `.env.example` docs for current APIs, runtime flow, and Alpaca paper trading. |
| 2026-04-26 | Telegram short-notify after summary: `GET /api/telegram/status`, `POST /api/telegram/notify` + SummaryPanel button (Bot API; not full report text). |
