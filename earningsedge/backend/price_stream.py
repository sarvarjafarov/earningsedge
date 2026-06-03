"""PriceStream — polls Alpha Vantage GLOBAL_QUOTE every 60 seconds.
Free-tier friendly. Broadcasts price_tick events to the frontend.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

import yfinance as yf

BroadcastFn = Callable[[dict[str, Any]], Awaitable[None]]


class PriceStream:
    def __init__(self, broadcast: BroadcastFn) -> None:
        self.broadcast = broadcast
        self._running = False

    async def start(self, ticker: str) -> None:
        self._running = True
        sym = ticker.strip().upper()
        loop = asyncio.get_running_loop()
        while self._running:
            try:
                def _fetch_price() -> Any:
                    return yf.Ticker(sym).fast_info.last_price

                raw_price = await loop.run_in_executor(None, _fetch_price)
                if raw_price is not None:
                    await self.broadcast(
                        {
                            "type": "price_tick",
                            "data": {
                                "ticker": sym,
                                "price": float(raw_price),
                                "size": None,
                                "timestamp": None,
                            },
                        }
                    )
            except Exception:
                pass
            await asyncio.sleep(60)

    async def stop(self) -> None:
        self._running = False
