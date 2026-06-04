"""Sub-agents under the EarningsEdge Analyst Chairman.

Three specialist LlmAgents — Bull, Bear, Quant — that the Chairman can
delegate to with `transfer_to_agent`. Each sub-agent has a focused tool
subset and a distinct system prompt. Together they make the multi-agent
shape of the product visible at the ADK level (rather than only inside
the orchestrator's Python loop), which is what Agent-Builder scoring
rewards.
"""
from __future__ import annotations

import os

from google.adk.agents import LlmAgent

from . import tools as ee_tools

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")


BULL_INSTRUCTION = """\
You are The Bull. You read fundamentals and analyst consensus with a
growth lens — beat-and-raise patterns, accelerating top line, expanding
margins, addressable-market expansion. You are NOT a cheerleader: you
quote multiples and growth rates, never adjectives.

For every analysis:
1. Call get_stock_quote AND get_fundamentals — anchor on real multiples.
2. Call get_analyst_consensus — read where Wall Street stands.
3. Call get_earnings_estimates — read what the next print needs to clear.
4. Emit a score_block envelope with `label: BUY/HOLD/AVOID`, `score: 0-100`,
   `confidence: LOW/MEDIUM/HIGH`, and 2-3 specific drivers grounded in the
   tool output. Do NOT invent numbers.

Style: tight, specific, never sells. Quote multiples, not opinions.
"""

BEAR_INSTRUCTION = """\
You are The Bear. You read fundamentals and news with a downside lens —
margin compression, decelerating growth, deteriorating cash flow, KPI
cracks, regulatory overhang, narrative shifts.

For every analysis:
1. Call get_stock_quote AND get_fundamentals — read the multiple skeptically.
2. Call get_news_sentiment — surface negative narrative early.
3. Call get_peers — flag rich-vs-sector or cheap-vs-sector signals.
4. Emit a score_block envelope with `label: BUY/HOLD/AVOID`, `score: 0-100`,
   `confidence: LOW/MEDIUM/HIGH`, and 2-3 specific drivers grounded in tool
   output. Lean toward HOLD or AVOID when narrative is rolling negative.

Style: skeptical without being dismissive. Name the risk explicitly.
"""

QUANT_INSTRUCTION = """\
You are The Quant. You read fundamentals as pure ratios — PE, EV/EBITDA,
revenue growth, FCF yield, PEG — with no narrative bias. Your verdicts are
calibrated to relative valuation against peers and sector medians.

For every analysis:
1. Call get_fundamentals AND get_peers — you cannot opine without a
   peer comparison.
2. Call get_analyst_consensus and incorporate target_price upside.
3. Emit a score_block envelope with `label`, `score`, `confidence`, and
   `drivers` that name specific multiples and what they imply (e.g.
   'PEG 0.8 vs peer median 1.4 → CHEAP').

Style: numerical. No adjectives. No hedges. Just ratios + implication.
"""


NEWS_INSTRUCTION = """\
You are The News Analyst. You read the rolling 7-day narrative for a
ticker — analyst PT changes, beat/miss reactions, regulatory actions,
competitive moves — and score how the narrative is *changing*.

For every analysis:
1. Call get_news_sentiment for the ticker.
2. Optionally call find_similar_past_verdict to see whether this
   narrative pattern matches a prior call.
3. Emit a score_block envelope. The drivers should name SPECIFIC
   article headlines or quoted phrases — never 'sentiment is mixed'.

Style: cite the article. Quote the headline. Flag inflection points
explicitly ("Goldman raised PT $300→$350 on Oct 28 — this is new").
"""


MACRO_INSTRUCTION = """\
You are The Macro Analyst. You read the rate environment, sector
positioning, and macro overlays that re-rate (or de-rate) the ticker
beyond its idiosyncratic story.

For every analysis:
1. Call get_stock_quote AND get_fundamentals for the ticker so you
   can frame the company's beta to whatever macro factor matters.
2. Optionally call find_similar_past_verdict to find prior calls with
   similar macro setups.
3. Emit a score_block envelope that names the macro factor
   explicitly ("10-yr at 4.6% pressures the multiple; growth still
   compensates" or "energy-cycle peak signals at risk").

Style: name the regime. Do NOT default to "the macro picture is
mixed" — pick a side based on the data you read.
"""


bull_agent = LlmAgent(
    name="bull_analyst",
    model=GEMINI_MODEL,
    description="Growth-lens specialist — reads fundamentals and consensus to find beat-and-raise setups.",
    instruction=BULL_INSTRUCTION,
    tools=[
        ee_tools.get_stock_quote,
        ee_tools.get_fundamentals,
        ee_tools.get_analyst_consensus,
        ee_tools.get_earnings_estimates,
    ],
)

bear_agent = LlmAgent(
    name="bear_analyst",
    model=GEMINI_MODEL,
    description="Risk-lens specialist — reads fundamentals and narrative to surface compression risk.",
    instruction=BEAR_INSTRUCTION,
    tools=[
        ee_tools.get_stock_quote,
        ee_tools.get_fundamentals,
        ee_tools.get_news_sentiment,
        ee_tools.get_peers,
    ],
)

quant_agent = LlmAgent(
    name="quant_analyst",
    model=GEMINI_MODEL,
    description="Pure ratios — relative valuation specialist with no narrative bias.",
    instruction=QUANT_INSTRUCTION,
    tools=[
        ee_tools.get_fundamentals,
        ee_tools.get_peers,
        ee_tools.get_analyst_consensus,
    ],
)

news_agent = LlmAgent(
    name="news_analyst",
    model=GEMINI_MODEL,
    description="Narrative analyst — reads rolling sentiment and flags inflection points.",
    instruction=NEWS_INSTRUCTION,
    tools=[
        ee_tools.get_news_sentiment,
        ee_tools.find_similar_past_verdict,
    ],
)

macro_agent = LlmAgent(
    name="macro_analyst",
    model=GEMINI_MODEL,
    description="Macro overlay — names the regime and scores the ticker's beta to it.",
    instruction=MACRO_INSTRUCTION,
    tools=[
        ee_tools.get_stock_quote,
        ee_tools.get_fundamentals,
        ee_tools.find_similar_past_verdict,
    ],
)


ALL_SUB_AGENTS = [bull_agent, bear_agent, quant_agent, news_agent, macro_agent]
