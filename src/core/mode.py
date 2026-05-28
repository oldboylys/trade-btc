"""运行模式管理：paper / testnet / live."""
from __future__ import annotations

from src.core.models import TradingMode


class ModeGuard:
    """
    运行模式守卫：确保非 live 模式下不执行真实下单。
    在真实连接器 place_order 之前调用 assert_can_trade()。
    """

    def __init__(self, mode: TradingMode) -> None:
        self._mode = mode

    @property
    def mode(self) -> TradingMode:
        return self._mode

    @property
    def is_paper(self) -> bool:
        return self._mode == TradingMode.PAPER

    @property
    def is_testnet(self) -> bool:
        return self._mode == TradingMode.TESTNET

    @property
    def is_live(self) -> bool:
        return self._mode == TradingMode.LIVE

    def assert_can_trade(self) -> None:
        """在 paper 模式下阻止真实下单."""
        if self._mode == TradingMode.PAPER:
            raise RuntimeError(
                "Trading is disabled in PAPER mode. "
                "Use PaperExchange instead of real connectors."
            )

    def assert_live(self) -> None:
        """只允许 live 模式调用（用于某些危险操作）."""
        if self._mode != TradingMode.LIVE:
            raise RuntimeError(f"Operation only allowed in LIVE mode, current={self._mode.value}")

    def __repr__(self) -> str:
        return f"ModeGuard(mode={self._mode.value})"
