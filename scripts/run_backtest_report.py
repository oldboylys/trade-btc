"""运行回测并导出详细 JSON 报告（供可视化使用）."""
from __future__ import annotations

import asyncio
import datetime
import json
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from apps.backtest.main import _build_strategy, _parse_date
from src.backtest.runner import BacktestRunner
from src.core.config import load_config
from src.core.models import Exchange
from src.marketdata.storage import MarketDataStorage
from src.risk.manager import RiskConfig


def _month_key(ms: int) -> str:
    dt = datetime.datetime.fromtimestamp(ms / 1000, tz=datetime.timezone.utc)
    return dt.strftime("%Y-%m")


def _build_payload(report, config: dict, start_str: str, end_str: str) -> dict:
    strat_cfg = config.get("strategies", {}).get("btc_multi_indicator", {})
    monthly: dict[str, dict] = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
    for t in report.trades:
        mk = _month_key(t.close_time_ms)
        monthly[mk]["trades"] += 1
        monthly[mk]["pnl"] += t.net_pnl
        if t.net_pnl > 0:
            monthly[mk]["wins"] += 1

    pnl_series = []
    cum = 0.0
    for t in sorted(report.trades, key=lambda x: x.close_time_ms):
        cum += t.net_pnl
        pnl_series.append({
            "close_time_ms": t.close_time_ms,
            "close_date": datetime.datetime.fromtimestamp(
                t.close_time_ms / 1000, tz=datetime.timezone.utc,
            ).strftime("%Y-%m-%d"),
            "cum_pnl": round(cum, 2),
            "net_pnl": round(t.net_pnl, 2),
            "close_reason": t.close_reason,
            "direction": t.direction,
        })

    hold_buckets = {"<4h": 0, "4-12h": 0, "12-24h": 0, ">24h": 0}
    for t in report.trades:
        h = t.hold_ms / 3_600_000
        if h < 4:
            hold_buckets["<4h"] += 1
        elif h < 12:
            hold_buckets["4-12h"] += 1
        elif h < 24:
            hold_buckets["12-24h"] += 1
        else:
            hold_buckets[">24h"] += 1

    avg_win = (
        sum(t.net_pnl for t in report.trades if t.net_pnl > 0) / report.win_trades
        if report.win_trades else 0
    )
    avg_loss = (
        sum(t.net_pnl for t in report.trades if t.net_pnl <= 0) / report.loss_trades
        if report.loss_trades else 0
    )
    profit_factor = (
        abs(sum(t.net_pnl for t in report.trades if t.net_pnl > 0))
        / abs(sum(t.net_pnl for t in report.trades if t.net_pnl <= 0))
        if report.loss_trades and sum(t.net_pnl for t in report.trades if t.net_pnl <= 0) != 0
        else None
    )

    return {
        "meta": {
            "symbol": report.symbol,
            "start": start_str,
            "end": end_str,
            "intervals": report.intervals,
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        },
        "strategy_config": {
            "signal_threshold": strat_cfg.get("signal_threshold"),
            "reversal_threshold": strat_cfg.get("reversal_threshold"),
            "require_1h_trend": strat_cfg.get("require_1h_trend"),
            "tp_pct": strat_cfg.get("tp_pct"),
            "sl_pct": strat_cfg.get("sl_pct"),
            "max_position_usdt": strat_cfg.get("max_position_usdt"),
        },
        "summary": {
            "total_trades": report.total_trades,
            "win_trades": report.win_trades,
            "loss_trades": report.loss_trades,
            "win_rate": round(report.win_rate, 4),
            "avg_hold_hours": round(report.avg_hold_hours, 2),
            "realized_pnl": float(report.realized_pnl),
            "return_pct": float(report.return_pct),
            "max_drawdown_pct": float(report.max_drawdown),
            "total_fee": float(report.total_fee),
            "total_signals": report.total_signals,
            "initial_balance": float(report.initial_balance),
            "final_balance": float(report.final_balance),
            "avg_win_usdt": round(avg_win, 2),
            "avg_loss_usdt": round(avg_loss, 2),
            "profit_factor": round(profit_factor, 2) if profit_factor else None,
            "exit_breakdown": report.exit_breakdown,
            "hold_buckets": hold_buckets,
        },
        "monthly": dict(sorted(monthly.items())),
        "equity_curve": [
            {"ts_ms": ts, "balance": bal} for ts, bal in report.equity_curve
        ],
        "pnl_series": pnl_series,
        "trades": [
            {
                "direction": t.direction,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "qty": t.qty,
                "gross_pnl": t.gross_pnl,
                "fee": t.fee,
                "net_pnl": t.net_pnl,
                "hold_hours": round(t.hold_ms / 3_600_000, 2),
                "close_reason": t.close_reason,
                "open_time_ms": t.open_time_ms,
                "close_time_ms": t.close_time_ms,
            }
            for t in report.trades
        ],
    }


async def run(
    config_dir: str = "config",
    start: str | None = None,
    end: str | None = None,
    signal_threshold: float | None = None,
    output: str | None = None,
) -> Path:
    config = load_config(config_dir)
    bt_cfg = config.get("backtest", {})
    risk_cfg_raw = config.get("risk", {})

    symbol = bt_cfg.get("symbol", "BTCUSDT")
    db_path = bt_cfg.get("db_path") or config.get("market_data", {}).get("db_path", "data/marketdata.db")
    intervals = bt_cfg.get("intervals", ["5m", "1h"])
    warmup_bars = int(bt_cfg.get("warmup_bars", 500))
    initial_balance = Decimal(str(bt_cfg.get("initial_balance", 100000)))

    start_str = start or bt_cfg.get("start", "2024-06-01")
    start_ms = _parse_date(start_str)
    if end:
        end_str = end
        end_ms = _parse_date(end)
    elif bt_cfg.get("end"):
        end_str = bt_cfg["end"]
        end_ms = _parse_date(end_str)
    else:
        end_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        end_ms = _parse_date(end_str)

    storage = MarketDataStorage(db_path)
    await storage.connect()
    strategy = _build_strategy(config)
    if signal_threshold is not None:
        strategy.signal_threshold = signal_threshold
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
            symbol=symbol,
            exchange=Exchange.BINANCE,
            start_ms=start_ms,
            end_ms=end_ms,
        )
    finally:
        await storage.close()

    out_dir = Path("reports")
    out_dir.mkdir(exist_ok=True)
    out_path = Path(output) if output else out_dir / "backtest_full.json"
    payload = _build_payload(report, config, start_str, end_str)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(payload["summary"], indent=2, ensure_ascii=False))
    print(f"\nFull report: {out_path}")
    return out_path


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--config-dir", default="config")
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    p.add_argument("--threshold", type=float, default=None, help="覆盖 signal_threshold")
    p.add_argument("--output", default=None, help="JSON 输出路径")
    args = p.parse_args()
    asyncio.run(run(args.config_dir, args.start, args.end, args.threshold, args.output))
