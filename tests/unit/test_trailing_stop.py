"""移动止损数学."""
from decimal import Decimal

from src.core.models import PositionSide
from src.execution.trailing_stop import (
    TrailingStopState,
    locked_stop_price,
    maybe_raise_stop,
    parse_trail_levels,
    progress_toward_tp,
)


def test_progress_and_lock_long():
    entry = Decimal("100")
    tp = Decimal("110")
    assert progress_toward_tp(PositionSide.LONG, entry, tp, Decimal("102.5")) == 0.25
    assert locked_stop_price(PositionSide.LONG, entry, tp, 0.10) == Decimal("101.0")


def test_trailing_activates_at_25_pct_long():
    state = TrailingStopState(
        entry=Decimal("100"),
        tp=Decimal("110"),
        side=PositionSide.LONG,
        levels=[(0.25, 0.10)],
    )
    new_sl, idx = maybe_raise_stop(state, Decimal("102"), Decimal("99"))
    assert new_sl is None
    new_sl, idx = maybe_raise_stop(state, Decimal("102.5"), Decimal("99"))
    assert new_sl == Decimal("101.0")
    assert idx == 0
    assert state.applied_index == 0
    new_sl, idx = maybe_raise_stop(state, Decimal("105"), Decimal("101"))
    assert new_sl is None


def test_multi_level_trailing():
    state = TrailingStopState(
        entry=Decimal("100"),
        tp=Decimal("110"),
        side=PositionSide.LONG,
        levels=[(0.25, 0.10), (0.50, 0.25), (0.80, 0.40)],
    )
    new_sl, idx = maybe_raise_stop(state, Decimal("102.5"), Decimal("99"))
    assert new_sl == Decimal("101.0")
    assert idx == 0
    new_sl, idx = maybe_raise_stop(state, Decimal("105"), Decimal("101"))
    assert new_sl == Decimal("102.5")
    assert idx == 1
    new_sl, idx = maybe_raise_stop(state, Decimal("108"), Decimal("102.5"))
    assert new_sl == Decimal("104.0")
    assert idx == 2


def test_parse_trail_levels():
    cfg = {"trail_levels": [[0.5, 0.25], [0.25, 0.1]]}
    assert parse_trail_levels(cfg) == [(0.25, 0.1), (0.5, 0.25)]
    assert parse_trail_levels({}) == [(0.50, 0.25), (0.80, 0.40)]


def test_position_pct_notional():
    from src.strategies.btc_multi_indicator.intraday import BTCMultiIndicatorIntradayStrategy

    s = BTCMultiIndicatorIntradayStrategy(use_position_pct=True, position_pct=0.30)
    s.set_account_equity(Decimal("100000"))
    assert s._target_notional() == Decimal("30000.00")


def test_dynamic_tp_in_range():
    from src.core.models import SignalDirection
    from src.strategies.btc_multi_indicator.intraday import BTCMultiIndicatorIntradayStrategy

    s = BTCMultiIndicatorIntradayStrategy(
        signal_threshold=0.46,
        use_dynamic_tp=True,
        tp_pct_min=0.015,
        tp_pct_max=0.08,
    )
    tp = s._resolve_tp_pct(
        SignalDirection.LONG,
        0.7,
        {"ema20": 100, "ema50": 90, "rsi14": 50},
        {"vol_ratio": 2.0, "macd_hist": 10},
    )
    assert 0.015 <= tp <= 0.08
