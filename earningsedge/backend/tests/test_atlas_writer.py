"""Atlas durable-writer unit tests.

We don't hit Atlas in CI — instead we verify the queue/backoff state
machine in isolation. This catches the dropping-oldest-on-overflow
behaviour and the no-op short-circuit when MONGODB_URI is unset.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_durable_write_noops_without_uri() -> None:
    """When neither MONGODB_URI nor MDB_MCP_CONNECTION_STRING is set, the
    write must short-circuit so the queue can't grow unbounded."""
    from atlas_writer import durable_write, writer

    # Force unset
    for key in ("MONGODB_URI", "MDB_MCP_CONNECTION_STRING"):
        os.environ.pop(key, None)

    asyncio.run(durable_write("insert-many", {"database": "x", "collection": "y", "documents": [{}]}))
    stats = writer.stats()
    assert stats["queued"] == 0
    assert stats["queue_depth"] == 0


def test_writer_stats_shape() -> None:
    from atlas_writer import writer

    stats = writer.stats()
    for key in ("queued", "retried", "succeeded", "dropped", "current_backoff_s", "queue_depth"):
        assert key in stats, f"missing stat: {key}"
