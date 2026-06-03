"""Volume Profile K 线策略."""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

from src.core.logging import get_logger
from src.core.models import Exchange, Kline, PositionSide, SignalDirection, TargetPosition
from src.indicators.pipeline import IndicatorPipeline
from src.strategies.kline_base import KlineStrategy
from src.strategies.volume_profile.profile import VolumeProfileBuilder
from src.strategies.volume_profile.signals import EntryMode, VPContext, evaluate_entry

logger = get_logger("strategy.volume_profile")


class VolumeProfileStrategy(KlineStrategy):
    name = "volume_profile"

    def __init__(
        self,
        symbol: str = "BTCUSDT",
        exchange: Exchange = Exchange.BINANCE,
        primary_tf: str = "5m",
        trend_tf: str = "1h",
        profile_timeframe: str = "5m",
        lookback_bars: int = 288,
        value_area_pct: float = 0.70,
        bin_count: int = 50,
        tick_size: float = 10.0,
        entry_mode: str = "val_poc_bounce",
        require_trend_1h: bool = True,
        min_distance_to_poc_pct: float = 0.003,
        max_position_usdt: Decimal = Decimal("10000"),
        tp_pct: float = 0.04,
        sl_pct: float = 0.02,
    ) -> None:
        super().__init__(
            symbol=symbol,
            exchange=exchange,
            primary_tf=primary_tf,
            trend_tf=trend_tf,
            max_position_usdt=max_position_usdt,
            tp_pct=tp_pct,
            sl_pct=sl_pct,
        )
        self.profile_timeframe = profile_timeframe
        self.entry_mode = EntryMode(entry_mode)
        self.require_trend_1h = require_trend_1h
        self.min_distance_to_poc_pct = min_distance_to_poc_pct
        self._profile = VolumeProfileBuilder(
            lookback_bars=lookback_bars,
            value_area_pct=value_area_pct,
            bin_count=bin_count,
            tick_size=tick_size,
        )
        self._pipeline = IndicatorPipeline(intervals=[primary_tf, trend_tf], max_bars=200)
        self._prev_close: float = 0.0

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> "VolumeProfileStrategy":
        return cls(
            symbol=cfg.get("symbol", "BTCUSDT"),
            exchange=Exchange.BINANCE,
            primary_tf=cfg.get("timeframe", "5m"),
            trend_tf=cfg.get("trend_timeframe", "1h"),
            profile_timeframe=cfg.get("profile_timeframe", cfg.get("timeframe", "5m")),
            lookback_bars=int(cfg.get("lookback_bars", 288)),
            value_area_pct=float(cfg.get("value_area_pct", 0.70)),
            bin_count=int(cfg.get("bin_count", 50)),
            tick_size=float(cfg.get("tick_size", 10)),
            entry_mode=cfg.get("entry_mode", "val_poc_bounce"),
            require_trend_1h=bool(cfg.get("require_trend_1h", True)),
            min_distance_to_poc_pct=float(cfg.get("min_distance_to_poc_pct", 0.003)),
            max_position_usdt=Decimal(str(cfg.get("max_position_usdt", 10000))),
            tp_pct=float(cfg.get("tp_pct", 0.04)),
            sl_pct=float(cfg.get("sl_pct", 0.02)),
        )

    def on_kline(
        self,
        kline: Kline,
        position_side: PositionSide | None = None,
    ) -> TargetPosition | None:
        if kline.symbol != self.symbol:
            return None

        if kline.interval in (self.trend_tf, self.primary_tf):
            self._pipeline.feed(kline)

        if kline.interval == self.profile_timeframe:
            self._profile.feed(kline)

        if kline.interval != self.primary_tf:
            return None

        levels = self._profile.levels
        if levels is None:
            return None

        trend = self._pipeline.get_features(self.trend_tf) or {}
        trend_bull = trend.get("ema20", 0) > trend.get("ema50", 0) > 0
        trend_bear = trend.get("ema20", 0) < trend.get("ema50", 0) and trend.get("ema50", 0) > 0

        close_f = float(kline.close)
        ctx = VPContext(
            close=close_f,
            prev_close=self._prev_close or close_f,
            low=float(kline.low),
            high=float(kline.high),
            levels=levels,
            trend_bullish_1h=trend_bull,
            trend_bearish_1h=trend_bear,
        )
        self._prev_close = close_f

        direction, confidence, reason = evaluate_entry(
            ctx,
            entry_mode=self.entry_mode,
            require_trend_1h=self.require_trend_1h,
            min_distance_to_poc_pct=self.min_distance_to_poc_pct,
            position_side=position_side,
        )

        if direction == SignalDirection.FLAT:
            if position_side is not None:
                return None
            return None

        if position_side == PositionSide.LONG and direction == SignalDirection.LONG:
            return None
        if position_side == PositionSide.SHORT and direction == SignalDirection.SHORT:
            return None

        return self.build_target(
            direction,
            Decimal(str(close_f)),
            confidence=confidence,
            reason=reason,
        )

    def get_signal_state(self, position_side: PositionSide | None = None) -> dict:
        lv = self._profile.levels
        if not lv:
            return {"ready": False, "strategy": self.name}
        return {
            "ready": True,
            "strategy": self.name,
            "poc": lv.poc,
            "vah": lv.vah,
            "val": lv.val,
            "hvn_count": len(lv.hvn),
            "lvn_count": len(lv.lvn),
            "entry_mode": self.entry_mode.value,
            "position": position_side.value if position_side else "flat",
        }

    @staticmethod
    def config_snapshot(**kwargs: Any) -> dict:
        return {
            "version": "1",
            "name": "Volume Profile",
            "primary_tf": kwargs.get("primary_tf", "5m"),
            "entry_mode": kwargs.get("entry_mode", "val_poc_bounce"),
            "require_trend_1h": kwargs.get("require_trend_1h", True),
            "tp_pct": kwargs.get("tp_pct", 0.04),
            "sl_pct": kwargs.get("sl_pct", 0.02),
        }
