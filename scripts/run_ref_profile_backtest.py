"""按参考扫参结果 + 满仓/动态止盈/止盈续开 回测."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.backtest.session import build_compact_summary, build_report_payload, run_backtest_once
from src.core.config import load_config


REF_RUN = _ROOT / "reports/sweeps/btc_v2/runs/39b5cf95e408.json"
OUTPUT = _ROOT / "reports/backtest_btc_v2_full100_reentry.json"


def _ref_params() -> dict:
    data = json.loads(REF_RUN.read_text(encoding="utf-8"))
    p = dict(data.get("params") or data.get("strategy_config") or {})
    for k in ("enabled", "symbol", "timeframe", "trend_timeframe"):
        p.pop(k, None)
    return p


async def main() -> None:
    config = load_config("config")
    ref = _ref_params()
    overrides = {
        **ref,
        "position_pct": 1.0,
        "use_position_pct": True,
        "use_dynamic_tp": True,
        "tp_pct_min": 0.015,
        "tp_pct_max": 0.08,
        "reentry_after_tp_enabled": True,
        "reentry_after_tp_bars": 24,
        "reentry_score_mult": 0.85,
    }
    result = await run_backtest_once(
        config,
        "btc_multi_indicator_v2",
        start="2024-06-01",
        strat_overrides=overrides,
        backtest_overrides={
            "risk": {
                "max_single_order_usdt": 500000,
                "max_position_usdt": 500000,
            },
        },
        check_enabled=False,
    )
    payload = build_report_payload(result)
    payload["profile"] = {
        "ref_run_id": "39b5cf95e408",
        "overrides": overrides,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    summary = build_compact_summary(result)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\nFull report: {OUTPUT}")


if __name__ == "__main__":
    asyncio.run(main())
