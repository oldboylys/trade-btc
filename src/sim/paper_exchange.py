"""纸交易所：实现 IExchange 接口，内部用撮合引擎与持仓账本."""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Callable, Optional

from src.connectors.base import IExchange
from src.core.clock import get_clock
from src.core.models import (
    AccountBalance, Exchange, Fill, FundingRate, Kline,
    MarkPrice, Order, OrderBook, OrderSide, OrderStatus, OrderType,
    Position, PositionSide, Symbol,
)
from src.sim.order_book import PaperMatchingEngine
from src.sim.position_book import PositionBook
from src.sim.slippage import FeeModel, SlippageModel


class PaperExchange(IExchange):
    """
    纸交易所：完整实现 IExchange，不与真实交易所通信。
    行情通过 on_kline / on_orderbook 方法注入。
    """

    def __init__(
        self,
        initial_balance: Decimal = Decimal("100000"),
        fee_model: FeeModel | None = None,
        slippage_model: SlippageModel | None = None,
    ) -> None:
        self._balance = initial_balance
        self._position_book = PositionBook(Exchange.SIM)
        self._engine = PaperMatchingEngine(
            exchange=Exchange.SIM,
            fee_model=fee_model or FeeModel(),
            slippage_model=slippage_model or SlippageModel(),
            on_fill=self._on_fill,
            on_order_update=self._on_order_update,
        )
        self._orders: dict[str, Order] = {}
        self._mark_prices: dict[str, Decimal] = {}
        self._connected = False

        self._order_callbacks: list[Callable[[Order], None]] = []
        self._position_callbacks: list[Callable[[Position], None]] = []

    # ---- 行情注入 ----

    def feed_kline(self, kline: Kline) -> None:
        self._mark_prices[kline.symbol] = kline.close
        self._position_book.update_mark_price(kline.symbol, kline.close)
        self._engine.on_kline(kline)

    def feed_orderbook(self, ob: OrderBook) -> None:
        if ob.mid_price:
            self._mark_prices[ob.symbol] = ob.mid_price
            self._position_book.update_mark_price(ob.symbol, ob.mid_price)
        self._engine.on_orderbook(ob)

    # ---- IExchange 实现 ----

    async def get_balance(self, asset: str = "USDT") -> AccountBalance:
        upnl = self._position_book.total_unrealized_pnl()
        return AccountBalance(
            exchange=Exchange.SIM,
            asset=asset,
            total=self._balance + upnl,
            available=self._balance,
            unrealized_pnl=upnl,
        )

    async def get_position(
        self, symbol: str, side: PositionSide = PositionSide.BOTH
    ) -> Optional[Position]:
        entry = self._position_book.get_position(symbol)
        if entry and entry.qty > 0:
            return entry.to_position()
        return None

    async def get_positions(self) -> list[Position]:
        return [e.to_position() for e in self._position_book.get_all_positions()]

    async def place_order(self, order: Order) -> Order:
        if not order.client_order_id:
            order.client_order_id = str(uuid.uuid4())
        order.exchange = Exchange.SIM
        self._orders[order.client_order_id] = order
        self._engine.submit_order(order)
        return order

    async def cancel_order(self, symbol: str, order_id: str) -> Order:
        result = self._engine.cancel_order(order_id)
        if result is None:
            order = self._orders.get(order_id)
            if order:
                order.status = OrderStatus.CANCELED
                return order
            raise ValueError(f"Order {order_id} not found")
        return result

    async def get_order(self, symbol: str, order_id: str) -> Order:
        order = self._orders.get(order_id)
        if order is None:
            raise ValueError(f"Order {order_id} not found")
        return order

    async def get_open_orders(self, symbol: str | None = None) -> list[Order]:
        orders = self._engine.open_orders
        if symbol:
            orders = [o for o in orders if o.symbol == symbol]
        return orders

    async def get_symbol_info(self, symbol: str) -> Symbol:
        return Symbol(
            base=symbol.replace("USDT", ""),
            quote="USDT",
            exchange=Exchange.SIM,
            raw_symbol=symbol,
        )

    async def get_klines(
        self, symbol: str, interval: str,
        start_ms: int | None = None, end_ms: int | None = None,
        limit: int = 500,
    ) -> list[Kline]:
        return []

    async def get_orderbook(self, symbol: str, depth: int = 20) -> OrderBook:
        return OrderBook(symbol=symbol, exchange=Exchange.SIM, ts_ms=get_clock().now_ms())

    async def get_funding_rate(self, symbol: str) -> FundingRate:
        return FundingRate(
            symbol=symbol, exchange=Exchange.SIM,
            rate=Decimal("0.0001"), ts_ms=get_clock().now_ms()
        )

    async def get_mark_price(self, symbol: str) -> MarkPrice:
        price = self._mark_prices.get(symbol, Decimal("0"))
        return MarkPrice(
            symbol=symbol, exchange=Exchange.SIM,
            mark_price=price, index_price=price,
            ts_ms=get_clock().now_ms(),
        )

    async def subscribe_klines(self, symbol, interval, callback):
        pass  # 行情通过 feed_kline 注入

    async def subscribe_orderbook(self, symbol, callback):
        pass

    async def subscribe_trades(self, symbol, callback):
        pass

    async def subscribe_orders(self, callback):
        self._order_callbacks.append(callback)

    async def subscribe_positions(self, callback):
        self._position_callbacks.append(callback)

    async def subscribe_funding_rate(self, symbol, callback):
        pass

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ---- 内部回调 ----

    def _on_fill(self, fill: Fill) -> None:
        self._position_book.on_fill(fill)
        notional = fill.price * fill.qty
        if fill.side == OrderSide.BUY:
            self._balance -= notional + fill.fee
        else:
            self._balance += notional - fill.fee

    def _on_order_update(self, order: Order) -> None:
        self._orders[order.client_order_id] = order
        for cb in self._order_callbacks:
            cb(order)

    # ---- 便捷属性 ----

    @property
    def position_book(self) -> PositionBook:
        return self._position_book

    @property
    def balance(self) -> Decimal:
        return self._balance
