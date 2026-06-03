"""Sanity checks for the new news aggregator — no LLM required."""
import os
import sys
import time

# Allow running as a plain script: `python tests/test_news_aggregation.py`
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from news_classification import (
    aggregate_news_records,
    recency_weight,
)


def _fresh_ts(days_ago: int = 0) -> int:
    return int(time.time()) - days_ago * 86400


def test_recency_weight_basics():
    now = int(time.time())
    assert recency_weight(now, now_ts=now) == 1.0
    assert recency_weight(now - 7 * 86400, now_ts=now) == 0.5
    assert recency_weight(now - 14 * 86400, now_ts=now) == 0.0
    assert recency_weight(now - 30 * 86400, now_ts=now) == 0.0
    assert recency_weight(None) == 0.5


def test_major_bearish_outweighs_minor_bullish():
    """One major bearish legal item should beat five minor bullish items."""
    recs = [
        {"label": "bearish", "event_type": "LEGAL_REGULATORY",
         "magnitude": "major", "timeframe": "weeks", "confidence": 0.9},
    ] + [
        {"label": "bullish", "event_type": "PRODUCT",
         "magnitude": "minor", "timeframe": "quarters", "confidence": 0.6}
        for _ in range(5)
    ]
    dts = [_fresh_ts(0)] * 6
    label, rationale, tilt, extras = aggregate_news_records(recs, dts)
    assert label == "bearish", f"expected bearish, got {label} ({rationale})"
    assert tilt < 0


def test_stale_news_loses_weight():
    """Fresh bullish news beats 14-day-old bearish news of same magnitude."""
    recs = [
        {"label": "bullish", "event_type": "EARNINGS",
         "magnitude": "material", "timeframe": "weeks", "confidence": 0.8},
        {"label": "bearish", "event_type": "EARNINGS",
         "magnitude": "material", "timeframe": "weeks", "confidence": 0.8},
    ]
    dts = [_fresh_ts(0), _fresh_ts(13)]  # bearish is ~13 days old
    label, _, tilt, _ = aggregate_news_records(recs, dts)
    assert label == "bullish"
    assert tilt > 0


def test_dedup_collapses_repeats():
    """Three outlets covering the same bearish downgrade shouldn't triple-count."""
    recs = [
        {"label": "bearish", "event_type": "ANALYST_ACTION",
         "magnitude": "material", "timeframe": "today", "confidence": 0.8}
        for _ in range(3)
    ]
    dts = [_fresh_ts(0)] * 3
    label, _, tilt, extras = aggregate_news_records(recs, dts)
    assert label == "bearish"
    assert extras["deduplicated_count"] == 1


def test_all_neutral_returns_neutral():
    recs = [
        {"label": "neutral", "event_type": "OTHER",
         "magnitude": "minor", "timeframe": "weeks", "confidence": 0.5}
        for _ in range(5)
    ]
    dts = [_fresh_ts(0)] * 5
    label, _, tilt, _ = aggregate_news_records(recs, dts)
    assert label == "neutral"
    assert abs(tilt) < 0.01


if __name__ == "__main__":
    test_recency_weight_basics()
    test_major_bearish_outweighs_minor_bullish()
    test_stale_news_loses_weight()
    test_dedup_collapses_repeats()
    test_all_neutral_returns_neutral()
    print("All news_aggregation tests passed.")

