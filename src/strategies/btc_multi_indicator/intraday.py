"""BTC 多指标日内版（btc_multi_indicator_v2）：更高频、更紧 TP/SL."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from src.core.models import Exchange, Kline, PositionSide, SignalDirection
from src.strategies.btc_multi_indicator.strategy import BTCMultiIndicatorStrategy


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
        self._account_equity: Decimal = Decimal("0")
        self._prev_vol_ratio: float = 1.0

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
            rsi_1h_long_max=float(cfg.get("rsi_1h_long_max", 78)),
        )

    def build_target(
        self,
        direction,
        close: Decimal,
        confidence: float = 0.0,
        reason: str = "",
    ):
        from src.core.clock import get_clock
        from src.core.models import SignalDirection, TargetPosition

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
