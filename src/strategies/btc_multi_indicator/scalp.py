"""BTC 1m 剥头皮：捕捉超卖/超买小段反弹，高频小 TP/SL."""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

from src.core.clock import get_clock
from src.core.logging import get_logger
from src.core.models import Exchange, Kline, PositionSide, SignalDirection, TargetPosition
from src.indicators.pipeline import IndicatorPipeline
from src.strategies.kline_base import KlineStrategy

logger = get_logger("strategy.btc_1m_scalp")


class BTC1mScalpStrategy(KlineStrategy):
    """
    1 分钟剥头皮策略（注册名 btc_1m_scalp）。

    设计取向：
      - 主周期 1m，5m 仅作轻量趋势过滤（不硬性要求 1h 同向）
      - 超卖区 RSI 拐头向上 → 做多吃小段反弹；超买区 RSI 拐头向下 → 做空
      - 紧 TP/SL（默认 0.5% / 0.4%），提高开单频率
    """

    name = "btc_1m_scalp"

    def __init__(
        self,
        symbol: str = "BTCUSDT",
        exchange: Exchange = Exchange.BINANCE,
        primary_tf: str = "1m",
        trend_tf: str = "5m",
        signal_threshold: float = 0.38,
        reversal_threshold: float = 0.52,
        min_score_edge: float = 0.03,
        rsi_long_max: float = 42.0,
        rsi_short_min: float = 58.0,
        require_rebound: bool = True,
        require_5m_not_opposed: bool = True,
        use_position_pct: bool = True,
        position_pct: float = 0.30,
        max_position_usdt: Decimal = Decimal("100000"),
        tp_pct: float = 0.005,
        sl_pct: float = 0.004,
        vol_spike_ratio: float = 1.3,
        vol_score_bonus: float = 0.08,
        bb_bounce_bonus: float = 0.10,
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
        self.signal_threshold = signal_threshold
        self.reversal_threshold = reversal_threshold
        self.min_score_edge = min_score_edge
        self.rsi_long_max = rsi_long_max
        self.rsi_short_min = rsi_short_min
        self.require_rebound = require_rebound
        self.require_5m_not_opposed = require_5m_not_opposed
        self.use_position_pct = use_position_pct
        self.position_pct = position_pct
        self.vol_spike_ratio = vol_spike_ratio
        self.vol_score_bonus = vol_score_bonus
        self.bb_bounce_bonus = bb_bounce_bonus

        self._pipeline = IndicatorPipeline(
            intervals=[primary_tf, trend_tf],
            max_bars=500,
        )
        self._account_equity: Decimal = Decimal("0")
        self._prev_rsi: float = 50.0
        self._prev_macd_hist: float = 0.0
        self._last_long_score: float = 0.0
        self._last_short_score: float = 0.0

    def set_account_equity(self, equity: Decimal) -> None:
        self._account_equity = equity

    def _target_notional(self) -> Decimal:
        if self.use_position_pct and self._account_equity > 0:
            return (self._account_equity * Decimal(str(self.position_pct))).quantize(
                Decimal("0.01"),
            )
        return self.max_position_usdt

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> "BTC1mScalpStrategy":
        return cls(
            symbol=cfg.get("symbol", "BTCUSDT"),
            exchange=Exchange.BINANCE,
            primary_tf=cfg.get("timeframe", "1m"),
            trend_tf=cfg.get("trend_timeframe", "5m"),
            signal_threshold=float(cfg.get("signal_threshold", 0.38)),
            reversal_threshold=float(cfg.get("reversal_threshold", 0.52)),
            min_score_edge=float(cfg.get("min_score_edge", 0.03)),
            rsi_long_max=float(cfg.get("rsi_long_max", 42)),
            rsi_short_min=float(cfg.get("rsi_short_min", 58)),
            require_rebound=bool(cfg.get("require_rebound", True)),
            require_5m_not_opposed=bool(cfg.get("require_5m_not_opposed", True)),
            use_position_pct=bool(cfg.get("use_position_pct", True)),
            position_pct=float(cfg.get("position_pct", 0.30)),
            max_position_usdt=Decimal(str(cfg.get("max_position_usdt", 100000))),
            tp_pct=float(cfg.get("tp_pct", 0.005)),
            sl_pct=float(cfg.get("sl_pct", 0.004)),
            vol_spike_ratio=float(cfg.get("vol_spike_ratio", 1.3)),
            vol_score_bonus=float(cfg.get("vol_score_bonus", 0.08)),
            bb_bounce_bonus=float(cfg.get("bb_bounce_bonus", 0.10)),
        )

    def build_target(
        self,
        direction: SignalDirection,
        close: Decimal,
        confidence: float = 0.0,
        reason: str = "",
    ) -> TargetPosition:
        notional = self._target_notional()
        tp_price = sl_price = None
        if direction == SignalDirection.LONG:
            qty = (notional / close).quantize(Decimal("0.001"))
            tp_price = (close * Decimal(str(1 + self.tp_pct))).quantize(Decimal("0.1"))
            sl_price = (close * Decimal(str(1 - self.sl_pct))).quantize(Decimal("0.1"))
        elif direction == SignalDirection.SHORT:
            qty = (notional / close).quantize(Decimal("0.001"))
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

    def _5m_not_opposed(self, direction: SignalDirection, trend: dict[str, float]) -> bool:
        if not self.require_5m_not_opposed:
            return True
        ema20 = trend.get("ema20", 0)
        ema50 = trend.get("ema50", 0)
        if ema50 <= 0:
            return True
        if direction == SignalDirection.LONG:
            return ema20 >= ema50 * 0.997
        if direction == SignalDirection.SHORT:
            return ema20 <= ema50 * 1.003
        return False

    def _rebound_ok(self, direction: SignalDirection, rsi: float) -> bool:
        if not self.require_rebound:
            return True
        if direction == SignalDirection.LONG:
            return rsi > self._prev_rsi
        if direction == SignalDirection.SHORT:
            return rsi < self._prev_rsi
        return False

    def _score(
        self,
        primary: dict[str, float],
        trend: dict[str, float],
    ) -> tuple[float, float]:
        rsi = float(primary.get("rsi14", 50))
        macd_hist = float(primary.get("macd_hist", 0))
        close = float(primary.get("close", 0))
        ema20 = float(primary.get("ema20", close))
        bb_pct = primary.get("bb_pct")
        vol_ratio = float(primary.get("vol_ratio", 1.0))

        long_score = 0.0
        short_score = 0.0

        if rsi <= self.rsi_long_max:
            long_score += 0.30
            if rsi > self._prev_rsi:
                long_score += 0.22
            if macd_hist > self._prev_macd_hist:
                long_score += 0.15
            if close > ema20:
                long_score += 0.12
            if bb_pct is not None and bb_pct < 0.25:
                long_score += self.bb_bounce_bonus

        if rsi >= self.rsi_short_min:
            short_score += 0.30
            if rsi < self._prev_rsi:
                short_score += 0.22
            if macd_hist < self._prev_macd_hist:
                short_score += 0.15
            if close < ema20:
                short_score += 0.12
            if bb_pct is not None and bb_pct > 0.75:
                short_score += self.bb_bounce_bonus

        if vol_ratio >= self.vol_spike_ratio:
            if rsi <= self.rsi_long_max:
                long_score += self.vol_score_bonus
            elif rsi >= self.rsi_short_min:
                short_score += self.vol_score_bonus

        trend_rsi = float(trend.get("rsi14", 50))
        if trend_rsi <= 45:
            long_score += 0.05
        elif trend_rsi >= 55:
            short_score += 0.05

        self._prev_rsi = rsi
        self._prev_macd_hist = macd_hist
        return min(1.0, long_score), min(1.0, short_score)

    def _entry_allowed(
        self,
        direction: SignalDirection,
        score: float,
        opposite: float,
        primary: dict[str, float],
        trend: dict[str, float],
    ) -> bool:
        rsi = float(primary.get("rsi14", 50))
        if direction == SignalDirection.LONG:
            if rsi > self.rsi_long_max:
                return False
        elif direction == SignalDirection.SHORT:
            if rsi < self.rsi_short_min:
                return False
        if not self._rebound_ok(direction, rsi):
            return False
        if not self._5m_not_opposed(direction, trend):
            return False
        if score < self.signal_threshold:
            return False
        if score < opposite + self.min_score_edge:
            return False
        return True

    def _reversal_allowed(
        self,
        direction: SignalDirection,
        score: float,
        opposite: float,
        primary: dict[str, float],
        trend: dict[str, float],
    ) -> bool:
        rsi = float(primary.get("rsi14", 50))
        if direction == SignalDirection.LONG and rsi > self.rsi_long_max + 8:
            return False
        if direction == SignalDirection.SHORT and rsi < self.rsi_short_min - 8:
            return False
        if not self._rebound_ok(direction, rsi):
            return False
        if score < self.reversal_threshold:
            return False
        if score < opposite + self.min_score_edge:
            return False
        return True

    def on_kline(
        self,
        kline: Kline,
        position_side: PositionSide | None = None,
    ) -> TargetPosition | None:
        if kline.symbol != self.symbol:
            return None
        if kline.interval != self.primary_tf:
            self.feed_auxiliary_kline(kline)
            return None

        self._pipeline.feed(kline)
        primary = self._pipeline.get_features(self.primary_tf)
        trend = self._pipeline.get_features(self.trend_tf)
        if not primary or not trend:
            return None

        long_score, short_score = self._score(primary, trend)
        self._last_long_score = long_score
        self._last_short_score = short_score
        close = Decimal(str(primary.get("close", 0)))
        if close <= 0:
            return None

        logger.info(
            "strategy_scores",
            interval=kline.interval,
            long=round(long_score, 3),
            short=round(short_score, 3),
            entry_threshold=self.signal_threshold,
            rsi=round(primary.get("rsi14", 50), 2),
            position=position_side.value if position_side else "flat",
        )

        direction = SignalDirection.FLAT
        confidence = 0.0
        reason = ""

        if position_side == PositionSide.LONG:
            if self._reversal_allowed(
                SignalDirection.SHORT, short_score, long_score, primary, trend,
            ):
                direction = SignalDirection.SHORT
                confidence = short_score
                reason = "reversal"
            else:
                return None
        elif position_side == PositionSide.SHORT:
            if self._reversal_allowed(
                SignalDirection.LONG, long_score, short_score, primary, trend,
            ):
                direction = SignalDirection.LONG
                confidence = long_score
                reason = "reversal"
            else:
                return None
        else:
            want_long = self._entry_allowed(
                SignalDirection.LONG, long_score, short_score, primary, trend,
            )
            want_short = self._entry_allowed(
                SignalDirection.SHORT, short_score, long_score, primary, trend,
            )
            if want_long:
                direction = SignalDirection.LONG
                confidence = long_score
                reason = "scalp_rebound_long"
            elif want_short:
                direction = SignalDirection.SHORT
                confidence = short_score
                reason = "scalp_rebound_short"
            else:
                return None

        target = self.build_target(direction, close, confidence=confidence, reason=reason)
        logger.info(
            "signal",
            direction=direction.value,
            confidence=round(confidence, 3),
            close=float(close),
            tp=float(target.tp_price) if target.tp_price else None,
            sl=float(target.sl_price) if target.sl_price else None,
            reason=reason,
        )
        return target

    def get_signal_state(self, position_side: PositionSide | None = None) -> dict:
        primary = self._pipeline.get_features(self.primary_tf) or {}
        trend = self._pipeline.get_features(self.trend_tf) or {}
        rsi = primary.get("rsi14")
        return {
            "ready": bool(primary and trend),
            "strategy": self.name,
            "variant": "1m_scalp",
            "primary": primary,
            "trend_5m": trend,
            "long_score": round(self._last_long_score, 3),
            "short_score": round(self._last_short_score, 3),
            "rsi_rebound_up": rsi is not None and rsi <= self.rsi_long_max and rsi > self._prev_rsi,
            "rsi_rebound_down": rsi is not None and rsi >= self.rsi_short_min and rsi < self._prev_rsi,
            "position_pct": self.position_pct,
            "target_notional": float(self._target_notional()),
        }

    @staticmethod
    def config_snapshot(**kwargs: Any) -> dict:
        return {
            "version": "1m_scalp",
            "name": "BTC 1m 剥头皮",
            "primary_tf": kwargs.get("primary_tf", "1m"),
            "trend_tf": kwargs.get("trend_tf", "5m"),
            "signal_threshold": kwargs.get("signal_threshold", 0.38),
            "tp_pct": kwargs.get("tp_pct", 0.005),
            "sl_pct": kwargs.get("sl_pct", 0.004),
            "rsi_long_max": kwargs.get("rsi_long_max", 42),
            "rsi_short_min": kwargs.get("rsi_short_min", 58),
            "position_pct": kwargs.get("position_pct", 0.30),
        }
