"""持仓移动止损：向 TP 推进后分档锁定利润."""

from __future__ import annotations



from dataclasses import dataclass, field

from decimal import Decimal

from typing import Any, Sequence



from src.core.models import PositionSide





def parse_trail_levels(cfg: dict[str, Any]) -> list[tuple[float, float]]:

    """解析 trail_levels 或单档 trail_activate/lock 配置."""

    raw = cfg.get("trail_levels")

    if raw:

        levels = [(float(a), float(l)) for a, l in raw]

    elif "trail_activate_progress" in cfg or "trail_lock_progress" in cfg:
        levels = [(
            float(cfg.get("trail_activate_progress", 0.50)),
            float(cfg.get("trail_lock_progress", 0.25)),
        )]
    else:
        levels = [(0.50, 0.25), (0.80, 0.40)]

    return sorted(levels, key=lambda x: x[0])





@dataclass

class TrailingStopState:

    entry: Decimal

    tp: Decimal

    side: PositionSide

    levels: list[tuple[float, float]] = field(default_factory=lambda: [(0.25, 0.10)])

    applied_index: int = -1



    @property

    def activate_progress(self) -> float:

        return self.levels[0][0] if self.levels else 0.25



    @property

    def lock_progress(self) -> float:

        idx = max(0, self.applied_index)

        if self.applied_index < 0:

            return self.levels[0][1] if self.levels else 0.10

        return self.levels[idx][1]





def progress_toward_tp(side: PositionSide, entry: Decimal, tp: Decimal, mark: Decimal) -> float:

    """沿 entry→TP 方向的完成度 [0, 1]（可 >1 表示已超过 TP）."""

    if side == PositionSide.LONG:

        span = tp - entry

        if span <= 0:

            return 0.0

        return float((mark - entry) / span)

    span = entry - tp

    if span <= 0:

        return 0.0

    return float((entry - mark) / span)





def locked_stop_price(

    side: PositionSide,

    entry: Decimal,

    tp: Decimal,

    lock_progress: float,

) -> Decimal:

    """止损设在 entry 与 TP 之间 lock_progress 位置."""

    lock = Decimal(str(lock_progress))

    if side == PositionSide.LONG:

        return (entry + (tp - entry) * lock).quantize(Decimal("0.1"))

    return (entry - (entry - tp) * lock).quantize(Decimal("0.1"))





def _is_better_stop(side: PositionSide, candidate: Decimal, current: Decimal) -> bool:

    if side == PositionSide.LONG:

        return candidate > current

    return candidate < current





def maybe_raise_stop(

    state: TrailingStopState,

    mark: Decimal,

    current_sl: Decimal,

) -> tuple[Decimal | None, int | None]:

    """

    按 levels 分档抬升止损（仅向更有利方向移动）。

    返回 (新止损价, 触发的 level 索引)；无变化则 (None, None)。

    """

    prog = progress_toward_tp(state.side, state.entry, state.tp, mark)

    best_idx: int | None = None

    best_sl: Decimal | None = None

    for i, (activate, lock) in enumerate(state.levels):

        if i <= state.applied_index:

            continue

        if prog < activate:

            break

        candidate = locked_stop_price(state.side, state.entry, state.tp, lock)

        if _is_better_stop(state.side, candidate, current_sl):

            best_idx = i

            best_sl = candidate

    if best_sl is None or best_idx is None:

        return None, None

    state.applied_index = best_idx

    return best_sl, best_idx





def levels_from_legacy(activate: float, lock: float) -> list[tuple[float, float]]:

    return [(activate, lock)]


