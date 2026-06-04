"""移动止损触达分析：统计被新止损打掉后是否仍可到 TP."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.backtest.runner import BacktestRunner
from src.backtest.session import merge_strategy_config, resolve_date_range
from src.core.config import load_config
from src.core.models import Exchange, Kline, PositionSide
from src.execution.trailing_stop import parse_trail_levels, progress_toward_tp
from src.marketdata.storage import MarketDataStorage
from src.risk.manager import RiskConfig
from src.strategies.factory import create_strategy
from src.strategies.registry import bootstrap


@dataclass
class OpenTrack:
    symbol: str
    direction: str
    entry: float
    tp: float
    original_sl: float
    open_ms: int
    max_progress: float = 0.0
    max_favorable_price: float = 0.0
    trail_events: list[dict] = field(default_factory=list)
    final_sl: float = 0.0


class TrailAnalyzer:
    """回测期间跟踪持仓 MFE 与移动止损事件."""

    def __init__(self, primary_tf: str = "5m") -> None:
        self.primary_tf = primary_tf
        self._open: dict[str, OpenTrack] = {}
        self.completed: list[dict] = []
        self._primary_klines: list[Kline] = []

    def set_primary_klines(self, klines: list[Kline]) -> None:
        self._primary_klines = [k for k in klines if k.interval == self.primary_tf]

    def on_bar(self, kline: Kline, paper_ex) -> None:
        if kline.interval != self.primary_tf:
            return
        symbol = kline.symbol
        pos = paper_ex.position_book.get_position(symbol)
        if pos and pos.qty > 0 and symbol not in self._open:
            tp_sl = paper_ex.position_book._tp_sl.get(symbol, (0.0, 0.0))
            self._open[symbol] = OpenTrack(
                symbol=symbol,
                direction=pos.side.value,
                entry=float(pos.entry_price),
                tp=float(tp_sl[0]),
                original_sl=float(tp_sl[1]),
                open_ms=kline.close_time,
                max_favorable_price=float(pos.entry_price),
                final_sl=float(tp_sl[1]),
            )
        rec = self._open.get(symbol)
        if not rec:
            return
        side = PositionSide.LONG if rec.direction == "LONG" else PositionSide.SHORT
        entry = Decimal(str(rec.entry))
        tp = Decimal(str(rec.tp))
        if side == PositionSide.LONG:
            favorable = float(kline.high)
            rec.max_favorable_price = max(rec.max_favorable_price, favorable)
            prog = progress_toward_tp(side, entry, tp, Decimal(str(favorable)))
        else:
            favorable = float(kline.low)
            rec.max_favorable_price = min(rec.max_favorable_price, favorable)
            prog = progress_toward_tp(side, entry, tp, Decimal(str(favorable)))
        rec.max_progress = max(rec.max_progress, prog)

    def on_trail(self, event: dict) -> None:
        rec = self._open.get(event["symbol"])
        if rec:
            rec.trail_events.append(event)
            rec.final_sl = event["new_sl"]

    def on_close(self, event: dict) -> None:
        symbol = event.get("symbol", "")
        rec = self._open.pop(symbol, None)
        if rec is None:
            return
        max_prog = rec.max_progress
        trail_applied = bool(event.get("trail_applied")) or len(rec.trail_events) > 0
        close_reason = event["close_reason"]
        exit_p = float(event["exit_price"])
        orig_sl = rec.original_sl
        final_sl = rec.final_sl if rec.trail_events else float(event.get("sl", rec.final_sl))
        tol = max(abs(rec.entry) * 0.001, 5.0)
        sl_moved = abs(final_sl - orig_sl) > tol * 0.1
        dist_final = abs(exit_p - final_sl)
        dist_orig = abs(exit_p - orig_sl)
        exited_at_trail_sl = (
            close_reason == "止损"
            and trail_applied
            and sl_moved
            and dist_final <= dist_orig
        )
        exited_at_original_sl = close_reason == "止损" and not exited_at_trail_sl
        row = {
            **event,
            "entry": rec.entry,
            "tp": rec.tp,
            "original_sl": rec.original_sl,
            "final_sl": rec.final_sl,
            "max_progress_pct": round(max_prog * 100, 2),
            "max_favorable_price": rec.max_favorable_price,
            "tp_hit_during_hold": max_prog >= 1.0,
            "trail_event_count": len(rec.trail_events),
            "trail_events": rec.trail_events,
            "exited_at_trail_sl": exited_at_trail_sl,
            "exited_at_original_sl": exited_at_original_sl,
            "sl_moved": sl_moved,
            "side": rec.direction,
        }
        self.completed.append(row)


def _scan_tp_after_exit(
    klines: list[Kline],
    trade: dict,
    horizon_ms: int = 7 * 24 * 3600_000,
) -> dict:
    close_ms = trade["close_time_ms"]
    tp = trade["tp"]
    side = trade["side"]
    end_ms = close_ms + horizon_ms
    max_after = trade["exit_price"]
    min_after = trade["exit_price"]
    tp_hit_after = False
    bars = 0
    for k in klines:
        if k.open_time <= close_ms:
            continue
        if k.open_time > end_ms:
            break
        bars += 1
        max_after = max(max_after, float(k.high))
        min_after = min(min_after, float(k.low))
        if side == "LONG" and float(k.high) >= tp:
            tp_hit_after = True
            break
        if side == "SHORT" and float(k.low) <= tp:
            tp_hit_after = True
            break
    return {
        "tp_hit_after_exit": tp_hit_after,
        "max_price_after_exit": max_after,
        "min_price_after_exit": min_after,
        "bars_scanned_after_exit": bars,
    }


def summarize(trades: list[dict], trail_levels: list[tuple[float, float]]) -> dict:
    trail_sl_exits = [t for t in trades if t["exited_at_trail_sl"]]
    orig_sl_exits = [t for t in trades if t.get("exited_at_original_sl")]
    trail_activated = [t for t in trades if t.get("trail_applied")]
    would_tp_after = [t for t in trail_sl_exits if t.get("tp_hit_after_exit")]
    reached_tp_in_hold = [t for t in trail_sl_exits if t.get("tp_hit_during_hold")]
    high_water = [t for t in trail_sl_exits if t["max_progress_pct"] >= 50 and not t["tp_hit_during_hold"]]

    prog_buckets = {"<25%": 0, "25-50%": 0, "50-80%": 0, "80-100%": 0, ">=100%(达TP)": 0}
    for t in trail_sl_exits:
        p = t["max_progress_pct"]
        if p >= 100:
            prog_buckets[">=100%(达TP)"] += 1
        elif p >= 80:
            prog_buckets["80-100%"] += 1
        elif p >= 50:
            prog_buckets["50-80%"] += 1
        elif p >= 25:
            prog_buckets["25-50%"] += 1
        else:
            prog_buckets["<25%"] += 1

    activated_prog = [t["max_progress_pct"] for t in trail_activated]

    return {
        "trail_levels": trail_levels,
        "total_trades": len(trades),
        "trail_activated_trades": len(trail_activated),
        "exited_at_original_sl": len(orig_sl_exits),
        "exited_at_trail_sl": len(trail_sl_exits),
        "tp_hit_during_hold_on_trail_exits": len(reached_tp_in_hold),
        "tp_hit_after_trail_sl_exit": len(would_tp_after),
        "tp_hit_after_trail_sl_pct": (
            round(len(would_tp_after) / len(trail_sl_exits) * 100, 1) if trail_sl_exits else 0
        ),
        "trail_exits_peaked_50pct_but_missed_tp": len(high_water),
        "avg_max_progress_trail_exits_pct": (
            round(sum(t["max_progress_pct"] for t in trail_sl_exits) / len(trail_sl_exits), 1)
            if trail_sl_exits else 0
        ),
        "avg_max_progress_trail_activated_pct": (
            round(sum(activated_prog) / len(activated_prog), 1) if activated_prog else 0
        ),
        "max_progress_buckets_trail_exits": prog_buckets,
        "trail_sl_exit_samples": [
            {
                "direction": t["direction"],
                "entry": t["entry"],
                "tp": t["tp"],
                "exit": t["exit_price"],
                "final_sl": t["final_sl"],
                "max_progress_pct": t["max_progress_pct"],
                "max_favorable_price": t["max_favorable_price"],
                "tp_hit_after_exit": t.get("tp_hit_after_exit"),
                "tp_hit_during_hold": t.get("tp_hit_during_hold"),
                "net_pnl": t.get("net_pnl"),
            }
            for t in sorted(trail_sl_exits, key=lambda x: -x["max_progress_pct"])[:15]
        ],
    }


async def run_analysis(
    config_dir: str,
    start: str | None,
    end: str | None,
    strat_overrides: dict | None,
    output: str,
) -> dict:
    bootstrap()
    config = load_config(config_dir)
    merged = merge_strategy_config(
        config, "btc_multi_indicator_v2", strat_overrides,
    )
    start_str, end_str, start_ms, end_ms = resolve_date_range(merged, start, end)
    strategy = create_strategy("btc_multi_indicator_v2", merged, check_enabled=False)
    trail_levels = parse_trail_levels(merged["strategies"]["btc_multi_indicator_v2"])

    bt_cfg = merged.get("backtest", {})
    risk_cfg = merged.get("risk", {})
    symbol = bt_cfg.get("symbol", strategy.symbol)
    db_path = bt_cfg.get("db_path") or merged.get("market_data", {}).get("db_path", "data/marketdata.db")
    intervals = bt_cfg.get("intervals", [strategy.primary_tf, getattr(strategy, "trend_tf", "1h")])
    warmup_bars = int(bt_cfg.get("warmup_bars", 500))
    initial_balance = Decimal(str(bt_cfg.get("initial_balance", 100000)))

    analyzer = TrailAnalyzer(primary_tf=strategy.primary_tf)
    storage = MarketDataStorage(db_path)
    await storage.connect()
    runner = BacktestRunner(
        storage=storage,
        strategy=strategy,
        initial_balance=initial_balance,
        risk_config=RiskConfig(
            max_position_usdt=Decimal(str(risk_cfg.get("max_position_usdt", 20000))),
            max_single_order_usdt=Decimal(str(risk_cfg.get("max_single_order_usdt", 5000))),
            max_daily_loss_usdt=Decimal(str(risk_cfg.get("max_daily_loss_usdt", 1000))),
        ),
        warmup_bars=warmup_bars,
        intervals=list(dict.fromkeys(intervals)),
        bar_hook=analyzer.on_bar,
        trail_hook=analyzer.on_trail,
        trade_close_hook=analyzer.on_close,
    )
    try:
        report = await runner.run(symbol, Exchange.BINANCE, start_ms, end_ms)
        from src.backtest.replay import merge_klines_for_replay

        klines_by_interval: dict[str, list[Kline]] = {}
        for interval in runner.intervals:
            klines_by_interval[interval] = await storage.load_klines(
                symbol=symbol, exchange=Exchange.BINANCE, interval=interval,
                start_ms=start_ms, end_ms=end_ms,
            )
        merged_klines = merge_klines_for_replay(klines_by_interval)
        analyzer.set_primary_klines(merged_klines)
        for rec in analyzer.completed:
            extra = _scan_tp_after_exit(analyzer._primary_klines, rec)
            rec.update(extra)

        summary = summarize(analyzer.completed, trail_levels)
        summary["start"] = start_str
        summary["end"] = end_str
        summary["realized_pnl"] = float(report.realized_pnl)
        summary["total_trades_backtest"] = report.total_trades
        out = Path(output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        return summary
    finally:
        await storage.close()


def main() -> None:
    p = argparse.ArgumentParser(description="分析移动止损触达与错失 TP 情况")
    p.add_argument("--config-dir", default="config")
    p.add_argument("--start", default="2024-06-01")
    p.add_argument("--end", default=None)
    p.add_argument("--output", default="reports/trail_analysis.json")
    p.add_argument(
        "--legacy",
        action="store_true",
        help="使用旧版单档 25%%→10%% 止损",
    )
    args = p.parse_args()
    overrides = {"trail_levels": [[0.25, 0.10]]} if args.legacy else None
    summary = asyncio.run(run_analysis(
        args.config_dir, args.start, args.end, overrides, args.output,
    ))
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
