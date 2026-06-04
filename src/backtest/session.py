"""单次回测会话：配置合并、执行、报告序列化."""
from __future__ import annotations

import copy
import datetime
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

from src.backtest.runner import BacktestReport, BacktestRunner
from src.core.models import Exchange
from src.marketdata.storage import MarketDataStorage
from src.risk.manager import RiskConfig
from src.strategies.factory import create_strategy
from src.strategies.registry import bootstrap, resolve_name


def parse_date(s: str) -> int:
    dt = datetime.datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc)
    return int(dt.timestamp() * 1000)


def resolve_date_range(
    config: dict[str, Any],
    start: str | None = None,
    end: str | None = None,
) -> tuple[str, str, int, int]:
    bt_cfg = config.get("backtest", {})
    start_str = start or bt_cfg.get("start", "2024-06-01")
    start_ms = parse_date(start_str)
    if end:
        end_str = end
        end_ms = parse_date(end)
    elif bt_cfg.get("end"):
        end_str = bt_cfg["end"]
        end_ms = parse_date(end_str)
    else:
        end_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        end_ms = parse_date(end_str)
    return start_str, end_str, start_ms, end_ms


def merge_strategy_config(
    config: dict[str, Any],
    strategy_name: str,
    strat_overrides: dict[str, Any] | None = None,
    backtest_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """深拷贝 config 并合并策略/回测覆盖项."""
    merged = copy.deepcopy(config)
    resolved = resolve_name(strategy_name)
    strategies = merged.setdefault("strategies", {})
    strat_cfg = dict(strategies.get(resolved, {}))
    if strat_overrides:
        strat_cfg.update(strat_overrides)
    strategies[resolved] = strat_cfg
    if backtest_overrides:
        for key, val in backtest_overrides.items():
            if isinstance(val, dict) and isinstance(merged.get(key), dict):
                merged[key] = {**merged[key], **val}
            else:
                merged[key] = val
    return merged


@dataclass
class BacktestSessionResult:
    report: BacktestReport
    config: dict[str, Any]
    strategy_name: str
    start_str: str
    end_str: str


async def run_backtest_once(
    config: dict[str, Any],
    strategy_name: str,
    *,
    start: str | None = None,
    end: str | None = None,
    strat_overrides: dict[str, Any] | None = None,
    backtest_overrides: dict[str, Any] | None = None,
    check_enabled: bool = False,
) -> BacktestSessionResult:
    bootstrap()
    merged = merge_strategy_config(
        config, strategy_name, strat_overrides, backtest_overrides,
    )
    resolved = resolve_name(strategy_name)
    start_str, end_str, start_ms, end_ms = resolve_date_range(merged, start, end)

    strategy = create_strategy(resolved, merged, check_enabled=check_enabled)
    bt_cfg = merged.get("backtest", {})
    risk_cfg_raw = merged.get("risk", {})

    symbol = bt_cfg.get("symbol", strategy.symbol)
    db_path = bt_cfg.get("db_path") or merged.get("market_data", {}).get(
        "db_path", "data/marketdata.db",
    )
    intervals = bt_cfg.get("intervals", [strategy.primary_tf, getattr(strategy, "trend_tf", "1h")])
    if isinstance(intervals, list):
        intervals = list(dict.fromkeys(intervals))
    warmup_bars = int(bt_cfg.get("warmup_bars", 500))
    initial_balance = Decimal(str(bt_cfg.get("initial_balance", 100000)))

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
            symbol=symbol,
            exchange=Exchange.BINANCE,
            start_ms=start_ms,
            end_ms=end_ms,
        )
    finally:
        await storage.close()

    return BacktestSessionResult(
        report=report,
        config=merged,
        strategy_name=resolved,
        start_str=start_str,
        end_str=end_str,
    )


def build_report_payload(
    result: BacktestSessionResult,
    *,
    include_trades: bool = True,
) -> dict[str, Any]:
    """构建详细 JSON 报告（含月度、持仓分布、profit_factor）."""
    from collections import defaultdict

    report = result.report
    config = result.config
    strat_cfg = config.get("strategies", {}).get(result.strategy_name, {})

    monthly: dict[str, dict] = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
    for t in report.trades:
        dt = datetime.datetime.fromtimestamp(t.close_time_ms / 1000, tz=datetime.timezone.utc)
        mk = dt.strftime("%Y-%m")
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
    loss_sum = sum(t.net_pnl for t in report.trades if t.net_pnl <= 0)
    profit_factor = (
        abs(sum(t.net_pnl for t in report.trades if t.net_pnl > 0)) / abs(loss_sum)
        if report.loss_trades and loss_sum != 0
        else None
    )

    summary = {
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
    }

    payload: dict[str, Any] = {
        "meta": {
            "strategy": result.strategy_name,
            "symbol": report.symbol,
            "start": result.start_str,
            "end": result.end_str,
            "intervals": report.intervals,
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        },
        "params": dict(strat_cfg),
        "strategy_config": dict(strat_cfg),
        "summary": summary,
        "monthly": dict(sorted(monthly.items())),
        "equity_curve": [{"ts_ms": ts, "balance": bal} for ts, bal in report.equity_curve],
        "pnl_series": pnl_series,
    }
    if include_trades:
        payload["trades"] = [
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
        ]
    return payload


def build_compact_summary(result: BacktestSessionResult) -> dict[str, Any]:
    """CLI / leaderboard 用的精简 summary."""
    r = result.report
    return {
        "strategy": result.strategy_name,
        "start": result.start_str,
        "end": result.end_str,
        "total_trades": r.total_trades,
        "win_trades": r.win_trades,
        "loss_trades": r.loss_trades,
        "win_rate": round(r.win_rate, 4),
        "avg_hold_hours": round(r.avg_hold_hours, 2),
        "realized_pnl": float(r.realized_pnl),
        "return_pct": float(r.return_pct),
        "max_drawdown_pct": float(r.max_drawdown),
        "total_fee": float(r.total_fee),
        "total_signals": r.total_signals,
        "exit_breakdown": r.exit_breakdown,
    }
