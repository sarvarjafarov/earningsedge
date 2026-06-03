# Hackathon Compliance — Google Cloud Rapid Agent

This document maps EarningsEdge against the
[Google Cloud Rapid Agent Hackathon](https://rapid-agent.devpost.com/)
requirements line-by-line, so judges can confirm compliance without
spelunking through the code.

| Track | Partner | Theme |
| --- | --- | --- |
| Partner track | **MongoDB** | **Financial Services** |

---

## Required technologies

| Requirement | Where it lives | Verify |
| --- | --- | --- |
| **Gemini 3** brain | Reasoning: `gemini-3.5-flash` (`backend/orchestrator.py`, `backend/adk_agents/root_agent.py`). Live transcription: `gemini-3.1-flash-live-preview` (`backend/agents/transcript_agent.py`). TTS voice replies: `gemini-2.5-flash-preview-tts` (`backend/agents/chat_agent.py`). | `grep -rn "gemini-3" backend/` |
| **Google Cloud Agent Builder (ADK)** | `backend/adk_agents/root_agent.py` — `LlmAgent(name="earningsedge_chairman")` with 11 tools registered. Exposed at `POST /api/adk/run`. | `curl -X POST http://localhost:8080/api/adk/run -d '{"prompt":"Cover NVDA"}' -H "Content-Type: application/json"` |
| **MongoDB MCP server** (partner track) | `backend/mcp_client.py` — Streamable HTTP transport against `mongodb-mcp-server@latest`. `backend/atlas_writer.py` — durable write queue with exponential backoff. Persists `sessions`, `trades`, `verdicts` collections. | `curl http://localhost:8080/api/mcp/status` |

---

## Repository requirements

| Requirement | Status |
| --- | --- |
| Public repository | ✅ This repo is public on GitHub |
| Open-source license at root, visible in About panel | ✅ MIT — see [LICENSE](../LICENSE) |
| Built within contest window | ✅ First commit June 2026 |

---

## Project requirements

| Requirement | Where |
| --- | --- |
| Hosted, accessible URL | Cloudflare quick tunnel via `scripts/start_demo.sh` — see [TUNNEL.md](TUNNEL.md). Render blueprint at `render.yaml` is the persistent deploy path. |
| Agent uses Google Cloud + MongoDB | `/api/adk/run` calls Gemini 3 via ADK, and tools persist through MongoDB MCP. |
| Multi-step planning, tool use, live services | Agent calls `get_stock_quote → get_fundamentals → get_analyst_consensus → get_peers → get_news_sentiment → draft_paper_trade → remember` in one verdict cycle. |

---

## Judging criteria — where to look

| Criterion | What it tests | Where it lives in EarningsEdge |
| --- | --- | --- |
| **Technological Implementation** | Quality of Google Cloud + partner integration | `backend/adk_agents/` (ADK), `backend/mcp_client.py` + `backend/atlas_writer.py` (MongoDB MCP), `backend/agents/transcript_agent.py` (Gemini Live), `backend/agents/chat_agent.py` (Gemini TTS). Tools are typed, results JSON-clean, failures non-fatal. |
| **Design** | UX is well thought-out | `frontend/` — 2-workspace React 19 app (Company cockpit + Trading panel). Per-tab session isolation (`x-session-id` header), live transcript, voice replies, single-click paper trades, no modal interruptions. |
| **Potential Impact** | How big the affected community is | Retail investors are the target. Every CEO call happens outside US trading hours, and most retail investors form opinions by reading 90-second tweets the next morning. EarningsEdge is the disciplined cockpit that lets them sleep on it, ask follow-ups in their own voice, and execute on a paper book before risking real capital. |
| **Quality of the Idea** | Creativity / uniqueness | Multi-specialist committee with weighted aggregation, hysteresis, and disagreement-aware confidence is genuinely novel for retail tools. Combining live audio transcription, voice Q&A, persistent memory, and paper execution in one cockpit is a combination no other consumer fintech ships today. |

---

## How a judge can verify in 60 seconds

```bash
# 1. Clone, install env
git clone https://github.com/sarvarjafarov/earningsedge.git
cd earningsedge/earningsedge
cp .env.example .env  # paste keys from the hackathon submission

# 2. One command brings up backend + MCP + tunnel
./scripts/start_demo.sh
# → prints PUBLIC URL: https://xxxxx-yyy-zzz.trycloudflare.com

# 3. Open the URL in Chrome
#    - Type NVDA → LOAD COMPANY → coverage populates in ~15 s
#    - Click ▶ Listen live → share a tab playing audio → transcript streams

# 4. Hit the ADK endpoint directly for the Agent Builder demo:
curl -X POST $URL/api/adk/run \
  -H "Content-Type: application/json" \
  -d '{"prompt":"Cover NVDA and draft a paper trade if warranted","ticker":"NVDA"}' | jq .

# 5. Confirm MongoDB writes are flowing
curl $URL/api/mcp/status | jq .
```

---

## Files added for hackathon compliance

| File | Purpose |
| --- | --- |
| `backend/adk_agents/__init__.py` | ADK package init — re-exports `root_agent` |
| `backend/adk_agents/root_agent.py` | The `LlmAgent` that satisfies the Agent Builder requirement |
| `backend/adk_agents/tools.py` | 11 tools the agent uses (market data + paper trading + Mongo memory) |
| `backend/mcp_client.py` | MongoDB MCP client with envelope handling + pymongo fallback |
| `backend/atlas_writer.py` | Durable write queue (exponential backoff) so a flaky Atlas free tier never blocks the user's hot path |
| `scripts/start_demo.sh` | One-command build + MCP server + uvicorn + Cloudflare tunnel |
| `docs/HACKATHON.md` | This file |
| `docs/TUNNEL.md` | Tunnel troubleshooting |
| `LICENSE` | MIT |

Nothing in the existing product code path was modified beyond the two
hook points in `main.py` (`/api/coverage` and `/api/order`) that persist
to MongoDB without blocking the response. The user-facing UI is
unchanged.
