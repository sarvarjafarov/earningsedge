"""Retired module.

The cascading base-signal + veto model has been replaced by the weighted
committee engine in agents.committee. This module now only preserves the
`_strip` helper for MetricsAgent's score-block computation.

Any import of compute_base_trade_signal / compute_coverage_trade_signal /
reconcile_with_dashboards from this module is an error — use
agents.committee.compute_committee instead.
"""
from __future__ import annotations

from typing import Any


def _strip(value: Any) -> str:
    """Normalize a numeric-string like '$22.1B' to a plain numeric string.

    Used by MetricsAgent to compare reported vs estimated values for
    surprise computation.
    """
    if value is None:
        return "0"
    s = str(value).replace("$", "").replace(",", "").replace("%", "").strip()
    multiplier = 1.0
    if s.lower().endswith("b"):
        multiplier = 1e9
        s = s[:-1]
    elif s.lower().endswith("m"):
        multiplier = 1e6
        s = s[:-1]
    elif s.lower().endswith("t"):
        multiplier = 1e12
        s = s[:-1]
    try:
        return str(float(s) * multiplier)
    except ValueError:
        return "0"
