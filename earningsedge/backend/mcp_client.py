"""MongoDB MCP client for EarningsEdge.

Routes every Atlas read/write through the official MongoDB MCP server
over a Streamable HTTP transport (the hackathon-required partner
integration). Falls back to pymongo direct after consecutive MCP
failures so the production hot path stays alive when the MCP sidecar
isn't running.

Envelope handling matches the MongoDB MCP server's prompt-injection
guardrails (results are wrapped in ``<untrusted-user-data-{uuid}>``
tags). EJSON ObjectId ``{"$oid": "..."}`` shapes are normalized to plain
hex strings so the rest of the codebase can treat documents as ordinary
JSON.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

_log = logging.getLogger("earningsedge.mcp")

# The mcp.client.streamable_http SSE handler logs BrokenResourceError on
# its cleanup path AFTER our call has already returned a result. It looks
# scary in the uvicorn log but never indicates a real failure. Silence it.
logging.getLogger("mcp.client.streamable_http").setLevel(logging.CRITICAL)
logging.getLogger("anyio").setLevel(logging.CRITICAL)

_MONGODB_CONN = os.getenv("MDB_MCP_CONNECTION_STRING") or os.getenv("MONGODB_URI", "")
_MCP_TIMEOUT_S = float(os.getenv("MCP_TIMEOUT_S", "10"))
_MCP_FAILURE_THRESHOLD = 3

_mcp_consecutive_failures = 0

_ENVELOPE_OPEN_RE = re.compile(r"<untrusted-user-data-[0-9a-f-]+>")
_ENVELOPE_CLOSE_RE = re.compile(r"</untrusted-user-data-[0-9a-f-]+>")


def _strip_envelope(text: str) -> tuple[str, bool]:
    """Return the inner envelope content.

    The MCP server prefixes results with a security warning that
    mentions the envelope tags inline, so a naive non-greedy regex
    captures the prose between two warning mentions instead of the
    real content. We anchor on the LAST closing tag and the matching
    opening tag immediately before it — the actual envelope is always
    last in the message.
    """
    last_close = None
    for m in _ENVELOPE_CLOSE_RE.finditer(text):
        last_close = m
    if last_close is None:
        return text, False
    # Find the latest opening tag that starts before last_close.
    last_open = None
    for m in _ENVELOPE_OPEN_RE.finditer(text):
        if m.start() < last_close.start():
            last_open = m
    if last_open is None:
        return text, False
    inner = text[last_open.end():last_close.start()].strip()
    return inner, True


def _normalize_oids(value: Any) -> Any:
    if isinstance(value, dict):
        if set(value.keys()) == {"$oid"}:
            return value["$oid"]
        return {k: _normalize_oids(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize_oids(v) for v in value]
    return value


def _mcp_url() -> str:
    base = os.getenv("MONGODB_MCP_URL", "http://127.0.0.1:8088").rstrip("/")
    return base if base.endswith("/mcp") else f"{base}/mcp"


async def mcp_call(tool: str, args: dict[str, Any]) -> Any:
    """Primary entry point. MCP-first when configured, pymongo fallback.

    When MONGODB_MCP_URL is unset (e.g. on Heroku where the partner MCP
    server isn't run as a sidecar to save memory), we skip the MCP path
    entirely so a connection-refused hang doesn't burn the request
    budget.
    """
    global _mcp_consecutive_failures
    if os.getenv("MONGODB_MCP_URL", "").strip() and _mcp_consecutive_failures < _MCP_FAILURE_THRESHOLD:
        try:
            result = await asyncio.wait_for(_mcp_call_strict(tool, args), timeout=_MCP_TIMEOUT_S)
            _mcp_consecutive_failures = 0
            return result
        except Exception as exc:  # noqa: BLE001
            _mcp_consecutive_failures += 1
            _log.warning(
                "mcp call failed (count=%d) tool=%s err=%s",
                _mcp_consecutive_failures, tool, exc,
            )
    return await _pymongo_fallback(tool, args)


async def _mcp_call_strict(tool: str, args: dict[str, Any]) -> Any:
    async with streamablehttp_client(_mcp_url()) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            if tool != "connect" and _MONGODB_CONN:
                try:
                    await session.call_tool("connect", {"connectionString": _MONGODB_CONN})
                except Exception:
                    pass
            result = await session.call_tool(tool, args)

    payloads: list[Any] = []
    for part in result.content or []:
        text = getattr(part, "text", None)
        if not isinstance(text, str):
            continue
        inner, had_envelope = _strip_envelope(text)
        candidate = inner if had_envelope else text
        try:
            payloads.append(_normalize_oids(json.loads(candidate)))
        except (TypeError, ValueError):
            # Prefer the cleaner inner envelope text over the full
            # warning-prose original — callers that don't get JSON still
            # want to read "Inserted 1 document", not the 800-char
            # security warning that precedes it.
            payloads.append(inner if had_envelope else text)
    if not payloads:
        return None
    structured = [p for p in payloads if not isinstance(p, str)]
    return structured[-1] if structured else payloads[-1]


_pymongo_client = None


def _get_pymongo_client():
    global _pymongo_client
    if _pymongo_client is None:
        from pymongo import MongoClient
        from pymongo.read_preferences import ReadPreference
        import certifi
        uri = _MONGODB_CONN
        if not uri:
            raise RuntimeError("MONGODB_URI / MDB_MCP_CONNECTION_STRING is not set")
        _pymongo_client = MongoClient(
            uri,
            tlsCAFile=certifi.where(),
            read_preference=ReadPreference.SECONDARY_PREFERRED,
            retryReads=True,
            retryWrites=True,
            serverSelectionTimeoutMS=15000,
        )
    return _pymongo_client


def _doc_clean(doc: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in doc.items():
        if hasattr(v, "binary"):
            out[k] = str(v)
        elif isinstance(v, dict):
            out[k] = _doc_clean(v)
        elif isinstance(v, list):
            out[k] = [_doc_clean(x) if isinstance(x, dict) else x for x in v]
        else:
            out[k] = v
    return out


async def _pymongo_fallback(tool: str, args: dict[str, Any]) -> Any:
    db_name = args.get("database") or os.getenv("MONGODB_DB", "earningsedge")
    coll_name = args.get("collection")

    from pymongo.errors import AutoReconnect, ServerSelectionTimeoutError

    def _run() -> Any:
        client = _get_pymongo_client()
        db = client[db_name]
        if tool == "find":
            filt = args.get("filter") or {}
            sort = args.get("sort")
            limit = int(args.get("limit") or 0)
            cur = db[coll_name].find(filt)
            if sort:
                cur = cur.sort([(k, v) for k, v in sort.items()])
            if limit > 0:
                cur = cur.limit(limit)
            return [_doc_clean(d) for d in cur]
        if tool == "insert-one":
            doc = args.get("document") or {}
            res = db[coll_name].insert_one(doc)
            return f"Inserted 1 document with id {res.inserted_id}."
        if tool == "insert-many":
            docs = args.get("documents") or []
            if not docs:
                return "No documents to insert."
            res = db[coll_name].insert_many(docs)
            return f"Inserted {len(res.inserted_ids)} documents."
        if tool == "update-many":
            res = db[coll_name].update_many(
                args.get("filter") or {},
                args.get("update") or {},
                upsert=bool(args.get("upsert")),
            )
            return f"Matched {res.matched_count}, modified {res.modified_count}."
        if tool == "list-databases":
            return list(client.list_database_names())
        if tool == "connect":
            client.admin.command("ping")
            return "connected"
        raise NotImplementedError(f"pymongo fallback does not implement tool {tool!r}")

    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            return await asyncio.to_thread(_run)
        except (AutoReconnect, ServerSelectionTimeoutError) as exc:
            last_exc = exc
            global _pymongo_client
            _pymongo_client = None
            await asyncio.sleep(0.6 * (attempt + 1))
            continue
    if last_exc:
        raise last_exc
    return None
