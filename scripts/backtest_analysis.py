"""多阈值回测对比 + 信号分布采集（不修改策略源码）."""
from __future__ import annotations

import asyncio
import datetime
import json
import sys
from decimal import Decimal
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from apps.backtest.main import _parse_date
from src.backtest.runner import BacktestRunner
from src.core.config import load_config
from src.core.models import Exchange
from src.marketdata.storage import MarketDataStorage
from src.risk.manager import RiskConfig
from src.strategies.btc_multi_indicator.strategy import BTCMultiIndicatorStrategy

from scripts.run_backtest_report import _build_payload, _month_key


async def _run_one(
    config: dict,
    start_str: str,
    end_str: str,
    signal_threshold: float,
    collect_scores: bool = False,
) -> tuple[dict, list[dict]]:
    bt_cfg = config.get("backtest", {})
    risk_cfg_raw = config.get("risk", {})
    symbol = bt_cfg.get("symbol", "BTCUSDT")
    db_path = bt_cfg.get("db_path") or config.get("market_data", {}).get("db_path", "data/marketdata.db")
    intervals = bt_cfg.get("intervals", ["5m", "1h"])
    warmup_bars = int(bt_cfg.get("warmup_bars", 500))
    initial_balance = Decimal(str(bt_cfg.get("initial_balance", 100000)))
    start_ms = _parse_date(start_str)
    end_ms = _parse_date(end_str)

    strat_cfg = config.get("strategies", {}).get("btc_multi_indicator", {})
    strategy = BTCMultiIndicatorStrategy(
        symbol=strat_cfg.get("symbol", "BTCUSDT"),
        exchange=Exchange.BINANCE,
        primary_tf=strat_cfg.get("timeframe", "5m"),
        trend_tf=strat_cfg.get("trend_timeframe", "1h"),
        signal_threshold=signal_threshold,
        reversal_threshold=float(strat_cfg.get("reversal_threshold", 0.75)),
        require_1h_trend=bool(strat_cfg.get("require_1h_trend", True)),
        max_position_usdt=Decimal(str(strat_cfg.get("max_position_usdt", 10000))),
        tp_pct=float(strat_cfg.get("tp_pct", 0.05)),
        sl_pct=float(strat_cfg.get("sl_pct", 0.025)),
        rsi_long_min=float(strat_cfg.get("rsi_long_min", 45)),
        rsi_long_max=float(strat_cfg.get("rsi_long_max", 68)),
        rsi_short_min=float(strat_cfg.get("rsi_short_min", 32)),
        rsi_short_max=float(strat_cfg.get("rsi_short_max", 55)),
        rsi_1h_long_max=float(strat_cfg.get("rsi_1h_long_max", 72)),
    )

    scores: list[dict] = []
    if collect_scores:
        orig = strategy.on_kline

        def _wrapped(kline, position_side=None):
            target = orig(kline, position_side=position_side)
            if kline.interval == strategy.primary_tf:
                primary = strategy._pipeline.get_features(strategy.primary_tf)
                trend = strategy._pipeline.get_features(strategy.trend_tf)
                if primary and trend:
                    ls, ss = strategy._score(primary, trend)
                    scores.append({
                        "ts_ms": kline.close_time,
                        "long": round(ls, 3),
                        "short": round(ss, 3),
                        "close": float(primary.get("close", 0)),
                    })
            return target

        strategy.on_kline = _wrapped  # type: ignore[method-assign]

    storage = MarketDataStorage(db_path)
    await storage.connect()
    runner = BacktestRunner(
        storage=storage,
        strategy=strategy,
        initial_balance=initial_balance,
        risk_config=RiskConfig(
            max_position_usdt=Decimal(str(risk_cfg_raw.get("max_position_usdt", 20000))),
            max_single_order_usdt=Decimal(str(risk_cfg_raw.get("max_single_order_usdt", 5000))),
            max_daily_loss_usdt=Decimal(str(risk_cfg_raw.get("max_daily_loss_usdt", 1000))),
        ),
        warmup_bars=warmup_bars,
        intervals=intervals,
    )
    try:
        report = await runner.run(
            symbol=symbol, exchange=Exchange.BINANCE, start_ms=start_ms, end_ms=end_ms,
        )
    finally:
        await storage.close()

    payload = _build_payload(report, config, start_str, end_str)
    payload["run_params"] = {"signal_threshold": signal_threshold}
    return payload, scores


async def main() -> None:
    config = load_config("config")
    start_str = "2024-06-02"
    end_str = "2024-06-05"
    thresholds = [0.50, 0.55, 0.60, 0.65, 0.70]

    sensitivity = []
    primary_report = None
    score_samples: list[dict] = []

    for th in thresholds:
        payload, scores = await _run_one(config, start_str, end_str, th, collect_scores=(th == 0.65))
        if th == 0.65:
            primary_report = payload
            score_samples = scores
        sensitivity.append({
            "signal_threshold": th,
            "total_trades": payload["summary"]["total_trades"],
            "win_rate": payload["summary"]["win_rate"],
            "realized_pnl": payload["summary"]["realized_pnl"],
            "total_signals": payload["summary"]["total_signals"],
        })

    long_scores = [s["long"] for s in score_samples]
    short_scores = [s["short"] for s in score_samples]

    def _hist(vals: list[float], bins: list[float]) -> list[dict]:
        out = []
        for i in range(len(bins) - 1):
            lo, hi = bins[i], bins[i + 1]
            n = sum(1 for v in vals if lo <= v < hi)
            out.append({"range": f"{lo:.2f}-{hi:.2f}", "count": n})
        return out

    bins = [0, 0.15, 0.30, 0.45, 0.60, 0.75, 0.90, 1.01]
    analysis = {
        "meta": {
            "note": "Binance 历史下载因网络中断，当前使用 2024-06-02~06-05 连续 5m 段（约 3.5 天，1000 根）+ 聚合 1h；非完整 2 年样本。",
            "period": f"{start_str} ~ {end_str}",
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        },
        "primary": primary_report,
        "sensitivity": sensitivity,
        "score_distribution": {
            "long_hist": _hist(long_scores, bins),
            "short_hist": _hist(short_scores, bins),
            "long_max": max(long_scores) if long_scores else 0,
            "long_avg": round(sum(long_scores) / len(long_scores), 3) if long_scores else 0,
            "short_max": max(short_scores) if short_scores else 0,
            "short_avg": round(sum(short_scores) / len(short_scores), 3) if short_scores else 0,
            "samples_above_065": sum(1 for v in long_scores if v >= 0.65),
            "samples_above_055": sum(1 for v in long_scores if v >= 0.55),
        },
        "score_timeseries": score_samples[::12][:80],
    }

    out = Path("reports/backtest_analysis.json")
    out.write_text(json.dumps(analysis, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(analysis["sensitivity"], indent=2, ensure_ascii=False))
    print(json.dumps(analysis["score_distribution"], indent=2, ensure_ascii=False))
    print(f"\nSaved: {out}")


if __name__ == "__main__":
    asyncio.run(main())
