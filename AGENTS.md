# AGENTS.md — AI assistants (Cursor, Claude, etc.)

This repository is **EarningsEdge**: a company intelligence + earnings-call analysis application.

## Read first

1. **`docs/PROJECT_CONTEXT.md`** — Vision, stack, flows, **tools inventory** (implemented `tools.py` vs `mcp-research.json` catalog).
2. **`.cursor/rules/`** — Project rules (when using Cursor).
3. **`docs/AGENTS-AUDIT.md`** — Backend agent responsibilities.
4. **`mcp-research.json`** (repo root) — Optional MCP servers / skills reference; configure in Cursor if you need them in-session.

## App entry

- Application code: **`earningsedge/`** (not the repo root).
- Dev setup: repository **`README.md`** (root — env vars, local run, APIs).

## Product boundary

Outputs may support **buy / sell / hold**-style decisions; they are **informational**. Disclaimers belong in the UI. Do not present outputs as personalized investment advice unless product/legal explicitly allows.

## Updating memory

After meaningful changes, append **`docs/PROJECT_CONTEXT.md`** (Decisions or Changelog).
