"""Static shape checks for the ADK layer.

These tests run without network access. They verify the agent graph
matches what the hackathon submission claims so the README and
HACKATHON.md stay honest: 13 tools, 5 sub-agents, all required tool
names present, every sub-agent has at least one tool.
"""
from __future__ import annotations

import os
import sys

# Tests run from backend/, but the ADK package imports tools.py which
# expects backend/ on sys.path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# A dummy key so genai.Client constructors don't blow up at import time.
os.environ.setdefault("GEMINI_API_KEY", "import-only-no-network-call")


def test_root_agent_identity_and_size() -> None:
    from adk_agents import root_agent
    assert root_agent.name == "earningsedge_chairman"
    assert len(root_agent.tools) == 13
    assert len(root_agent.sub_agents) == 5


def test_all_required_tools_registered() -> None:
    from adk_agents import root_agent
    names = {fn.__name__ for fn in root_agent.tools}
    required = {
        "get_stock_quote",
        "get_fundamentals",
        "get_analyst_consensus",
        "get_peers",
        "get_news_sentiment",
        "get_earnings_estimates",
        "get_paper_account",
        "get_paper_positions",
        "draft_paper_trade",
        "remember",
        "recall",
        "find_similar_past_verdict",
        "remember_verdict",
    }
    missing = required - names
    assert not missing, f"missing tools: {missing}"


def test_sub_agents_identity() -> None:
    from adk_agents.sub_agents import ALL_SUB_AGENTS
    names = {a.name for a in ALL_SUB_AGENTS}
    assert names == {
        "bull_analyst",
        "bear_analyst",
        "quant_analyst",
        "news_analyst",
        "macro_analyst",
    }


def test_every_sub_agent_has_tools() -> None:
    from adk_agents.sub_agents import ALL_SUB_AGENTS
    for sa in ALL_SUB_AGENTS:
        assert sa.tools, f"sub-agent {sa.name} has no tools"


def test_chairman_prompt_mandates_memory_loop() -> None:
    """The Chairman must call find_similar_past_verdict before synthesis
    and remember_verdict after. The 'memory closes the loop' story is the
    headline of the demo video — if this drifts out of the prompt the
    video will misrepresent the product."""
    from adk_agents.root_agent import CHAIRMAN_INSTRUCTION
    assert "find_similar_past_verdict" in CHAIRMAN_INSTRUCTION
    assert "remember_verdict" in CHAIRMAN_INSTRUCTION
    assert "BEFORE" in CHAIRMAN_INSTRUCTION.upper() or "before composing" in CHAIRMAN_INSTRUCTION
