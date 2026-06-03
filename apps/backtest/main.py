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
from src.strategies.btc_multi_indicator.strategy import BTCMultiIndicatorStrategy


def _parse_date(s: str) -> int:
    dt = datetime.datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc)
    return int(dt.timestamp() * 1000)


def _build_strategy(config: dict) -> BTCMultiIndicatorStrategy:
    strat_cfg = config.get("strategies", {}).get("btc_multi_indicator", {})
    return BTCMultiIndicatorStrategy(
        symbol=strat_cfg.get("symbol", "BTCUSDT"),
        exchange=Exchange.BINANCE,
        primary_tf=strat_cfg.get("timeframe", "5m"),
        trend_tf=strat_cfg.get("trend_timeframe", "1h"),
        signal_threshold=float(strat_cfg.get("signal_threshold", 0.65)),
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


def _format_report(report, start_str: str, end_str: str) -> str:
    tp = report.exit_breakdown.get("止盈", 0)
    sl = report.exit_breakdown.get("止损", 0)
    rev = report.exit_breakdown.get("信号反转", 0)
    pnl_sign = "+" if report.realized_pnl >= 0 else ""
    lines = [
        f"回测区间: {start_str} ~ {end_str}",
        f"总平仓: {report.total_trades} 笔 | 胜率: {report.win_rate * 100:.1f}% "
        f"({report.win_trades}W / {report.loss_trades}L)",
        f"止盈: {tp} | 止损: {sl} | 反转: {rev}",
        f"平均持仓: {report.avg_hold_hours:.1f} 小时",
        f"累计净盈亏: {pnl_sign}{float(report.realized_pnl):,.0f} USDT | "
        f"最大回撤: {float(report.max_drawdown):.1f}%",
        f"收益率: {float(report.return_pct):.2f}% | 手续费: {float(report.total_fee):,.2f} USDT",
    ]
    return "\n".join(lines)


async def _run_backtest(
    config_dir: str,
    start: str | None,
    end: str | None,
    output: str | None,
) -> None:
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
            "symbol": report.symbol,
            "intervals": report.intervals,
            "start_ms": report.start_ms,
            "end_ms": report.end_ms,
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


@click.command()
@click.option("--config-dir", default="config", help="配置目录")
@click.option("--start", default=None, help="回测起始日期 YYYY-MM-DD")
@click.option("--end", default=None, help="回测结束日期 YYYY-MM-DD")
@click.option("--output", default=None, help="JSON 报告输出路径")
def cli(config_dir: str, start: str | None, end: str | None, output: str | None) -> None:
    """运行 BTC 多指标策略历史回测."""
    asyncio.run(_run_backtest(config_dir, start, end, output))


if __name__ == "__main__":
    cli()
