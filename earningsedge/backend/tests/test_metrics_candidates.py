"""Test the candidate-scoring metrics agent selects the right values."""
import asyncio
import os
import sys
from unittest.mock import AsyncMock

# Allow running as a plain script: `python tests/test_metrics_candidates.py`
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def test_high_confidence_top_line_beats_low_confidence_segment():
    # Simulate agent state and test _best_candidate logic directly.
    from agents.metrics_agent import MetricsAgent

    queue = asyncio.Queue()
    broadcast = AsyncMock()
    ctx = {"ticker": "TEST", "preloaded_estimates": {}}
    agent = MetricsAgent(queue, broadcast, ctx)

    # Segment revenue extracted first with high confidence
    agent._candidates["revenue_reported"].append({
        "value": "$18B", "confidence": 0.9, "class": "segment"
    })
    # Top-line extracted later with slightly lower confidence
    agent._candidates["revenue_reported"].append({
        "value": "$22.1B", "confidence": 0.85, "class": "top_line"
    })

    best = agent._best_candidate("revenue_reported")
    assert best["value"] == "$22.1B", f"expected top-line to win, got {best}"


def test_higher_confidence_replaces_lower():
    from agents.metrics_agent import MetricsAgent

    queue = asyncio.Queue()
    broadcast = AsyncMock()
    ctx = {"ticker": "TEST", "preloaded_estimates": {}}
    agent = MetricsAgent(queue, broadcast, ctx)

    agent._candidates["eps_reported"].append({
        "value": "$4.00", "confidence": 0.6, "class": "gaap"
    })
    agent._candidates["eps_reported"].append({
        "value": "$4.93", "confidence": 0.92, "class": "gaap"
    })

    best = agent._best_candidate("eps_reported")
    assert best["value"] == "$4.93"


if __name__ == "__main__":
    test_high_confidence_top_line_beats_low_confidence_segment()
    test_higher_confidence_replaces_lower()
    print("All metrics_candidates tests passed.")

