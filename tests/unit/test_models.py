"""测试统一数据模型."""
from decimal import Decimal

import pytest

from src.core.models import (
    Exchange, FundingRate, Kline, Order, OrderSide,
    OrderStatus, OrderType, Position, PositionSide,
    SignalDirection, Symbol, TargetPosition,
)


def test_symbol_name():
    s = Symbol(base="BTC", quote="USDT", exchange=Exchange.BINANCE, raw_symbol="BTCUSDT")
    assert s.name == "BTCUSDT"


def test_order_is_terminal():
    o = Order(
        client_order_id="test",
        exchange=Exchange.BINANCE,
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        qty=Decimal("0.1"),
        status=OrderStatus.FILLED,
    )
    assert o.is_terminal is True

    o.status = OrderStatus.OPEN
    assert o.is_terminal is False


def test_order_remaining_qty():
    o = Order(
        client_order_id="test",
        exchange=Exchange.BINANCE,
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        qty=Decimal("1.0"),
        filled_qty=Decimal("0.4"),
        status=OrderStatus.PARTIALLY_FILLED,
    )
    assert o.remaining_qty == Decimal("0.6")


def test_position_pnl():
    pos = Position(
        symbol="BTCUSDT",
        exchange=Exchange.BINANCE,
        side=PositionSide.LONG,
        qty=Decimal("0.1"),
        entry_price=Decimal("50000"),
        mark_price=Decimal("50000"),
    )
    pos.update_pnl(Decimal("51000"))
    assert pos.unrealized_pnl == Decimal("100")

    pos.side = PositionSide.SHORT
    pos.entry_price = Decimal("50000")
    pos.update_pnl(Decimal("49000"))
    assert pos.unrealized_pnl == Decimal("100")


def test_funding_rate():
    fr = FundingRate(
        symbol="BTCUSDT",
        exchange=Exchange.BINANCE,
        rate=Decimal("0.0001"),
    )
    assert float(fr.rate) == pytest.approx(0.0001)


def test_target_position():
    tp = TargetPosition(
        symbol="BTCUSDT",
        exchange=Exchange.BINANCE,
        direction=SignalDirection.LONG,
        target_qty=Decimal("0.1"),
        confidence=0.75,
    )
    assert tp.direction == SignalDirection.LONG
    assert tp.target_qty == Decimal("0.1")
