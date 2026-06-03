"""PeerAgent — one-shot peer-relative valuation score.

Runs during the coverage briefing. Uses PEG (P/E divided by revenue growth)
as the primary lens — growth-adjusted valuation is more meaningful than raw
P/E when peers have different growth rates.

Logic:
  1. Fetch competitors list (which now includes the target ticker).
  2. Compute peer median PEG (excluding target) and peer median P/E.
  3. Compare target to peer median on both axes.
  4. Undervalued if target_PEG < 0.8 * peer_median AND target is at or
     below peer median P/E.
  5. Overvalued if target_PEG > 1.3 * peer_median OR target_P/E is more
     than 1.5x peer median.
  6. Otherwise fairly valued.

Emits the standardized score_block envelope in the broadcast.
"""

from __future__ import annotations

from statistics import median
from typing import Any, Awaitable, Callable

from agents.specialist_schema import make_driver, make_score_block
from tools import get_competitors

BroadcastFn = Callable[[dict[str, Any]], Awaitable[None]]


def _num(x: Any) -> float | None:
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


class PeerAgent:
    def __init__(
        self,
        broadcast: BroadcastFn,
        ctx: dict[str, Any],
    ) -> None:
        self.broadcast = broadcast
        self.ctx = ctx

    async def run(self) -> None:
        ticker = (self.ctx.get("ticker") or "").strip().upper()
        if not ticker:
            await self.broadcast({
                "type": "status",
                "data": {
                    "state": "error",
                    "message": "peer: no ticker in context",
                },
            })
            return

        data = await get_competitors(ticker)
        if not isinstance(data, dict) or data.get("error"):
            await self.broadcast({
                "type": "peer_valuation",
                "data": {
                    "ticker": ticker,
                    "score_block": make_score_block(
                        50,
                        confidence="LOW",
                        reason="No peer data available for comparison.",
                        sample_size=0,
                        freshness="stale",
                    ),
                },
            })
            return

        peers_all = data.get("peers") or []
        target = next((p for p in peers_all if p.get("is_target")), None)
        peers = [p for p in peers_all if not p.get("is_target")]

        # Collect peer medians
        peer_pegs = [_num(p.get("peg")) for p in peers]
        peer_pegs = [v for v in peer_pegs if v is not None and v > 0]
        peer_pes = [_num(p.get("pe_ratio")) for p in peers]
        peer_pes = [v for v in peer_pes if v is not None and v > 0]

        target_peg = _num(target.get("peg")) if target else None
        target_pe = _num(target.get("pe_ratio")) if target else None

        median_peg = median(peer_pegs) if peer_pegs else None
        median_pe = median(peer_pes) if peer_pes else None

        score = 50
        drivers: list[dict[str, Any]] = []
        reason = ""

        # Build score from PEG comparison first (preferred metric)
        if target_peg is not None and median_peg is not None:
            peg_ratio = target_peg / median_peg
            if peg_ratio < 0.8:
                score += 15
                drivers.append(make_driver(
                    f"PEG {target_peg:.2f} vs peer median {median_peg:.2f} "
                    f"({peg_ratio:.0%} of peers) — growth-adjusted cheap",
                    "bullish",
                    0.8,
                ))
            elif peg_ratio > 1.3:
                score -= 15
                drivers.append(make_driver(
                    f"PEG {target_peg:.2f} vs peer median {median_peg:.2f} "
                    f"({peg_ratio:.0%} of peers) — growth-adjusted expensive",
                    "bearish",
                    0.8,
                ))
            else:
                drivers.append(make_driver(
                    f"PEG {target_peg:.2f} in line with peer median "
                    f"{median_peg:.2f}",
                    "neutral",
                    0.4,
                ))
            reason = (
                f"PEG {target_peg:.2f} vs peer median {median_peg:.2f} "
                f"across {len(peer_pegs)} peers."
            )

        # P/E divergence from the peer median is a real absolute signal that
        # always contributes to the score — not just when PEG is missing.
        # PEG normalizes for trailing growth, but a 70%+ multiple premium is
        # a meaningful overpayment risk regardless of growth justification.
        # Reviewer hit a case where target was P/E 16.1 vs peer median 9.1
        # (~77% premium) and the verdict was NEUTRAL because PEG looked OK.
        if target_pe is not None and median_pe is not None:
            pe_ratio = target_pe / median_pe
            if pe_ratio > 1.5:
                if target_peg is None or median_peg is None:
                    # PEG unavailable — the only quantitative signal we have
                    # for relative valuation, weight it heavily.
                    score -= 12
                    drivers.append(make_driver(
                        f"P/E {target_pe:.1f} vs peer median {median_pe:.1f} "
                        f"({pe_ratio:.0%}) — premium not verifiable via PEG",
                        "bearish",
                        0.6,
                    ))
                else:
                    # PEG is computed and may already say "in line" or "rich".
                    # An additional 50%+ P/E premium is still bearish — the
                    # absolute multiple matters even when growth justifies it
                    # because growth can disappoint and the multiple compresses.
                    extra_penalty = 6 if pe_ratio <= 1.7 else 10
                    score -= extra_penalty
                    drivers.append(make_driver(
                        f"P/E {target_pe:.1f} vs peer median {median_pe:.1f} "
                        f"({pe_ratio:.0%}) — absolute multiple rich vs peers",
                        "bearish",
                        0.55,
                    ))
            elif pe_ratio < 0.7:
                target_growth = _num(target.get("revenue_growth")) if target else None
                if target_growth is not None and target_growth > 0:
                    score += 8
                    drivers.append(make_driver(
                        f"P/E {target_pe:.1f} vs peer median {median_pe:.1f} "
                        f"({pe_ratio:.0%}) — discount with positive growth",
                        "bullish",
                        0.6,
                    ))
            if not reason:
                reason = (
                    f"P/E {target_pe:.1f} vs peer median {median_pe:.1f} "
                    f"across {len(peer_pes)} peers."
                )

        peer_n = len(peers)
        signals = sum([
            target_peg is not None and median_peg is not None,
            target_pe is not None and median_pe is not None,
        ])
        if signals == 2 and peer_n >= 3:
            confidence = "HIGH"
        elif signals >= 1 and peer_n >= 2:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        if not reason:
            reason = "Insufficient peer data for valuation comparison."

        score_block = make_score_block(
            score=score,
            confidence=confidence,
            reason=reason,
            drivers=drivers,
            sample_size=peer_n,
            freshness="fresh" if peer_n > 0 else "stale",
        )

        await self.broadcast({
            "type": "peer_valuation",
            "data": {
                "ticker": ticker,
                "target_peg": target_peg,
                "target_pe": target_pe,
                "median_peg": median_peg,
                "median_pe": median_pe,
                "peer_count": peer_n,
                "score_block": score_block,
            },
        })

    def stop(self) -> None:
        """No background loop — nothing to cancel."""
        pass

