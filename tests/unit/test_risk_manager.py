"""测试风险控制."""
from decimal import Decimal

import pytest

from src.core.models import Exchange, Order, OrderSide, OrderType, SignalDirection, TargetPosition
from src.risk.manager import RiskAction, RiskConfig, RiskManager


@pytest.fixture
def risk():
    cfg = RiskConfig(
        max_position_usdt=Decimal("10000"),
        max_single_order_usdt=Decimal("5000"),
        max_daily_loss_usdt=Decimal("500"),
        max_consecutive_losses=3,
    )
    return RiskManager(cfg)


def make_target(qty: Decimal = Decimal("0.1")) -> TargetPosition:
    return TargetPosition(
        symbol="BTCUSDT",
        exchange=Exchange.BINANCE,
        direction=SignalDirection.LONG,
        target_qty=qty,
        confidence=0.7,
    )


def test_allow_normal_order(risk):
    target = make_target(Decimal("0.1"))
    result = risk.check_target_position(target, Decimal("5000"), Decimal("50000"))
    assert result.action == RiskAction.ALLOW


def test_block_on_circuit_break(risk):
    risk.on_realized_pnl(Decimal("-200"))
    risk.on_realized_pnl(Decimal("-200"))
    risk.on_realized_pnl(Decimal("-200"))
    assert risk.state.is_circuit_broken is True

    target = make_target()
    result = risk.check_target_position(target, Decimal("0"), Decimal("50000"))
    assert result.action == RiskAction.BLOCK
    assert "circuit_break" in result.reason


def test_block_on_daily_loss(risk):
    risk.on_realized_pnl(Decimal("-600"))
    assert risk.state.is_circuit_broken is True


def test_reduce_only_on_disconnect(risk):
    risk.on_disconnect()
    target = make_target(Decimal("1.0"))
    result = risk.check_target_position(target, Decimal("0"), Decimal("50000"))
    assert result.action == RiskAction.REDUCE_ONLY


def test_single_order_limit(risk):
    order = Order(
        client_order_id="test",
        exchange=Exchange.BINANCE,
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        qty=Decimal("1.0"),  # 50000 * 1.0 = 50000 > 5000
    )
    result = risk.check_order(order, Decimal("50000"))
    assert result.action == RiskAction.BLOCK


def test_position_notional_cap(risk):
    target = make_target(Decimal("1.0"))  # 50000 > 10000 limit
    result = risk.check_target_position(target, Decimal("0"), Decimal("50000"))
    assert result.action == RiskAction.REDUCE_ONLY
    assert result.adjusted_qty is not None
    assert result.adjusted_qty * Decimal("50000") <= Decimal("10000")


def test_price_deviation_block(risk):
    risk.on_price_update("BTCUSDT", Decimal("50000"))
    target = make_target()
    # 价格异常跳变 10%
    result = risk.check_target_position(target, Decimal("0"), Decimal("56000"))
    assert result.action == RiskAction.BLOCK


def test_reconnect_clears_disconnect(risk):
    risk.on_disconnect()
    assert risk.state.is_disconnected is True
    risk.on_reconnect()
    assert risk.state.is_disconnected is False
