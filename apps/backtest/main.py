"""回测 CLI 入口."""
from __future__ import annotations

import asyncio
import datetime
import json
import sys
from decimal import Decimal
from pathlib import Path

import click

_ROOT = Path(__file__).parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.backtest.runner import BacktestRunner
from src.core.config import load_config
from src.core.models import Exchange
from src.marketdata.storage import MarketDataStorage
from src.risk.manager import RiskConfig
from src.strategies.factory import create_strategy, get_strategy_kind
from src.strategies.kline_base import KlineStrategy
from src.strategies.registry import bootstrap, resolve_name


def _parse_date(s: str) -> int:
    dt = datetime.datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc)
    return int(dt.timestamp() * 1000)


def _format_report(report, start_str: str, end_str: str) -> str:
    tp = report.exit_breakdown.get("止盈", 0)
    sl = report.exit_breakdown.get("止损", 0)
    rev = report.exit_breakdown.get("信号反转", 0)
    pnl_sign = "+" if report.realized_pnl >= 0 else ""
    lines = [
        f"回测区间: {start_str} ~ {end_str}",
        f"总平仓: {report.total_trades} 笔 | 胜率: {report.win_rate * 100:.1f}% "
        f"({report.win_trades}W / {report.loss_trades}L)",
        f"信号数: {report.total_signals}",
        f"止盈: {tp} | 止损: {sl} | 反转: {rev}",
        f"平均持仓: {report.avg_hold_hours:.1f} 小时",
        f"累计净盈亏: {pnl_sign}{float(report.realized_pnl):,.0f} USDT | "
        f"最大回撤: {float(report.max_drawdown):.1f}%",
        f"收益率: {float(report.return_pct):.2f}% | 手续费: {float(report.total_fee):,.2f} USDT",
    ]
    return "\n".join(lines)


async def _run_backtest(
    config_dir: str,
    strategy_name: str,
    start: str | None,
    end: str | None,
    output: str | None,
    force: bool,
) -> None:
    bootstrap()
    resolved = resolve_name(strategy_name)
    if get_strategy_kind(resolved) != "kline":
        raise click.ClickException(f"Strategy '{resolved}' is not a kline strategy (cannot backtest).")

    config = load_config(config_dir)
    bt_cfg = config.get("backtest", {})
    risk_cfg_raw = config.get("risk", {})

    strategy: KlineStrategy = create_strategy(resolved, config, check_enabled=not force)

    symbol = bt_cfg.get("symbol", strategy.symbol)
    db_path = bt_cfg.get("db_path") or config.get("market_data", {}).get("db_path", "data/marketdata.db")
    intervals = bt_cfg.get("intervals", [strategy.primary_tf, getattr(strategy, "trend_tf", "1h")])
    if isinstance(intervals, list):
        intervals = list(dict.fromkeys(intervals))
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

    print(_format_report(report, start_str, end_str))

    if output:
        out_path = Path(output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "strategy": resolved,
            "symbol": report.symbol,
            "intervals": report.intervals,
            "start_ms": report.start_ms,
            "end_ms": report.end_ms,
            "total_signals": report.total_signals,
            "total_trades": report.total_trades,
            "win_trades": report.win_trades,
            "loss_trades": report.loss_trades,
            "win_rate": report.win_rate,
            "avg_hold_hours": report.avg_hold_hours,
            "exit_breakdown": report.exit_breakdown,
            "realized_pnl": float(report.realized_pnl),
            "max_drawdown": float(report.max_drawdown),
            "return_pct": float(report.return_pct),
            "total_fee": float(report.total_fee),
        }
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"报告已写入: {out_path}")


def _default_strategy(config: dict) -> str:
    return config.get("strategy", {}).get("default", "btc_multi_indicator")


@click.command()
@click.option("--config-dir", default="config", help="配置目录")
@click.option("--strategy", default=None, help="策略名（默认 config strategy.default）")
@click.option("--start", default=None, help="回测起始日期 YYYY-MM-DD")
@click.option("--end", default=None, help="回测结束日期 YYYY-MM-DD")
@click.option("--output", default=None, help="JSON 报告输出路径")
@click.option("--force", is_flag=True, help="忽略 strategies.<name>.enabled=false")
def cli(
    config_dir: str,
    strategy: str | None,
    start: str | None,
    end: str | None,
    output: str | None,
    force: bool,
) -> None:
    """运行 K 线策略历史回测."""
    cfg = load_config(config_dir)
    name = strategy or _default_strategy(cfg)
    asyncio.run(_run_backtest(config_dir, name, start, end, output, force))


if __name__ == "__main__":
    cli()
