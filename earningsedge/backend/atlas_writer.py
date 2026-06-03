"""AtlasWriter — durable write queue for the MongoDB MCP layer.

Writes that hit transient Atlas / MCP failures are queued and replayed
in the background with exponential backoff. The caller's hot path never
waits on Atlas: the first attempt runs inline (so a healthy cluster
costs nothing) and only failures are deferred.

Use ``durable_write`` from app code; never call ``mcp_call`` directly
for writes — that path will raise on a flaky Atlas free tier and break
the user-facing request that triggered it.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any

_log = logging.getLogger("earningsedge.atlas_writer")

MAX_QUEUE_LEN = 5000
BACKOFF_MIN_S = 1.0
BACKOFF_MAX_S = 30.0
RETRY_BATCH_SIZE = 50


class AtlasWriter:
    def __init__(self) -> None:
        self._queue: deque[tuple[str, dict[str, Any], int, float]] = deque()
        self._stats = {
            "queued": 0,
            "retried": 0,
            "succeeded": 0,
            "dropped": 0,
            "current_backoff_s": BACKOFF_MIN_S,
        }
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._stopped = asyncio.Event()

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._retry_loop(), name="atlas-writer-retry")

    async def stop(self) -> None:
        self._stopped.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    def stats(self) -> dict[str, Any]:
        return {**self._stats, "queue_depth": len(self._queue)}

    async def submit(self, tool: str, args: dict[str, Any]) -> None:
        from mcp_client import mcp_call
        try:
            await mcp_call(tool, args)
        except Exception as exc:  # noqa: BLE001
            _log.debug("inline write failed (%s), queueing: %s", tool, exc)
            await self._enqueue(tool, args)

    async def _enqueue(self, tool: str, args: dict[str, Any]) -> None:
        async with self._lock:
            if len(self._queue) >= MAX_QUEUE_LEN:
                self._queue.popleft()
                self._stats["dropped"] += 1
            self._queue.append((tool, args, 0, time.monotonic()))
            self._stats["queued"] += 1

    async def _retry_loop(self) -> None:
        backoff = BACKOFF_MIN_S
        from mcp_client import mcp_call

        while not self._stopped.is_set():
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=backoff)
                return
            except asyncio.TimeoutError:
                pass

            async with self._lock:
                batch: list[tuple[str, dict[str, Any], int, float]] = []
                for _ in range(min(RETRY_BATCH_SIZE, len(self._queue))):
                    batch.append(self._queue.popleft())

            if not batch:
                backoff = BACKOFF_MIN_S
                continue

            any_success = False
            still_failing: list[tuple[str, dict[str, Any], int, float]] = []
            for tool, args, attempts, first_seen in batch:
                self._stats["retried"] += 1
                try:
                    await mcp_call(tool, args)
                    any_success = True
                    self._stats["succeeded"] += 1
                except Exception:
                    still_failing.append((tool, args, attempts + 1, first_seen))

            if still_failing:
                async with self._lock:
                    for item in reversed(still_failing):
                        self._queue.appendleft(item)
                if not any_success:
                    backoff = min(BACKOFF_MAX_S, backoff * 2)
            else:
                backoff = BACKOFF_MIN_S

            self._stats["current_backoff_s"] = backoff


writer = AtlasWriter()


async def durable_write(tool: str, args: dict[str, Any]) -> None:
    """Inline-first, queue-on-failure write. Never raises."""
    await writer.submit(tool, args)
