"""EarningsEdge multi-agent system. Specialized agents run in parallel
during a live earnings call:

  TranscriptAgent       — owns the Gemini Live audio session, emits sentences
  MetricsAgent          — extracts financial figures, computes beat/miss
  SentimentAgent        — scores management tone every N sentences
  FinalSynthesisAgent   — final analyst view: intraday signal reconciled with all dashboard agents
  HighlightLexiconAgent — proposes analyst trigger phrases for transcript highlighting
  ChatAgent              — /api/ask side-channel Q&A (text-only over WebSocket)
  MacroAgent             — one-shot FRED macro snapshot + Gemini equity read
  TechnicalAgent         — one-shot Alpha Vantage indicators + Gemini technical read
  PeerAgent              — one-shot peer-relative valuation via PEG + P/E
"""
from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Imported only for type checkers; runtime uses lazy imports to avoid circular deps
    from agents.chat_agent import ChatAgent
    from agents.final_synthesis_agent import FinalSynthesisAgent
    from agents.highlight_lexicon_agent import HighlightLexiconAgent
    from agents.macro_agent import MacroAgent
    from agents.metrics_agent import MetricsAgent
    from agents.sentiment_agent import SentimentAgent
    from agents.peer_agent import PeerAgent
    from agents.technical_agent import TechnicalAgent
    from agents.transcript_agent import TranscriptAgent

__all__ = [
    "ChatAgent",
    "TranscriptAgent",
    "MetricsAgent",
    "SentimentAgent",
    "FinalSynthesisAgent",
    "HighlightLexiconAgent",
    "MacroAgent",
    "TechnicalAgent",
    "PeerAgent",
]

_EXPORTS: dict[str, str] = {
    "ChatAgent": "agents.chat_agent",
    "TranscriptAgent": "agents.transcript_agent",
    "MetricsAgent": "agents.metrics_agent",
    "SentimentAgent": "agents.sentiment_agent",
    "FinalSynthesisAgent": "agents.final_synthesis_agent",
    "HighlightLexiconAgent": "agents.highlight_lexicon_agent",
    "MacroAgent": "agents.macro_agent",
    "TechnicalAgent": "agents.technical_agent",
    "PeerAgent": "agents.peer_agent",
}


def __getattr__(name: str) -> Any:
    mod_path = _EXPORTS.get(name)
    if not mod_path:
        raise AttributeError(name)
    mod = import_module(mod_path)
    return getattr(mod, name)

