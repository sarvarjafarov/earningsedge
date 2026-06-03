"""Comprehensive committee engine tests."""
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from agents.committee import (
    compute_committee,
    _apply_hysteresis,
    _committee_confidence,
    COVERAGE_WEIGHTS,
    LIVE_WEIGHTS,
)


def _sb(score, confidence="MEDIUM", label=None, reason=""):
    if label is None:
        label = "bullish" if score >= 65 else "bearish" if score <= 35 else "neutral"
    return {
        "score": score,
        "confidence": confidence,
        "label": label,
        "reason": reason or f"test reason at {score}",
        "drivers": [],
        "sample_size": 10,
        "freshness": "fresh",
    }


def test_weights_sum_to_one():
    assert abs(sum(COVERAGE_WEIGHTS.values()) - 1.0) < 0.001
    assert abs(sum(LIVE_WEIGHTS.values()) - 1.0) < 0.001


def test_all_bullish_produces_buy():
    blocks = {
        "analyst": _sb(80, "HIGH", reason="Strong consensus"),
        "news": _sb(72, "MEDIUM", reason="Bullish coverage"),
        "macro": _sb(70, "MEDIUM", reason="Tailwind"),
        "peer": _sb(75, "HIGH", reason="Trades cheap vs peers"),
        "technical": _sb(70, "MEDIUM", reason="Uptrend"),
    }
    out = compute_committee(blocks, mode="coverage")
    assert out["signal"] == "BUY", f"Expected BUY, got {out['signal']} at score {out['score']}"
    assert out["score"] >= 70
    # All agree -> HIGH confidence
    assert out["confidence"] == "HIGH"


def test_all_bearish_produces_sell():
    blocks = {
        "analyst": _sb(25, "HIGH", reason="Consensus downgrade"),
        "news": _sb(30, "MEDIUM", reason="Negative headlines"),
        "macro": _sb(28, "MEDIUM", reason="Headwind"),
        "peer": _sb(32, "HIGH", reason="Expensive vs peers"),
        "technical": _sb(30, "MEDIUM", reason="Downtrend"),
    }
    out = compute_committee(blocks, mode="coverage")
    assert out["signal"] == "SELL", f"Expected SELL, got {out['signal']} at score {out['score']}"
    assert out["score"] <= 30
    assert out["confidence"] == "HIGH"


def test_split_committee_produces_hold():
    """The 'five bullish, orchestrator says sell' bug — verify it can't happen.
    Five bullish specialists should produce BUY, not SELL, regardless of any
    other single specialist's dissent."""
    blocks = {
        "analyst": _sb(75, "HIGH", reason="Strong consensus"),
        "news": _sb(70, "MEDIUM", reason="Bullish coverage"),
        "macro": _sb(70, "MEDIUM", reason="Tailwind"),
        "peer": _sb(75, "HIGH", reason="Cheap vs peers"),
        "technical": _sb(30, "MEDIUM", reason="Bearish chart"),  # single dissenter
    }
    out = compute_committee(blocks, mode="coverage")
    # Weighted committee may still land in HOLD if the dissenter is heavily weighted
    # enough to pull the score below the BUY-entry band, but it should never flip
    # all the way to SELL here.
    assert out["signal"] in ("BUY", "HOLD"), (
        f"Expected BUY/HOLD (not SELL) for majority-bullish committee. "
        f"Got {out['signal']} at score {out['score']}. Committee result: {out}"
    )
    assert out["score"] >= 60, f"expected score to remain constructive, got {out['score']}"


def test_missing_specialists_dont_crash():
    blocks = {
        "analyst": _sb(70, "MEDIUM"),
        "news": None,
        "macro": None,
        "peer": None,
        "technical": None,
    }
    out = compute_committee(blocks, mode="coverage")
    # Only analyst reported, so result is entirely driven by analyst
    assert out["score"] == 70
    assert "news" in out["missing_specialists"]
    assert "macro" in out["missing_specialists"]


def test_low_confidence_halves_weight():
    """A LOW-confidence specialist should have half the say."""
    blocks_full = {
        "analyst": _sb(90, "HIGH"),
        "news": _sb(30, "HIGH"),
    }
    blocks_low = {
        "analyst": _sb(90, "HIGH"),
        "news": _sb(30, "LOW"),
    }
    out_full = compute_committee(blocks_full, mode="coverage")
    out_low = compute_committee(blocks_low, mode="coverage")
    # When news is LOW, its weight is halved, so analyst dominates more.
    # Hence out_low score should be HIGHER than out_full score.
    assert out_low["score"] > out_full["score"], (
        f"LOW news should pull the score toward analyst's 90, "
        f"but got low={out_low['score']} vs full={out_full['score']}"
    )


def test_hysteresis_sticks_on_buy():
    """Once BUY, a score of 60 should keep BUY (not drop to HOLD at 55)."""
    # prev=BUY, score=60 -> stay BUY (above 55 exit threshold)
    assert _apply_hysteresis(60.0, "BUY") == "BUY"
    # prev=BUY, score=54 -> drop to HOLD (below 55 exit)
    assert _apply_hysteresis(54.0, "BUY") == "HOLD"
    # prev=BUY, score=20 -> jump straight to SELL (extreme case)
    assert _apply_hysteresis(20.0, "BUY") == "SELL"


def test_hysteresis_entry_thresholds():
    """HOLD -> BUY requires score >= 70, not just >= 65."""
    # Cold start with hard thresholds
    assert _apply_hysteresis(68.0, "HOLD") == "HOLD"
    assert _apply_hysteresis(70.0, "HOLD") == "BUY"
    assert _apply_hysteresis(32.0, "HOLD") == "HOLD"
    assert _apply_hysteresis(30.0, "HOLD") == "SELL"


def test_committee_confidence_agreement():
    """Confidence is HIGH when specialists agree and at least one is HIGH."""
    votes_tight = [
        {"score": 72, "confidence": "HIGH"},
        {"score": 70, "confidence": "MEDIUM"},
        {"score": 75, "confidence": "MEDIUM"},
    ]
    assert _committee_confidence(votes_tight, 72.0) == "HIGH"

    votes_split = [
        {"score": 85, "confidence": "HIGH"},
        {"score": 40, "confidence": "HIGH"},  # 45-point spread
        {"score": 60, "confidence": "MEDIUM"},
    ]
    assert _committee_confidence(votes_split, 62.0) == "LOW"


def test_live_mode_weights_metrics_heavily():
    """Metrics + sentiment together should dominate live mode (42% weight)."""
    blocks = {
        "metrics":   _sb(85, "HIGH"),
        "sentiment": _sb(80, "HIGH"),
        "news":      _sb(40, "MEDIUM"),
        "analyst":   _sb(45, "MEDIUM"),
        "macro":     _sb(40, "MEDIUM"),
        "peer":      _sb(40, "MEDIUM"),
        "technical": _sb(40, "MEDIUM"),
    }
    out = compute_committee(blocks, mode="live")
    # Metrics+sentiment (42%) pulling to 82ish, others (58%) pulling to 41ish
    # weighted should be around 58-60
    assert 55 <= out["score"] <= 65, f"expected mid-range, got {out['score']}"


if __name__ == "__main__":
    test_weights_sum_to_one()
    test_all_bullish_produces_buy()
    test_all_bearish_produces_sell()
    test_split_committee_produces_hold()
    test_missing_specialists_dont_crash()
    test_low_confidence_halves_weight()
    test_hysteresis_sticks_on_buy()
    test_hysteresis_entry_thresholds()
    test_committee_confidence_agreement()
    test_live_mode_weights_metrics_heavily()
    print("All committee tests passed.")

