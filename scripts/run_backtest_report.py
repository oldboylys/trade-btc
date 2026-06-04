"""运行回测并导出详细 JSON 报告（供可视化使用）."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.backtest.session import build_report_payload, run_backtest_once
from src.backtest.sweep import parse_set_args
from src.core.config import load_config


async def run(
    config_dir: str = "config",
    strategy: str = "btc_multi_indicator_v2",
    start: str | None = None,
    end: str | None = None,
    set_overrides: list[str] | None = None,
    output: str | None = None,
) -> Path:
    config = load_config(config_dir)
    strat_overrides = parse_set_args(set_overrides or [])
    if not set_overrides and strategy == "btc_multi_indicator":
        pass

    result = await run_backtest_once(
        config,
        strategy,
        start=start,
        end=end,
        strat_overrides=strat_overrides or None,
        check_enabled=False,
    )
    payload = build_report_payload(result)

    out_dir = Path("reports")
    out_dir.mkdir(exist_ok=True)
    out_path = Path(output) if output else out_dir / f"backtest_{strategy}.json"
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(payload["summary"], indent=2, ensure_ascii=False))
    print(f"\nFull report: {out_path}")
    return out_path


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--config-dir", default="config")
    p.add_argument("--strategy", default="btc_multi_indicator_v2")
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    p.add_argument("--set", action="append", default=[], help="覆盖策略参数 key=value")
    p.add_argument("--threshold", type=float, default=None, help="快捷覆盖 signal_threshold")
    p.add_argument("--output", default=None, help="JSON 输出路径")
    args = p.parse_args()
    overrides = list(args.set)
    if args.threshold is not None:
        overrides.append(f"signal_threshold={args.threshold}")
    asyncio.run(run(
        args.config_dir,
        args.strategy,
        args.start,
        args.end,
        overrides,
        args.output,
    ))
