"""ICT K 线策略：结构 + FVG + OB + 流动性 + Premium/Discount."""
from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Any, Optional

from src.core.logging import get_logger
from src.core.models import Exchange, Kline, PositionSide, SignalDirection, TargetPosition
from src.indicators.pipeline import IndicatorPipeline
from src.strategies.ict.fvg import FVGTracker
from src.strategies.ict.liquidity import LiquidityTracker
from src.strategies.ict.order_block import OrderBlockTracker
from src.strategies.ict.premium import PremiumDiscount
from src.strategies.ict.structure import Bias, StructureTracker
from src.strategies.kline_base import KlineStrategy

logger = get_logger("strategy.ict")


def _in_killzone(utc_now: datetime.datetime, windows: list[str]) -> bool:
    if not windows:
        return True
    t = utc_now.time()
    for w in windows:
        if "-" not in w:
            continue
        start_s, end_s = w.split("-", 1)
        try:
            sh, sm = map(int, start_s.strip().split(":"))
            eh, em = map(int, end_s.strip().split(":"))
            start = datetime.time(sh, sm)
            end = datetime.time(eh, em)
            if start <= t <= end:
                return True
        except ValueError:
            continue
    return False


class ICTStrategy(KlineStrategy):
    name = "ict"

    def __init__(
        self,
        symbol: str = "BTCUSDT",
        exchange: Exchange = Exchange.BINANCE,
        primary_tf: str = "5m",
        structure_tf: str = "1h",
        swing_lookback: int = 3,
        displacement_atr_mult: float = 1.5,
        fvg_min_gap_pct: float = 0.001,
        ob_max_age_bars: int = 48,
        liquidity_equal_threshold_pct: float = 0.0005,
        require_premium_discount: bool = True,
        ote_low: float = 0.62,
        ote_high: float = 0.79,
        killzone_enabled: bool = False,
        killzone_utc: Optional[list[str]] = None,
        signal_threshold: float = 0.6,
        reversal_threshold: float = 0.75,
        max_position_usdt: Decimal = Decimal("10000"),
        tp_pct: float = 0.05,
        sl_pct: float = 0.025,
    ) -> None:
        super().__init__(
            symbol=symbol,
            exchange=exchange,
            primary_tf=primary_tf,
            trend_tf=structure_tf,
            max_position_usdt=max_position_usdt,
            tp_pct=tp_pct,
            sl_pct=sl_pct,
        )
        self.structure_tf = structure_tf
        self.signal_threshold = signal_threshold
        self.reversal_threshold = reversal_threshold
        self.require_premium_discount = require_premium_discount
        self.killzone_enabled = killzone_enabled
        self.killzone_utc = killzone_utc or []

        self._struct_1h = StructureTracker(swing_lookback=swing_lookback)
        self._struct_5m = StructureTracker(swing_lookback=swing_lookback)
        self._fvg = FVGTracker(min_gap_pct=fvg_min_gap_pct)
        self._ob = OrderBlockTracker(
            displacement_atr_mult=displacement_atr_mult,
            max_age_bars=ob_max_age_bars,
        )
        self._liq = LiquidityTracker(equal_threshold_pct=liquidity_equal_threshold_pct)
        self._pd = PremiumDiscount(ote_low=ote_low, ote_high=ote_high)
        self._pipeline = IndicatorPipeline(intervals=[primary_tf, structure_tf], max_bars=300)

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> "ICTStrategy":
        return cls(
            symbol=cfg.get("symbol", "BTCUSDT"),
            exchange=Exchange.BINANCE,
            primary_tf=cfg.get("timeframe", "5m"),
            structure_tf=cfg.get("structure_timeframe", "1h"),
            swing_lookback=int(cfg.get("swing_lookback", 3)),
            displacement_atr_mult=float(cfg.get("displacement_atr_mult", 1.5)),
            fvg_min_gap_pct=float(cfg.get("fvg_min_gap_pct", 0.001)),
            ob_max_age_bars=int(cfg.get("ob_max_age_bars", 48)),
            liquidity_equal_threshold_pct=float(cfg.get("liquidity_equal_threshold_pct", 0.0005)),
            require_premium_discount=bool(cfg.get("require_premium_discount", True)),
            ote_low=float(cfg.get("ote_low", 0.62)),
            ote_high=float(cfg.get("ote_high", 0.79)),
            killzone_enabled=bool(cfg.get("killzone_enabled", False)),
            killzone_utc=cfg.get("killzone_utc"),
            signal_threshold=float(cfg.get("signal_threshold", 0.6)),
            reversal_threshold=float(cfg.get("reversal_threshold", 0.75)),
            max_position_usdt=Decimal(str(cfg.get("max_position_usdt", 10000))),
            tp_pct=float(cfg.get("tp_pct", 0.05)),
            sl_pct=float(cfg.get("sl_pct", 0.025)),
        )

    def on_kline(
        self,
        kline: Kline,
        position_side: PositionSide | None = None,
    ) -> TargetPosition | None:
        if kline.symbol != self.symbol:
            return None

        if kline.interval in (self.primary_tf, self.structure_tf):
            self._pipeline.feed(kline)

        if kline.interval == self.structure_tf:
            self._struct_1h.feed(kline)
            self._pd.feed(kline)
            return None

        if kline.interval != self.primary_tf:
            return None

        st5 = self._struct_5m.feed(kline)
        primary = self._pipeline.get_features(self.primary_tf) or {}
        atr = float(primary.get("atr14", 0) or primary.get("close", 0) * 0.01)
        self._fvg.feed(kline)
        self._ob.feed(kline, atr=atr)
        bull_sweep, bear_sweep = self._liq.feed(kline)

        close = float(kline.close)
        low, high = float(kline.low), float(kline.high)
        bias_1h = self._struct_1h.state.bias

        if self.killzone_enabled:
            utc_now = datetime.datetime.fromtimestamp(
                kline.close_time / 1000, tz=datetime.timezone.utc,
            )
            if not _in_killzone(utc_now, self.killzone_utc) and position_side is None:
                return None

        long_score, short_score, reason = self._score(
            close, low, high, st5, bias_1h, bull_sweep, bear_sweep,
        )

        if position_side == PositionSide.LONG:
            if short_score >= self.reversal_threshold and st5.choch_bear:
                direction = SignalDirection.SHORT
                conf = short_score
            else:
                return None
        elif position_side == PositionSide.SHORT:
            if long_score >= self.reversal_threshold and st5.choch_bull:
                direction = SignalDirection.LONG
                conf = long_score
            else:
                return None
        else:
            if long_score >= self.signal_threshold and long_score > short_score:
                direction = SignalDirection.LONG
                conf = long_score
            elif short_score >= self.signal_threshold and short_score > long_score:
                direction = SignalDirection.SHORT
                conf = short_score
            else:
                return None

        if direction == SignalDirection.LONG and self.require_premium_discount:
            if not self._pd.allows_long(close):
                return None
        if direction == SignalDirection.SHORT and self.require_premium_discount:
            if not self._pd.allows_short(close):
                return None

        return self.build_target(
            direction,
            Decimal(str(close)),
            confidence=conf,
            reason=reason,
        )

    def _score(
        self,
        close: float,
        low: float,
        high: float,
        st5,
        bias_1h: Bias,
        bull_sweep: bool,
        bear_sweep: bool,
    ) -> tuple[float, float, str]:
        long_score = 0.0
        short_score = 0.0
        parts: list[str] = []

        if bias_1h == Bias.BULLISH:
            long_score += 0.25
        elif bias_1h == Bias.BEARISH:
            short_score += 0.25

        if st5.bos_bull or st5.choch_bull:
            long_score += 0.25
            parts.append("5m_bull_struct")
        if st5.bos_bear or st5.choch_bear:
            short_score += 0.25
            parts.append("5m_bear_struct")

        if self._fvg.touch_mid_bull(low, close):
            long_score += 0.20
            parts.append("fvg_bull")
        if self._fvg.touch_mid_bear(high, close):
            short_score += 0.20
            parts.append("fvg_bear")

        if self._ob.test_bull(low, close):
            long_score += 0.15
            parts.append("ob_bull")
        if self._ob.test_bear(high, close):
            short_score += 0.15
            parts.append("ob_bear")

        if bull_sweep:
            long_score += 0.15
            parts.append("liq_sweep_bull")
        if bear_sweep:
            short_score += 0.15
            parts.append("liq_sweep_bear")

        return min(1.0, long_score), min(1.0, short_score), "+".join(parts) or "ict"

    def get_signal_state(self, position_side: PositionSide | None = None) -> dict:
        st1 = self._struct_1h.state
        st5 = self._struct_5m.state
        return {
            "ready": True,
            "strategy": self.name,
            "bias_1h": st1.bias.value,
            "bias_5m": st5.bias.value,
            "active_fvg": len([g for g in self._fvg.gaps if g.active]),
            "active_ob": len([b for b in self._ob.blocks if b.active]),
            "zone": self._pd.zone(
                float(self._pipeline.get_features(self.primary_tf).get("close", 0))
                if self._pipeline.get_features(self.primary_tf)
                else 0,
            ).value if self._pd.state.high > 0 else "unknown",
            "position": position_side.value if position_side else "flat",
            "signal_threshold": self.signal_threshold,
        }

    @staticmethod
    def config_snapshot(**kwargs: Any) -> dict:
        return {
            "version": "1",
            "name": "ICT",
            "primary_tf": kwargs.get("primary_tf", "5m"),
            "structure_tf": kwargs.get("trend_tf", "1h"),
            "signal_threshold": kwargs.get("signal_threshold", 0.6),
            "reversal_threshold": kwargs.get("reversal_threshold", 0.75),
            "killzone_enabled": kwargs.get("killzone_enabled", False),
            "tp_pct": kwargs.get("tp_pct", 0.05),
            "sl_pct": kwargs.get("sl_pct", 0.025),
        }
