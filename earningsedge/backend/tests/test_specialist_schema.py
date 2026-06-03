"""Quick sanity checks for the specialist score envelope."""
import os
import sys

# Allow running as a plain script: `python tests/test_specialist_schema.py`
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from agents.specialist_schema import make_score_block, make_driver


def test_score_block_clamps():
    sb = make_score_block(score=150)
    assert sb["score"] == 100, f"expected 100, got {sb['score']}"
    assert sb["label"] == "bullish"

    sb = make_score_block(score=-20)
    assert sb["score"] == 0
    assert sb["label"] == "bearish"


def test_score_block_label_thresholds():
    assert make_score_block(65)["label"] == "bullish"
    assert make_score_block(64)["label"] == "neutral"
    assert make_score_block(36)["label"] == "neutral"
    assert make_score_block(35)["label"] == "bearish"


def test_score_block_confidence_normalization():
    assert make_score_block(50, confidence="weird")["confidence"] == "MEDIUM"
    assert make_score_block(50, confidence="high")["confidence"] == "HIGH"
    assert make_score_block(50, confidence="  low  ")["confidence"] == "LOW"


def test_score_block_handles_nonnumeric_score():
    sb = make_score_block(score="not a number")
    assert sb["score"] == 50
    assert sb["label"] == "neutral"


def test_driver_normalization():
    d = make_driver("RSI 72", direction="BULLISH", weight=1.5)
    assert d["direction"] == "bullish"
    assert d["weight"] == 1.0

    d = make_driver("", direction="nonsense", weight=-0.5)
    assert d["direction"] == "neutral"
    assert d["weight"] == 0.0


def test_score_block_drivers_clipped_to_eight():
    drivers = [make_driver(f"d{i}", "bullish", 0.5) for i in range(20)]
    sb = make_score_block(70, drivers=drivers)
    assert len(sb["drivers"]) == 8


if __name__ == "__main__":
    test_score_block_clamps()
    test_score_block_label_thresholds()
    test_score_block_confidence_normalization()
    test_score_block_handles_nonnumeric_score()
    test_driver_normalization()
    test_score_block_drivers_clipped_to_eight()
    print("All specialist_schema tests passed.")

"""Quick sanity checks for the specialist score envelope."""
import sys
from pathlib import Path

_backend_root = Path(__file__).resolve().parent.parent
if str(_backend_root) not in sys.path:
    sys.path.insert(0, str(_backend_root))

from agents.specialist_schema import make_driver, make_score_block


def test_score_block_clamps():
    sb = make_score_block(score=150)
    assert sb["score"] == 100
    assert sb["label"] == "bullish"

    sb = make_score_block(score=-20)
    assert sb["score"] == 0
    assert sb["label"] == "bearish"


def test_score_block_label_thresholds():
    assert make_score_block(65)["label"] == "bullish"
    assert make_score_block(64)["label"] == "neutral"
    assert make_score_block(35)["label"] == "bearish"
    assert make_score_block(36)["label"] == "neutral"


def test_score_block_confidence_normalization():
    assert make_score_block(50, confidence="weird")["confidence"] == "MEDIUM"
    assert make_score_block(50, confidence="high")["confidence"] == "HIGH"


def test_driver_normalization():
    d = make_driver("RSI 72", direction="BULLISH", weight=1.5)
    assert d["direction"] == "bullish"
    assert d["weight"] == 1.0

    d = make_driver("", direction="nonsense", weight=-0.5)
    assert d["direction"] == "neutral"
    assert d["weight"] == 0.0


if __name__ == "__main__":
    test_score_block_clamps()
    test_score_block_label_thresholds()
    test_score_block_confidence_normalization()
    test_driver_normalization()
    print("All specialist_schema tests passed.")
