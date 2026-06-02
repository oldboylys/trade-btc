"""BTC 多指标策略 v2：结合 Coolish 账本行为研究调参."""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from src.core.clock import get_clock
from src.core.logging import get_logger
from src.core.models import (
    Exchange, Kline, PositionSide, SignalDirection, TargetPosition,
)
from src.indicators.pipeline import IndicatorPipeline
from src.strategies.base import IStrategy

logger = get_logger("strategy.btc_multi_indicator")


class BTCMultiIndicatorStrategy(IStrategy):
    """
    BTC 多指标策略 v2（Coolish 研究驱动）
    ─────────────────────────────────────
    5m 主信号 + 1h 趋势硬过滤（约 60% 历史回合在趋势方向开仓）：
      - 多头：5m 得分 >= threshold 且 1h EMA20>EMA50，RSI 在 [rsi_long_min, rsi_long_max]
      - 空头：镜像 + 1h 空头排列
      - 1h RSI > rsi_1h_long_max 时不追多（避免过热延伸区盲目加仓）

    持仓（波段，中位持仓约 8.7h）：
      - 开仓后得分回落不平仓
      - 平仓：TP/SL（默认 5% / 2.5%）或反向得分 >= reversal_threshold（高于开仓阈值）
    """

    name = "btc_multi_indicator"

    def __init__(
        self,
        symbol: str = "BTCUSDT",
        exchange: Exchange = Exchange.BINANCE,
        primary_tf: str = "5m",
        trend_tf: str = "1h",
        signal_threshold: float = 0.65,
        reversal_threshold: float = 0.75,
        require_1h_trend: bool = True,
        max_position_usdt: Decimal = Decimal("10000"),
        tp_pct: float = 0.05,
        sl_pct: float = 0.025,
        rsi_long_min: float = 45.0,
        rsi_long_max: float = 68.0,
        rsi_short_min: float = 32.0,
        rsi_short_max: float = 55.0,
        rsi_1h_long_max: float = 72.0,
    ) -> None:
        self.symbol = symbol
        self.exchange = exchange
        self.primary_tf = primary_tf
        self.trend_tf = trend_tf
        self.signal_threshold = signal_threshold
        self.reversal_threshold = reversal_threshold
        self.require_1h_trend = require_1h_trend
        self.max_position_usdt = max_position_usdt
        self.tp_pct = tp_pct
        self.sl_pct = sl_pct
        self.rsi_long_min = rsi_long_min
        self.rsi_long_max = rsi_long_max
        self.rsi_short_min = rsi_short_min
        self.rsi_short_max = rsi_short_max
        self.rsi_1h_long_max = rsi_1h_long_max

        self._pipeline = IndicatorPipeline(
            intervals=[primary_tf, trend_tf, "1m"],
            max_bars=500,
        )
        self._last_signal: SignalDirection = SignalDirection.FLAT

    def on_kline(
        self,
        kline: Kline,
        position_side: PositionSide | None = None,
    ) -> TargetPosition | None:
        if kline.symbol != self.symbol:
            return None

        if kline.interval != self.primary_tf:
            if kline.interval in self._pipeline.intervals:
                self._pipeline.feed(kline)
            return None

        self._pipeline.feed(kline)
        primary = self._pipeline.get_features(self.primary_tf)
        trend = self._pipeline.get_features(self.trend_tf)

        if not primary or not trend:
            logger.info(
                "strategy_no_features",
                interval=kline.interval,
                has_primary=bool(primary),
                has_trend=bool(trend),
            )
            return None

        long_score, short_score = self._score(primary, trend)
        logger.info(
            "strategy_scores",
            interval=kline.interval,
            long=round(long_score, 3),
            short=round(short_score, 3),
            entry_threshold=self.signal_threshold,
            reversal_threshold=self.reversal_threshold,
            close=float(kline.close),
            position=position_side.value if position_side else "flat",
        )
        close = Decimal(str(primary.get("close", 0)))
        if close <= 0:
            return None

        want_long = self._entry_allowed(SignalDirection.LONG, long_score, short_score, trend)
        want_short = self._entry_allowed(SignalDirection.SHORT, short_score, long_score, trend)

        direction = SignalDirection.FLAT
        confidence = 0.0

        if position_side == PositionSide.LONG:
            if self._reversal_allowed(SignalDirection.SHORT, short_score, long_score, trend):
                direction = SignalDirection.SHORT
                confidence = short_score
                logger.info("strategy_reversal", from_side="long", to_side="short")
            else:
                logger.info(
                    "strategy_hold",
                    side="long",
                    long=round(long_score, 3),
                    short=round(short_score, 3),
                )
                return None
        elif position_side == PositionSide.SHORT:
            if self._reversal_allowed(SignalDirection.LONG, long_score, short_score, trend):
                direction = SignalDirection.LONG
                confidence = long_score
                logger.info("strategy_reversal", from_side="short", to_side="long")
            else:
                logger.info(
                    "strategy_hold",
                    side="short",
                    long=round(long_score, 3),
                    short=round(short_score, 3),
                )
                return None
        else:
            if want_long:
                direction = SignalDirection.LONG
                confidence = long_score
            elif want_short:
                direction = SignalDirection.SHORT
                confidence = short_score
            else:
                return None

        target_qty = Decimal("0")
        tp_price: Optional[Decimal] = None
        sl_price: Optional[Decimal] = None

        if direction == SignalDirection.LONG:
            target_qty = (self.max_position_usdt / close).quantize(Decimal("0.001"))
            tp_price = (close * Decimal(str(1 + self.tp_pct))).quantize(Decimal("0.1"))
            sl_price = (close * Decimal(str(1 - self.sl_pct))).quantize(Decimal("0.1"))
        elif direction == SignalDirection.SHORT:
            target_qty = (self.max_position_usdt / close).quantize(Decimal("0.001"))
            tp_price = (close * Decimal(str(1 - self.tp_pct))).quantize(Decimal("0.1"))
            sl_price = (close * Decimal(str(1 + self.sl_pct))).quantize(Decimal("0.1"))

        self._last_signal = direction

        target = TargetPosition(
            symbol=self.symbol,
            exchange=self.exchange,
            direction=direction,
            target_qty=target_qty,
            confidence=confidence,
            tp_price=tp_price,
            sl_price=sl_price,
            reason=self._reason(long_score, short_score, primary, trend),
            ts_ms=get_clock().now_ms(),
        )
        logger.info(
            "signal",
            direction=direction.value,
            confidence=round(confidence, 3),
            close=float(close),
            tp=float(tp_price) if tp_price else None,
            sl=float(sl_price) if sl_price else None,
        )
        return target

    def _trend_bullish_1h(self, trend: dict[str, float]) -> bool:
        t_ema20 = trend.get("ema20", 0)
        t_ema50 = trend.get("ema50", 0)
        return t_ema20 > t_ema50 > 0

    def _trend_bearish_1h(self, trend: dict[str, float]) -> bool:
        t_ema20 = trend.get("ema20", 0)
        t_ema50 = trend.get("ema50", 0)
        return t_ema20 < t_ema50 and t_ema50 > 0

    def _passes_1h_trend_gate(self, direction: SignalDirection, trend: dict[str, float]) -> bool:
        if not self.require_1h_trend:
            return True
        if direction == SignalDirection.LONG:
            return self._trend_bullish_1h(trend)
        if direction == SignalDirection.SHORT:
            return self._trend_bearish_1h(trend)
        return False

    def _passes_rsi_1h_filter(self, direction: SignalDirection, trend: dict[str, float]) -> bool:
        rsi_1h = trend.get("rsi14", 50)
        if direction == SignalDirection.LONG and rsi_1h > self.rsi_1h_long_max:
            return False
        return True

    def _entry_allowed(
        self,
        direction: SignalDirection,
        score: float,
        opposite_score: float,
        trend: dict[str, float],
    ) -> bool:
        threshold = self.signal_threshold
        if direction == SignalDirection.LONG:
            if score < threshold or score <= opposite_score:
                return False
        else:
            if score < threshold or score <= opposite_score:
                return False
        if not self._passes_1h_trend_gate(direction, trend):
            return False
        if not self._passes_rsi_1h_filter(direction, trend):
            return False
        return True

    def _reversal_allowed(
        self,
        new_direction: SignalDirection,
        score: float,
        opposite_score: float,
        trend: dict[str, float],
    ) -> bool:
        if score < self.reversal_threshold or score <= opposite_score:
            return False
        if not self._passes_1h_trend_gate(new_direction, trend):
            return False
        if not self._passes_rsi_1h_filter(new_direction, trend):
            return False
        return True

    def _score(
        self,
        primary: dict[str, float],
        trend: dict[str, float],
    ) -> tuple[float, float]:
        """计算多头/空头信号分值 [0,1]."""
        long_score = 0.0
        short_score = 0.0

        close = primary.get("close", 0)
        ema20 = primary.get("ema20", 0)
        ema50 = primary.get("ema50", 0)
        macd_hist = primary.get("macd_hist", 0)
        rsi = primary.get("rsi14", 50)
        bb_mid = primary.get("bb_mid", close)
        vol_ratio = primary.get("vol_ratio", 1.0)

        if ema20 > ema50 > 0:
            long_score += 0.25
        elif ema20 < ema50:
            short_score += 0.25

        if macd_hist > 0:
            long_score += 0.25
        elif macd_hist < 0:
            short_score += 0.25

        if self.rsi_long_min <= rsi <= self.rsi_long_max:
            long_score += 0.20
        elif self.rsi_short_min <= rsi <= self.rsi_short_max:
            short_score += 0.20
        elif rsi >= 70:
            short_score += 0.10
        elif rsi <= 35:
            long_score += 0.05

        if close > bb_mid > 0:
            long_score += 0.15
        elif close < bb_mid:
            short_score += 0.15

        if vol_ratio >= 1.2:
            if long_score >= short_score:
                long_score += 0.15
            else:
                short_score += 0.15

        if self.require_1h_trend:
            if self._trend_bullish_1h(trend):
                short_score = max(0.0, short_score - 0.35)
            elif self._trend_bearish_1h(trend):
                long_score = max(0.0, long_score - 0.35)

        return min(1.0, long_score), min(1.0, short_score)

    def get_signal_state(
        self,
        position_side: PositionSide | None = None,
    ) -> dict:
        """供看板/API 使用的实时指标与得分快照."""
        primary = self._pipeline.get_features(self.primary_tf) or {}
        trend = self._pipeline.get_features(self.trend_tf) or {}
        if not primary or not trend:
            return {"ready": False}

        long_score, short_score = self._score(primary, trend)
        trend_bull = self._trend_bullish_1h(trend)
        trend_bear = self._trend_bearish_1h(trend)
        rsi_1h = trend.get("rsi14")
        rsi_5m = primary.get("rsi14")

        can_long = self._entry_allowed(SignalDirection.LONG, long_score, short_score, trend)
        can_short = self._entry_allowed(SignalDirection.SHORT, short_score, long_score, trend)
        can_rev_long = self._reversal_allowed(SignalDirection.LONG, long_score, short_score, trend)
        can_rev_short = self._reversal_allowed(SignalDirection.SHORT, short_score, long_score, trend)

        pos = position_side.value if position_side else "flat"
        if pos == "long":
            action = "reverse_short" if can_rev_short else "hold"
        elif pos == "short":
            action = "reverse_long" if can_rev_long else "hold"
        elif can_long:
            action = "open_long"
        elif can_short:
            action = "open_short"
        else:
            action = "wait"

        return {
            "ready": True,
            "updated_tf": self.primary_tf,
            "long_score": round(long_score, 3),
            "short_score": round(short_score, 3),
            "signal_threshold": self.signal_threshold,
            "reversal_threshold": self.reversal_threshold,
            "action": action,
            "position": pos,
            "primary": {
                "close": primary.get("close"),
                "ema20": primary.get("ema20"),
                "ema50": primary.get("ema50"),
                "macd_hist": primary.get("macd_hist"),
                "rsi14": rsi_5m,
                "bb_pct": primary.get("bb_pct"),
                "vol_ratio": primary.get("vol_ratio"),
            },
            "trend_1h": {
                "ema20": trend.get("ema20"),
                "ema50": trend.get("ema50"),
                "rsi14": rsi_1h,
                "trend_bullish": trend_bull,
                "trend_bearish": trend_bear,
            },
            "gates": {
                "require_1h_trend": self.require_1h_trend,
                "trend_bullish_1h": trend_bull,
                "trend_bearish_1h": trend_bear,
                "rsi_1h_long_blocked": (
                    rsi_1h is not None and rsi_1h > self.rsi_1h_long_max
                ),
                "rsi_5m_long_ok": (
                    rsi_5m is not None
                    and self.rsi_long_min <= rsi_5m <= self.rsi_long_max
                ),
                "rsi_5m_short_ok": (
                    rsi_5m is not None
                    and self.rsi_short_min <= rsi_5m <= self.rsi_short_max
                ),
                "can_open_long": can_long,
                "can_open_short": can_short,
                "can_reverse_long": can_rev_long,
                "can_reverse_short": can_rev_short,
            },
        }

    @staticmethod
    def config_snapshot(**kwargs) -> dict:
        """静态策略参数（看板展示）."""
        return {
            "version": "v2",
            "name": "BTC 多指标 v2（Coolish）",
            "primary_tf": kwargs.get("primary_tf", "5m"),
            "trend_tf": kwargs.get("trend_tf", "1h"),
            "signal_threshold": kwargs.get("signal_threshold", 0.65),
            "reversal_threshold": kwargs.get("reversal_threshold", 0.75),
            "require_1h_trend": kwargs.get("require_1h_trend", True),
            "tp_pct": kwargs.get("tp_pct", 0.05),
            "sl_pct": kwargs.get("sl_pct", 0.025),
            "rsi_long_min": kwargs.get("rsi_long_min", 45),
            "rsi_long_max": kwargs.get("rsi_long_max", 68),
            "rsi_short_min": kwargs.get("rsi_short_min", 32),
            "rsi_short_max": kwargs.get("rsi_short_max", 55),
            "rsi_1h_long_max": kwargs.get("rsi_1h_long_max", 72),
        }

    def _reason(
        self,
        long_score: float,
        short_score: float,
        feats: dict[str, float],
        trend: dict[str, float],
    ) -> str:
        return (
            f"long={long_score:.2f} short={short_score:.2f} "
            f"rsi5m={feats.get('rsi14', 0):.1f} rsi1h={trend.get('rsi14', 0):.1f} "
            f"macd_hist={feats.get('macd_hist', 0):.2f}"
        )
