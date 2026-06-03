"""ADK (Google Agent Development Kit) layer for EarningsEdge.

This package exposes the hackathon-required `root_agent` — an `LlmAgent`
that orchestrates EarningsEdge's existing specialist functions
(fundamentals, peers, news sentiment, analyst consensus, macro,
technicals, paper trades, persistent memory via MongoDB MCP) as tools
under a single Gemini 3 brain.

The product UI continues to call the legacy `orchestrator.py` flow for
the live-audio path — this module is the additive Agent-Builder entry
point used by `/api/adk/*` endpoints in main.py and is what
Vertex AI Agent Engine / `adk run` would invoke.
"""

from .root_agent import root_agent

__all__ = ["root_agent"]
