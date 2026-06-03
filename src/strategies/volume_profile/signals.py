"""Volume Profile 入场/过滤规则."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from src.core.models import PositionSide, SignalDirection
from src.strategies.volume_profile.profile import ProfileLevels


class EntryMode(str, Enum):
    VAL_POC_BOUNCE = "val_poc_bounce"
    HVN_LVN_REVERSION = "hvn_lvn_reversion"


@dataclass
class VPContext:
    close: float
    prev_close: float
    low: float
    high: float
    levels: ProfileLevels
    trend_bullish_1h: bool
    trend_bearish_1h: bool


def evaluate_entry(
    ctx: VPContext,
    *,
    entry_mode: EntryMode,
    require_trend_1h: bool,
    min_distance_to_poc_pct: float,
    position_side: PositionSide | None,
) -> tuple[SignalDirection, float, str]:
    """返回 (direction, confidence, reason). FLAT 表示无信号."""
    lv = ctx.levels
    close = ctx.close
    poc_dist = abs(close - lv.poc) / lv.poc if lv.poc > 0 else 1.0

    if position_side is not None:
        return _exit_or_reverse(ctx, position_side, min_distance_to_poc_pct)

    if require_trend_1h and not (ctx.trend_bullish_1h or ctx.trend_bearish_1h):
        return SignalDirection.FLAT, 0.0, "no_1h_trend"

    if entry_mode == EntryMode.HVN_LVN_REVERSION:
        return _hvn_lvn_entry(ctx, require_trend_1h, poc_dist, min_distance_to_poc_pct)
    return _val_poc_entry(ctx, require_trend_1h, poc_dist, min_distance_to_poc_pct)


def _val_poc_entry(
    ctx: VPContext,
    require_trend_1h: bool,
    poc_dist: float,
    min_dist: float,
) -> tuple[SignalDirection, float, str]:
    lv = ctx.levels
    close = ctx.close
    in_va = lv.val <= close <= lv.vah

    long_ok = (
        ctx.prev_close <= lv.val * 1.002
        and close > lv.val
        and (in_va or close >= lv.poc * (1 - min_dist))
        and (not require_trend_1h or ctx.trend_bullish_1h)
    )
    short_ok = (
        ctx.prev_close >= lv.vah * 0.998
        and close < lv.vah
        and (in_va or close <= lv.poc * (1 + min_dist))
        and (not require_trend_1h or ctx.trend_bearish_1h)
    )

    if long_ok and poc_dist >= min_dist * 0.5:
        conf = 0.55 + (0.15 if close >= lv.poc else 0.0)
        return SignalDirection.LONG, min(0.85, conf), "val_poc_bounce_long"
    if short_ok and poc_dist >= min_dist * 0.5:
        conf = 0.55 + (0.15 if close <= lv.poc else 0.0)
        return SignalDirection.SHORT, min(0.85, conf), "val_poc_bounce_short"
    return SignalDirection.FLAT, 0.0, "wait"


def _hvn_lvn_entry(
    ctx: VPContext,
    require_trend_1h: bool,
    poc_dist: float,
    min_dist: float,
) -> tuple[SignalDirection, float, str]:
    lv = ctx.levels
    close = ctx.close
    nearest_lvn = min(lv.lvn, key=lambda x: abs(x - close), default=None) if lv.lvn else None
    nearest_hvn = min(lv.hvn, key=lambda x: abs(x - close), default=None) if lv.hvn else None

    if nearest_lvn and ctx.low < nearest_lvn and close > nearest_lvn:
        if not require_trend_1h or ctx.trend_bullish_1h:
            return SignalDirection.LONG, 0.6, "lvn_reclaim_long"
    if nearest_hvn and ctx.high > nearest_hvn and close < nearest_hvn:
        if not require_trend_1h or ctx.trend_bearish_1h:
            return SignalDirection.SHORT, 0.6, "hvn_reject_short"
    return SignalDirection.FLAT, 0.0, "wait"


def _exit_or_reverse(
    ctx: VPContext,
    position_side: PositionSide,
    min_dist: float,
) -> tuple[SignalDirection, float, str]:
    lv = ctx.levels
    close = ctx.close
    if position_side == PositionSide.LONG:
        if close < lv.val * (1 - min_dist):
            return SignalDirection.SHORT, 0.75, "va_breakdown_reverse"
        return SignalDirection.FLAT, 0.0, "hold_long"
    if close > lv.vah * (1 + min_dist):
        return SignalDirection.LONG, 0.75, "va_breakout_reverse"
    return SignalDirection.FLAT, 0.0, "hold_short"
