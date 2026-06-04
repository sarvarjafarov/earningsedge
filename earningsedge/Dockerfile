# Multi-stage image: React build + Python backend + MongoDB MCP sidecar.
#
# Local:
#   docker build -t earningsedge .
#   docker run --rm -p 8080:8080 --env-file .env earningsedge
#
# Heroku (Container Registry):
#   heroku container:push web -a <app-name>
#   heroku container:release web -a <app-name>
#
# The container starts the mongodb-mcp-server on :8088 in the background
# then runs uvicorn on $PORT (defaults to 8080 locally; Heroku injects
# the real PORT at runtime). uvicorn serves both the FastAPI API and
# the React SPA from `./static`.

# ---------- Stage 1: build the React frontend ----------
FROM node:20-bookworm-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
ENV DISABLE_ESLINT_PLUGIN=true
RUN npm run build

# ---------- Stage 2: Python runtime (slim — no Node sidecar) ----------
# The full requirements.txt pulls pandas/yfinance/pandas-ta which together
# bust the Heroku Basic 512MB ceiling. requirements-prod.txt drops them;
# the code paths that need them (price-tick loop, optional analyst
# fallback, technicals from pandas_ta) are guarded with try/except and
# degrade gracefully. mongodb-mcp-server isn't installed here either —
# in production, mcp_client.py uses the pymongo direct path. The MCP
# server is the local-dev story (scripts/start_demo.sh starts it).
FROM python:3.12-slim-bookworm
WORKDIR /app/backend

COPY backend/requirements-prod.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./
COPY --from=frontend /app/frontend/build ./static

ENV PYTHONUNBUFFERED=1 \
    MONGODB_MCP_URL=http://127.0.0.1:8088 \
    MCP_PORT=8088

EXPOSE 8080

# start.sh launches mongodb-mcp-server in the background then execs
# uvicorn so the FastAPI process owns the Heroku dyno's signal handling.
COPY start.sh /usr/local/bin/start.sh
RUN chmod +x /usr/local/bin/start.sh

CMD ["/usr/local/bin/start.sh"]
