"""策略基类接口."""
from __future__ import annotations

import abc

from src.core.models import Kline, TargetPosition


class IStrategy(abc.ABC):
    name: str = "base"

    @abc.abstractmethod
    def on_kline(self, kline: Kline) -> TargetPosition | None:
        """接收闭合K线，返回目标仓位（None=不操作）."""

    def on_start(self) -> None:
        """策略启动回调."""

    def on_stop(self) -> None:
        """策略停止回调."""
