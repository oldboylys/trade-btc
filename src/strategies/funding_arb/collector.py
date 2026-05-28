"""资金费率数据采集与标准化."""
from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from typing import Optional

from src.connectors.base import IExchange
from src.core.logging import get_logger
from src.core.models import Exchange, FundingRate

logger = get_logger("funding_arb.collector")


class FundingRateSnapshot:
    """多交易所资金费率快照."""

    def __init__(self) -> None:
        self._rates: dict[tuple[str, str], FundingRate] = {}  # (exchange, symbol) -> rate

    def update(self, rate: FundingRate) -> None:
        self._rates[(rate.exchange.value, rate.symbol)] = rate

    def get(self, exchange: Exchange, symbol: str) -> Optional[FundingRate]:
        return self._rates.get((exchange.value, symbol))

    def get_all_for_symbol(self, symbol: str) -> list[FundingRate]:
        return [
            r for (ex, sym), r in self._rates.items()
            if sym == symbol
        ]

    def max_spread(self, symbol: str) -> tuple[Optional[FundingRate], Optional[FundingRate], Decimal]:
        """返回资金费率最高和最低的交易所，以及利差."""
        rates = self.get_all_for_symbol(symbol)
        if len(rates) < 2:
            return None, None, Decimal("0")
        sorted_rates = sorted(rates, key=lambda r: r.rate, reverse=True)
        high = sorted_rates[0]
        low = sorted_rates[-1]
        spread = high.rate - low.rate
        return high, low, spread


class FundingRateCollector:
    """定期从各交易所拉取资金费率并更新快照."""

    def __init__(
        self,
        snapshot: FundingRateSnapshot,
        poll_interval: float = 30.0,
    ) -> None:
        self.snapshot = snapshot
        self.poll_interval = poll_interval
        self._exchanges: dict[str, tuple[IExchange, list[str]]] = {}
        self._running = False

    def register(self, name: str, exchange: IExchange, symbols: list[str]) -> None:
        self._exchanges[name] = (exchange, symbols)

    async def start(self) -> None:
        self._running = True
        asyncio.create_task(self._poll_loop())
        logger.info("funding_rate_collector_started")

    async def stop(self) -> None:
        self._running = False

    async def _poll_loop(self) -> None:
        while self._running:
            await self._poll_once()
            await asyncio.sleep(self.poll_interval)

    async def _poll_once(self) -> None:
        for name, (exchange, symbols) in self._exchanges.items():
            for symbol in symbols:
                try:
                    rate = await exchange.get_funding_rate(symbol)
                    self.snapshot.update(rate)
                    logger.debug(
                        "funding_rate_updated",
                        exchange=name,
                        symbol=symbol,
                        rate=float(rate.rate),
                    )
                except Exception as exc:
                    logger.warning("funding_rate_fetch_failed",
                                   exchange=name, symbol=symbol, error=str(exc))
