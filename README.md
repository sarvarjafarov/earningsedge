# EarningsEdge

> **The disciplined AI cockpit retail investors deserve.**
>
> Pick a ticker. EarningsEdge fans out across fundamentals, peers,
> analyst consensus, news sentiment, macro and technicals, then composes
> a weighted committee verdict in ~15 seconds. Click *Listen live* and
> share any Chrome tab playing audio — earnings webcast, CNBC interview,
> podcast — and the same committee narrates it line-by-line. Ask
> follow-ups in your own voice; the analyst answers in its own voice.
> Approve a paper trade with one tap.

**Built for the [Google Cloud Rapid Agent Hackathon](https://rapid-agent.devpost.com/) — Financial Services theme · MongoDB partner track.**

| Hackathon ingredient | Where it lives |
| --- | --- |
| Gemini 3 brain | `gemini-3.5-flash` (reasoning), `gemini-3.1-flash-live-preview` (transcription), `gemini-2.5-flash-preview-tts` (voice replies) |
| Google Cloud Agent Builder (ADK) | [backend/adk_agents/root_agent.py](earningsedge/backend/adk_agents/root_agent.py) — `LlmAgent` with 11 tools, exposed at `POST /api/adk/run` |
| MongoDB MCP server (partner) | [backend/mcp_client.py](earningsedge/backend/mcp_client.py) + [backend/atlas_writer.py](earningsedge/backend/atlas_writer.py) — Streamable HTTP transport with durable retry queue |
| Hosted demo URL | One command: `./scripts/start_demo.sh` → public `*.trycloudflare.com` URL — see [docs/TUNNEL.md](docs/TUNNEL.md) |
| Compliance writeup | [docs/HACKATHON.md](docs/HACKATHON.md) — judging-criteria map + 60-second verify script |

---

## Setup in 10 minutes

You need **6 API keys** plus a free MongoDB Atlas cluster. Five keys are 1-click signups; the sixth (Gemini Live) needs ~3 minutes of GCP setup — see [Gemini Live access](#gemini-live-access-the-1-trap) below.

### 1. Clone + env file

```bash
git clone https://github.com/sarvarjafarov/earningsedge.git
cd earningsedge/earningsedge
cp .env.example .env
```

Open `earningsedge/.env` in a text editor — you'll paste keys into it as you go.

### 2. Get the API keys

Open each link in a new tab, sign up (all free tiers are fine for the demo), copy the key into the matching slot in `.env`.

| Variable in `.env` | Where to get it | What you do |
| --- | --- | --- |
| `GEMINI_API_KEY` | **See [Gemini Live access](#gemini-live-access-the-1-trap)** ↓ | Don't use the AI Studio key — Live API will be denied. |
| `ALPHA_VANTAGE_API_KEY` | https://www.alphavantage.co/support/#api-key | Email → instant free key. |
| `FINNHUB_API_KEY` | https://finnhub.io/register | Sign up → API key on dashboard. |
| `FMP_API_KEY` | https://site.financialmodelingprep.com/developer/docs/ | "Get my Free API Key" button. |
| `FRED_API_KEY` | https://fred.stlouisfed.org/docs/api/api_key.html | Free St. Louis Fed account → Request API Key. |
| `ALPACA_API_KEY` + `ALPACA_SECRET_KEY` | https://alpaca.markets → Paper Trading → API Keys | "Generate New Key" — paper trading, no real money. Keep `ALPACA_BASE_URL=https://paper-api.alpaca.markets`. |

### 3. Install + start the backend

Requires **Python 3.11+** (3.13 tested).

```bash
cd earningsedge/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

You should see `Application startup complete.` Health check: <http://127.0.0.1:8000/health>

### 4. Install + start the frontend (new terminal)

Requires **Node 18+**.

```bash
cd earningsedge/frontend
npm install
npm start
```

The UI opens at <http://localhost:3000> (or `:3001` if `:3000` is taken). Backend on `:8000` is auto-detected.

### 5. First run — the 30-second smoke test

1. Type `NVDA` (or any ticker) into the form → click **LOAD COMPANY**. Coverage should populate in 10–15 seconds: fundamentals, peers, news, macro, technicals, committee verdict.
2. Open a YouTube earnings replay or any podcast/news clip in **a separate Chrome tab**.
3. Back in EarningsEdge, click **▶ Listen live** → Chrome's share dialog opens.
4. **Pick the Chrome tab** (not Window, not Entire Screen) → **CHECK the "Share tab audio" checkbox** at the bottom. *This is the single most-missed step.*
5. Click **Share**. Within ~2 seconds you should see transcript lines streaming in.

### 6. Optional: voice replies in Ask the Analyst

The chat panel (mic icon) replies with both text *and* spoken audio. No setup needed beyond `GEMINI_API_KEY` — uses `gemini-2.5-flash-preview-tts`.

---

## Gemini Live access (the #1 trap)

The free Gemini API key from [aistudio.google.com](https://aistudio.google.com/apikey) **does not have access to the Live API** that streams transcription. You'll see `1008 access denied` in the backend log.

You need a key from a **billing-enabled GCP project** with the **Generative Language API** enabled. Here's the click-by-click:

1. Go to <https://console.cloud.google.com/> → create or select a project.
2. **Billing** → link a billing account. (Live API has a free tier; you won't be charged for demo usage but billing must be linked.)
3. **APIs & Services → Library** → search for **"Generative Language API"** → **Enable**.
4. **APIs & Services → Credentials** → **+ Create credentials** → **API key**.
5. Click **Restrict key** → under **API restrictions** select **Restrict key** → pick **Generative Language API**. Save.
6. Copy the key. It will look like `AIzaSy…` (or `AQ.Ab8…` if your org policy enforces service-account binding — both work).
7. Paste into `earningsedge/.env` as `GEMINI_API_KEY=…`. Restart the backend (`uvicorn` does **not** auto-reload `.env`).

Verify it works:

```bash
cd earningsedge/backend
.venv/bin/python -c "
import asyncio, os
from dotenv import load_dotenv
load_dotenv('../.env')
from google import genai
from google.genai import types

async def t():
    c = genai.Client(api_key=os.getenv('GEMINI_API_KEY'))
    cfg = types.LiveConnectConfig(response_modalities=['AUDIO'],
        input_audio_transcription=types.AudioTranscriptionConfig())
    async with c.aio.live.connect(model='gemini-3.1-flash-live-preview', config=cfg) as s:
        print('Gemini Live: CONNECTED')
asyncio.run(t())
"
```

`Gemini Live: CONNECTED` → you're good.

---

## What can you stream?

Anything playing in a Chrome tab. The pipeline doesn't care what the audio *is* — it just transcribes and feeds the transcript to the agents. Examples that work well:

- **Live earnings webcast** (the original use case — Cisco IR, Tesla earnings.com, Bloomberg replay)
- **News interview** (CNBC YouTube, Bloomberg TV stream, Yahoo Finance live)
- **Conference talk / fireside chat** (a16z podcast, conference YouTube live, AWS re:Invent stream)
- **Podcast replay** (Acquired, Invest Like the Best — paste an episode in a Chrome tab and Listen live)
- **Pre-recorded analyst day** (paste the YouTube URL in a tab)

The **pre-call coverage** dashboards (peers, macro, fundamentals, analyst consensus, committee verdict) require a ticker — that's why the load-company step is mandatory before Listen live.

---

## Repository layout

| Path | Purpose |
| --- | --- |
| **`earningsedge/`** | Main application — FastAPI backend, React frontend, Dockerfile. |
| **`docs/`** | Project context, agent audit, MCP template. |
| **`mcp-research.json`** | MCP catalog reference. |
| **`AGENTS.md`** | Pointer for AI assistants. |

### Inside `earningsedge/`

```text
earningsedge/
  backend/       FastAPI, agents, tools, trade_executor
    agents/        Per-specialist agents (Macro, Technical, Peer, Sentiment,
                   Transcript, Chat, Committee, FinalSynthesis, ...)
    main.py        HTTP + WebSocket entry points
    orchestrator.py  Briefing flow + per-tab session routing
    tools.py       Market-data adapters (Finnhub, FMP, Alpha Vantage, FRED,
                   yfinance), brand-alias map, ticker resolution
  frontend/      React 19 (Company + Trading workspaces)
  Dockerfile     Single-container build (multi-stage)
  .env.example   Template for environment variables
```

---

## API surface

### REST

| Method | Path | Purpose |
| --- | --- | --- |
| GET  | `/health` | Liveness check |
| POST | `/api/coverage` | Load a ticker — returns analyst opinion + fans out to all agents over WS |
| POST | `/api/briefing` | Same as coverage; legacy alias |
| POST | `/api/ask` | Ask the Analyst — returns text + streams agent_audio |
| POST | `/api/pause` / `/api/resume` / `/api/stop` | Session control |
| GET  | `/api/account` / `/api/positions` / `/api/orders` / `/api/pl_analytics` | Alpaca paper |
| POST | `/api/order` | Submit paper order |
| POST | `/api/telegram/notify` | Optional — short ping to a team group when a summary is ready |
| POST | `/api/adk/run` | **Hackathon entry point** — runs the EarningsEdge Analyst Chairman LlmAgent (Google Cloud Agent Builder / ADK) on a user prompt with 11 tools |
| GET  | `/api/mcp/status` | **Hackathon entry point** — MongoDB MCP durable-writer queue diagnostics |

### WebSocket

| Path | Purpose |
| --- | --- |
| `/ws?session_id=…` | Dashboard event stream (transcript, score blocks, status) |
| `/ws/audio?session_id=…` | Browser → backend PCM audio frames (16 kHz mono Int16) |

Each browser tab uses its own `session_id` (per-tab `sessionStorage`) so two tabs running different tickers stay isolated.

---

## Common gotchas

| Symptom | Fix |
| --- | --- |
| `Failed to fetch` on **LOAD COMPANY** | URL bar must be `localhost:3000`/`3001`, not `127.0.0.1:8000`. The backend on `:8000` has no homepage in dev mode. |
| Transcript shows 1–2 lines then stops | The **Share tab audio** checkbox was unchecked. Stop the session, click **Listen live** again, share the same tab with audio. |
| `1008 access denied` on Gemini Live | The `GEMINI_API_KEY` is from AI Studio free tier. See [Gemini Live access](#gemini-live-access-the-1-trap). |
| Backend log spams `Live stream closed cleanly (1000)` | Old version. Pull `main` — current `transcript_agent.py` runs minimal config + buffer-flush on each clean close. |
| `LOADING…` button stuck | Backend event loop jammed (usually from previous transcription session). Kill uvicorn, restart. Will be fixed properly in a future commit. |
| Voice reply on Ask Analyst is silent | Browser blocked autoplay on first run. Click anywhere on the page once, then ask again. |

---

## Deploy / share a public URL

### Fastest: Cloudflare quick tunnel (zero-config, judge-ready)

```bash
./scripts/start_demo.sh
# → builds the React frontend, mounts it into FastAPI on :8080,
#   starts the mongodb-mcp-server on :8088, then opens a
#   Cloudflare quick tunnel and prints the public URL:
#
#   PUBLIC URL → https://xxxxx-yyy-zzz.trycloudflare.com
```

This is the path used for the hackathon demo URL. No DNS, no account,
no certificates — just a public `*.trycloudflare.com` URL that stays
live until you `Ctrl-C`. See [docs/TUNNEL.md](docs/TUNNEL.md) for
troubleshooting.

### Persistent: Render

The repo is also set up for [Render](https://render.com) — see
`render.yaml`. New → Blueprint → connect this repo → paste env vars.
Set `ALLOWED_ORIGINS=https://your-service.onrender.com`.

### Single-container local build

```bash
cd earningsedge
docker build -t earningsedge .
docker run -p 8080:8080 --env-file .env earningsedge
```

---

## Other documentation

- [docs/HACKATHON.md](docs/HACKATHON.md) — Google Cloud Rapid Agent compliance map + 60-second verify script
- [docs/TUNNEL.md](docs/TUNNEL.md) — Cloudflare quick-tunnel run/stop/troubleshoot
- [docs/PROJECT_CONTEXT.md](docs/PROJECT_CONTEXT.md) — handoff, stack, decisions
- [docs/AGENTS-AUDIT.md](docs/AGENTS-AUDIT.md) — backend agent roles
- [mcp-research.json](mcp-research.json) — MCP catalog reference

---

## Secrets, Cursor MCP, and Git

- **App env:** only `earningsedge/.env` (from `earningsedge/.env.example`). Gitignored — do not commit.
- **Cursor MCP:** do not commit `.cursor/mcp.json`. Keep secrets in `earningsedge/.env`, then:
  ```bash
  cd earningsedge
  python scripts/sync_cursor_mcp.py
  ```
  Or copy `.cursor/mcp.json.example` locally. See `AGENTS.md` and `docs/PROJECT_CONTEXT.md`.
- **Commit** code, `earningsedge/.env.example`, and this README — **not** `earningsedge/.env`.
  If `.env` is staged: `git restore --staged earningsedge/.env`
