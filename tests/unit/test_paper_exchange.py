"""测试纸交易撮合与持仓账本."""
from decimal import Decimal

import pytest

from src.core.models import Exchange, Kline, Order, OrderSide, OrderStatus, OrderType
from src.sim.paper_exchange import PaperExchange


@pytest.fixture
def paper_ex():
    ex = PaperExchange(initial_balance=Decimal("100000"))
    return ex


def make_kline(price: float = 50000.0) -> Kline:
    return Kline(
        symbol="BTCUSDT", exchange=Exchange.SIM, interval="1m",
        open_time=1_700_000_000_000, close_time=1_700_000_059_999,
        open=Decimal(str(price - 10)),
        high=Decimal(str(price + 50)),
        low=Decimal(str(price - 50)),
        close=Decimal(str(price)),
        volume=Decimal("100"), quote_volume=Decimal("5000000"),
        num_trades=500, is_closed=True,
    )


@pytest.mark.asyncio
async def test_market_buy_fills_immediately(paper_ex):
    paper_ex.feed_kline(make_kline(50000))
    order = Order(
        client_order_id="test_buy",
        exchange=Exchange.SIM,
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        qty=Decimal("0.1"),
    )
    result = await paper_ex.place_order(order)
    assert result.status == OrderStatus.FILLED
    assert result.filled_qty == Decimal("0.1")


@pytest.mark.asyncio
async def test_position_updated_after_buy(paper_ex):
    paper_ex.feed_kline(make_kline(50000))
    order = Order(
        client_order_id="test_buy_pos",
        exchange=Exchange.SIM,
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        qty=Decimal("0.5"),
    )
    await paper_ex.place_order(order)
    pos = await paper_ex.get_position("BTCUSDT")
    assert pos is not None
    assert pos.qty == Decimal("0.5")


@pytest.mark.asyncio
async def test_limit_order_fills_on_kline(paper_ex):
    paper_ex.feed_kline(make_kline(50000))
    order = Order(
        client_order_id="test_limit",
        exchange=Exchange.SIM,
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        qty=Decimal("0.1"),
        price=Decimal("49960"),  # 限价低于 low(49950), 不会被 make_kline 触发
    )
    result = await paper_ex.place_order(order)
    assert result.status == OrderStatus.OPEN

    # 喂入一根 low 穿越挂单价的 K 线
    kline = Kline(
        symbol="BTCUSDT", exchange=Exchange.SIM, interval="1m",
        open_time=1_700_000_060_000, close_time=1_700_000_119_999,
        open=Decimal("49970"), high=Decimal("49980"),
        low=Decimal("49950"), close=Decimal("49960"),
        volume=Decimal("100"), quote_volume=Decimal("5000000"),
        num_trades=200, is_closed=True,
    )
    paper_ex.feed_kline(kline)
    updated = await paper_ex.get_order("BTCUSDT", "test_limit")
    assert updated.status == OrderStatus.FILLED


@pytest.mark.asyncio
async def test_balance_decreases_after_buy(paper_ex):
    paper_ex.feed_kline(make_kline(50000))
    initial_balance = paper_ex.balance
    order = Order(
        client_order_id="bal_test",
        exchange=Exchange.SIM,
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        qty=Decimal("1.0"),
    )
    await paper_ex.place_order(order)
    assert paper_ex.balance < initial_balance
