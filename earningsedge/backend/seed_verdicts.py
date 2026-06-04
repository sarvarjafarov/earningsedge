"""Seed sample past verdicts so Atlas Vector Search has something to match.

Run once after wiring MONGODB_URI:
    python seed_verdicts.py

Inserts ~8 realistic historical-style verdicts across NVDA, MSFT, TSLA,
AAPL, AMD, GOOGL, META. Each is embedded with Gemini text-embedding-004
so a search against current call language ("compute capacity",
"margin compression", "beat-and-raise") returns relevant prior takes.

Idempotent: looks at existing seed-marked docs and only inserts ones
that aren't already present.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time

from dotenv import load_dotenv

load_dotenv("../.env")
sys.path.insert(0, ".")

from vector_memory import remember_verdict, ensure_index  # noqa: E402

SEED_VERDICTS = [
    {
        "ticker": "NVDA",
        "action": "Add",
        "score": 78,
        "confidence": "HIGH",
        "text": (
            "Q3 2024 — Bull case held: data-center revenue +94% Y/Y, Blackwell ramp ahead "
            "of guide, AI-factory CapEx commentary from hyperscalers re-rated the multiple. "
            "Quant flagged PEG 0.7 vs sector 1.4 — extreme cheap on growth-adjusted. Bear "
            "flagged China export-control overhang as the only real risk; sized around it. "
            "Sized to upper bound of position."
        ),
        "sources": ["NVDA Q3 2024 earnings call", "Jensen at GTC 2024"],
        "ts": 1729900000000,
        "seed": True,
    },
    {
        "ticker": "NVDA",
        "action": "Trim",
        "score": 42,
        "confidence": "MEDIUM",
        "text": (
            "Q1 2024 — CFO's compute-capacity language got cautious mid-call: 'supply "
            "remains constrained through year-end' shifted to 'supply situation has eased' "
            "Q&A. Bull saw this as positive — more deliveries. Bear (correct call) flagged "
            "it as a peak-pricing signal. Stock dropped 6.2% in 7 days post-print. "
            "Lesson: compute-capacity language is a tell."
        ),
        "sources": ["NVDA Q1 2024 earnings call"],
        "ts": 1714600000000,
        "seed": True,
    },
    {
        "ticker": "MSFT",
        "action": "Hold",
        "score": 55,
        "confidence": "MEDIUM",
        "text": (
            "Q2 2024 — Azure growth 30% met but did not exceed; CapEx guide raised to "
            "$80B which pressured FCF. Bull pointed to AI workload migration as durable. "
            "Bear flagged margin compression from accelerated depreciation on AI servers. "
            "Quant called it fair — PE 35 with growth decelerating from 35% to 28%. "
            "Net: no edge either way, sit."
        ),
        "sources": ["MSFT Q2 2024 earnings call"],
        "ts": 1706800000000,
        "seed": True,
    },
    {
        "ticker": "TSLA",
        "action": "Avoid",
        "score": 22,
        "confidence": "HIGH",
        "text": (
            "Q4 2023 — Worst call in two years. CFO dodged margin questions twice. "
            "Cybertruck production miss not addressed. Bear had this nailed: 'demand "
            "weakness across the lineup' was the takeaway. Stock dropped 12% in 5 days. "
            "Bull's narrative about 'energy storage will save the margin' did not survive "
            "Q&A. Avoid until evidence the demand backdrop has stabilised."
        ),
        "sources": ["TSLA Q4 2023 earnings call"],
        "ts": 1706000000000,
        "seed": True,
    },
    {
        "ticker": "AAPL",
        "action": "Hold",
        "score": 50,
        "confidence": "LOW",
        "text": (
            "Q4 2024 — Services growth 13% beat; iPhone in China down 7% vs consensus "
            "down 9% — modest beat on the most-bearish-line item. CapEx commentary "
            "muted. Bull happy with services margin. Bear unhappy with China line. "
            "Quant says fairly valued. No conviction either way; hold."
        ),
        "sources": ["AAPL Q4 2024 earnings call"],
        "ts": 1730500000000,
        "seed": True,
    },
    {
        "ticker": "AMD",
        "action": "Add",
        "score": 71,
        "confidence": "MEDIUM",
        "text": (
            "Q3 2024 — MI300 data-center revenue $3.5B vs consensus $3.1B. CFO guided "
            "MI325 ramp ahead of schedule. Bull says AMD is finally a credible #2 in "
            "AI accelerators. Bear flags client PC weakness as a drag. Quant calls "
            "PEG 0.9 vs NVDA 0.7 — close enough to NVDA on growth-adjusted multiple "
            "to be interesting. Add small."
        ),
        "sources": ["AMD Q3 2024 earnings call"],
        "ts": 1730000000000,
        "seed": True,
    },
    {
        "ticker": "GOOGL",
        "action": "Add",
        "score": 68,
        "confidence": "MEDIUM",
        "text": (
            "Q3 2024 — Search revenue +12% Y/Y, ahead of fears about AI-overview "
            "cannibalization. Cloud +35% accelerating. CapEx guide $80B raised concern. "
            "Bull says AI infrastructure spend pays for itself if cloud growth holds. "
            "Bear says 'show me FCF'. Quant: 23x earnings, fairly priced on growth. "
            "Modest add."
        ),
        "sources": ["GOOGL Q3 2024 earnings call"],
        "ts": 1730300000000,
        "seed": True,
    },
    {
        "ticker": "META",
        "action": "Add",
        "score": 73,
        "confidence": "HIGH",
        "text": (
            "Q3 2024 — Revenue +19% Y/Y, ad pricing +11%. AI-driven targeting credited. "
            "Reality Labs loss widened to $4.4B but Zuckerberg's tone shifted from "
            "'embrace it' to 'we are evaluating'. Bull says ad business compensates. "
            "Bear says Reality Labs is the bear case. Quant says PE 25 with 19% top-line "
            "growth is the bull case. Add."
        ),
        "sources": ["META Q3 2024 earnings call"],
        "ts": 1730100000000,
        "seed": True,
    },
    {
        "ticker": "PLTR",
        "action": "Add",
        "score": 81,
        "confidence": "HIGH",
        "text": (
            "Q3 2024 — Bull case held with high conviction. Cathie Wood-style read: "
            "AIP (Artificial Intelligence Platform) deal closures accelerated to 104 "
            "vs 65 prior, with mid-market wins broadening beyond government — exactly "
            "the disruptive-innovation platform transition (defense → enterprise AI) "
            "that drives Wright's-Law unit-economics improvement through 2030. "
            "Druckenmiller-style setup analysis flagged the asymmetric upside: "
            "concentrated position warranted given the multi-year revenue durability. "
            "Burry-style dissent on the multiple (PE 220) was loud but does not "
            "anchor on TAM expansion. Memory: this rhymes with NVDA Q3 2023 — "
            "platform-thesis verdicts where the multiple is paying for visible "
            "5-year growth tend to compound rather than compress. Add with "
            "conviction; trim only on a thesis break in deal-closure velocity."
        ),
        "sources": ["PLTR Q3 2024 earnings call", "AIP partner pipeline disclosure"],
        "ts": 1730500000000,
        "seed": True,
    },
    {
        "ticker": "NFLX",
        "action": "Trim",
        "score": 42,
        "confidence": "MEDIUM",
        "text": (
            "Q4 2024 — Jim Cramer-style read on the narrative: ad-tier subscriber "
            "additions are the headline the market wants, but management's tone on "
            "content spend was defensive — 'we will be disciplined' said three times "
            "in fifteen minutes of Q&A. Cramer's instinct: when management defends a "
            "line that wasn't being attacked, it's because they know it will be. "
            "Howard Marks-style cycle read: streaming-content cycle is late — peak "
            "subscriber concentration in mature markets, content amortization "
            "beginning to compress operating margin even as revenue grows. Burry "
            "flagged the same: gross margin off 70 bps fourth straight quarter even "
            "as the headline beat. Trim into strength; this is not the cycle to chase "
            "the multiple."
        ),
        "sources": ["NFLX Q4 2024 earnings call"],
        "ts": 1730800000000,
        "seed": True,
    },
    {
        "ticker": "AMZN",
        "action": "Add",
        "score": 76,
        "confidence": "HIGH",
        "text": (
            "Q3 2024 — Druckenmiller-style concentrated-bet setup. AWS growth "
            "re-accelerated to 19.1% from 17.4% — the macro AI-infrastructure cycle "
            "is mid-cycle and Amazon's capex acceleration ($75B+ guided) is the "
            "company's most aggressive in a decade. Cathie Wood-style innovation "
            "thesis: generative AI revenue contribution is now visible across "
            "Bedrock and Trainium — AWS is no longer the laggard in AI inference. "
            "Marks-style cycle warning is the dissent: capex peaking calls usually "
            "precede multiple compression by 12 months. Net: the 6-12 month setup "
            "is asymmetric to the upside; the 24-month tail risk is real but distant. "
            "Add at this multiple; revisit on first capex-deceleration signal."
        ),
        "sources": ["AMZN Q3 2024 earnings call"],
        "ts": 1730200000000,
        "seed": True,
    },
]


async def main() -> None:
    print(f"Seeding {len(SEED_VERDICTS)} sample verdicts …")
    inserted = 0
    skipped = 0
    for v in SEED_VERDICTS:
        # Idempotency check via direct pymongo (cheap)
        from pymongo import MongoClient
        import certifi
        c = MongoClient(os.environ["MONGODB_URI"], tlsCAFile=certifi.where(), serverSelectionTimeoutMS=10000)
        coll = c[os.getenv("MONGODB_DB", "earningsedge")]["verdicts"]
        if coll.count_documents({"ticker": v["ticker"], "ts": v["ts"], "seed": True}) > 0:
            skipped += 1
            print(f"  skip   {v['ticker']:6} (already seeded)")
            continue
        res = await remember_verdict(v)
        if res.get("ok"):
            inserted += 1
            print(f"  insert {v['ticker']:6} embed={res.get('embedded')}")
        else:
            print(f"  FAIL   {v['ticker']:6} {res.get('error')}")

    print(f"\nInserted {inserted}, skipped {skipped}.")
    print("Building Vector Search index …")
    res = await ensure_index()
    print(res)
    print("\nDone. The index takes ~30 s to become queryable on Atlas free tier.")
    print("Sleep ~45 s before calling /api/vector/search.")


if __name__ == "__main__":
    asyncio.run(main())
