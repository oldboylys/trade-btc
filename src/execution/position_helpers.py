"""执行后同步移动止损注册."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

DEFAULT_TRAIL_LEVELS: list[tuple[float, float]] = [(0.50, 0.25), (0.80, 0.40)]


def set_strategy_equity(strategy: Any, balance: Decimal, unrealized_pnl: Decimal) -> None:
    equity = balance + unrealized_pnl
    if hasattr(strategy, "set_account_equity"):
        strategy.set_account_equity(equity)


def register_trailing_if_needed(
    position_book: Any,
    strategy: Any,
    symbol: str,
) -> None:
    if not getattr(strategy, "trail_stop_enabled", True):
        return
    pos = position_book.get_position(symbol)
    if not pos or pos.qty <= 0:
        return
    tp_sl = position_book._tp_sl.get(symbol)
    if not tp_sl:
        return
    tp, sl = tp_sl
    levels = list(getattr(strategy, "trail_levels", None) or DEFAULT_TRAIL_LEVELS)
    position_book.register_trailing_stop(
        symbol,
        pos.entry_price,
        Decimal(str(tp)),
        Decimal(str(sl)),
        pos.side,
        levels=levels,
    )
