"""参数扫回测：run_id、网格展开、leaderboard 持久化."""
from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from itertools import product
from pathlib import Path
from typing import Any, Iterator

SWEEP_DIR = Path("reports/sweeps/btc_v2")

LEADERBOARD_COLUMNS = [
    "run_id",
    "generated_at",
    "start",
    "end",
    "signal_threshold",
    "reversal_threshold",
    "tp_pct",
    "sl_pct",
    "rsi_oversold_max",
    "rsi_overbought_min",
    "vol_spike_ratio",
    "min_score_edge",
    "total_trades",
    "win_rate",
    "realized_pnl",
    "return_pct",
    "max_drawdown_pct",
    "total_fee",
    "total_signals",
    "avg_hold_hours",
    "profit_factor",
]


def compute_run_id(
    strategy_name: str,
    start: str,
    end: str,
    params: dict[str, Any],
) -> str:
    payload = {
        "strategy": strategy_name,
        "start": start,
        "end": end,
        "params": {k: params[k] for k in sorted(params)},
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def expand_grid(grid: dict[str, list[Any]]) -> list[dict[str, Any]]:
    if not grid:
        return [{}]
    keys = sorted(grid.keys())
    values = [grid[k] if isinstance(grid[k], list) else [grid[k]] for k in keys]
    return [dict(zip(keys, combo)) for combo in product(*values)]


def load_grid_yaml(path: Path) -> dict[str, Any]:
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    base = data.get("base", {})
    fixed = data.get("fixed", {})
    grid = data.get("grid", {})
    backtest_overrides = data.get("backtest_overrides", {})
    strategy = fixed.get("strategy", "btc_multi_indicator_v2")

    combos = expand_grid(grid)
    runs = []
    for params in combos:
        merged_params = {**fixed, **params}
        merged_params.pop("strategy", None)
        runs.append({
            "strategy": strategy,
            "start": base.get("start"),
            "end": base.get("end"),
            "params": merged_params,
            "backtest_overrides": backtest_overrides,
        })
    return {"runs": runs, "meta": data}


def parse_set_args(set_args: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in set_args:
        if "=" not in item:
            raise ValueError(f"Invalid --set format: {item!r} (expected key=value)")
        key, val = item.split("=", 1)
        key = key.strip()
        val = val.strip()
        if val.lower() in ("true", "false"):
            out[key] = val.lower() == "true"
        else:
            try:
                if "." in val:
                    out[key] = float(val)
                else:
                    out[key] = int(val)
            except ValueError:
                out[key] = val
    return out


def leaderboard_row(run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    params = payload.get("params", {})
    summary = payload.get("summary", {})
    meta = payload.get("meta", {})
    return {
        "run_id": run_id,
        "generated_at": meta.get("generated_at", ""),
        "start": meta.get("start", ""),
        "end": meta.get("end", ""),
        "signal_threshold": params.get("signal_threshold"),
        "reversal_threshold": params.get("reversal_threshold"),
        "tp_pct": params.get("tp_pct"),
        "sl_pct": params.get("sl_pct"),
        "rsi_oversold_max": params.get("rsi_oversold_max"),
        "rsi_overbought_min": params.get("rsi_overbought_min"),
        "vol_spike_ratio": params.get("vol_spike_ratio"),
        "min_score_edge": params.get("min_score_edge"),
        "total_trades": summary.get("total_trades"),
        "win_rate": summary.get("win_rate"),
        "realized_pnl": summary.get("realized_pnl"),
        "return_pct": summary.get("return_pct"),
        "max_drawdown_pct": summary.get("max_drawdown_pct"),
        "total_fee": summary.get("total_fee"),
        "total_signals": summary.get("total_signals"),
        "avg_hold_hours": summary.get("avg_hold_hours"),
        "profit_factor": summary.get("profit_factor"),
    }


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def write_run_json(run_dir: Path, run_id: str, payload: dict[str, Any]) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    out = run_dir / f"{run_id}.json"
    payload = {**payload, "run_id": run_id}
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def rebuild_leaderboard(sweep_dir: Path | None = None) -> list[dict[str, Any]]:
    sweep_dir = sweep_dir or SWEEP_DIR
    runs_dir = sweep_dir / "runs"
    rows: list[dict[str, Any]] = []
    if runs_dir.is_dir():
        for path in sorted(runs_dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                rid = payload.get("run_id", path.stem)
                rows.append(leaderboard_row(rid, payload))
            except (json.JSONDecodeError, OSError):
                continue
    rows.sort(key=lambda r: (r.get("realized_pnl") or 0), reverse=True)

    sweep_dir.mkdir(parents=True, exist_ok=True)
    csv_path = sweep_dir / "leaderboard.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=LEADERBOARD_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    json_path = sweep_dir / "leaderboard.json"
    json_path.write_text(
        json.dumps(rows, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    return rows


def run_exists(sweep_dir: Path, run_id: str) -> bool:
    return (sweep_dir / "runs" / f"{run_id}.json").is_file()
