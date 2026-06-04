"""btc_multi_indicator_v2 参数扫回测：网格 / 单次 --set，写入 leaderboard."""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.backtest.session import (
    build_report_payload,
    merge_strategy_config,
    resolve_date_range,
    run_backtest_once,
)
from src.backtest.sweep import (
    SWEEP_DIR,
    append_jsonl,
    compute_run_id,
    load_grid_yaml,
    parse_set_args,
    rebuild_leaderboard,
    run_exists,
    write_run_json,
)
from src.core.config import load_config


async def _run_one(
    config: dict,
    strategy: str,
    start: str | None,
    end: str | None,
    params: dict,
    backtest_overrides: dict,
    sweep_dir: Path,
    resume: bool,
) -> str | None:
    merged = merge_strategy_config(
        config, strategy, params, backtest_overrides or None,
    )
    start_str, end_str, _, _ = resolve_date_range(merged, start, end)
    run_id = compute_run_id(strategy, start_str, end_str, params)
    if resume and run_exists(sweep_dir, run_id):
        print(f"  skip {run_id} (exists)")
        return None

    print(f"  run {run_id} params={params}")
    result = await run_backtest_once(
        config,
        strategy,
        start=start_str,
        end=end_str,
        strat_overrides=params,
        backtest_overrides=backtest_overrides or None,
        check_enabled=False,
    )
    payload = build_report_payload(result)
    write_run_json(sweep_dir / "runs", run_id, payload)
    append_jsonl(sweep_dir / "runs.jsonl", {
        "run_id": run_id,
        "generated_at": payload["meta"]["generated_at"],
        "params": params,
        "summary": payload["summary"],
    })
    s = payload["summary"]
    print(
        f"    -> trades={s['total_trades']} win_rate={s['win_rate']:.1%} "
        f"pnl={s['realized_pnl']:+.0f} fee={s['total_fee']:.0f}",
    )
    return run_id


async def main_async(args: argparse.Namespace) -> None:
    config = load_config(args.config_dir)
    sweep_dir = Path(args.output_dir)
    sweep_dir.mkdir(parents=True, exist_ok=True)

    jobs: list[dict] = []
    if args.grid:
        spec = load_grid_yaml(Path(args.grid))
        jobs = spec["runs"]
        if args.max_runs:
            jobs = jobs[: args.max_runs]
    elif args.set:
        params = parse_set_args(args.set)
        strategy = args.strategy or "btc_multi_indicator_v2"
        jobs = [{
            "strategy": strategy,
            "start": args.start,
            "end": args.end,
            "params": params,
            "backtest_overrides": {},
        }]
    else:
        raise SystemExit("Specify --grid or at least one --set key=value")

    print(f"Sweep: {len(jobs)} job(s) -> {sweep_dir}")
    new_count = 0
    for i, job in enumerate(jobs, 1):
        print(f"[{i}/{len(jobs)}]")
        rid = await _run_one(
            config,
            job["strategy"],
            job.get("start") or args.start,
            job.get("end") or args.end,
            job.get("params", {}),
            job.get("backtest_overrides", {}),
            sweep_dir,
            args.resume,
        )
        if rid:
            new_count += 1

    rows = rebuild_leaderboard(sweep_dir)
    print(f"\nDone: {new_count} new run(s), {len(rows)} total in leaderboard")
    print("\nTop 5 by realized_pnl:")
    for r in rows[:5]:
        print(
            f"  {r['run_id']}  th={r.get('signal_threshold')}  "
            f"trades={r.get('total_trades')}  pnl={r.get('realized_pnl'):+.0f}  "
            f"win={float(r.get('win_rate') or 0):.1%}",
        )
    print(f"\nLeaderboard: {sweep_dir / 'leaderboard.csv'}")


def main() -> None:
    p = argparse.ArgumentParser(description="btc_multi_indicator_v2 参数扫回测")
    p.add_argument("--config-dir", default="config")
    p.add_argument("--grid", default=None, help="网格 YAML，如 config/sweeps/btc_v2_grid.yaml")
    p.add_argument("--strategy", default="btc_multi_indicator_v2")
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    p.add_argument("--set", action="append", default=[], help="单次覆盖，如 signal_threshold=0.52")
    p.add_argument("--output-dir", default=str(SWEEP_DIR))
    p.add_argument("--resume", action="store_true", help="跳过已存在 run_id")
    p.add_argument("--max-runs", type=int, default=None, help="网格最多执行 N 组")
    args = p.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
