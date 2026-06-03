#!/usr/bin/env bash
# Bring up EarningsEdge for hackathon judging in a single command.
#
# What it does:
#   1. Build the React frontend (`npm run build`) once
#   2. Copy `frontend/build` → `backend/static` so FastAPI serves the SPA
#   3. Start uvicorn on :8080 with the .env from `earningsedge/.env`
#   4. Start the MongoDB MCP server on :8088 (Streamable HTTP)
#   5. Start a Cloudflare quick tunnel pointed at :8080 — prints the URL
#
# Stop everything with Ctrl-C; the trap below tears it all down cleanly.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="$ROOT/earningsedge"

# ---------------------------------------------------------------------------
# 1. Prereq checks (fail fast)
# ---------------------------------------------------------------------------
command -v node >/dev/null       || { echo "node 18+ required"; exit 1; }
command -v npm >/dev/null        || { echo "npm required"; exit 1; }
command -v cloudflared >/dev/null || { echo "cloudflared required: brew install cloudflared"; exit 1; }
command -v npx >/dev/null        || { echo "npx required"; exit 1; }

[[ -f "$APP/.env" ]] || { echo "missing $APP/.env (copy from .env.example)"; exit 1; }

# ---------------------------------------------------------------------------
# 2. Build the frontend into backend/static (matches the Render Dockerfile)
# ---------------------------------------------------------------------------
if [[ "${SKIP_BUILD:-}" != "1" ]]; then
  echo "==> building React frontend"
  (cd "$APP/frontend" && npm install --prefer-offline --no-audit && npm run build)
  rm -rf "$APP/backend/static"
  cp -R "$APP/frontend/build" "$APP/backend/static"
fi

# ---------------------------------------------------------------------------
# 3. Backend venv
# ---------------------------------------------------------------------------
if [[ ! -d "$APP/backend/.venv" ]]; then
  echo "==> creating Python venv"
  python3 -m venv "$APP/backend/.venv"
fi
"$APP/backend/.venv/bin/pip" install --quiet --upgrade pip
"$APP/backend/.venv/bin/pip" install --quiet -r "$APP/backend/requirements.txt"

# ---------------------------------------------------------------------------
# 4. Start the MongoDB MCP server (Streamable HTTP on :8088)
# ---------------------------------------------------------------------------
MCP_LOG="$ROOT/.cloudflared/mcp.log"
mkdir -p "$ROOT/.cloudflared"
echo "==> starting mongodb-mcp-server on :8088"
( set +e; cd "$APP" && nohup npx -y mongodb-mcp-server@latest \
    --transport http --httpPort 8088 \
    > "$MCP_LOG" 2>&1 & echo $! > "$ROOT/.cloudflared/mcp.pid"; )

# ---------------------------------------------------------------------------
# 5. Start uvicorn (serves API + SPA from one origin on :8080)
# ---------------------------------------------------------------------------
UVI_LOG="$ROOT/.cloudflared/uvicorn.log"
echo "==> starting uvicorn on :8080"
( cd "$APP/backend" && nohup "$APP/backend/.venv/bin/uvicorn" main:app \
    --host 0.0.0.0 --port 8080 \
    > "$UVI_LOG" 2>&1 & echo $! > "$ROOT/.cloudflared/uvicorn.pid"; )

# Wait for uvicorn to be ready.
for _ in $(seq 1 30); do
  if curl -sf http://127.0.0.1:8080/health >/dev/null; then break; fi
  sleep 1
done

# ---------------------------------------------------------------------------
# 6. Start the Cloudflare quick tunnel — prints public URL
# ---------------------------------------------------------------------------
TUN_LOG="$ROOT/.cloudflared/tunnel.log"
echo "==> starting cloudflared quick tunnel"
( nohup cloudflared tunnel --url http://localhost:8080 \
    > "$TUN_LOG" 2>&1 & echo $! > "$ROOT/.cloudflared/tunnel.pid"; )

# Cleanup on Ctrl-C.
cleanup() {
  echo
  echo "==> stopping background processes"
  for pidfile in "$ROOT/.cloudflared"/{uvicorn,mcp,tunnel}.pid; do
    [[ -f "$pidfile" ]] && kill "$(cat "$pidfile")" 2>/dev/null || true
    rm -f "$pidfile"
  done
}
trap cleanup INT TERM

# Print the tunnel URL when cloudflared discovers it.
echo
echo "Logs:"
echo "  tail -f $UVI_LOG  $MCP_LOG  $TUN_LOG"
echo
echo "Waiting for tunnel URL …"
for _ in $(seq 1 30); do
  url=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$TUN_LOG" 2>/dev/null | head -1)
  if [[ -n "$url" ]]; then
    echo
    echo "  PUBLIC URL → $url"
    echo
    break
  fi
  sleep 1
done

echo "Ctrl-C to stop."
wait
