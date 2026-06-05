"""BTC 多指标日内版（btc_multi_indicator_v2）：更高频、更紧 TP/SL."""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

from src.core.clock import get_clock
from src.core.logging import get_logger
from src.core.models import Exchange, Kline, PositionSide, SignalDirection, TargetPosition
from src.strategies.btc_multi_indicator.strategy import BTCMultiIndicatorStrategy

logger = get_logger("strategy.btc_multi_indicator_v2")


class BTCMultiIndicatorIntradayStrategy(BTCMultiIndicatorStrategy):
    """
    日内交易取向的 BTC 多指标策略（注册名 btc_multi_indicator_v2）。

    相对波段版的主要差异：
      - 开仓仅在 5m RSI 极端区：超卖 ≤ rsi_oversold_max（默认 15）做多，超买 ≥ rsi_overbought_min（默认 75）做空
      - 成交量相对均量剧增（vol_ratio / 短时跳升）时加分或抑制震荡追单
      - 5m EMA 趋势门控 + 1h 软过滤 + 紧 TP/SL
      - 账户权益 position_pct 复利仓位；分档移动止损锁盈
    """

    name = "btc_multi_indicator_v2"

    def __init__(
        self,
        symbol: str = "BTCUSDT",
        exchange: Exchange = Exchange.BINANCE,
        primary_tf: str = "5m",
        trend_tf: str = "1h",
        signal_threshold: float = 0.52,
        reversal_threshold: float = 0.62,
        min_score_edge: float = 0.05,
        require_1h_trend: bool = False,
        require_5m_trend: bool = True,
        use_1h_soft_filter: bool = True,
        soft_1h_extra_threshold: float = 0.08,
        trend_penalty_1h: float = 0.12,
        rsi_oversold_max: float = 15.0,
        rsi_overbought_min: float = 75.0,
        require_rsi_extreme: bool = True,
        vol_spike_ratio: float = 1.8,
        vol_surge_mult: float = 1.25,
        vol_spike_score_bonus: float = 0.12,
        vol_spike_mid_rsi_penalty: float = 0.25,
        require_vol_spike_for_entry: bool = False,
        use_position_pct: bool = True,
        position_pct: float = 0.30,
        max_position_usdt: Decimal = Decimal("100000"),
        tp_pct: float = 0.015,
        sl_pct: float = 0.01,
        trail_stop_enabled: bool = True,
        trail_levels: list[tuple[float, float]] | None = None,
        use_dynamic_tp: bool = False,
        tp_pct_min: float = 0.015,
        tp_pct_max: float = 0.08,
        reentry_after_tp_enabled: bool = False,
        reentry_after_tp_bars: int = 24,
        reentry_score_mult: float = 0.85,
        rsi_1h_long_max: float = 78.0,
    ) -> None:
        # 父类 RSI 区间仅用于评分权重：对齐极端区
        super().__init__(
            symbol=symbol,
            exchange=exchange,
            primary_tf=primary_tf,
            trend_tf=trend_tf,
            signal_threshold=signal_threshold,
            reversal_threshold=reversal_threshold,
            require_1h_trend=require_1h_trend,
            max_position_usdt=max_position_usdt,
            tp_pct=tp_pct,
            sl_pct=sl_pct,
            rsi_long_min=0.0,
            rsi_long_max=rsi_oversold_max,
            rsi_short_min=rsi_overbought_min,
            rsi_short_max=100.0,
            rsi_1h_long_max=rsi_1h_long_max,
        )
        self.min_score_edge = min_score_edge
        self.require_5m_trend = require_5m_trend
        self.use_1h_soft_filter = use_1h_soft_filter
        self.soft_1h_extra_threshold = soft_1h_extra_threshold
        self.trend_penalty_1h = trend_penalty_1h
        self.rsi_oversold_max = rsi_oversold_max
        self.rsi_overbought_min = rsi_overbought_min
        self.require_rsi_extreme = require_rsi_extreme
        self.vol_spike_ratio = vol_spike_ratio
        self.vol_surge_mult = vol_surge_mult
        self.vol_spike_score_bonus = vol_spike_score_bonus
        self.vol_spike_mid_rsi_penalty = vol_spike_mid_rsi_penalty
        self.require_vol_spike_for_entry = require_vol_spike_for_entry
        self.use_position_pct = use_position_pct
        self.position_pct = position_pct
        self.trail_stop_enabled = trail_stop_enabled
        self.trail_levels = sorted(
            trail_levels or [(0.50, 0.25), (0.80, 0.40)],
            key=lambda x: x[0],
        )
        self.use_dynamic_tp = use_dynamic_tp
        self.tp_pct_min = tp_pct_min
        self.tp_pct_max = tp_pct_max
        self.reentry_after_tp_enabled = reentry_after_tp_enabled
        self.reentry_after_tp_bars = reentry_after_tp_bars
        self.reentry_score_mult = reentry_score_mult
        self._account_equity: Decimal = Decimal("0")
        self._prev_vol_ratio: float = 1.0
        self._reentry_side: str | None = None
        self._reentry_bars_left: int = 0

    def set_account_equity(self, equity: Decimal) -> None:
        """由 runner / 回测在每根 K 线前注入（余额 + 浮动盈亏）."""
        self._account_equity = equity

    def _target_notional(self) -> Decimal:
        if self.use_position_pct and self._account_equity > 0:
            return (self._account_equity * Decimal(str(self.position_pct))).quantize(
                Decimal("0.01"),
            )
        return self.max_position_usdt

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> "BTCMultiIndicatorIntradayStrategy":
        from src.execution.trailing_stop import parse_trail_levels

        oversold = float(cfg.get("rsi_oversold_max", cfg.get("rsi_oversold", 15)))
        overbought = float(cfg.get("rsi_overbought_min", cfg.get("rsi_overbought", 75)))
        return cls(
            symbol=cfg.get("symbol", "BTCUSDT"),
            exchange=Exchange.BINANCE,
            primary_tf=cfg.get("timeframe", "5m"),
            trend_tf=cfg.get("trend_timeframe", "1h"),
            signal_threshold=float(cfg.get("signal_threshold", 0.52)),
            reversal_threshold=float(cfg.get("reversal_threshold", 0.62)),
            min_score_edge=float(cfg.get("min_score_edge", 0.05)),
            require_1h_trend=bool(cfg.get("require_1h_trend", False)),
            require_5m_trend=bool(cfg.get("require_5m_trend", True)),
            use_1h_soft_filter=bool(cfg.get("use_1h_soft_filter", True)),
            soft_1h_extra_threshold=float(cfg.get("soft_1h_extra_threshold", 0.08)),
            trend_penalty_1h=float(cfg.get("trend_penalty_1h", 0.12)),
            rsi_oversold_max=oversold,
            rsi_overbought_min=overbought,
            require_rsi_extreme=bool(cfg.get("require_rsi_extreme", True)),
            vol_spike_ratio=float(cfg.get("vol_spike_ratio", 1.8)),
            vol_surge_mult=float(cfg.get("vol_surge_mult", 1.25)),
            vol_spike_score_bonus=float(cfg.get("vol_spike_score_bonus", 0.12)),
            vol_spike_mid_rsi_penalty=float(cfg.get("vol_spike_mid_rsi_penalty", 0.25)),
            require_vol_spike_for_entry=bool(cfg.get("require_vol_spike_for_entry", False)),
            use_position_pct=bool(cfg.get("use_position_pct", True)),
            position_pct=float(cfg.get("position_pct", 0.30)),
            max_position_usdt=Decimal(str(cfg.get("max_position_usdt", 100000))),
            tp_pct=float(cfg.get("tp_pct", 0.015)),
            sl_pct=float(cfg.get("sl_pct", 0.01)),
            trail_stop_enabled=bool(cfg.get("trail_stop_enabled", True)),
            trail_levels=parse_trail_levels(cfg),
            use_dynamic_tp=bool(cfg.get("use_dynamic_tp", False)),
            tp_pct_min=float(cfg.get("tp_pct_min", 0.015)),
            tp_pct_max=float(cfg.get("tp_pct_max", 0.08)),
            reentry_after_tp_enabled=bool(cfg.get("reentry_after_tp_enabled", False)),
            reentry_after_tp_bars=int(cfg.get("reentry_after_tp_bars", 24)),
            reentry_score_mult=float(cfg.get("reentry_score_mult", 0.85)),
            rsi_1h_long_max=float(cfg.get("rsi_1h_long_max", 78)),
        )

    def on_trade_closed(self, event: dict) -> None:
        """止盈平仓后，在后续若干根 K 线内允许趋势续开."""
        if not self.reentry_after_tp_enabled:
            return
        if event.get("close_reason") != "止盈":
            return
        side = event.get("direction")
        if side in ("LONG", "SHORT"):
            self._reentry_side = side
            self._reentry_bars_left = self.reentry_after_tp_bars
            logger.info(
                "reentry_armed",
                side=side,
                bars=self.reentry_after_tp_bars,
            )

    def _resolve_tp_pct(
        self,
        direction: SignalDirection,
        confidence: float,
        trend: dict[str, float],
        primary: dict[str, float],
    ) -> float:
        if not self.use_dynamic_tp:
            return self.tp_pct
        lo, hi = self.tp_pct_min, self.tp_pct_max
        span = max(0.05, 1.0 - self.signal_threshold)
        prog = min(1.0, max(0.0, (confidence - self.signal_threshold) / span))
        tp = lo + (hi - lo) * prog
        if direction == SignalDirection.LONG and self._trend_bullish_1h(trend):
            tp = min(hi, tp + 0.005)
        elif direction == SignalDirection.SHORT and self._trend_bearish_1h(trend):
            tp = min(hi, tp + 0.005)
        _, is_surge = self._vol_spike_flags(float(primary.get("vol_ratio", 1.0)))
        if is_surge:
            tp = min(hi, tp + 0.003)
        macd = float(primary.get("macd_hist", 0))
        if direction == SignalDirection.LONG and macd > 0:
            tp = min(hi, tp * 1.05)
        elif direction == SignalDirection.SHORT and macd < 0:
            tp = min(hi, tp * 1.05)
        return max(lo, min(hi, tp))

    def _trend_still_favors(
        self,
        direction: SignalDirection,
        trend: dict[str, float],
        primary: dict[str, float],
    ) -> bool:
        if direction == SignalDirection.LONG:
            if self._trend_bullish_1h(trend):
                return True
            if self.require_5m_trend:
                ema20 = primary.get("ema20", 0)
                ema50 = primary.get("ema50", 0)
                return ema20 > ema50 > 0
            return False
        if direction == SignalDirection.SHORT:
            if self._trend_bearish_1h(trend):
                return True
            if self.require_5m_trend:
                ema20 = primary.get("ema20", 0)
                ema50 = primary.get("ema50", 0)
                return ema20 < ema50 and ema50 > 0
        return False

    def _try_reentry_after_tp(
        self,
        long_score: float,
        short_score: float,
        trend: dict[str, float],
        primary: dict[str, float],
        close: Decimal,
    ) -> TargetPosition | None:
        if self._reentry_bars_left <= 0 or not self._reentry_side:
            return None
        self._reentry_bars_left -= 1
        direction = (
            SignalDirection.LONG
            if self._reentry_side == "LONG"
            else SignalDirection.SHORT
        )
        score = long_score if direction == SignalDirection.LONG else short_score
        opposite = short_score if direction == SignalDirection.LONG else long_score
        if not self._trend_still_favors(direction, trend, primary):
            return None
        thresh = self.signal_threshold * self.reentry_score_mult
        if score < thresh or score < opposite + self.min_score_edge * 0.5:
            return None
        if not self._passes_5m_trend_gate(direction, primary):
            return None
        if not self._passes_rsi_1h_filter(direction, trend):
            return None
        tp_pct = self._resolve_tp_pct(direction, score, trend, primary)
        logger.info(
            "reentry_after_tp",
            side=direction.value,
            score=round(score, 3),
            tp_pct=round(tp_pct, 4),
            bars_left=self._reentry_bars_left,
        )
        return self.build_target(
            direction,
            close,
            confidence=score,
            reason="reentry_after_tp",
            tp_pct=tp_pct,
        )

    def build_target(
        self,
        direction,
        close: Decimal,
        confidence: float = 0.0,
        reason: str = "",
        tp_pct: float | None = None,
    ) -> TargetPosition:
        notional = self._target_notional()
        pct = tp_pct if tp_pct is not None else self.tp_pct
        tp_price = sl_price = None
        if direction == SignalDirection.LONG:
            qty = (notional / close).quantize(Decimal("0.001"))
            tp_price = (close * Decimal(str(1 + pct))).quantize(Decimal("0.1"))
            sl_price = (close * Decimal(str(1 - self.sl_pct))).quantize(Decimal("0.1"))
        elif direction == SignalDirection.SHORT:
            qty = (notional / close).quantize(Decimal("0.001"))
            tp_price = (close * Decimal(str(1 - pct))).quantize(Decimal("0.1"))
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
        reason = ""

        if position_side == PositionSide.LONG:
            if self._reversal_allowed(SignalDirection.SHORT, short_score, long_score, trend):
                direction = SignalDirection.SHORT
                confidence = short_score
                reason = "reversal"
            else:
                return None
        elif position_side == PositionSide.SHORT:
            if self._reversal_allowed(SignalDirection.LONG, long_score, short_score, trend):
                direction = SignalDirection.LONG
                confidence = long_score
                reason = "reversal"
            else:
                return None
        else:
            reentry = self._try_reentry_after_tp(long_score, short_score, trend, primary, close)
            if reentry is not None:
                return reentry
            if want_long:
                direction = SignalDirection.LONG
                confidence = long_score
                reason = "entry"
            elif want_short:
                direction = SignalDirection.SHORT
                confidence = short_score
                reason = "entry"
            else:
                return None

        tp_pct = self._resolve_tp_pct(direction, confidence, trend, primary)
        target = self.build_target(
            direction,
            close,
            confidence=confidence,
            reason=reason,
            tp_pct=tp_pct,
        )
        logger.info(
            "signal",
            direction=direction.value,
            confidence=round(confidence, 3),
            close=float(close),
            tp_pct=round(tp_pct, 4),
            tp=float(target.tp_price) if target.tp_price else None,
            sl=float(target.sl_price) if target.sl_price else None,
            reason=reason,
        )
        return target

    def _is_rsi_oversold(self, rsi: float) -> bool:
        return rsi <= self.rsi_oversold_max

    def _is_rsi_overbought(self, rsi: float) -> bool:
        return rsi >= self.rsi_overbought_min

    def _passes_rsi_extreme_gate(self, direction: SignalDirection, rsi: float) -> bool:
        if not self.require_rsi_extreme:
            return True
        if direction == SignalDirection.LONG:
            return self._is_rsi_oversold(rsi)
        if direction == SignalDirection.SHORT:
            return self._is_rsi_overbought(rsi)
        return False

    def _vol_spike_flags(self, vol_ratio: float) -> tuple[bool, bool]:
        """相对均量放大 / 相对上一根 5m 继续跳升（不修改状态）."""
        is_spike = vol_ratio >= self.vol_spike_ratio
        is_surge = is_spike and vol_ratio >= self._prev_vol_ratio * self.vol_surge_mult
        return is_spike, is_surge

    def _passes_vol_gate(self, direction: SignalDirection, rsi: float, vol_ratio: float) -> bool:
        is_spike, _ = self._vol_spike_flags(vol_ratio)
        if self.require_vol_spike_for_entry and not is_spike:
            return False
        # 非极端 RSI + 放量：视为震荡洗盘或追价，禁止开仓
        if is_spike and not self._is_rsi_oversold(rsi) and not self._is_rsi_overbought(rsi):
            return False
        return True

    def _passes_5m_trend_gate(self, direction: SignalDirection, primary: dict[str, float]) -> bool:
        if not self.require_5m_trend:
            return True
        ema20 = primary.get("ema20", 0)
        ema50 = primary.get("ema50", 0)
        rsi = primary.get("rsi14", 50)
        # 极端超卖反弹：允许略逆 5m 空头排列；极端超买回落同理
        if direction == SignalDirection.LONG and self._is_rsi_oversold(rsi):
            return ema20 > ema50 * 0.998 or ema50 <= 0
        if direction == SignalDirection.SHORT and self._is_rsi_overbought(rsi):
            return ema20 < ema50 * 1.002 and ema50 > 0
        if direction == SignalDirection.LONG:
            return ema20 > ema50 > 0
        if direction == SignalDirection.SHORT:
            return ema20 < ema50 and ema50 > 0
        return False

    def _passes_1h_soft_gate(
        self,
        direction: SignalDirection,
        score: float,
        trend: dict[str, float],
    ) -> bool:
        if not self.use_1h_soft_filter:
            return True
        opposed = (
            direction == SignalDirection.LONG and self._trend_bearish_1h(trend)
        ) or (
            direction == SignalDirection.SHORT and self._trend_bullish_1h(trend)
        )
        if not opposed:
            return True
        return score >= self.signal_threshold + self.soft_1h_extra_threshold

    def _entry_allowed(
        self,
        direction: SignalDirection,
        score: float,
        opposite_score: float,
        trend: dict[str, float],
    ) -> bool:
        primary = self._pipeline.get_features(self.primary_tf) or {}
        rsi = float(primary.get("rsi14", 50))
        vol_ratio = float(primary.get("vol_ratio", 1.0))

        if not self._passes_rsi_extreme_gate(direction, rsi):
            return False
        if not self._passes_vol_gate(direction, rsi, vol_ratio):
            return False
        if not self._passes_5m_trend_gate(direction, primary):
            return False
        if score < self.signal_threshold:
            return False
        if score < opposite_score + self.min_score_edge:
            return False
        if self.require_1h_trend and not self._passes_1h_trend_gate(direction, trend):
            return False
        if not self._passes_rsi_1h_filter(direction, trend):
            return False
        if not self._passes_1h_soft_gate(direction, score, trend):
            return False
        return True

    def _reversal_allowed(
        self,
        new_direction: SignalDirection,
        score: float,
        opposite_score: float,
        trend: dict[str, float],
    ) -> bool:
        primary = self._pipeline.get_features(self.primary_tf) or {}
        rsi = float(primary.get("rsi14", 50))
        if not self._passes_rsi_extreme_gate(new_direction, rsi):
            return False
        if score < self.reversal_threshold:
            return False
        if score < opposite_score + self.min_score_edge:
            return False
        if self.require_1h_trend and not self._passes_1h_trend_gate(new_direction, trend):
            return False
        if not self._passes_rsi_1h_filter(new_direction, trend):
            return False
        return True

    def _score(
        self,
        primary: dict[str, float],
        trend: dict[str, float],
    ) -> tuple[float, float]:
        long_score, short_score = super()._score(primary, trend)

        if self.use_1h_soft_filter or not self.require_1h_trend:
            if self._trend_bullish_1h(trend):
                short_score = max(0.0, short_score - self.trend_penalty_1h)
            elif self._trend_bearish_1h(trend):
                long_score = max(0.0, long_score - self.trend_penalty_1h)

        rsi = float(primary.get("rsi14", 50))
        vol_ratio = float(primary.get("vol_ratio", 1.0))
        is_spike = vol_ratio >= self.vol_spike_ratio
        is_surge = is_spike and vol_ratio >= self._prev_vol_ratio * self.vol_surge_mult

        if self._is_rsi_oversold(rsi):
            long_score = min(1.0, long_score + 0.15)
            short_score = max(0.0, short_score - 0.20)
        elif self._is_rsi_overbought(rsi):
            short_score = min(1.0, short_score + 0.15)
            long_score = max(0.0, long_score - 0.20)
        else:
            long_score = max(0.0, long_score - self.vol_spike_mid_rsi_penalty)
            short_score = max(0.0, short_score - self.vol_spike_mid_rsi_penalty)

        if is_surge:
            if self._is_rsi_oversold(rsi):
                long_score = min(1.0, long_score + self.vol_spike_score_bonus)
            elif self._is_rsi_overbought(rsi):
                short_score = min(1.0, short_score + self.vol_spike_score_bonus)
        elif is_spike:
            if self._is_rsi_oversold(rsi):
                long_score = min(1.0, long_score + self.vol_spike_score_bonus * 0.6)
            elif self._is_rsi_overbought(rsi):
                short_score = min(1.0, short_score + self.vol_spike_score_bonus * 0.6)
            else:
                long_score = max(0.0, long_score - self.vol_spike_mid_rsi_penalty)
                short_score = max(0.0, short_score - self.vol_spike_mid_rsi_penalty)

        bb_pct = primary.get("bb_pct")
        if bb_pct is not None:
            if bb_pct < 0.15 and self._is_rsi_oversold(rsi):
                long_score = min(1.0, long_score + 0.05)
            elif bb_pct > 0.85 and self._is_rsi_overbought(rsi):
                short_score = min(1.0, short_score + 0.05)

        self._prev_vol_ratio = vol_ratio
        return min(1.0, long_score), min(1.0, short_score)

    def get_signal_state(self, position_side: PositionSide | None = None) -> dict:
        state = super().get_signal_state(position_side=position_side)
        if state.get("ready"):
            primary = state.get("primary") or {}
            rsi = primary.get("rsi14")
            vol = primary.get("vol_ratio")
            state["variant"] = "intraday_v2"
            state["position_pct"] = self.position_pct
            state["account_equity"] = float(self._account_equity)
            state["target_notional"] = float(self._target_notional())
            state["rsi_oversold_max"] = self.rsi_oversold_max
            state["rsi_overbought_min"] = self.rsi_overbought_min
            state["rsi_extreme_long"] = rsi is not None and rsi <= self.rsi_oversold_max
            state["rsi_extreme_short"] = rsi is not None and rsi >= self.rsi_overbought_min
            state["vol_spike"] = vol is not None and vol >= self.vol_spike_ratio
            state["vol_surge"] = (
                vol is not None
                and vol >= self.vol_spike_ratio
                and vol >= self._prev_vol_ratio * self.vol_surge_mult
            )
        return state

    @staticmethod
    def config_snapshot(**kwargs: Any) -> dict:
        return {
            "version": "intraday_v2",
            "name": "BTC 多指标日内 V2",
            "primary_tf": kwargs.get("primary_tf", "5m"),
            "trend_tf": kwargs.get("trend_tf", "1h"),
            "signal_threshold": kwargs.get("signal_threshold", 0.48),
            "reversal_threshold": kwargs.get("reversal_threshold", 0.62),
            "rsi_oversold_max": kwargs.get("rsi_oversold_max", 15),
            "rsi_overbought_min": kwargs.get("rsi_overbought_min", 75),
            "vol_spike_ratio": kwargs.get("vol_spike_ratio", 1.8),
            "position_pct": kwargs.get("position_pct", 0.30),
            "trail_levels": kwargs.get("trail_levels", [[0.50, 0.25], [0.80, 0.40]]),
            "tp_pct": kwargs.get("tp_pct", 0.015),
            "sl_pct": kwargs.get("sl_pct", 0.01),
        }
