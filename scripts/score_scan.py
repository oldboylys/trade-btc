"""全周期信号/得分扫描（含正确预热）."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from src.backtest.replay import merge_klines_for_replay
from src.backtest.runner import _split_warmup_replay, _warmup_indicators
from src.backtest.session import parse_date
from src.core.config import load_config
from src.core.models import Exchange, PositionSide
from src.marketdata.storage import MarketDataStorage
from src.strategies.factory import create_strategy
from src.strategies.registry import bootstrap


async def main(
    strategy_name: str = "btc_multi_indicator",
    start: str = "2024-06-01",
    end: str = "2026-06-03",
) -> None:
    bootstrap()
    config = load_config("config")
    start_ms = parse_date(start)
    end_ms = parse_date(end)
    strat_cfg = config.get("strategies", {}).get(strategy_name, {})
    th = float(strat_cfg.get("signal_threshold", 0.65))

    storage = MarketDataStorage("data/marketdata.db")
    await storage.connect()
    k5 = await storage.load_klines("BTCUSDT", Exchange.BINANCE, "5m", start_ms, end_ms)
    k1 = await storage.load_klines("BTCUSDT", Exchange.BINANCE, "1h", start_ms, end_ms)
    merged = merge_klines_for_replay({"5m": k5, "1h": k1})
    warmup, replay = _split_warmup_replay(merged, 500, "5m")

    strategy = create_strategy(strategy_name, config, check_enabled=False)
    _warmup_indicators(strategy, warmup)

    max_l = max_s = 0.0
    above_th = 0
    signals = 0
    n5 = 0
    side = None

    for k in replay:
        t = strategy.on_kline(k, position_side=side)
        if k.interval == "5m":
            n5 += 1
            p = strategy._pipeline.get_features("5m")
            tr = strategy._pipeline.get_features("1h")
            if p and tr:
                ls, ss = strategy._score(p, tr)
                max_l = max(max_l, ls)
                max_s = max(max_s, ss)
                if max(ls, ss) >= th:
                    above_th += 1
        if t is not None:
            signals += 1
            side = (
                PositionSide.LONG if t.direction.value == "long"
                else PositionSide.SHORT if t.direction.value == "short"
                else None
            )

    print(f"strategy={strategy_name} replay_5m_bars={n5}")
    print(f"threshold={th} max_long={max_l:.3f} max_short={max_s:.3f}")
    print(f"bars_score>={th}: {above_th} signals={signals}")
    await storage.close()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", default="btc_multi_indicator")
    ap.add_argument("--start", default="2024-06-01")
    ap.add_argument("--end", default="2026-06-03")
    ns = ap.parse_args()
    asyncio.run(main(ns.strategy, ns.start, ns.end))
