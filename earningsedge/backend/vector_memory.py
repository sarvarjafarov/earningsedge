"""Atlas Vector Search wrapper for EarningsEdge's verdict memory.

Stores embedded verdicts in MongoDB and exposes a ``$vectorSearch``
aggregation as an async function the ADK Chairman can call as a tool.

The collection is ``verdicts`` in db ``earningsedge``. Each document:
    {
        _id: ObjectId,
        ticker: "NVDA",
        action: "Add" | "Hold" | "Trim" | "Avoid",
        score: 0-100,
        confidence: "LOW" | "MEDIUM" | "HIGH",
        text: "<full chairman synthesis>",
        text_embedding: [768 floats],
        ts: epoch_ms,
        sources: ["earnings call Q3 2024", ...]
    }

The Vector Search index is named ``verdict_vec_idx`` and is created
on-demand by ``ensure_index``. Atlas free tier supports Vector Search
on M0 clusters — the index builds in ~30 seconds.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import certifi
from pymongo import MongoClient
from pymongo.operations import SearchIndexModel

from embedding import EMBED_DIM, embed_text

_log = logging.getLogger("earningsedge.vector_memory")

INDEX_NAME = "verdict_vec_idx"
COLLECTION = "verdicts"


def _client() -> MongoClient:
    uri = os.environ["MONGODB_URI"]
    return MongoClient(
        uri,
        tlsCAFile=certifi.where(),
        serverSelectionTimeoutMS=int(os.getenv("MONGODB_SELECT_TIMEOUT_MS", "5000")),
        socketTimeoutMS=8000,
    )


def _db():
    return _client()[os.getenv("MONGODB_DB", "earningsedge")]


async def ensure_index() -> dict[str, Any]:
    """Idempotent: create the Vector Search index if it doesn't exist."""
    import asyncio

    def _sync() -> dict[str, Any]:
        try:
            coll = _db()[COLLECTION]
            existing = {idx.get("name") for idx in coll.list_search_indexes()}
            if INDEX_NAME in existing:
                return {"ok": True, "status": "already_exists"}
            model = SearchIndexModel(
                definition={
                    "fields": [
                        {
                            "type": "vector",
                            "path": "text_embedding",
                            "numDimensions": EMBED_DIM,
                            "similarity": "cosine",
                        },
                        {"type": "filter", "path": "ticker"},
                    ],
                },
                name=INDEX_NAME,
                type="vectorSearch",
            )
            coll.create_search_index(model=model)
            return {"ok": True, "status": "created"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    return await asyncio.to_thread(_sync)


async def remember_verdict(doc: dict[str, Any]) -> dict[str, Any]:
    """Embed the verdict text and persist with the embedding.

    Falls back to writing without an embedding when Gemini's embedding
    endpoint is unavailable, so memory writes never block the chairman.
    """
    import asyncio

    text = doc.get("text") or doc.get("response") or ""
    vec = await embed_text(text) if text else None
    payload = {**doc}
    if vec is not None:
        payload["text_embedding"] = vec

    def _sync() -> dict[str, Any]:
        try:
            res = _db()[COLLECTION].insert_one(payload)
            return {"ok": True, "id": str(res.inserted_id), "embedded": vec is not None}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    return await asyncio.to_thread(_sync)


async def find_similar_verdicts(
    query: str,
    ticker: str | None = None,
    k: int = 5,
) -> list[dict[str, Any]]:
    """Run a vector-similarity search against past verdicts.

    Tries Atlas ``$vectorSearch`` first. When Atlas SSL is failing
    (intermittent free-tier issue from cloud egress) we fall through
    to ``verdict_corpus.fallback_search`` — a pure-Python cosine
    similarity over the shipped seed corpus. The caller can't tell
    which path served them; both return the same shape.
    """
    import asyncio

    vec = await embed_text(query)
    if vec is None:
        return []

    def _sync() -> list[dict[str, Any]]:
        try:
            pipeline: list[dict[str, Any]] = [
                {
                    "$vectorSearch": {
                        "index": INDEX_NAME,
                        "path": "text_embedding",
                        "queryVector": vec,
                        "numCandidates": max(50, k * 10),
                        "limit": k,
                        **({"filter": {"ticker": ticker.upper()}} if ticker else {}),
                    }
                },
                {
                    "$project": {
                        "_id": 0,
                        "ticker": 1,
                        "action": 1,
                        "score": 1,
                        "confidence": 1,
                        "text": 1,
                        "ts": 1,
                        "sources": 1,
                        "similarity": {"$meta": "vectorSearchScore"},
                    }
                },
            ]
            return list(_db()[COLLECTION].aggregate(pipeline))
        except Exception as exc:  # noqa: BLE001
            _log.warning("atlas vector search failed: %s", exc)
            return []

    try:
        rows = await asyncio.wait_for(asyncio.to_thread(_sync), timeout=6.0)
    except asyncio.TimeoutError:
        _log.info("atlas vector search timed out, falling back to corpus")
        rows = []

    if rows:
        return rows

    # Atlas unavailable — fall back to in-memory corpus.
    try:
        from verdict_corpus import fallback_search
        return await fallback_search(query, ticker=ticker, k=k)
    except Exception as exc:  # noqa: BLE001
        _log.warning("corpus fallback failed: %s", exc)
        return []
