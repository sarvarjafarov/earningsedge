#!/usr/bin/env bash
# Keep a Cloudflare quick tunnel alive across disconnect storms.
#
# Cloudflare's free tier occasionally drops the control stream — the
# tunnel process keeps retrying but cycles get a new URL. This script
# runs cloudflared as a child, monitors its log for a fresh URL, writes
# it to ./.cloudflared/current-url, and restarts the tunnel cleanly when
# the control stream stays broken for more than 90 seconds.
#
# Usage:
#   ./scripts/tunnel_watchdog.sh                # tunnels to :8000
#   PORT=8080 ./scripts/tunnel_watchdog.sh      # custom port
#
# Stop with Ctrl-C — the trap kills cloudflared cleanly.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${PORT:-8000}"
STATE_DIR="$ROOT/.cloudflared"
URL_FILE="$STATE_DIR/current-url"
mkdir -p "$STATE_DIR"

command -v cloudflared >/dev/null || { echo "cloudflared not installed"; exit 1; }

cleanup() {
  echo "watchdog: shutting down"
  [[ -n "${CHILD_PID:-}" ]] && kill "$CHILD_PID" 2>/dev/null || true
  rm -f "$URL_FILE"
}
trap cleanup INT TERM EXIT

run_tunnel_once() {
  local logfile="$STATE_DIR/tunnel-$(date +%s).log"
  echo "watchdog: starting cloudflared → http://localhost:$PORT (log: $logfile)"
  cloudflared tunnel --url "http://localhost:$PORT" --logfile "$logfile" &
  CHILD_PID=$!

  # Wait up to 30 s for the URL to appear.
  local url=""
  for _ in $(seq 1 30); do
    url=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$logfile" 2>/dev/null | head -1)
    [[ -n "$url" ]] && break
    sleep 1
  done

  if [[ -z "$url" ]]; then
    echo "watchdog: no URL after 30 s, restarting"
    kill "$CHILD_PID" 2>/dev/null || true
    return 1
  fi

  echo "$url" > "$URL_FILE"
  echo "watchdog: TUNNEL UP → $url"

  # Health-check loop. If five consecutive checks fail, restart.
  local fails=0
  while kill -0 "$CHILD_PID" 2>/dev/null; do
    sleep 30
    if curl -fsS --max-time 10 "$url/health" >/dev/null 2>&1; then
      fails=0
    else
      fails=$((fails + 1))
      echo "watchdog: health check failed ($fails/3)"
      if (( fails >= 3 )); then
        echo "watchdog: tunnel unhealthy, restarting"
        kill "$CHILD_PID" 2>/dev/null || true
        return 1
      fi
    fi
  done

  echo "watchdog: cloudflared exited, restarting"
  return 1
}

while true; do
  run_tunnel_once
  sleep 5
done
