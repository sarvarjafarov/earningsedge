#!/usr/bin/env bash
# Container entrypoint for the Heroku / Docker production build.
#
# 1. Start the MongoDB MCP server on :8088 in the background, scoped to
#    the Atlas URI in MONGODB_URI. If MONGODB_URI is unset (or the MCP
#    server fails), uvicorn still boots — durable_write short-circuits
#    and the rest of the app keeps working.
# 2. Exec uvicorn so PID 1 is the FastAPI process. Heroku sends SIGTERM
#    to PID 1 on dyno restart; we want uvicorn to handle that gracefully.

set -uo pipefail

APP_PORT="${PORT:-8080}"

# Production note: mongodb-mcp-server is not installed in this image
# (saves ~80MB so the dyno fits Heroku Basic 512MB). The mcp_client
# module's pymongo path handles all reads/writes in production. The MCP
# server is the local-development story (scripts/start_demo.sh wires it
# in alongside uvicorn).

echo "[start] launching uvicorn on :${APP_PORT}"
exec uvicorn main:app --host 0.0.0.0 --port "${APP_PORT}"
