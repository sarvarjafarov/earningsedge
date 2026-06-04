"""In-memory seed verdict corpus + cosine-similarity fallback.

Atlas free-tier (M0) clusters intermittently fail SSL handshakes to
cloud egress IPs (Cloud Run, Heroku, Render). When that happens, the
``$vectorSearch`` aggregation can't run and ``find_similar_verdicts``
returns empty — which means the Chairman's memory-citation story
breaks in the demo.

This module ships the seeded corpus as JSON inside the container,
embeds it once at startup (cached on disk), and provides a pure-Python
cosine-similarity search that runs offline. It's used as a fallback
when Atlas Vector Search is unreachable.

The same 11 documents live in Atlas under db=earningsedge,
collection=verdicts — when Atlas recovers, real ``$vectorSearch``
takes over. This is a graceful-degradation layer, not a replacement.
"""
from __future__ import annotations

import json
import logging
import math
import os
import pathlib
from typing import Any

_log = logging.getLogger("earningsedge.corpus")

_CORPUS_PATH = pathlib.Path(__file__).resolve().parent / "seed_corpus.json"

_corpus: list[dict[str, Any]] | None = None
_corpus_warmed = False


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _load_disk_corpus() -> list[dict[str, Any]]:
    """Read the JSON file if it exists. Each row needs at minimum a
    ``text`` field; ``text_embedding`` is optional and re-computed on
    demand if missing."""
    if not _CORPUS_PATH.is_file():
        return []
    try:
        rows = json.loads(_CORPUS_PATH.read_text())
        if isinstance(rows, list):
            return rows
    except Exception as exc:  # noqa: BLE001
        _log.warning("failed to load %s: %s", _CORPUS_PATH, exc)
    return []


async def warm_corpus() -> None:
    """Load the corpus and ensure each row has an embedding.

    Safe to call multiple times — only does work the first time.
    Imports embed_text lazily so a missing Gemini key doesn't crash
    container boot.
    """
    global _corpus, _corpus_warmed
    if _corpus_warmed:
        return
    _corpus_warmed = True

    rows = _load_disk_corpus()
    if not rows:
        _log.info("no on-disk corpus at %s — fallback disabled", _CORPUS_PATH)
        _corpus = []
        return

    # If any rows are missing embeddings, fill them.
    to_embed = [(i, r["text"]) for i, r in enumerate(rows) if not r.get("text_embedding") and r.get("text")]
    if to_embed:
        try:
            from embedding import embed_text
            for i, txt in to_embed:
                vec = await embed_text(txt)
                if vec is not None:
                    rows[i]["text_embedding"] = vec
        except Exception as exc:  # noqa: BLE001
            _log.warning("corpus warm-up embedding failed: %s", exc)

    _corpus = rows
    embedded = sum(1 for r in rows if r.get("text_embedding"))
    _log.info("verdict_corpus warmed: %d rows, %d embedded", len(rows), embedded)


async def fallback_search(
    query: str, ticker: str | None = None, k: int = 5
) -> list[dict[str, Any]]:
    """Cosine-similarity search over the in-memory corpus.

    Returns rows shaped like Atlas $vectorSearch output so the caller
    can't tell which path served them: ticker, action, score,
    confidence, text, ts, similarity.
    """
    await warm_corpus()
    if not _corpus:
        return []

    try:
        from embedding import embed_text
        qvec = await embed_text(query)
    except Exception:  # noqa: BLE001
        qvec = None
    if qvec is None:
        return []

    candidates = _corpus
    if ticker:
        t = ticker.upper()
        candidates = [r for r in _corpus if r.get("ticker") == t]
        if not candidates:
            candidates = _corpus  # no exact ticker — return any matches by language

    scored = []
    for r in candidates:
        vec = r.get("text_embedding")
        if not vec:
            continue
        sim = _cosine(qvec, vec)
        scored.append((sim, r))
    scored.sort(key=lambda x: x[0], reverse=True)

    out: list[dict[str, Any]] = []
    for sim, r in scored[:k]:
        out.append({
            "ticker": r.get("ticker"),
            "action": r.get("action"),
            "score": r.get("score"),
            "confidence": r.get("confidence"),
            "text": r.get("text"),
            "ts": r.get("ts"),
            "sources": r.get("sources"),
            "similarity": sim,
        })
    return out
