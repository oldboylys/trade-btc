"""纸交易订单状态机与撮合引擎."""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Callable, Optional

from src.core.clock import get_clock
from src.core.logging import get_logger
from src.core.models import (
    Exchange, Fill, Kline, Order, OrderBook, OrderSide,
    OrderStatus, OrderType, PositionSide,
)
from src.sim.slippage import FeeModel, SlippageModel

logger = get_logger("sim.matching")


class PaperMatchingEngine:
    """
    纸交易撮合：
    - 市价单：立即按 mark_price + 滑点成交
    - 限价单：当 bid/ask 穿越挂单价时成交
    - 止盈/止损条件单：当价格触及 stop_price 时触发
    """

    def __init__(
        self,
        exchange: Exchange = Exchange.SIM,
        fee_model: FeeModel | None = None,
        slippage_model: SlippageModel | None = None,
        on_fill: Callable[[Fill], None] | None = None,
        on_order_update: Callable[[Order], None] | None = None,
    ) -> None:
        self.exchange = exchange
        self.fee_model = fee_model or FeeModel()
        self.slippage_model = slippage_model or SlippageModel()
        self.on_fill = on_fill
        self.on_order_update = on_order_update

        self._open_orders: dict[str, Order] = {}  # client_order_id -> Order
        self._mark_price: dict[str, Decimal] = {}
        self._orderbook: dict[str, OrderBook] = {}

    def submit_order(self, order: Order) -> Order:
        order.created_at_ms = get_clock().now_ms()
        order.updated_at_ms = order.created_at_ms

        mark = self._mark_price.get(order.symbol)

        if order.order_type == OrderType.MARKET:
            fill_price = self.slippage_model.apply(
                mark or Decimal("0"), order.side, order.qty
            )
            self._fill_order(order, fill_price, order.qty)
        else:
            order.status = OrderStatus.OPEN
            self._open_orders[order.client_order_id] = order
            if self.on_order_update:
                self.on_order_update(order)

        return order

    def cancel_order(self, client_order_id: str) -> Order | None:
        order = self._open_orders.pop(client_order_id, None)
        if order:
            order.status = OrderStatus.CANCELED
            order.updated_at_ms = get_clock().now_ms()
            if self.on_order_update:
                self.on_order_update(order)
        return order

    def on_kline(self, kline: Kline) -> None:
        self._mark_price[kline.symbol] = kline.close
        self._process_open_orders(kline.symbol, kline)

    def on_orderbook(self, ob: OrderBook) -> None:
        self._orderbook[ob.symbol] = ob
        if ob.mid_price:
            self._mark_price[ob.symbol] = ob.mid_price
        self._process_open_orders(ob.symbol)

    def _process_open_orders(
        self, symbol: str, kline: Kline | None = None
    ) -> None:
        to_fill: list[tuple[Order, Decimal]] = []
        mark = self._mark_price.get(symbol)

        for order in list(self._open_orders.values()):
            if order.symbol != symbol:
                continue

            if order.order_type in (OrderType.STOP_MARKET, OrderType.TAKE_PROFIT_MARKET):
                if order.stop_price is None or mark is None:
                    continue
                triggered = (
                    (order.side == OrderSide.BUY and mark >= order.stop_price)
                    or (order.side == OrderSide.SELL and mark <= order.stop_price)
                )
                if triggered:
                    fill_price = self.slippage_model.apply(mark, order.side, order.qty)
                    to_fill.append((order, fill_price))

            elif order.order_type == OrderType.LIMIT:
                if order.price is None or kline is None:
                    continue
                crossed = (
                    (order.side == OrderSide.BUY and kline.low <= order.price)
                    or (order.side == OrderSide.SELL and kline.high >= order.price)
                )
                if crossed:
                    to_fill.append((order, order.price))

        for order, price in to_fill:
            self._open_orders.pop(order.client_order_id, None)
            self._fill_order(order, price, order.remaining_qty)

    def _fill_order(self, order: Order, price: Decimal, qty: Decimal) -> None:
        notional = price * qty
        fee = self.fee_model.calc_fee(notional)

        order.filled_qty += qty
        order.avg_fill_price = price
        order.fee += fee
        order.status = (
            OrderStatus.FILLED
            if order.filled_qty >= order.qty
            else OrderStatus.PARTIALLY_FILLED
        )
        order.updated_at_ms = get_clock().now_ms()

        fill = Fill(
            fill_id=str(uuid.uuid4()),
            order_id=order.client_order_id,
            exchange=self.exchange,
            symbol=order.symbol,
            side=order.side,
            price=price,
            qty=qty,
            ts_ms=get_clock().now_ms(),
            fee=fee,
        )

        if self.on_fill:
            self.on_fill(fill)
        if self.on_order_update:
            self.on_order_update(order)

        logger.debug(
            "fill",
            symbol=order.symbol,
            side=order.side.value,
            price=float(price),
            qty=float(qty),
            fee=float(fee),
        )

    @property
    def open_orders(self) -> list[Order]:
        return list(self._open_orders.values())
