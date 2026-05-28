"""BTC 多指标策略 v1：多空信号 + 目标仓位 + 止盈止损."""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from src.core.clock import get_clock
from src.core.logging import get_logger
from src.core.models import (
    Exchange, Kline, SignalDirection, TargetPosition,
)
from src.indicators.pipeline import IndicatorPipeline
from src.strategies.base import IStrategy

logger = get_logger("strategy.btc_multi_indicator")


class BTCMultiIndicatorStrategy(IStrategy):
    """
    BTC 多指标策略 v1
    ─────────────────
    信号逻辑（5m 主信号 + 1h 趋势过滤）：
      多头条件（所有条件满足权重加总 >= threshold）：
        - EMA20 > EMA50（短期上升趋势）              权重 0.25
        - MACD 金叉（macd_hist > 0）                 权重 0.25
        - RSI 介于 40-70（不超买）                    权重 0.20
        - 价格在布林带中轨以上                         权重 0.15
        - 成交量放大（vol_ratio > 1.2）               权重 0.15
      空头：镜像反转
      趋势过滤：1h EMA20/EMA50 方向一致

    输出：TargetPosition 含 tp_price / sl_price。
    """

    name = "btc_multi_indicator"

    def __init__(
        self,
        symbol: str = "BTCUSDT",
        exchange: Exchange = Exchange.BINANCE,
        primary_tf: str = "5m",
        trend_tf: str = "1h",
        signal_threshold: float = 0.6,
        max_position_usdt: Decimal = Decimal("10000"),
        tp_pct: float = 0.03,
        sl_pct: float = 0.015,
    ) -> None:
        self.symbol = symbol
        self.exchange = exchange
        self.primary_tf = primary_tf
        self.trend_tf = trend_tf
        self.signal_threshold = signal_threshold
        self.max_position_usdt = max_position_usdt
        self.tp_pct = tp_pct
        self.sl_pct = sl_pct

        self._pipeline = IndicatorPipeline(
            intervals=[primary_tf, trend_tf, "1m"],
            max_bars=500,
        )
        self._last_signal: SignalDirection = SignalDirection.FLAT

    def on_kline(self, kline: Kline) -> TargetPosition | None:
        if kline.symbol != self.symbol:
            return None

        self._pipeline.feed(kline)
        primary = self._pipeline.get_features(self.primary_tf)
        trend = self._pipeline.get_features(self.trend_tf)

        if not primary or not trend:
            return None

        long_score, short_score = self._score(primary, trend)
        close = Decimal(str(primary.get("close", 0)))
        if close <= 0:
            return None

        direction = SignalDirection.FLAT
        confidence = 0.0

        if long_score >= self.signal_threshold:
            direction = SignalDirection.LONG
            confidence = long_score
        elif short_score >= self.signal_threshold:
            direction = SignalDirection.SHORT
            confidence = short_score

        if direction == SignalDirection.FLAT and self._last_signal == SignalDirection.FLAT:
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
            reason=self._reason(long_score, short_score, primary),
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

        # EMA 趋势 (0.25)
        if ema20 > ema50 > 0:
            long_score += 0.25
        elif ema20 < ema50:
            short_score += 0.25

        # MACD 柱 (0.25)
        if macd_hist > 0:
            long_score += 0.25
        elif macd_hist < 0:
            short_score += 0.25

        # RSI (0.20)
        if 40 <= rsi <= 65:
            long_score += 0.20
        elif rsi >= 70 or rsi <= 35:
            short_score += 0.20

        # BB位置 (0.15)
        if close > bb_mid > 0:
            long_score += 0.15
        elif close < bb_mid:
            short_score += 0.15

        # 成交量 (0.15)
        if vol_ratio >= 1.2:
            long_score += 0.15
            short_score += 0.15  # 量增只增强方向确定性，两边都加

        # 1h 趋势过滤：不一致则惩罚 0.3
        t_ema20 = trend.get("ema20", 0)
        t_ema50 = trend.get("ema50", 0)
        if t_ema20 > t_ema50 > 0:
            short_score = max(0.0, short_score - 0.3)
        elif t_ema20 < t_ema50:
            long_score = max(0.0, long_score - 0.3)

        return long_score, short_score

    def _reason(
        self,
        long_score: float,
        short_score: float,
        feats: dict[str, float],
    ) -> str:
        return (
            f"long={long_score:.2f} short={short_score:.2f} "
            f"rsi={feats.get('rsi14', 0):.1f} "
            f"macd_hist={feats.get('macd_hist', 0):.2f}"
        )
