"""K 线驱动策略基类."""
from __future__ import annotations

from abc import abstractmethod
from decimal import Decimal
from typing import Any, Optional

from src.core.models import Exchange, Kline, PositionSide, TargetPosition
from src.strategies.base import IStrategy


class KlineStrategy(IStrategy):
    """所有 on_kline 策略的公共字段与辅助方法."""

    name: str = "kline_strategy"
    primary_tf: str = "5m"
    trend_tf: str = "1h"

    def __init__(
        self,
        symbol: str = "BTCUSDT",
        exchange: Exchange = Exchange.BINANCE,
        primary_tf: str = "5m",
        trend_tf: str = "1h",
        max_position_usdt: Decimal = Decimal("10000"),
        tp_pct: float = 0.05,
        sl_pct: float = 0.025,
    ) -> None:
        self.symbol = symbol
        self.exchange = exchange
        self.primary_tf = primary_tf
        self.trend_tf = trend_tf
        self.max_position_usdt = max_position_usdt
        self.tp_pct = tp_pct
        self.sl_pct = sl_pct

    @classmethod
    @abstractmethod
    def from_config(cls, cfg: dict[str, Any]) -> "KlineStrategy":
        """从 strategies.<name> 配置段构造实例."""

    def feed_auxiliary_kline(self, kline: Kline) -> None:
        """非主周期 K 线仅更新指标管道."""
        pipeline = getattr(self, "_pipeline", None)
        if pipeline is not None and kline.interval in pipeline.intervals:
            pipeline.feed(kline)

    def get_signal_state(self, position_side: PositionSide | None = None) -> dict:
        return {"ready": False, "strategy": self.name}

    @staticmethod
    def config_snapshot(**kwargs: Any) -> dict:
        return dict(kwargs)

    def build_target(
        self,
        direction,
        close: Decimal,
        confidence: float = 0.0,
        reason: str = "",
    ) -> TargetPosition:
        from src.core.clock import get_clock
        from src.core.models import SignalDirection

        tp_price: Optional[Decimal] = None
        sl_price: Optional[Decimal] = None
        if direction == SignalDirection.LONG:
            qty = (self.max_position_usdt / close).quantize(Decimal("0.001"))
            tp_price = (close * Decimal(str(1 + self.tp_pct))).quantize(Decimal("0.1"))
            sl_price = (close * Decimal(str(1 - self.sl_pct))).quantize(Decimal("0.1"))
        elif direction == SignalDirection.SHORT:
            qty = (self.max_position_usdt / close).quantize(Decimal("0.001"))
            tp_price = (close * Decimal(str(1 - self.tp_pct))).quantize(Decimal("0.1"))
            sl_price = (close * Decimal(str(1 + self.sl_pct))).quantize(Decimal("0.1"))
        else:
            qty = Decimal("0")

        return TargetPosition(
            symbol=self.symbol,
            exchange=self.exchange,
            direction=direction,
            target_qty=qty,
            confidence=confidence,
            tp_price=tp_price,
            sl_price=sl_price,
            reason=reason,
            ts_ms=get_clock().now_ms(),
        )
