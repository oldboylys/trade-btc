"""行情服务：订阅多交易所/多品种/多周期，聚合后落盘并发布事件."""
from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Callable

from src.connectors.base import IExchange
from src.core.events import get_bus
from src.core.logging import get_logger
from src.core.models import Exchange, Kline, OrderBook, Trade
from src.marketdata.aggregator import KlineAggregator
from src.marketdata.storage import MarketDataStorage

logger = get_logger("marketdata.feed")

# 事件类型
EVT_KLINE = "kline"
EVT_KLINE_CLOSED = "kline_closed"
EVT_ORDERBOOK = "orderbook"
EVT_TRADE = "trade"


class MarketDataFeed:
    def __init__(
        self,
        storage: MarketDataStorage,
        target_intervals: list[str] | None = None,
    ) -> None:
        self.storage = storage
        self.target_intervals = target_intervals or ["1m", "5m", "15m", "1h"]
        self._aggregators: dict[str, KlineAggregator] = {}
        self._exchanges: dict[str, IExchange] = {}
        self._tasks: list[asyncio.Task] = []

    def register_exchange(self, name: str, exchange: IExchange) -> None:
        self._exchanges[name] = exchange

    def _get_aggregator(self, symbol: str, exchange: Exchange) -> KlineAggregator:
        key = f"{exchange.value}:{symbol}"
        if key not in self._aggregators:
            agg = KlineAggregator(
                symbol=symbol,
                exchange=exchange,
                target_intervals=self.target_intervals,
                on_kline_closed=self._on_kline_closed,
            )
            self._aggregators[key] = agg
        return self._aggregators[key]

    def _on_kline_closed(self, kline: Kline) -> None:
        bus = get_bus()
        bus.publish_nowait(EVT_KLINE_CLOSED, kline)
        asyncio.ensure_future(self.storage.save_kline(kline))

    async def subscribe(
        self, exchange_name: str, symbol: str, interval: str = "1m"
    ) -> None:
        ex = self._exchanges.get(exchange_name)
        if ex is None:
            raise ValueError(f"Exchange {exchange_name} not registered")

        agg = self._get_aggregator(symbol, Exchange(exchange_name))

        async def on_kline(kline: Kline) -> None:
            closed = agg.feed(kline)
            bus = get_bus()
            await bus.publish(EVT_KLINE, kline)
            for ck in closed:
                await bus.publish(EVT_KLINE_CLOSED, ck)

        async def on_orderbook(ob: OrderBook) -> None:
            bus = get_bus()
            await bus.publish(EVT_ORDERBOOK, ob)

        await ex.subscribe_klines(symbol, interval, callback=lambda k: asyncio.ensure_future(on_kline(k)))
        await ex.subscribe_orderbook(symbol, callback=lambda ob: asyncio.ensure_future(on_orderbook(ob)))
        logger.info("subscribed", exchange=exchange_name, symbol=symbol, interval=interval)

    async def start(self) -> None:
        await self.storage.connect()
        logger.info("market_data_feed_started")

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await self.storage.close()
        logger.info("market_data_feed_stopped")


class HistoricalDataLoader:
    """从数据库读取历史K线，用于回测驱动."""

    def __init__(self, storage: MarketDataStorage) -> None:
        self.storage = storage

    async def load(
        self,
        symbol: str,
        exchange: Exchange,
        interval: str,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> list[Kline]:
        return await self.storage.load_klines(
            symbol=symbol,
            exchange=exchange,
            interval=interval,
            start_ms=start_ms,
            end_ms=end_ms,
        )


class BacktestDriver:
    """回测驱动器：顺序回放历史K线，触发策略回调."""

    def __init__(self, storage: MarketDataStorage) -> None:
        self.storage = storage

    async def run(
        self,
        symbol: str,
        exchange: Exchange,
        interval: str,
        on_kline: Callable[[Kline], None],
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> int:
        klines = await self.storage.load_klines(
            symbol=symbol, exchange=exchange, interval=interval,
            start_ms=start_ms, end_ms=end_ms,
        )
        for kline in klines:
            on_kline(kline)
        logger.info("backtest_replay_done", count=len(klines))
        return len(klines)
