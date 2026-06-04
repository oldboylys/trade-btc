"""回测 CLI 入口."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import click

_ROOT = Path(__file__).parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.backtest.session import build_compact_summary, run_backtest_once
from src.core.config import load_config
from src.strategies.factory import get_strategy_kind
from src.strategies.registry import bootstrap, resolve_name


def _format_report(summary: dict, start_str: str, end_str: str) -> str:
    tp = summary.get("exit_breakdown", {}).get("止盈", 0)
    sl = summary.get("exit_breakdown", {}).get("止损", 0)
    rev = summary.get("exit_breakdown", {}).get("信号反转", 0)
    pnl = summary.get("realized_pnl", 0)
    pnl_sign = "+" if pnl >= 0 else ""
    lines = [
        f"回测区间: {start_str} ~ {end_str}",
        f"总平仓: {summary['total_trades']} 笔 | 胜率: {summary['win_rate'] * 100:.1f}% "
        f"({summary['win_trades']}W / {summary['loss_trades']}L)",
        f"信号数: {summary['total_signals']}",
        f"止盈: {tp} | 止损: {sl} | 反转: {rev}",
        f"平均持仓: {summary['avg_hold_hours']:.1f} 小时",
        f"累计净盈亏: {pnl_sign}{pnl:,.0f} USDT | "
        f"最大回撤: {summary['max_drawdown_pct']:.1f}%",
        f"收益率: {summary['return_pct']:.2f}% | 手续费: {summary['total_fee']:,.2f} USDT",
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
    result = await run_backtest_once(
        config,
        resolved,
        start=start,
        end=end,
        check_enabled=not force,
    )
    summary = build_compact_summary(result)
    print(_format_report(summary, result.start_str, result.end_str))

    if output:
        out_path = Path(output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "strategy": resolved,
            "symbol": result.report.symbol,
            "intervals": result.report.intervals,
            "start_ms": result.report.start_ms,
            "end_ms": result.report.end_ms,
            **summary,
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
