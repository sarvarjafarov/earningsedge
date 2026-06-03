# Multi-stage image: React static build + FastAPI serving `static/`.
#
# Local:
#   docker build -t earningsedge .
#   docker run --rm -p 8080:8080 --env-file .env earningsedge
#
# The app listens on host 0.0.0.0; map host port to container port (default 8080).

FROM node:20-bookworm-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
WORKDIR /app/backend

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./
COPY --from=frontend /app/frontend/build ./static

ENV PYTHONUNBUFFERED=1
EXPOSE 8080

CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"]
