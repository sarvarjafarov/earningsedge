"""Gemini-backed text embeddings for Atlas Vector Search.

We use Google's `text-embedding-004` (768 dims) — the same family as
the Gemini 3 reasoning models — so the embedding distribution lines up
with how the Chairman writes verdicts. A single async helper returns a
plain Python list of floats; the Atlas Vector Search aggregation
consumes that list directly.

Failures are non-fatal: callers get `None` instead of an exception so
the write/read path that depends on embeddings keeps working when
Gemini quota is exhausted or the embedding model is briefly unavailable.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Sequence

_log = logging.getLogger("earningsedge.embedding")

EMBED_MODEL = os.getenv("GEMINI_EMBED_MODEL", "gemini-embedding-001")
EMBED_DIM = 768  # we request truncated 768-dim output for Atlas index efficiency


async def embed_text(text: str) -> list[float] | None:
    """Embed a single string. Returns None on any failure."""
    if not text or not text.strip():
        return None
    try:
        from google import genai
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        # The official SDK exposes embed_content on the sync `models` API;
        # offload to a thread so we don't block the FastAPI event loop.
        def _sync_embed() -> list[float] | None:
            try:
                from google.genai import types as genai_types
                cfg = genai_types.EmbedContentConfig(
                    output_dimensionality=EMBED_DIM,
                    task_type="SEMANTIC_SIMILARITY",
                )
                res = client.models.embed_content(
                    model=EMBED_MODEL,
                    contents=text,
                    config=cfg,
                )
                # SDK shape: res.embeddings is a list with one EmbedContentEmbedding
                # whose .values is the float vector.
                embeds = getattr(res, "embeddings", None) or []
                if not embeds:
                    return None
                values = getattr(embeds[0], "values", None)
                return list(values) if values else None
            except Exception as exc:  # noqa: BLE001
                _log.warning("embed_content failed: %s", exc)
                return None

        return await asyncio.to_thread(_sync_embed)
    except KeyError:
        _log.warning("embed_text: GEMINI_API_KEY not set")
        return None
    except Exception as exc:  # noqa: BLE001
        _log.warning("embed_text outer failure: %s", exc)
        return None


async def embed_many(texts: Sequence[str]) -> list[list[float] | None]:
    """Embed a list of strings concurrently. Order preserved."""
    return await asyncio.gather(*(embed_text(t) for t in texts))
