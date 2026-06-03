"""Sanity checks for PEG computation and PeerAgent scoring."""
import asyncio
import os
import sys
from unittest.mock import AsyncMock

# Allow running as a plain script: `python tests/test_peer_peg.py`
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tools import _compute_peg
from agents.peer_agent import PeerAgent


def test_peg_basic():
    # Growth is now always expected in percent form, no decimal auto-detect.
    # 20 P/E, 20% growth -> PEG of 1.0
    assert _compute_peg(20.0, 20.0) == 1.0
    # 30 P/E, 15% growth -> PEG of 2.0
    assert _compute_peg(30.0, 15.0) == 2.0
    # Negative growth -> None
    assert _compute_peg(25.0, -5.0) is None
    # Zero growth -> None
    assert _compute_peg(25.0, 0.0) is None
    # Missing inputs -> None
    assert _compute_peg(None, 20.0) is None
    assert _compute_peg(20.0, None) is None
    # Negative P/E -> None
    assert _compute_peg(-15.0, 20.0) is None
    # PEG > 10 (near-zero growth) -> None for sanity
    assert _compute_peg(50.0, 0.01) is None


def test_normalize_percentage_yfinance():
    from tools import _normalize_percentage
    assert _normalize_percentage(0.732, source="yfinance", field_name="revenue_growth") == 73.2
    assert _normalize_percentage(0.54, source="yfinance", field_name="gross_margin") == 54.0
    assert _normalize_percentage(6.0, source="yfinance", field_name="revenue_growth") is None
    assert _normalize_percentage(None, source="yfinance", field_name="revenue_growth") is None


def test_normalize_percentage_finnhub():
    from tools import _normalize_percentage
    assert _normalize_percentage(34.34, source="finnhub", field_name="revenue_growth") == 34.34
    assert _normalize_percentage(0.34, source="finnhub", field_name="revenue_growth") == 34.0
    assert _normalize_percentage(1000.0, source="finnhub", field_name="revenue_growth") is None


def test_sanity_check_pe():
    from tools import _sanity_check_pe
    assert _sanity_check_pe(25.5) == 25.5
    assert _sanity_check_pe(0) is None
    assert _sanity_check_pe(-10) is None
    assert _sanity_check_pe(10000) is None
    assert _sanity_check_pe(None) is None
    assert _sanity_check_pe("not a number") is None


def test_peer_agent_undervalued():
    # Target PEG 0.5, peer median PEG 1.0 — target should score bullish
    # This test checks PeerAgent scoring logic without running full async.
    async def fake_get_competitors(ticker):
        return {
            "ticker": ticker,
            "peers": [
                {"ticker": ticker, "name": ticker, "is_target": True,
                 "pe_ratio": 15.0, "revenue_growth": 30.0, "peg": 0.5,
                 "ev_ebitda": 10.0, "gross_margin": 0.5,
                 "operating_margin": 0.3},
                {"ticker": "PEER1", "name": "Peer 1", "is_target": False,
                 "pe_ratio": 25.0, "revenue_growth": 25.0, "peg": 1.0,
                 "ev_ebitda": 15.0, "gross_margin": 0.4,
                 "operating_margin": 0.2},
                {"ticker": "PEER2", "name": "Peer 2", "is_target": False,
                 "pe_ratio": 28.0, "revenue_growth": 28.0, "peg": 1.0,
                 "ev_ebitda": 16.0, "gross_margin": 0.45,
                 "operating_margin": 0.25},
                {"ticker": "PEER3", "name": "Peer 3", "is_target": False,
                 "pe_ratio": 30.0, "revenue_growth": 30.0, "peg": 1.0,
                 "ev_ebitda": 18.0, "gross_margin": 0.42,
                 "operating_margin": 0.28},
            ],
        }
    import agents.peer_agent as peer_agent_mod
    orig = peer_agent_mod.get_competitors
    peer_agent_mod.get_competitors = fake_get_competitors
    try:
        broadcast = AsyncMock()
        ctx = {"ticker": "TEST"}
        agent = PeerAgent(broadcast, ctx)
        asyncio.run(agent.run())
        # Last call should be peer_valuation with bullish score
        calls = [c.args[0] for c in broadcast.call_args_list]
        pv = next(c for c in calls if c.get("type") == "peer_valuation")
        sb = pv["data"]["score_block"]
        assert sb["label"] == "bullish", (
            f"Expected bullish for target with PEG 0.5 vs peer median 1.0, "
            f"got {sb['label']} (score {sb['score']})"
        )
    finally:
        peer_agent_mod.get_competitors = orig


if __name__ == "__main__":
    test_peg_basic()
    test_normalize_percentage_yfinance()
    test_normalize_percentage_finnhub()
    test_sanity_check_pe()
    test_peer_agent_undervalued()
    print("All peer_peg tests passed.")

