"""统一交易所接口抽象层 IExchange."""
from __future__ import annotations

import abc
from decimal import Decimal
from typing import AsyncIterator, Callable, Optional

from src.core.models import (
    AccountBalance, Fill, FundingRate, Kline, MarkPrice,
    Order, OrderBook, OrderSide, OrderType, Position,
    PositionSide, Symbol,
)


class IExchange(abc.ABC):
    """所有交易所连接器必须实现此接口."""

    # -------- REST: 账户/订单 --------

    @abc.abstractmethod
    async def get_balance(self, asset: str = "USDT") -> AccountBalance:
        """获取资产余额."""

    @abc.abstractmethod
    async def get_position(
        self, symbol: str, side: PositionSide = PositionSide.BOTH
    ) -> Optional[Position]:
        """获取当前持仓."""

    @abc.abstractmethod
    async def get_positions(self) -> list[Position]:
        """获取所有持仓."""

    @abc.abstractmethod
    async def place_order(self, order: Order) -> Order:
        """下单，返回携带 exchange_order_id 的 Order."""

    @abc.abstractmethod
    async def cancel_order(self, symbol: str, order_id: str) -> Order:
        """撤单."""

    @abc.abstractmethod
    async def get_order(self, symbol: str, order_id: str) -> Order:
        """查询订单状态."""

    @abc.abstractmethod
    async def get_open_orders(self, symbol: str | None = None) -> list[Order]:
        """获取所有挂单."""

    @abc.abstractmethod
    async def get_symbol_info(self, symbol: str) -> Symbol:
        """获取交易规则."""

    # -------- REST: 行情 --------

    @abc.abstractmethod
    async def get_klines(
        self, symbol: str, interval: str,
        start_ms: int | None = None, end_ms: int | None = None,
        limit: int = 500,
    ) -> list[Kline]:
        """获取历史K线."""

    @abc.abstractmethod
    async def get_orderbook(self, symbol: str, depth: int = 20) -> OrderBook:
        """获取当前盘口."""

    @abc.abstractmethod
    async def get_funding_rate(self, symbol: str) -> FundingRate:
        """获取资金费率."""

    @abc.abstractmethod
    async def get_mark_price(self, symbol: str) -> MarkPrice:
        """获取标记价格."""

    # -------- WebSocket 订阅 --------

    @abc.abstractmethod
    async def subscribe_klines(
        self,
        symbol: str,
        interval: str,
        callback: Callable[[Kline], None],
    ) -> None:
        """订阅K线推送."""

    @abc.abstractmethod
    async def subscribe_orderbook(
        self,
        symbol: str,
        callback: Callable[[OrderBook], None],
    ) -> None:
        """订阅盘口推送."""

    @abc.abstractmethod
    async def subscribe_trades(
        self,
        symbol: str,
        callback: Callable[[Fill], None],
    ) -> None:
        """订阅成交推送."""

    @abc.abstractmethod
    async def subscribe_orders(
        self,
        callback: Callable[[Order], None],
    ) -> None:
        """订阅订单回报."""

    @abc.abstractmethod
    async def subscribe_positions(
        self,
        callback: Callable[[Position], None],
    ) -> None:
        """订阅持仓变动."""

    @abc.abstractmethod
    async def subscribe_funding_rate(
        self,
        symbol: str,
        callback: Callable[[FundingRate], None],
    ) -> None:
        """订阅资金费率更新."""

    # -------- 连接控制 --------

    @abc.abstractmethod
    async def connect(self) -> None:
        """建立连接（REST session + WS）."""

    @abc.abstractmethod
    async def disconnect(self) -> None:
        """断开所有连接."""

    @property
    @abc.abstractmethod
    def is_connected(self) -> bool:
        """连接状态."""


class ExchangeError(Exception):
    """交易所接口基础异常."""

    def __init__(
        self,
        message: str,
        code: int | None = None,
        raw: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.raw = raw


class OrderRejectedError(ExchangeError):
    """下单被拒绝."""


class InsufficientMarginError(ExchangeError):
    """保证金不足."""


class RateLimitError(ExchangeError):
    """触发限频."""


class ConnectionError(ExchangeError):
    """连接断开."""
